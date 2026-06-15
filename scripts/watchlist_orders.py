#!/usr/bin/env python3
"""Automate "Шаг 2 — Ордера по watchlist" with Telegram confirmation.

Producer/consumer over a shared ledger (``trading-data/logs/pending_orders_<date>.json``):

  send    (producer) — for each watchlist candidate whose thesis is ENTRY_READY,
            send a Telegram card with "✅ Открыть" / "✋ Не открывать" buttons and
            record it in the ledger as ``pending``. Places NO orders. Skipped
            entirely when the exposure gate is not ``allow`` or the watchlist is
            stale. Runs from the premarket slot and standalone.

  listen  (consumer / daemon) — long-poll Telegram for button taps and watch IB
            for fills. On "Открыть": place a native bracket in Interactive Brokers
            (entry buy-stop + protective stop + take-profit) — only when the
            two-lock guard is satisfied (``IB_ALLOW_ORDER_PLACEMENT=true`` + ``--live``).
            The thesis stays ENTRY_READY; it transitions to ACTIVE (with the real
            fill price) only when the entry order actually fills. On "Не открывать"
            or timeout the thesis stays ENTRY_READY.

Reuses the scheduler's gate/watchlist/thesis helpers (``run_trading_schedule``),
the IB write helpers (``place_ib_bracket``), and the interactive Telegram helpers
(``telegram_interactive``).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Make the skill scripts importable (each carries its own sibling deps).
sys.path.insert(0, str(PROJECT_ROOT / "skills" / "ib-portfolio-manager" / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "skills" / "send-telegram" / "scripts"))

import place_ib_bracket as pib  # noqa: E402
import run_trading_schedule as sched  # noqa: E402 — reuses gate/watchlist/thesis helpers + loads .env
import telegram_interactive as ti  # noqa: E402

ENTRY_READY = "ENTRY_READY"
DEFAULT_WINDOW_SEC = 25_200  # ~7h: 15:00 CET cards through ~22:00 US close
POLL_TIMEOUT = 25  # getUpdates long-poll hold (seconds)
FILL_CHECK_EVERY_SEC = 30
MAX_FILL_TRANSITION_ATTEMPTS = 5  # give up flipping ENTRY_READY->ACTIVE after N tries

log = logging.getLogger("watchlist_orders")


# --------------------------------------------------------------------------- #
# Ledger I/O
# --------------------------------------------------------------------------- #
def _logs_dir() -> Path:
    return sched.TRADING_DATA_DIR / "logs"


def ledger_path(date_str: str) -> Path:
    return _logs_dir() / f"pending_orders_{date_str}.json"


def offset_path() -> Path:
    return _logs_dir() / "telegram_offset.json"


def _read_json_file(path: Path) -> dict | None:
    """Tolerant local JSON-object reader (kept independent of the scheduler's
    ``_read_json`` so ledger I/O survives tests that stub that helper)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_ledger(date_str: str) -> dict:
    data = _read_json_file(ledger_path(date_str))
    if not isinstance(data, dict) or "orders" not in data:
        return {"date": date_str, "mode": None, "orders": {}}
    data.setdefault("orders", {})
    return data


def save_ledger(date_str: str, ledger: dict) -> None:
    _atomic_write_json(ledger_path(date_str), ledger)


def load_offset() -> int | None:
    data = _read_json_file(offset_path())
    if isinstance(data, dict) and isinstance(data.get("offset"), int):
        return data["offset"]
    return None


def save_offset(offset: int) -> None:
    _atomic_write_json(offset_path(), {"offset": offset})


def _atomic_write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# Pure matching: watchlist candidate -> ENTRY_READY thesis -> order card
# --------------------------------------------------------------------------- #
def _entry_ready_index(theses: list[dict]) -> tuple[dict, dict]:
    """Index ENTRY_READY theses by thesis_id and by (ticker, side)."""
    by_id: dict[str, dict] = {}
    by_ticker_side: dict[tuple[str, str], dict] = {}
    for t in theses:
        if str(t.get("status", "")).upper() != ENTRY_READY:
            continue
        tid = t.get("thesis_id")
        if not tid:
            continue
        by_id[tid] = t
        key = (str(t.get("ticker", "")).upper(), str(t.get("side") or "long").lower())
        by_ticker_side.setdefault(key, t)
    return by_id, by_ticker_side


def match_thesis(cand: dict, by_id: dict, by_ticker_side: dict) -> dict | None:
    """Match a watchlist candidate to an ENTRY_READY thesis (id first, then ticker+side)."""
    tid = cand.get("thesis_id")
    if tid and tid in by_id:
        return by_id[tid]
    key = (str(cand.get("ticker", "")).upper(), str(cand.get("side") or "long").lower())
    return by_ticker_side.get(key)


def build_card(cand: dict, thesis: dict, date_str: str) -> dict | None:
    """Build an order card from a candidate's geometry; None when geometry is incomplete."""
    pivot, stop, target, shares = (
        cand.get("pivot"),
        cand.get("stop"),
        cand.get("target"),
        cand.get("shares"),
    )
    if any(v in (None, "") for v in (pivot, stop, target, shares)):
        return None
    side = str(cand.get("side") or thesis.get("side") or "long").lower()
    tid = thesis["thesis_id"]
    return {
        "thesis_id": tid,
        "ticker": cand.get("ticker"),
        "side": side,
        "company": cand.get("company_name") or cand.get("company"),
        "setup": cand.get("setup"),
        "pivot": pivot,
        "worst_entry": cand.get("worst_entry"),
        "stop": stop,
        "target": target,
        "shares": shares,
        "risk_dollars": cand.get("risk_dollars"),
        "coid": pib.coid_for(tid, date_str),
    }


def select_cards(wl: dict, theses: list[dict], date_str: str) -> list[dict]:
    """All order cards for watchlist candidates that map to an ENTRY_READY thesis."""
    by_id, by_ts = _entry_ready_index(theses)
    cards: list[dict] = []
    for cand in wl.get("candidates", []):
        if not isinstance(cand, dict):
            continue
        thesis = match_thesis(cand, by_id, by_ts)
        if not thesis:
            continue
        card = build_card(cand, thesis, date_str)
        if card:
            cards.append(card)
    return cards


# --------------------------------------------------------------------------- #
# send (producer)
# --------------------------------------------------------------------------- #
def _today_iso(date_str: str | None) -> str:
    return date_str or dt.date.today().isoformat()


def _parse_date(date_str: str) -> dt.date:
    try:
        return dt.date.fromisoformat(date_str)
    except ValueError:
        return dt.date.today()


def cmd_send(args) -> int:
    date_str = _today_iso(args.date)
    gate = sched.read_decision(sched.decision_path(date_str))
    if gate.get("degraded") or gate.get("decision") != "allow":
        log.info(
            "gate=%s degraded=%s → карточки не рассылаются",
            gate.get("decision"),
            bool(gate.get("degraded")),
        )
        return 0

    wl_file = sched.latest_watchlist()
    wl = sched._read_json(wl_file) if wl_file else None
    if not wl:
        log.info("watchlist отсутствует → нечего слать")
        return 0
    if not sched._watchlist_is_fresh(wl, _parse_date(date_str)):
        log.warning("watchlist устарел (%s) → карточки не рассылаются", wl.get("date"))
        return 0

    cards = select_cards(wl, sched._list_theses(), date_str)
    if not cards:
        log.info("нет ENTRY_READY-кандидатов с геометрией → нечего слать")
        return 0

    ledger = load_ledger(date_str)
    badge = pib.mode_badge()
    bot_token = chat_id = None
    if not args.no_telegram and not args.dry_run:
        bot_token, chat_id = ti.resolve_credentials()

    sent = 0
    for card in cards:
        tid = card["thesis_id"]
        existing = ledger["orders"].get(tid)
        if existing and existing.get("status") in {
            "pending",
            "placed",
            "filled",
            "skipped",
            "expired",
        }:
            continue  # already carded / resolved today — idempotent re-send guard
        if args.dry_run:
            log.info(
                "(dry-run) карточка %s %s вход %s стоп %s цель %s x%s",
                card["ticker"],
                card["side"],
                card["pivot"],
                card["stop"],
                card["target"],
                card["shares"],
            )
            continue
        message_id = None
        if not args.no_telegram:
            message_id = ti.send_order_card(
                card, tid, bot_token=bot_token, chat_id=chat_id, mode_badge=badge
            )
            if message_id is None:
                log.warning("не удалось отправить карточку %s", tid)
                continue
        ledger["orders"][tid] = {
            **card,
            "message_id": message_id,
            "chat_id": chat_id,
            "status": "pending",
            "order_ids": [],
            "entry_order_id": None,
            "placed_at": None,
            "fill_price": None,
            "error": None,
        }
        sent += 1

    if not args.dry_run:
        ledger["mode"] = "paper" if pib.is_paper() else "live"
        save_ledger(date_str, ledger)
    log.info("разослано карточек: %d/%d", sent, len(cards))
    return 0


# --------------------------------------------------------------------------- #
# PID lock (mirrors run_trading_autopilot)
# --------------------------------------------------------------------------- #
def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OverflowError, OSError):
        return True
    return True


def acquire_lock(path: Path) -> bool:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            pid = int(path.read_text().strip())
        except (ValueError, OSError):
            pid = -1
        if pid > 0 and _pid_alive(pid):
            return False
        try:
            path.unlink()
        except OSError:
            return False
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        f.write(str(os.getpid()))
    return True


def release_lock(path: Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Daemon helpers (callbacks -> placement -> fill -> ACTIVE)
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def heat_ok_for(card: dict) -> tuple[bool, str]:
    """Backstop the plan's heat rule (≥6% / 6 positions -> no new orders).

    A missing heat file does NOT block (the human just confirmed the tap), but it
    is logged. A present file that shows no free slot, or insufficient remaining
    heat for this candidate's risk, blocks placement.
    """
    heat_file = sched._latest(sched.JOURNAL_DIR, "portfolio_heat_*.json")
    heat = _read_json_file(heat_file) if heat_file else None
    if not heat:
        return True, "нет свежего heat-файла (heat-гейт пропущен)"
    slots = heat.get("remaining_position_slots")
    if isinstance(slots, (int, float)) and slots <= 0:
        return False, "нет свободных слотов позиций (heat)"
    remaining = heat.get("remaining_heat_dollars")
    risk = card.get("risk_dollars")
    if isinstance(remaining, (int, float)) and isinstance(risk, (int, float)) and risk > remaining:
        return False, f"риск ${risk:g} > свободного heat ${remaining:g}"
    return True, "ok"


def _edit(entry: dict, text: str, bot_token: str) -> None:
    """Best-effort rewrite of a card's text (drops its buttons)."""
    if entry.get("message_id") is None or entry.get("chat_id") is None:
        return
    try:
        ti.edit_card(bot_token, entry["chat_id"], entry["message_id"], text)
    except Exception as exc:  # noqa: BLE001 - never let a Telegram hiccup crash the daemon
        log.warning("edit_card failed for %s: %s", entry.get("thesis_id"), exc)


def handle_open(entry: dict, port: int | None, *, live: bool, bot_token: str) -> None:
    """Tap "Открыть": heat/lock/auth-gated native-bracket placement.

    The thesis is NOT transitioned here — it stays ENTRY_READY and only becomes
    ACTIVE when the entry order actually fills (see ``check_fills``).
    """
    if entry.get("status") in {"placed", "filled"}:
        return  # idempotent: already acted

    ok, reason = heat_ok_for(entry)
    if not ok:
        entry["status"] = "skipped"
        entry["error"] = reason
        _edit(entry, f"⛔️ Не выставлен: {reason}. Тезис остаётся ENTRY_READY.", bot_token)
        return

    allowed, why = pib.order_placement_status(live)
    if not allowed:
        entry["status"] = "preview"
        entry["error"] = why
        _edit(
            entry,
            f"👁 Preview ({why}): ордер НЕ отправлен. Включи IB_ALLOW_ORDER_PLACEMENT + --live. "
            "Тезис остаётся ENTRY_READY.",
            bot_token,
        )
        return

    if port is None:
        entry["status"] = "error"
        entry["error"] = "gateway unavailable"
        _edit(
            entry,
            "❗️IB Gateway недоступен/не авторизован — ордер не выставлен. Тезис ENTRY_READY.",
            bot_token,
        )
        return

    try:
        # Idempotency: a live order already carrying this cOID means we placed it
        # before (crash/restart) — don't double-place.
        if entry["coid"] in pib.live_order_refs(port):
            entry["status"] = "placed"
            _edit(
                entry,
                "✅ Ордер уже выставлен (повтор обнаружен). ACTIVE при исполнении.",
                bot_token,
            )
            return
        conid = pib.resolve_conid(port, entry["ticker"])
        account_id = pib.resolve_account_id(port)
        orders = pib.build_bracket_orders(
            entry["side"],
            conid,
            entry["shares"],
            entry["pivot"],
            entry["stop"],
            entry["target"],
            entry["coid"],
        )
        result = pib.submit_bracket(port, account_id, orders)
    except (ConnectionError, LookupError, ValueError) as exc:
        entry["status"] = "error"
        entry["error"] = str(exc)
        _edit(entry, f"❗️Ошибка постановки: {exc}. Тезис остаётся ENTRY_READY.", bot_token)
        return

    if result.get("ok"):
        entry["status"] = "placed"
        entry["order_ids"] = result["order_ids"]
        entry["entry_order_id"] = result["entry_order_id"]
        entry["placed_at"] = _now_iso()
        _edit(
            entry,
            f"✅ Bracket выставлен: {entry['ticker']} x{entry['shares']} "
            f"(вход ${entry['pivot']:g}, стоп ${entry['stop']:g}, цель ${entry['target']:g}). "
            "Перейдёт в ACTIVE при исполнении.",
            bot_token,
        )
    else:
        entry["status"] = "error"
        entry["error"] = "broker rejected order"
        _edit(entry, "❗️Брокер отклонил ордер. Тезис остаётся ENTRY_READY.", bot_token)


def handle_skip(entry: dict, *, bot_token: str) -> None:
    """Tap "Не открывать": leave the thesis ENTRY_READY, just mark/strip the card."""
    if entry.get("status") in {"placed", "filled"}:
        return
    entry["status"] = "skipped"
    _edit(entry, "✋ Пропущено — тезис остаётся ENTRY_READY.", bot_token)


def expire_pending_cards(ledger: dict, *, bot_token: str) -> bool:
    """On daemon timeout, strip buttons from any still-pending card.

    A card left un-tapped when the listen window ends is "timed out": its thesis
    stays ENTRY_READY and the inline keyboard is removed (``_edit`` rewrites the
    text without ``reply_markup``) so a late tap — after the daemon is gone and
    can no longer react — is impossible. Returns whether anything changed.
    """
    changed = False
    for entry in ledger["orders"].values():
        if entry.get("status") != "pending":
            continue
        entry["status"] = "expired"
        _edit(entry, "⏳ Время вышло — ордер не выставлен, тезис остаётся ENTRY_READY.", bot_token)
        changed = True
    return changed


def transition_to_active(thesis_id: str, price: float, shares: float) -> bool:
    """ENTRY_READY -> ACTIVE via the trader-memory CLI (records the real fill)."""
    cmd = [
        sys.executable,
        str(sched.TRADER_MEMORY_CLI),
        "store",
        "open-position",
        thesis_id,
        "--actual-price",
        str(price),
        "--actual-date",
        _now_iso(),
        "--shares",
        str(shares),
    ]
    try:
        res = subprocess.run(
            cmd, cwd=sched.PROJECT_ROOT, capture_output=True, text=True, timeout=120
        )
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("open-position failed for %s: %s", thesis_id, exc)
        return False
    if res.returncode != 0:
        log.warning(
            "open-position rc=%s for %s: %s",
            res.returncode,
            thesis_id,
            (res.stderr or "").strip()[:200],
        )
        return False
    return True


def check_fills(ledger: dict, port: int, *, bot_token: str) -> bool:
    """Detect filled entries and transition their theses to ACTIVE. Returns changed."""
    changed = False
    for entry in ledger["orders"].values():
        if entry.get("status") != "placed":
            continue
        entry_order_id = entry.get("entry_order_id")
        if not entry_order_id:
            continue
        try:
            status = pib.order_fill_status(port, entry_order_id)
        except (ConnectionError, OSError) as exc:
            log.warning("fill check failed for %s: %s", entry.get("thesis_id"), exc)
            continue
        if not status.get("filled"):
            continue
        price = status.get("avg_price") or entry["pivot"]
        if transition_to_active(entry["thesis_id"], price, entry["shares"]):
            entry["status"] = "filled"
            entry["fill_price"] = price
            _edit(entry, f"🟢 Исполнен по ${price:g} → тезис ACTIVE.", bot_token)
            changed = True
        else:
            attempts = entry.get("fill_transition_attempts", 0) + 1
            entry["fill_transition_attempts"] = attempts
            if attempts >= MAX_FILL_TRANSITION_ATTEMPTS:
                entry["status"] = "error"
                entry["error"] = "open-position failed repeatedly"
                _edit(
                    entry,
                    "❗️Ордер исполнен, но не удалось перевести тезис в ACTIVE — сделай вручную.",
                    bot_token,
                )
                changed = True
    return changed


def _connect_port(args, cache: dict) -> int | None:
    """Lazily connect to the Gateway, caching the port; None (logged) on failure."""
    if cache.get("port") is not None:
        return cache["port"]
    try:
        cache["port"] = pib.connect(timeout=args.__dict__.get("timeout", pib.DEFAULT_TIMEOUT))
    except ConnectionError as exc:
        log.warning("IB Gateway not reachable: %s", exc)
        cache["port"] = None
    return cache["port"]


# --------------------------------------------------------------------------- #
# listen (consumer / daemon)
# --------------------------------------------------------------------------- #
def cmd_listen(args) -> int:
    date_str = _today_iso(args.date)
    _attach_file_log()
    lock = _logs_dir() / "watchlist_orders.lock"
    if not acquire_lock(lock):
        log.warning("другой listen-демон уже запущен — выходим")
        return 0
    try:
        bot_token, _chat_id = ti.resolve_credentials()
    except RuntimeError as exc:
        log.error("Telegram creds отсутствуют: %s", exc)
        release_lock(lock)
        return 1

    log.info(
        "listen daemon up: date=%s live=%s window=%ss mode=%s",
        date_str,
        args.live,
        args.window_sec,
        pib.mode_badge(),
    )
    offset = load_offset()
    deadline = time.monotonic() + args.window_sec
    last_fill_check = 0.0
    port_cache: dict = {}

    try:
        while True:
            ledger = load_ledger(date_str)
            changed = False

            poll_timeout = 0 if args.once else POLL_TIMEOUT
            updates = ti.poll_updates(bot_token, offset, timeout=poll_timeout)
            for update in updates:
                uid = update.get("update_id")
                if uid is not None:
                    offset = uid + 1  # advance so Telegram never redelivers
                cb = ti.extract_callback(update)
                if not cb:
                    continue
                ti.answer_callback(bot_token, cb["callback_query_id"])
                entry = ledger["orders"].get(cb["token"])
                if entry is None:
                    continue  # stale / unknown token
                if cb["action"] == ti.ACTION_OPEN:
                    port = _connect_port(args, port_cache)
                    handle_open(entry, port, live=args.live, bot_token=bot_token)
                else:
                    handle_skip(entry, bot_token=bot_token)
                changed = True
            if offset is not None:
                save_offset(offset)

            now = time.monotonic()
            if (now - last_fill_check >= FILL_CHECK_EVERY_SEC or args.once) and any(
                e.get("status") == "placed" for e in ledger["orders"].values()
            ):
                last_fill_check = now
                port = _connect_port(args, port_cache)
                if port is not None and check_fills(ledger, port, bot_token=bot_token):
                    changed = True

            if changed:
                save_ledger(date_str, ledger)
            if args.once:
                break
            if time.monotonic() >= deadline:
                # Window over: strip buttons from any card the trader never tapped
                # (timed out -> thesis stays ENTRY_READY, no late taps possible).
                ledger = load_ledger(date_str)
                if expire_pending_cards(ledger, bot_token=bot_token):
                    save_ledger(date_str, ledger)
                break
    finally:
        release_lock(lock)
    log.info("listen daemon exit")
    return 0


def _attach_file_log() -> None:
    path = _logs_dir() / "watchlist_orders.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(path)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(fh)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _configure_logging(verbose: bool = False) -> None:
    if log.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.DEBUG if verbose else logging.INFO)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_send = sub.add_parser("send", help="send order cards for ENTRY_READY watchlist candidates")
    p_send.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    p_send.add_argument(
        "--dry-run", action="store_true", help="print cards, send nothing, no ledger write"
    )
    p_send.add_argument(
        "--no-telegram", action="store_true", help="build ledger but skip the actual send"
    )
    p_send.set_defaults(func=cmd_send)

    p_listen = sub.add_parser("listen", help="daemon: handle button taps + fills")
    p_listen.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    p_listen.add_argument(
        "--live", action="store_true", help="actually place orders (needs IB_ALLOW_ORDER_PLACEMENT)"
    )
    p_listen.add_argument(
        "--window-sec", type=int, default=DEFAULT_WINDOW_SEC, help="self-exit after N seconds"
    )
    p_listen.add_argument(
        "--once", action="store_true", help="single poll/fill pass then exit (testing)"
    )
    p_listen.set_defaults(func=cmd_listen)
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
