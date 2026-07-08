"""Parse the ``analysis/signals.md`` journal into structured signal records.

Self-contained on purpose: trader-memory-core is packaged as a standalone
``.skill`` and cannot import the scheduler (``scripts/run_trading_schedule.py``)
or the shared ``scripts/lib`` layer. This mirrors the real format the
``ticker-analysis`` skill writes — a date-first heading, a status emoji
(🟢 BUY / 🟡 HOLD / 🔴 SELL), ``$``-prefixed numbers and
``**Trigger для Long/Short:**`` lines — and keeps the same parse semantics as
``scripts/run_trading_schedule.py:_parse_signals_md`` and
``ui/server/src/lib/signals.ts``.

The single public entry point is :func:`parse_signals_md`, consumed by the
``ticker-analysis`` ingest adapter in ``thesis_ingest.py``.
"""

from __future__ import annotations

import re

# Block heading the ticker-analysis skill writes (date FIRST):
#   ## 2026-06-12 — AOS — 🟢 BUY (reversal)
_SIGNAL_HEADING_RE = re.compile(
    r"^##\s+(\d{4}-\d{2}-\d{2})\s*[—\-]\s*([A-Za-z0-9.\-]+)\s*(?:[—\-]\s*(.*))?$",
    re.MULTILINE,
)


# Price token with optional thousands separators: "3,960", "1,030.00", "45.50",
# "98". Without this, a four-figure price like "$3,960" parses as 3.0 (the regex
# stops at the comma), silently arming a thesis/alert at trigger $3 / stop $4.
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"


def _to_float(raw: str) -> float:
    return float(raw.replace(",", ""))


def _first_dollar(s: str) -> float | None:
    """First $-prefixed number in a line; bare number as fallback."""
    m = re.search(rf"\$\s*({_NUM})", s)
    if not m:
        m = re.search(rf"({_NUM})", s)
    return _to_float(m.group(1)) if m else None


def _all_dollars(s: str) -> list[float]:
    out = [_to_float(x) for x in re.findall(rf"\$\s*({_NUM})", s)]
    if not out:
        out = [_to_float(x) for x in re.findall(rf"({_NUM})", s)]
    return out


def _report_link(s: str) -> str | None:
    """Extract the report path from a ``**Полный отчёт:**`` line.

    Prefers the repo-relative path inside backticks (the canonical form the
    skill writes), then falls back to the markdown link target.
    """
    m = re.search(r"`([^`]+\.md)`", s)
    if m:
        return m.group(1).strip()
    m = re.search(r"\]\(([^)]+\.md)\)", s)
    return m.group(1).strip() if m else None


def parse_block(date: str, ticker: str, status: str, body: str) -> dict | None:
    """Parse one signal block into a record, or None when it has no levels.

    A 🟡 HOLD block never carries an armable setup -> None. A block missing a
    direction, trigger or stop is likewise unusable as a thesis -> None.
    """
    if "HOLD" in status.upper() or "🟡" in status:
        return None

    direction = None
    if re.search(r"🟢\s*BUY", body):
        direction = "long"
    elif re.search(r"🔴\s*SELL", body):
        direction = "short"

    trigger = None
    for line in body.splitlines():
        m = re.search(
            r"\*\*Trigger\s+(?:для\s+)?(Long|Short)\b[^:]*:\*\*\s*(.+)$", line, re.IGNORECASE
        )
        if m:
            trigger = _first_dollar(m.group(2))
            if direction is None:
                direction = "long" if m.group(1).lower() == "long" else "short"
            break
    if direction not in ("long", "short") or trigger is None:
        return None

    stop = t1 = t2 = t3 = entry_low = entry_high = report = None
    for line in body.splitlines():
        if re.search(r"Альтернатив[ау]", line, re.IGNORECASE):
            continue
        if stop is None:
            m = re.search(r"\*\*Stop:\*\*\s*(.+)$", line, re.IGNORECASE)
            if m:
                stop = _first_dollar(m.group(1))
                continue
        if t1 is None:
            m = re.search(r"\*\*T1(?:\s*/\s*T2)?(?:\s*/\s*T3)?:\*\*\s*(.+)$", line, re.IGNORECASE)
            if m:
                nums = _all_dollars(m.group(1))
                t1 = nums[0] if nums else None
                t2 = nums[1] if len(nums) > 1 else None
                t3 = nums[2] if len(nums) > 2 else None
                continue
        if entry_low is None:
            m = re.search(r"\*\*Entry[^:]*:\*\*\s*(.+)$", line, re.IGNORECASE)
            if m:
                nums = _all_dollars(m.group(1))
                if nums:
                    entry_low, entry_high = min(nums), max(nums)
                continue
        if report is None and "Полный отчёт" in line:
            report = _report_link(line)

    if None in (trigger, stop, t1):
        return None
    return {
        "ticker": ticker.upper(),
        "date": date,
        "direction": direction,
        "trigger": trigger,
        "stop": stop,
        "t1": t1,
        "t2": t2,
        "t3": t3,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "report": report,
    }


def parse_signals_md(text: str, ticker: str | None = None) -> list[dict]:
    """Return the latest armable signal record per ticker from a signals.md text.

    Blocks are separated by ``\\n---\\n``. For each ticker only the LAST block in
    file order is kept (mirrors the scheduler) so a re-analysis supersedes the
    prior signal. HOLD blocks and blocks missing direction/trigger/stop/t1 are
    dropped. ``ticker`` (case-insensitive) restricts the result to one symbol.
    """
    want = ticker.upper() if ticker else None
    latest: dict[str, dict] = {}
    for chunk in text.split("\n---\n"):
        m = _SIGNAL_HEADING_RE.search(chunk)
        if not m:
            continue
        block_date, block_ticker, block_status = m.group(1), m.group(2).upper(), (m.group(3) or "")
        if want is not None and block_ticker != want:
            continue
        record = parse_block(block_date, block_ticker, block_status.strip(), chunk)
        if record is not None:
            latest[block_ticker] = record  # last block in file order wins
    return list(latest.values())
