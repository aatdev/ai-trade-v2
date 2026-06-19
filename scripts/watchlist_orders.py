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
        # Optional scale-out targets: when both present, the size splits 50/25/25
        # into independent sub-brackets (see place_ib_bracket.build_sub_brackets).
        "t2": cand.get("t2"),
        "t3": cand.get("t3"),
        "shares": shares,
        "risk_dollars": cand.get("risk_dollars"),
        "coid": pib.coid_for(tid, date_str),
    }


def _side_allowed(side: str, gate_decision: str) -> bool:
    """Side permitted by the regime gate (mirrors trading_signals._candidate_open_signal):
    longs only under ``allow``; shorts only under ``restrict`` / ``cash-priority``."""
    if str(side or "long").lower() == "long":
        return gate_decision == "allow"
    return gate_decision in ("restrict", "cash-priority")


def select_cards(wl: dict, theses: list[dict], date_str: str, gate_decision: str) -> list[dict]:
    """Order cards for ENTRY_READY watchlist candidates whose side the gate permits.

    Long candidates surface only under ``allow``; short candidates only under
    ``restrict`` / ``cash-priority`` — so a fresh short watchlist (built by the
    evening short branch) gets confirmation cards too, not just longs.
    """
    by_id, by_ts = _entry_ready_index(theses)
    cards: list[dict] = []
    for cand in wl.get("candidates", []):
        if not isinstance(cand, dict):
            continue
        thesis = match_thesis(cand, by_id, by_ts)
        if not thesis:
            continue
        card = build_card(cand, thesis, date_str)
        if card and _side_allowed(card["side"], gate_decision):
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
    # Only a degraded/unknown regime blocks cards outright. A clean allow sends
    # long cards; a clean restrict/cash-priority sends short cards (per-side
    # filtering happens in select_cards).
    if gate.get("degraded"):
        log.info("gate degraded (%s) → карточки не рассылаются", gate.get("decision"))
        return 0
    decision = gate.get("decision")

    wl_file = sched.latest_watchlist()
    wl = sched._read_json(wl_file) if wl_file else None
    if not wl:
        log.info("watchlist отсутствует → нечего слать")
        return 0
    if not sched._watchlist_is_fresh(wl, _parse_date(date_str)):
        log.warning("watchlist устарел (%s) → карточки не рассылаются", wl.get("date"))
        return 0

    cards = select_cards(wl, sched._list_theses(), date_str, decision)
    if not cards:
        log.info("нет ENTRY_READY-кандидатов (по стороне гейта %s) → нечего слать", decision)
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
            "kind": "open",
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


SCALE_TOKEN_PREFIX = "2r-"
_SCALE_RESOLVED = {"pending", "scaled", "skipped", "preview", "expired", "error"}


def cmd_scale_card(args) -> int:
    """Producer: send a +2R scale-out card for one open position (one per day/thesis)."""
    date_str = _today_iso(args.date)
    token = SCALE_TOKEN_PREFIX + args.thesis_id
    card = {
        "kind": "scale",
        "thesis_id": args.thesis_id,
        "ticker": args.ticker,
        "side": (args.side or "long").lower(),
        "shares": args.shares,
        "entry_price": args.entry,
        "current_price": args.price,
    }
    ledger = load_ledger(date_str)
    existing = ledger["orders"].get(token)
    if existing and existing.get("status") in _SCALE_RESOLVED:
        log.info("+2R карточка уже была сегодня для %s", token)
        return 0
    if args.dry_run:
        log.info("(dry-run) +2R карточка %s x%s @ ~%s", args.ticker, args.shares, args.price)
        return 0
    bot_token, chat_id = ti.resolve_credentials()
    mid = ti.send_scale_card(
        card, token, bot_token=bot_token, chat_id=chat_id, mode_badge=pib.mode_badge()
    )
    if mid is None:
        log.warning("не удалось отправить +2R карточку %s", token)
        return 0
    ledger["orders"][token] = {
        **card,
        "message_id": mid,
        "chat_id": chat_id,
        "status": "pending",
        "sold_qty": None,
        "remaining_qty": None,
        "scale_order_ids": [],
        "error": None,
    }
    ledger["mode"] = "paper" if pib.is_paper() else "live"
    save_ledger(date_str, ledger)
    log.info("+2R карточка отправлена: %s", token)
    return 0


CLOSE_TOKEN_PREFIX = "close-"
_CLOSE_RESOLVED = {"pending", "closed", "skipped", "preview", "expired", "error"}


def cmd_close_card(args) -> int:
    """Producer: send a position-management exit card (one per day/thesis)."""
    date_str = _today_iso(args.date)
    token = CLOSE_TOKEN_PREFIX + args.thesis_id
    card = {
        "kind": "close",
        "thesis_id": args.thesis_id,
        "ticker": args.ticker,
        "side": (args.side or "long").lower(),
        "shares": args.shares,
        "price": args.price,
        "reason": args.reason,
        "exit_reason": args.exit_reason,
    }
    ledger = load_ledger(date_str)
    existing = ledger["orders"].get(token)
    if existing and existing.get("status") in _CLOSE_RESOLVED:
        log.info("close карточка уже была сегодня для %s", token)
        return 0
    if args.dry_run:
        log.info("(dry-run) close карточка %s: %s", args.ticker, args.reason)
        return 0
    bot_token, chat_id = ti.resolve_credentials()
    mid = ti.send_close_card(
        card, token, bot_token=bot_token, chat_id=chat_id, mode_badge=pib.mode_badge()
    )
    if mid is None:
        log.warning("не удалось отправить close карточку %s", token)
        return 0
    ledger["orders"][token] = {
        **card,
        "message_id": mid,
        "chat_id": chat_id,
        "status": "pending",
        "close_order_ids": [],
        "error": None,
    }
    ledger["mode"] = "paper" if pib.is_paper() else "live"
    save_ledger(date_str, ledger)
    log.info("close карточка отправлена: %s", token)
    return 0


CLOSE_DETECTED_TOKEN_PREFIX = "closed-"


def cmd_close_detected_card(args) -> int:
    """Producer: send a detected-external-close confirmation card (one/day/thesis).

    Fires when the scheduler notices a tracked-open thesis is no longer in the
    live IB snapshot — i.e. it was likely closed OUTSIDE the system (manual exit
    in TWS, stop filled while we weren't watching). The card is confirm-gated and
    places NO order; confirming only records the close + postmortem. Suppressed
    when the same thesis was already closed through the system today (a resolved
    ``close-<id>`` card), to avoid a redundant second card on the race."""
    date_str = _today_iso(args.date)
    token = CLOSE_DETECTED_TOKEN_PREFIX + args.thesis_id
    ledger = load_ledger(date_str)
    sys_close = ledger["orders"].get(CLOSE_TOKEN_PREFIX + args.thesis_id)
    if sys_close and sys_close.get("status") == "closed":
        log.info("detected-close: %s уже закрыт через систему сегодня — пропуск", args.thesis_id)
        return 0
    existing = ledger["orders"].get(token)
    if existing and existing.get("status") in _CLOSE_RESOLVED:
        log.info("detected-close карточка уже была сегодня для %s", token)
        return 0
    card = {
        "kind": "close_detected",
        "thesis_id": args.thesis_id,
        "ticker": args.ticker,
        "side": (args.side or "long").lower(),
        "shares": args.shares,
        "price": args.price,
        "reason": args.reason,
        "exit_reason": args.exit_reason or "manual",
    }
    if args.dry_run:
        log.info("(dry-run) detected-close карточка %s: %s", args.ticker, args.reason)
        return 0
    bot_token, chat_id = ti.resolve_credentials()
    mid = ti.send_close_detected_card(
        card, token, bot_token=bot_token, chat_id=chat_id, mode_badge=pib.mode_badge()
    )
    if mid is None:
        log.warning("не удалось отправить detected-close карточку %s", token)
        return 0
    ledger["orders"][token] = {
        **card,
        "message_id": mid,
        "chat_id": chat_id,
        "status": "pending",
        "close_order_ids": [],
        "error": None,
    }
    ledger["mode"] = "paper" if pib.is_paper() else "live"
    save_ledger(date_str, ledger)
    log.info("detected-close карточка отправлена: %s", token)
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
        # Idempotency (date-agnostic): a live order carrying this thesis's
        # `wl-<id>-` prefix — placed THIS run or an earlier session — means a
        # bracket is already working, so don't double-place. GTC entries (the
        # default) rest across days under `wl-<id>-<earlier-date>-…`, so match the
        # thesis prefix, NOT the dated base coid (which would miss them and stack a
        # duplicate). Also covers per-attempt nonce + per-tranche `…-t{i}` suffixes.
        prefix = pib.coid_prefix(entry["thesis_id"])
        if any(ref.startswith(prefix) for ref in pib.live_order_refs(port)):
            entry["status"] = "placed"
            _edit(
                entry,
                "✅ Ордер уже выставлен (повтор обнаружен). ACTIVE при исполнении.",
                bot_token,
            )
            return
        conid = pib.resolve_conid(port, entry["ticker"])
        account_id = pib.resolve_account_id(port)
        # Scale-out (T2+T3) → several INDEPENDENT native sub-brackets, one POST
        # each (IB collapses a single-POST multi-OCA bracket into one group and
        # leaves it stuck Pending Submit). A single target → one bracket.
        #
        # Submit under a per-ATTEMPT cOID base (`{coid}-{nonce}`): IB forbids
        # reusing a cancelled order's Local order ID within a session, so a
        # re-place after a cancel must use fresh cOIDs. The stored `entry["coid"]`
        # stays the stable base prefix used for detection / idempotency above.
        attempt_coid = f"{entry['coid']}-{pib.attempt_nonce()}"
        brackets = pib.build_sub_brackets(
            entry["side"],
            conid,
            entry["shares"],
            entry["pivot"],
            entry["stop"],
            entry["target"],
            attempt_coid,
            target2=entry.get("t2"),
            target3=entry.get("t3"),
        )
        log.info(
            "placing %d sub-bracket(s) (%d legs) for %s",
            len(brackets),
            sum(len(b) for b in brackets),
            entry["thesis_id"],
        )
        result = pib.submit_brackets(port, account_id, brackets)
    except (ConnectionError, LookupError, ValueError) as exc:
        entry["status"] = "error"
        entry["error"] = str(exc)
        _edit(entry, f"❗️Ошибка постановки: {exc}. Тезис остаётся ENTRY_READY.", bot_token)
        return

    if result.get("ok"):
        entry["status"] = "placed"
        entry["order_ids"] = result["order_ids"]
        entry["entry_order_id"] = result["entry_order_id"]
        entry["entry_order_ids"] = result.get("entry_order_ids", [])
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
        reason = result.get("reason") or "broker rejected order"
        entry["error"] = reason
        entry["raw"] = result.get("raw")  # full IB response for diagnostics
        log.warning(
            "broker rejected %s: %s | raw=%s", entry["thesis_id"], reason, result.get("raw")
        )
        _edit(entry, f"❗️Брокер отклонил ордер: {reason}. Тезис остаётся ENTRY_READY.", bot_token)


def handle_skip(entry: dict, *, bot_token: str) -> None:
    """Tap "Не открывать": leave the thesis ENTRY_READY, just mark/strip the card."""
    if entry.get("status") in {"placed", "filled"}:
        return
    entry["status"] = "skipped"
    _edit(entry, "✋ Пропущено — тезис остаётся ENTRY_READY.", bot_token)


def record_trim(thesis_id: str, shares_sold: float, price: float) -> bool:
    """Record a +2R partial close in trader-memory (ACTIVE -> PARTIALLY_CLOSED)."""
    cmd = [
        sys.executable,
        str(sched.TRADER_MEMORY_CLI),
        "store",
        "trim",
        thesis_id,
        "--shares-sold",
        str(shares_sold),
        "--price",
        str(price),
        "--date",
        _now_iso(),
        "--reason",
        "+2R partial (confirmed)",
    ]
    try:
        res = subprocess.run(
            cmd, cwd=sched.PROJECT_ROOT, capture_output=True, text=True, timeout=120
        )
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("trim failed for %s: %s", thesis_id, exc)
        return False
    if res.returncode != 0:
        log.warning(
            "trim rc=%s for %s: %s", res.returncode, thesis_id, (res.stderr or "").strip()[:200]
        )
        return False
    return True


def handle_scale_out(entry: dict, port: int | None, *, live: bool, bot_token: str) -> None:
    """Tap "Зафиксировать 50%" on a +2R card: sell 50% MKT, move stop to breakeven.

    Sells half the position at market (exit side), tears down the old bracket's
    working stop/target, arms a fresh breakeven stop for the remainder, and
    records the partial close (ACTIVE -> PARTIALLY_CLOSED) in trader-memory.
    """
    if entry.get("status") in {"scaled"}:
        return  # idempotent: already scaled

    allowed, why = pib.order_placement_status(live)
    if not allowed:
        entry["status"] = "preview"
        entry["error"] = why
        _edit(entry, f"👁 Preview ({why}): +2R не исполнен. Позиция без изменений.", bot_token)
        return
    if port is None:
        entry["status"] = "error"
        entry["error"] = "gateway unavailable"
        _edit(entry, "❗️IB Gateway недоступен — +2R не исполнен. Позиция без изменений.", bot_token)
        return

    shares = entry.get("shares") or 0
    sell_qty = max(1, int(shares // 2))
    remaining = shares - sell_qty
    side = entry["side"]
    exit_action = pib.exit_action_for(side)
    try:
        conid = pib.resolve_conid(port, entry["ticker"])
        account_id = pib.resolve_account_id(port)
        close = pib.place_market_close(port, account_id, conid, exit_action, sell_qty)
        if not close.get("ok"):
            entry["status"] = "error"
            entry["error"] = "scale MKT rejected"
            _edit(entry, "❗️Рыночный ордер на 50% отклонён — позиция без изменений.", bot_token)
            return
        # Tear down the old (full-size) bracket children, arm a breakeven stop.
        for oid in pib.working_exit_orders(port, conid, exit_action):
            pib.cancel_order(port, account_id, oid)
        be_ok = True
        if remaining > 0:
            be = pib.place_stop(
                port, account_id, conid, exit_action, remaining, entry["entry_price"]
            )
            be_ok = bool(be.get("ok"))
    except (ConnectionError, LookupError, ValueError) as exc:
        entry["status"] = "error"
        entry["error"] = str(exc)
        _edit(entry, f"❗️Ошибка +2R: {exc}. Проверь позицию вручную.", bot_token)
        return

    trim_price = entry.get("current_price") or entry["entry_price"]
    record_trim(entry["thesis_id"], sell_qty, trim_price)
    entry["status"] = "scaled"
    entry["sold_qty"] = sell_qty
    entry["remaining_qty"] = remaining
    entry["scale_order_ids"] = close.get("order_ids", [])
    be_note = (
        f"стоп остатка {remaining} → безубыток ${entry['entry_price']:g}"
        if remaining > 0 and be_ok
        else (
            "остаток без стопа — выставь вручную" if remaining > 0 else "позиция закрыта полностью"
        )
    )
    _edit(entry, f"💰 +2R: продано {sell_qty} рыночным; {be_note}.", bot_token)


def handle_scale_skip(entry: dict, *, bot_token: str) -> None:
    """Tap "Не сейчас" on a +2R card: leave the position untouched."""
    if entry.get("status") in {"scaled"}:
        return
    entry["status"] = "skipped"
    _edit(entry, "✋ +2R пропущен — позиция без изменений.", bot_token)


def record_close(thesis_id: str, price: float, exit_reason: str) -> bool:
    """Full close in trader-memory (ACTIVE/PARTIALLY_CLOSED -> CLOSED)."""
    cmd = [
        sys.executable,
        str(sched.TRADER_MEMORY_CLI),
        "store",
        "close",
        thesis_id,
        "--exit-reason",
        exit_reason,
        "--actual-price",
        str(price),
        "--actual-date",
        _now_iso(),
    ]
    try:
        res = subprocess.run(
            cmd, cwd=sched.PROJECT_ROOT, capture_output=True, text=True, timeout=120
        )
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("close failed for %s: %s", thesis_id, exc)
        return False
    if res.returncode != 0:
        log.warning(
            "close rc=%s for %s: %s", res.returncode, thesis_id, (res.stderr or "").strip()[:200]
        )
        return False
    return True


def generate_postmortem(thesis_id: str) -> bool:
    """Generate the postmortem markdown for a just-closed thesis (best-effort).

    Runs the trader-memory ``review postmortem`` step right after the thesis is
    recorded CLOSED, so the per-trade postmortem is produced automatically rather
    than waiting on a manual trade-memory-loop run. A failure here never breaks
    the close itself — it is logged and surfaced on the card so the trader can
    regenerate it manually. Idempotent: re-running overwrites ``pm_<id>.md``."""
    cmd = [
        sys.executable,
        str(sched.TRADER_MEMORY_CLI),
        "review",
        "postmortem",
        thesis_id,
    ]
    try:
        res = subprocess.run(
            cmd, cwd=sched.PROJECT_ROOT, capture_output=True, text=True, timeout=180
        )
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("postmortem failed for %s: %s", thesis_id, exc)
        return False
    if res.returncode != 0:
        log.warning(
            "postmortem rc=%s for %s: %s",
            res.returncode,
            thesis_id,
            (res.stderr or "").strip()[:200],
        )
        return False
    return True


def _record_close_and_postmortem(thesis_id: str, price: float, exit_reason: str) -> str:
    """Record a CLOSED outcome and auto-generate its postmortem.

    Returns a short Telegram suffix describing what happened: postmortem saved,
    postmortem failed (close recorded), or close not recorded at all."""
    if not record_close(thesis_id, price, exit_reason):
        return " ⚠️ Закрытие в журнале не записано — сделай вручную."
    if generate_postmortem(thesis_id):
        return " Постмортем сохранён."
    return " Постмортем не сгенерён — запусти review postmortem вручную."


def handle_close(entry: dict, port: int | None, *, live: bool, bot_token: str) -> None:
    """Tap "Закрыть" on a position-management exit card: market-close the full
    remaining position and cancel the protective bracket legs.

    Cancels the working stop/target FIRST (so they cannot fire against a flat
    position), then closes at market, then records the close (-> CLOSED) in
    trader-memory."""
    if entry.get("status") in {"closed"}:
        return  # idempotent

    allowed, why = pib.order_placement_status(live)
    if not allowed:
        entry["status"] = "preview"
        entry["error"] = why
        _edit(entry, f"👁 Preview ({why}): закрытие не исполнено. Позиция без изменений.", bot_token)
        return
    if port is None:
        entry["status"] = "error"
        entry["error"] = "gateway unavailable"
        _edit(
            entry,
            "❗️IB Gateway недоступен — закрытие не исполнено. Позиция без изменений.",
            bot_token,
        )
        return

    shares = entry.get("shares") or 0
    side = entry["side"]
    exit_action = pib.exit_action_for(side)
    try:
        conid = pib.resolve_conid(port, entry["ticker"])
        account_id = pib.resolve_account_id(port)
        # Tear down protective legs BEFORE closing, else they'd fire on a flat book.
        for oid in pib.working_exit_orders(port, conid, exit_action):
            pib.cancel_order(port, account_id, oid)
        close = pib.place_market_close(port, account_id, conid, exit_action, shares)
        if not close.get("ok"):
            entry["status"] = "error"
            entry["error"] = "close MKT rejected"
            _edit(
                entry, "❗️Рыночный ордер на закрытие отклонён — проверь позицию вручную.", bot_token
            )
            return
    except (ConnectionError, LookupError, ValueError) as exc:
        entry["status"] = "error"
        entry["error"] = str(exc)
        _edit(entry, f"❗️Ошибка закрытия: {exc}. Проверь позицию вручную.", bot_token)
        return

    price = entry.get("price") or entry.get("entry_price") or 0
    pm_note = _record_close_and_postmortem(
        entry["thesis_id"], price, entry.get("exit_reason") or "manual"
    )
    entry["status"] = "closed"
    entry["close_order_ids"] = close.get("order_ids", [])
    _edit(
        entry,
        f"⛔️ Закрыто рыночным {shares} шт; защитные ордера сняты. Тезис → CLOSED.{pm_note}",
        bot_token,
    )


def handle_close_detected(entry: dict, *, bot_token: str) -> None:
    """Tap "Записать закрытие" on a detected-external-close card.

    The position is already flat at the broker (it dropped out of the IB
    snapshot), so this places NO order — it only records the CLOSED outcome and
    auto-generates the postmortem. Idempotent on a card already resolved closed;
    on a failed journal write the card flags it for a manual fix."""
    if entry.get("status") in {"closed"}:
        return  # idempotent
    price = entry.get("price") or entry.get("entry_price") or 0
    if not record_close(entry["thesis_id"], price, entry.get("exit_reason") or "manual"):
        entry["status"] = "error"
        entry["error"] = "record close failed"
        _edit(entry, "❗️Не удалось записать закрытие в журнал — сделай вручную.", bot_token)
        return
    pm_note = (
        " Постмортем сохранён."
        if generate_postmortem(entry["thesis_id"])
        else " Постмортем не сгенерён — запусти review postmortem вручную."
    )
    entry["status"] = "closed"
    _edit(entry, f"✅ Закрытие записано, тезис → CLOSED.{pm_note}", bot_token)


def handle_close_skip(entry: dict, *, bot_token: str) -> None:
    """Tap "Оставить"/"Не сейчас" on an exit card: leave things untouched."""
    if entry.get("status") in {"closed"}:
        return
    entry["status"] = "skipped"
    if entry.get("kind") == "close_detected":
        _edit(entry, "✋ Закрытие не записано — тезис без изменений.", bot_token)
    else:
        _edit(entry, "✋ Оставлено — позиция без изменений.", bot_token)


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
                kind = entry.get("kind", "open")
                if cb["action"] == ti.ACTION_OPEN:
                    if kind == "close_detected":
                        # Position already flat at the broker — no Gateway needed.
                        handle_close_detected(entry, bot_token=bot_token)
                    else:
                        port = _connect_port(args, port_cache)
                        if kind == "scale":
                            handle_scale_out(entry, port, live=args.live, bot_token=bot_token)
                        elif kind == "close":
                            handle_close(entry, port, live=args.live, bot_token=bot_token)
                        else:
                            handle_open(entry, port, live=args.live, bot_token=bot_token)
                elif kind == "scale":
                    handle_scale_skip(entry, bot_token=bot_token)
                elif kind in ("close", "close_detected"):
                    handle_close_skip(entry, bot_token=bot_token)
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
# UI path (Telegram-free): open-now / cancel / sync
#
# The same ledger + handle_open + check_fills machinery the Telegram daemon
# uses, driven instead by explicit per-thesis commands so the web dashboard can
# place / cancel a bracket and reconcile fills without a bot. A UI-placed entry
# carries message_id=None, so every _edit() Telegram side effect is a no-op.
# --------------------------------------------------------------------------- #
def _emit(obj: dict) -> None:
    """Print a single JSON result line for the UI job to capture."""
    json.dump(obj, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def _bracket_still_live(entry: dict, args) -> bool:
    """True when a live IB order still carries this entry's cOID (a genuine no-op).

    A ledger entry can be left at "placed" while the bracket is actually gone at
    the broker — cancelled/cleared manually, or it never transmitted. Before
    treating "placed" as a no-op, re-validate read-only against IB: match the base
    cOID as a PREFIX (sub-brackets are ``{coid}-t{i}``). This is a SAME-DAY check —
    the ledger entry and its bracket carry the same dated coid — so the dated coid
    is the right key here; cross-session dedup lives in ``handle_open`` (which
    matches the date-agnostic ``coid_prefix``). On any gateway failure — or a
    missing cOID — assume it IS still live: the conservative default never silently
    re-places when we cannot confirm the broker is empty.
    """
    coid = entry.get("coid")
    if not coid:
        return True
    try:
        port = pib.connect(timeout=getattr(args, "timeout", pib.DEFAULT_TIMEOUT))
        refs = pib.live_order_refs(port)
    except (ConnectionError, OSError, ValueError):
        return True
    return any(str(r).startswith(coid) for r in refs)


def _auto_size_shares(pivot, stop) -> tuple[int | None, float | None]:
    """Risk-based shares from the trading profile when the caller passed none.

    A thesis created from a signal (ticker-analysis) has no planner-sized
    watchlist candidate, so the UI can't supply a size. Fall back to the same
    fixed-fractional sizing the scheduler/reconcile use:
    shares = account x risk% / |pivot - stop|, capped at max_position_pct.
    Returns (shares, risk_dollars), or (None, None) when the profile can't size.
    """
    profile = sched._read_json(sched.TRADING_DATA_DIR / "trading_profile.json") or {}
    return sched._profile_sized_shares(profile, pivot, stop)


def cmd_open_now(args) -> int:
    """Place a native bracket for one ENTRY_READY thesis (no Telegram card).

    Geometry is passed explicitly by the caller (the UI server reads the thesis
    entry/exit levels and forwards them), mirroring scale-card / close-card.
    When --shares is omitted (e.g. a signal-derived thesis with no planner
    sizing), size it from the trading profile. Preview unless BOTH --live and
    IB_ALLOW_ORDER_PLACEMENT are set.
    """
    date_str = _today_iso(args.date)
    tid = args.thesis_id

    shares = args.shares
    risk_dollars = getattr(args, "risk_dollars", None)
    if shares is None:
        shares, sized_risk = _auto_size_shares(args.pivot, args.stop)
        if shares is None:
            log.error("open-now: cannot size %s — supply --shares or check profile/levels", tid)
            _emit(
                {
                    "ok": False,
                    "thesis_id": tid,
                    "error": "could not size position — supply a share count or check "
                    "trading_profile.json (account_size/risk_pct) and the entry/stop levels",
                }
            )
            return 1
        if risk_dollars is None:
            risk_dollars = sized_risk
        log.info("open-now: auto-sized %s → %s shares (risk $%s)", tid, shares, risk_dollars)

    cand = {
        "ticker": args.ticker,
        "side": args.side,
        "pivot": args.pivot,
        "stop": args.stop,
        "target": args.target,
        "t2": getattr(args, "target2", None),
        "t3": getattr(args, "target3", None),
        "shares": shares,
        "worst_entry": getattr(args, "worst_entry", None),
        "risk_dollars": risk_dollars,
    }
    card = build_card(cand, {"thesis_id": tid, "side": args.side}, date_str)
    if card is None:
        log.error("open-now: incomplete geometry for %s", tid)
        _emit({"ok": False, "thesis_id": tid, "error": "incomplete geometry"})
        return 1

    ledger = load_ledger(date_str)
    existing = ledger["orders"].get(tid)
    if existing and existing.get("status") in {"placed", "filled"}:
        # A filled entry is a real position — never re-place. A "placed" entry is
        # a genuine no-op only while its bracket is STILL live at the broker; if it
        # was cancelled/cleared (manually, or it never transmitted), the ledger is
        # stale — re-validate against IB and fall through to re-place when gone.
        if existing.get("status") == "filled" or _bracket_still_live(existing, args):
            log.info("open-now: %s already %s — no-op", tid, existing["status"])
            _emit(
                {
                    "ok": True,
                    "thesis_id": tid,
                    "status": existing["status"],
                    "order_ids": existing.get("order_ids", []),
                    "note": "already placed",
                }
            )
            return 0
        log.info("open-now: %s ledger=placed but no live bracket at broker — re-placing", tid)

    entry = {
        **card,
        "kind": "open",
        "message_id": None,
        "chat_id": None,
        "status": "pending",
        "order_ids": [],
        "entry_order_id": None,
        "placed_at": None,
        "error": None,
        "fill_price": None,
        "source": "ui",
    }

    live = bool(args.live) and not getattr(args, "dry_run", False)
    port = None
    # Only touch the Gateway when both gates actually clear; handle_open re-checks
    # heat / placement / idempotency and is the source of truth.
    if live and heat_ok_for(entry)[0] and pib.order_placement_status(live)[0]:
        try:
            port = pib.connect(timeout=getattr(args, "timeout", pib.DEFAULT_TIMEOUT))
        except ConnectionError as exc:
            log.warning("open-now: gateway connect failed: %s", exc)  # handle_open → error

    handle_open(entry, port, live=live, bot_token=None)

    ledger["orders"][tid] = entry
    ledger["mode"] = "paper" if pib.is_paper() else "live"
    save_ledger(date_str, ledger)
    out = {
        "ok": entry["status"] in {"placed", "filled"},
        "thesis_id": tid,
        "status": entry["status"],
        "order_ids": entry.get("order_ids", []),
        "entry_order_id": entry.get("entry_order_id"),
        "entry_order_ids": entry.get("entry_order_ids", []),
        "error": entry.get("error"),
    }
    if entry.get("raw") is not None:
        out["raw"] = entry["raw"]  # full IB response on rejection (diagnostics)
    _emit(out)
    return 0


def cmd_cancel(args) -> int:
    """Cancel a placed-but-unfilled bracket for one thesis (the UI 'delete' button).

    Refuses once the entry has filled — a live position must be exited via close,
    not by cancelling its protective legs (which would leave it unprotected).
    """
    date_str = _today_iso(args.date)
    tid = args.thesis_id
    ledger = load_ledger(date_str)
    entry = ledger["orders"].get(tid)
    if entry is None:
        log.error("cancel: no ledger entry for %s", tid)
        _emit({"ok": False, "thesis_id": tid, "error": "no order on record for this thesis"})
        return 1
    if entry.get("status") != "placed":
        log.warning("cancel: %s is %s, not 'placed' — refusing", tid, entry.get("status"))
        _emit(
            {
                "ok": False,
                "thesis_id": tid,
                "status": entry.get("status"),
                "error": "only a placed (unfilled) bracket can be cancelled — use close once filled",
            }
        )
        return 1

    order_ids = entry.get("order_ids") or []
    try:
        port = pib.connect(timeout=getattr(args, "timeout", pib.DEFAULT_TIMEOUT))
        account_id = pib.resolve_account_id(port)
    except (ConnectionError, LookupError) as exc:
        log.warning("cancel: gateway unavailable: %s", exc)
        _emit({"ok": False, "thesis_id": tid, "error": str(exc)})
        return 2

    cancelled, gone, errors = [], [], []
    for oid in order_ids:
        try:
            pib.cancel_order(port, account_id, oid)
            cancelled.append(oid)
        except (ConnectionError, OSError, ValueError) as exc:  # noqa: BLE001 - per-leg degrade
            # Cancelling a sub-bracket parent cascades to its children, so the
            # child DELETE then reports "doesn't exist" — that's the goal state,
            # not a failure. Same for a never-transmitted / Inactive leg.
            if _order_already_gone(str(exc)):
                gone.append(oid)
            else:
                log.warning("cancel: leg %s failed: %s", oid, exc)
                errors.append(oid)

    entry["status"] = "cancelled"
    entry["error"] = None if not errors else f"legs not cancelled: {','.join(errors)}"
    save_ledger(date_str, ledger)
    _emit(
        {"ok": not errors, "thesis_id": tid, "cancelled": cancelled, "gone": gone, "errors": errors}
    )
    return 0


def _order_already_gone(msg: str) -> bool:
    """True when a cancel failure means there's simply no live order to cancel
    (already cancelled, never transmitted, or a non-cancellable Inactive leg)."""
    m = msg.lower()
    return "doesn't exist" in m or "does not exist" in m or "not found" in m


def cmd_cancel_orders(args) -> int:
    """Cancel specific IB orders by id (the IB-account tab's per-order delete).

    Order-id-centric (no ledger): DELETEs each id at the broker. Cancelling a
    bracket parent cascades to its children at IB; passing every leg id is also
    safe (already-done legs just error and are reported).
    """
    ids = [s.strip() for s in (args.order_ids or "").split(",") if s.strip()]
    if not ids:
        _emit({"ok": False, "error": "no order ids supplied"})
        return 1
    try:
        port = pib.connect(timeout=getattr(args, "timeout", pib.DEFAULT_TIMEOUT))
        account_id = pib.resolve_account_id(port)
    except (ConnectionError, LookupError) as exc:
        log.warning("cancel-orders: gateway unavailable: %s", exc)
        _emit({"ok": False, "error": str(exc)})
        return 2

    cancelled, gone, errors, reasons = [], [], [], {}
    for oid in ids:
        try:
            pib.cancel_order(port, account_id, oid)
            cancelled.append(oid)
        except (ConnectionError, OSError, ValueError) as exc:  # noqa: BLE001 - per-id degrade
            msg = str(exc)
            if _order_already_gone(msg):
                # No live order with this id (already cancelled / never transmitted /
                # a non-cancellable Inactive leg) — the goal state is satisfied.
                gone.append(oid)
            else:
                log.warning("cancel-orders: %s failed: %s", oid, exc)
                errors.append(oid)
                reasons[oid] = msg
    out = {"ok": not errors, "cancelled": cancelled, "gone": gone, "errors": errors}
    if reasons:
        out["reasons"] = reasons
    _emit(out)
    return 0


def cmd_sync(args) -> int:
    """Telegram-free fill reconcile: flip ENTRY_READY→ACTIVE for any filled entry.

    Same detection as the daemon's periodic check_fills; the _edit() card update
    is a no-op for UI-placed entries (message_id is None). Run on a 1-min timer
    or behind the dashboard's 'Сверить с IB' button.
    """
    date_str = _today_iso(args.date)
    ledger = load_ledger(date_str)
    if not any(e.get("status") == "placed" for e in ledger["orders"].values()):
        _emit({"ok": True, "transitioned": [], "note": "no placed orders"})
        return 0
    try:
        port = pib.connect(timeout=getattr(args, "timeout", pib.DEFAULT_TIMEOUT))
    except ConnectionError as exc:
        log.warning("sync: gateway unavailable: %s", exc)
        _emit({"ok": False, "error": str(exc)})
        return 2

    before = {tid: e.get("status") for tid, e in ledger["orders"].items()}
    if check_fills(ledger, port, bot_token=None):
        save_ledger(date_str, ledger)
    transitioned = [
        tid
        for tid, e in ledger["orders"].items()
        if before.get(tid) == "placed" and e.get("status") == "filled"
    ]
    _emit({"ok": True, "transitioned": transitioned})
    return 0


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

    p_scale = sub.add_parser("scale-card", help="send a +2R scale-out card for one open position")
    p_scale.add_argument("--thesis-id", required=True)
    p_scale.add_argument("--ticker", required=True)
    p_scale.add_argument("--side", choices=["long", "short"], default="long")
    p_scale.add_argument("--shares", type=float, required=True)
    p_scale.add_argument("--entry", type=float, required=True, help="entry price (breakeven stop)")
    p_scale.add_argument("--price", type=float, required=True, help="current price (~+2R)")
    p_scale.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    p_scale.add_argument("--dry-run", action="store_true", help="log only, send nothing")
    p_scale.set_defaults(func=cmd_scale_card)

    p_close = sub.add_parser("close-card", help="send a rule-violation exit card for one position")
    p_close.add_argument("--thesis-id", required=True)
    p_close.add_argument("--ticker", required=True)
    p_close.add_argument("--side", choices=["long", "short"], default="long")
    p_close.add_argument("--shares", type=float, required=True)
    p_close.add_argument(
        "--price", type=float, required=True, help="reference price for the close record"
    )
    p_close.add_argument("--reason", required=True, help="why (time-stop / EMA20 / SMA50)")
    p_close.add_argument("--exit-reason", choices=["time_stop", "manual"], default="manual")
    p_close.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    p_close.add_argument("--dry-run", action="store_true", help="log only, send nothing")
    p_close.set_defaults(func=cmd_close_card)

    p_cdet = sub.add_parser(
        "close-detected-card",
        help="confirm-record a position that disappeared from the IB snapshot",
    )
    p_cdet.add_argument("--thesis-id", required=True)
    p_cdet.add_argument("--ticker", required=True)
    p_cdet.add_argument("--side", choices=["long", "short"], default="long")
    p_cdet.add_argument("--shares", type=float, default=None)
    p_cdet.add_argument(
        "--price", type=float, default=None, help="approx exit price for the close record"
    )
    p_cdet.add_argument("--reason", default="позиции нет в IB", help="why the close was detected")
    p_cdet.add_argument("--exit-reason", choices=["time_stop", "manual"], default="manual")
    p_cdet.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    p_cdet.add_argument("--dry-run", action="store_true", help="log only, send nothing")
    p_cdet.set_defaults(func=cmd_close_detected_card)

    # UI path (Telegram-free): driven per-thesis by the web dashboard.
    p_open = sub.add_parser(
        "open-now", help="place a bracket for one ENTRY_READY thesis (UI path, no Telegram)"
    )
    p_open.add_argument("--thesis-id", required=True)
    p_open.add_argument("--ticker", required=True)
    p_open.add_argument("--side", choices=["long", "short"], default="long")
    p_open.add_argument(
        "--shares",
        type=float,
        default=None,
        help="share count; omit to auto-size from trading_profile.json (risk%% of stop)",
    )
    p_open.add_argument("--pivot", type=float, required=True, help="entry buy/sell-stop trigger")
    p_open.add_argument("--stop", type=float, required=True, help="protective stop-loss")
    p_open.add_argument("--target", type=float, required=True, help="take-profit target (T1)")
    p_open.add_argument(
        "--target2", type=float, default=None, help="T2 (with T3 → 50/25/25 scale-out)"
    )
    p_open.add_argument("--target3", type=float, default=None, help="T3 take-profit")
    p_open.add_argument("--worst-entry", type=float, default=None, help="latest acceptable entry")
    p_open.add_argument("--risk-dollars", type=float, default=None, help="risk $ for heat gate")
    p_open.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    p_open.add_argument(
        "--live", action="store_true", help="actually POST (needs IB_ALLOW_ORDER_PLACEMENT)"
    )
    p_open.add_argument("--dry-run", action="store_true", help="force preview even with --live")
    p_open.add_argument("--timeout", type=float, default=pib.DEFAULT_TIMEOUT)
    p_open.set_defaults(func=cmd_open_now)

    p_cancel = sub.add_parser("cancel", help="cancel a placed (unfilled) bracket for one thesis")
    p_cancel.add_argument("--thesis-id", required=True)
    p_cancel.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    p_cancel.add_argument("--timeout", type=float, default=pib.DEFAULT_TIMEOUT)
    p_cancel.set_defaults(func=cmd_cancel)

    p_cxo = sub.add_parser("cancel-orders", help="cancel specific IB orders by id (IB-account tab)")
    p_cxo.add_argument("--order-ids", required=True, help="comma-separated IB order ids")
    p_cxo.add_argument("--timeout", type=float, default=pib.DEFAULT_TIMEOUT)
    p_cxo.set_defaults(func=cmd_cancel_orders)

    p_sync = sub.add_parser(
        "sync", help="Telegram-free fill reconcile: ENTRY_READY→ACTIVE on entry fill"
    )
    p_sync.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    p_sync.add_argument("--timeout", type=float, default=pib.DEFAULT_TIMEOUT)
    p_sync.set_defaults(func=cmd_sync)
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
