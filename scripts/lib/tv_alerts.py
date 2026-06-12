#!/usr/bin/env python3
"""TradingView alert management for the trading-schedule auto mode (stdlib).

Bridges the evening watchlist (``schedule/watchlist_<date>.json``) and the
TradingView Desktop alert UI, reusing the signals-alerts skill's Node/CDP
scripts (``create_alerts.mjs`` / ``delete_alerts.mjs``):

  * ``tv_available``            — fast probe of the CDP endpoint (:9222) so
                                  callers can notify the trader IMMEDIATELY
                                  when TradingView Desktop is not running.
  * ``watchlist_to_alert_plan`` — watchlist candidates -> plan JSON in the
                                  exact shape parse_signals.mjs produces
                                  (Trigger / Stop / T1 per candidate).
  * ``sync_watchlist_alerts``   — bring TV alerts in line with the current
                                  watchlist: purge alerts of dropped tickers,
                                  diff-delete stale levels, create missing.
  * ``purge_watchlist_alerts``  — drop alerts for tickers that became
                                  irrelevant intraday (e.g. MISSED entries).

Every alert message carries the ``[WL]`` tag and all delete operations are
scoped with ``--message-contains [WL]`` — manually curated alerts from
``analysis/signals.md`` (no tag) are never touched. The set of tickers we
created alerts for is tracked in a state file so the next sync can clean up
dropped candidates.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ALERT_SCRIPTS_DIR = REPO_ROOT / "skills" / "signals-alerts" / "scripts"

# Marks auto-created watchlist alerts; ownership scope for every delete.
WL_TAG = "[WL]"

CDP_HOST = "127.0.0.1"
DEFAULT_CDP_PORT = 9222

NODE_FALLBACK_PATHS = ["/opt/homebrew/bin/node", "/usr/local/bin/node"]


# --------------------------------------------------------------------------- #
# TradingView availability probe
# --------------------------------------------------------------------------- #
def tv_available(*, timeout: float = 3.0) -> bool:
    """True when TradingView Desktop is reachable over CDP (tv launch)."""
    port = os.environ.get("TV_CDP_PORT", str(DEFAULT_CDP_PORT))
    url = f"http://{CDP_HOST}:{port}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # nosec B310
            return bool(resp.read())
    except OSError:
        return False
    except Exception:  # noqa: BLE001 — any probe failure means "not available"
        return False


# --------------------------------------------------------------------------- #
# Watchlist -> alert plan (same shape as parse_signals.mjs output)
# --------------------------------------------------------------------------- #
def _fmt(price: float) -> str:
    return f"{price:g}"


def watchlist_to_alert_plan(watchlist: dict | None) -> dict:
    """Build the create/delete plan: Trigger / Stop / T1 alerts per candidate.

    Candidates without a ticker, pivot or stop are reported in ``skipped``.
    Messages carry the [WL] tag (ownership scope) and embed the price, so a
    level change naturally re-creates the alert through diff sync.
    """
    signals, skipped = [], []
    for c in (watchlist or {}).get("candidates") or []:
        ticker = str(c.get("ticker", "")).upper()
        pivot, stop, target = c.get("pivot"), c.get("stop"), c.get("target")
        if not ticker or not pivot or not stop:
            skipped.append({"ticker": ticker or "?", "reason": "нет ticker/pivot/stop"})
            continue
        is_long = c.get("side", "long") != "short"
        dir_ru = "лонг" if is_long else "шорт"
        up = "Crossing Up" if is_long else "Crossing Down"
        down = "Crossing Down" if is_long else "Crossing Up"
        alerts = [
            {
                "level": "Trigger",
                "price": pivot,
                "price_condition": up,
                "message": f"{ticker}: {WL_TAG} вход ({dir_ru}) — Trigger ${_fmt(pivot)}",
            },
            {
                "level": "Stop",
                "price": stop,
                "price_condition": down,
                "message": f"{ticker}: {WL_TAG} стоп ({dir_ru}) — Stop ${_fmt(stop)}",
            },
        ]
        if target:
            alerts.append(
                {
                    "level": "T1",
                    "price": target,
                    "price_condition": up,
                    "message": f"{ticker}: {WL_TAG} цель ({dir_ru}) — T1 ${_fmt(target)}",
                }
            )
        signals.append(
            {"ticker": ticker, "direction": "LONG" if is_long else "SHORT", "alerts": alerts}
        )
    return {"signals": signals, "skipped": skipped}


# --------------------------------------------------------------------------- #
# Alerted-tickers state (who we created alerts for)
# --------------------------------------------------------------------------- #
def load_alerts_state(path: Path | str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("tickers", [])
            return data
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {"tickers": []}


def save_alerts_state(path: Path | str, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Node script execution
# --------------------------------------------------------------------------- #
def _resolve_node() -> str:
    override = os.environ.get("NODE_BIN")
    if override:
        return override
    if shutil.which("node"):
        return "node"
    for candidate in NODE_FALLBACK_PATHS:
        if Path(candidate).is_file():
            return candidate
    return "node"


def _run_node(script: str, args: list[str], *, project_root: Path, timeout: int) -> dict:
    """Run one signals-alerts Node script; return its JSON stdout or {error}."""
    cmd = [_resolve_node(), str(ALERT_SCRIPTS_DIR / script), *args]
    try:
        res = subprocess.run(cmd, cwd=project_root, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"error": f"{script}: timed out after {timeout}s"}
    except OSError as exc:
        return {"error": f"{script}: launch error: {exc}"}
    out = (res.stdout or "").strip()
    try:
        data = json.loads(out) if out else {}
    except json.JSONDecodeError:
        data = {}
    if res.returncode != 0 and "error" not in data:
        data["error"] = f"{script}: rc={res.returncode}: {(res.stderr or out)[-400:]}"
    return data


def _accumulate(summary: dict, res: dict) -> None:
    if res.get("error"):
        summary["errors"] += 1
        summary["error_details"].append(str(res["error"])[:300])
        return
    s = res.get("summary") or {}
    for key in ("created", "deleted", "kept", "skipped", "not_found_in_ui"):
        summary[key] += int(s.get(key) or 0)
    summary["errors"] += int(s.get("errors") or 0)


def _new_summary() -> dict:
    return {
        "created": 0,
        "deleted": 0,
        "kept": 0,
        "skipped": 0,
        "not_found_in_ui": 0,
        "errors": 0,
        "error_details": [],
    }


# --------------------------------------------------------------------------- #
# Sync / purge
# --------------------------------------------------------------------------- #
def sync_watchlist_alerts(
    watchlist: dict | None,
    state_path: Path | str,
    *,
    project_root: Path = REPO_ROOT,
    timeout: int = 900,
) -> dict:
    """Bring TV alerts in line with the watchlist (create new / delete stale).

    1. Purge [WL] alerts of tickers dropped since the previous sync.
    2. Diff-delete stale [WL] levels for current tickers (--keep-from-plan).
    3. Create missing alerts (create_alerts.mjs dedupes by message).
    Requires TradingView Desktop; callers should check tv_available() first.
    """
    plan = watchlist_to_alert_plan(watchlist)
    current = sorted({s["ticker"] for s in plan["signals"]})
    state = load_alerts_state(state_path)
    previous = {str(t).upper() for t in state.get("tickers") or []}
    dropped = sorted(previous - set(current))

    summary = _new_summary()
    if not current and not dropped:
        return summary

    if dropped:
        _accumulate(
            summary,
            _run_node(
                "delete_alerts.mjs",
                ["--tickers", ",".join(dropped), "--message-contains", WL_TAG],
                project_root=project_root,
                timeout=timeout,
            ),
        )

    if current:
        tmp_dir = Path(project_root) / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        plan_path = tmp_dir / f"watchlist_alert_plan_{int(time.time())}.json"
        plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            _accumulate(
                summary,
                _run_node(
                    "delete_alerts.mjs",
                    ["--keep-from-plan", "--message-contains", WL_TAG, "--file", str(plan_path)],
                    project_root=project_root,
                    timeout=timeout,
                ),
            )
            _accumulate(
                summary,
                _run_node(
                    "create_alerts.mjs",
                    ["--file", str(plan_path)],
                    project_root=project_root,
                    timeout=timeout,
                ),
            )
        finally:
            plan_path.unlink(missing_ok=True)  # tmp/ plans must not accumulate

    if summary["errors"]:
        # A failed delete must be retried on the next sync, not forgotten:
        # keep every ticker we may still own alerts for.
        save_alerts_state(state_path, {**state, "tickers": sorted(set(current) | previous)})
    else:
        save_alerts_state(state_path, {**state, "tickers": current})
    return summary


def purge_watchlist_alerts(
    tickers: list[str],
    state_path: Path | str,
    *,
    project_root: Path = REPO_ROOT,
    timeout: int = 600,
) -> dict:
    """Delete [WL] alerts for tickers that are no longer relevant (intraday)."""
    summary = _new_summary()
    wanted = sorted({str(t).upper() for t in tickers if t})
    if not wanted:
        return summary
    _accumulate(
        summary,
        _run_node(
            "delete_alerts.mjs",
            ["--tickers", ",".join(wanted), "--message-contains", WL_TAG],
            project_root=project_root,
            timeout=timeout,
        ),
    )
    if not summary["errors"]:
        # Only forget tickers whose purge actually succeeded; otherwise the
        # orphaned [WL] alerts would never be retried.
        state = load_alerts_state(state_path)
        remaining = [t for t in state.get("tickers") or [] if str(t).upper() not in set(wanted)]
        save_alerts_state(state_path, {**state, "tickers": remaining})
    return summary
