#!/usr/bin/env python3
"""Sync TradingView ``[TH]`` alerts with the open trader-memory theses.

Backs the UI Trader-Memory card "Синхронизировать алерты с TV" button. Brings
TradingView Desktop alerts in line with the current open theses:

  * one alert set per open thesis (IDEA / ENTRY_READY / ACTIVE /
    PARTIALLY_CLOSED): ``Trigger = entry.target_price``,
    ``Stop = exit.stop_loss``, ``T1 = exit.take_profit``;
  * every message carries the ``[TH]`` ownership tag, and every delete is
    scoped with ``--message-contains [TH]`` — manual alerts and ``[WL]``
    watchlist alerts are never touched;
  * theses that closed / invalidated / were deleted drop out of the plan and
    have their ``[TH]`` alerts purged (tickers tracked in
    ``<data-dir>/logs/thesis_alerts_state.json``).

Thesis levels are read via ``trader_memory_cli.py store ... list --full`` (the
launcher re-execs under ``uv`` so PyYAML is available); this script itself is
stdlib-only.

Exit codes: 0 ok; 1 error (incl. TradingView Desktop not reachable).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

import tv_alerts as talerts  # noqa: E402

TRADER_MEMORY_CLI = REPO_ROOT / "skills" / "trader-memory-core" / "scripts" / "trader_memory_cli.py"


def log(msg: str) -> None:
    """Human-readable progress to stdout, streamed live in the UI job log.

    Mirrors recalc_watchlist_from_profile.log: flushed so the SSE stream shows
    each step as it happens rather than only the final JSON at the end.
    """
    print(msg, flush=True)


def _resolve_data_dir(arg: str | None) -> Path:
    """Trading-data root: --data-dir, else $TRADING_DATE_DIR (env or repo .env),
    else ``trading-data`` under the repo. Mirrors recalc_watchlist_from_profile."""
    base = arg or os.environ.get("TRADING_DATE_DIR")
    if not base:
        try:
            for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
                line = line.strip().removeprefix("export ").lstrip()
                if line.startswith("TRADING_DATE_DIR="):
                    base = line.partition("=")[2].strip().strip("'\"")
                    break
        except OSError:
            pass
    if not base:
        base = "trading-data"
    p = Path(base).expanduser()
    return p if p.is_absolute() else REPO_ROOT / p


def _load_open_theses(state_dir: Path) -> list[dict]:
    """Full thesis documents (with entry/exit levels) via the trader-memory CLI.

    Shells ``store ... list --full``; the launcher re-execs under ``uv`` so
    PyYAML is available to thesis_store.py. Returns the full list (open and
    closed) — ``theses_to_alert_plan`` decides which keep alerts.
    """
    cmd = [
        sys.executable,
        str(TRADER_MEMORY_CLI),
        "store",
        "--state-dir",
        str(state_dir),
        "list",
        "--full",
    ]
    env = os.environ.copy()
    env.setdefault("CLAUDE_TRADING_SKILLS_REPO", str(REPO_ROOT))
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if res.returncode != 0:
        raise RuntimeError(
            f"trader_memory_cli store list --full failed (rc={res.returncode}): "
            f"{(res.stderr or res.stdout or '')[-400:]}"
        )
    out = (res.stdout or "").strip()
    data = json.loads(out) if out else []
    return data if isinstance(data, list) else []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sync TradingView [TH] alerts with open theses")
    ap.add_argument("--data-dir", help="Override trading-data dir (else $TRADING_DATE_DIR)")
    ap.add_argument(
        "--timeout", type=int, default=900, help="Per Node-script timeout, seconds (default 900)"
    )
    args = ap.parse_args(argv)

    data_dir = _resolve_data_dir(args.data_dir)
    state_dir = data_dir / "journal" / "theses"
    state_path = data_dir / "logs" / "thesis_alerts_state.json"
    log(f"[sync-thesis-alerts] data-dir: {data_dir}")

    # Fail fast with an actionable message when TradingView Desktop isn't up:
    # the Node CDP scripts would otherwise hang/time out per ticker.
    log("→ Проверяю TradingView Desktop (CDP :9222)…")
    if not talerts.tv_available():
        log("✗ TradingView Desktop недоступен — запусти его и повтори синхронизацию.")
        print(
            json.dumps(
                {
                    "error": "TradingView Desktop недоступен (CDP :9222). Запусти "
                    "TradingView Desktop и повтори синхронизацию.",
                    "tv_available": False,
                },
                ensure_ascii=False,
            )
        )
        return 1
    log("✓ TradingView Desktop доступен")

    log("→ Читаю открытые тезисы (store list --full)…")
    try:
        theses = _load_open_theses(state_dir)
    except (RuntimeError, json.JSONDecodeError) as exc:
        log(f"✗ Не удалось прочитать тезисы: {exc}")
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1

    plan = talerts.theses_to_alert_plan(theses)
    tickers = sorted({s["ticker"] for s in plan["signals"]})
    log(
        f"✓ Тезисов всего: {len(theses)}; под [TH]-алерты (открытых): "
        f"{len(plan['signals'])} → {', '.join(tickers) or '—'}"
    )
    if plan["skipped"]:
        log(f"⚠ Пропущены (нет target/stop): {', '.join(s['ticker'] for s in plan['skipped'])}")

    log("→ Синхронизирую [TH] алерты с TradingView (purge → diff-delete → create)…")
    summary = talerts.sync_thesis_alerts(
        theses, state_path, project_root=REPO_ROOT, timeout=args.timeout
    )
    log(
        f"✓ Готово: создано {summary.get('created', 0)}, удалено {summary.get('deleted', 0)}, "
        f"без изменений {summary.get('kept', 0)}, дубликатов {summary.get('skipped', 0)}, "
        f"ошибок {summary.get('errors', 0)}"
    )
    for detail in summary.get("error_details") or []:
        log(f"  ✗ {detail}")

    result = {
        "summary": summary,
        "tickers": tickers,
        "skipped": plan["skipped"],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if summary.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
