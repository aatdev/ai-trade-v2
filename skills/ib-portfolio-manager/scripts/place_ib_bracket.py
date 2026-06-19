#!/usr/bin/env python3
"""Place a native Interactive Brokers bracket order via the local Gateway REST API.

This is the *write* companion to the strictly read-only ``fetch_ib_snapshot.py``.
Where that script only issues ``GET`` requests, this one can ``POST`` a real
order to the bundled IB Gateway Client Portal API. It therefore carries a
deliberate two-lock safety design (see ``order_placement_status``): a bracket is
only submitted when BOTH the environment flag ``IB_ALLOW_ORDER_PLACEMENT=true``
is set AND the caller passes ``--live`` / ``live=True``. In every other case the
script (and the importable helpers) operate in *preview* mode: they resolve the
contract, build the exact ``orders`` array, and return/print it WITHOUT posting.

Note: the direct REST path bypasses the MCP server's ``IB_READ_ONLY_MODE`` gate
(that flag only controls whether the MCP *registers* its ``place_order`` tool —
it cannot stop a direct POST). The two-lock guard here is the real safety net.

Bracket mechanics (IBKR Client Portal Web API): a native bracket is one POST to
``/iserver/account/{id}/orders`` whose ``orders`` array sets a ``cOID`` (client
order id) on the parent and ``parentId`` equal to that ``cOID`` on each child.
Children arm only when the parent fills and IB makes the two children an OCA
pair (one fills -> the other cancels).

A multi-target scale-out is NOT one POST with several OCA pairs — IB collapses
per-tranche ``ocaGroup`` values into a single group and the whole order sticks in
"Pending Submit" (never transmits, uncancellable). Instead it is N INDEPENDENT
native brackets, one POST each (``build_sub_brackets`` / ``submit_brackets``):
the size splits 50/25/25 and each tranche becomes its own parent + stop + take
bracket with a unique cOID, so IB manages each OCA pair natively.

Connection discovery + auth probing are reused from ``check_ib_connection.py``;
the self-signed-localhost HTTP helper mirrors ``fetch_ib_snapshot.py``.

Usage (preview, posts nothing):
    python3 place_ib_bracket.py --ticker NVDA --side long --shares 50 \
        --pivot 155.23 --stop 150.00 --target 167.68

Usage (actually submit — requires the env flag too):
    IB_ALLOW_ORDER_PLACEMENT=true python3 place_ib_bracket.py --live \
        --ticker NVDA --side long --shares 50 --pivot 155.23 --stop 150 --target 167.68

Environment Variables:
    IB_ALLOW_ORDER_PLACEMENT  'true' to permit real POSTs (default false)
    IB_PAPER_TRADING          'true' for paper (default), 'false' for live
    IB_GATEWAY_RUNTIME_DIR    Override the ib-gateway/.runtime directory location
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from typing import Any

# Sibling import: Python puts this script's own dir on sys.path[0], so the
# preflight-check module resolves whether invoked from the repo root or scripts/.
import check_ib_connection as cic

DEFAULT_TIMEOUT = 20.0
MAX_REPLIES = 16  # bound the order-confirmation reply chain (a far-target
# sub-bracket can raise a precautionary "price exceeds N% constraint" prompt)

# Child of the "watchlist_orders" logger so reply-chain messages propagate to its
# handler when driven from there; silent when this module runs standalone.
log = logging.getLogger("watchlist_orders.place_ib_bracket")


# --------------------------------------------------------------------------- #
# Low-level HTTP (self-signed localhost Gateway; verification intentionally off)
# --------------------------------------------------------------------------- #
def http_get_json(port: int, api_path: str, timeout: float = DEFAULT_TIMEOUT) -> Any:
    """GET ``https://localhost:<port>/v1/api<api_path>`` and parse JSON.

    Mirrors ``fetch_ib_snapshot.http_get_json``: uses ``requests`` when present,
    falling back to the standard library so the script works bare.
    """
    url = f"https://localhost:{port}/v1/api{api_path}"
    try:
        import requests  # type: ignore

        _silence_insecure_warnings()
        resp = requests.get(url, verify=False, timeout=timeout)  # noqa: S501
        resp.raise_for_status()
        return resp.json()
    except ImportError:
        return _http_json_urllib("GET", url, None, timeout)


def http_post_json(
    port: int, api_path: str, body: dict | None, timeout: float = DEFAULT_TIMEOUT
) -> Any:
    """POST JSON to ``https://localhost:<port>/v1/api<api_path>`` and parse JSON.

    The write-side counterpart to ``http_get_json``. ``body`` is JSON-encoded;
    ``None`` posts an empty body (some Client Portal endpoints accept that).
    """
    url = f"https://localhost:{port}/v1/api{api_path}"
    try:
        import requests  # type: ignore

        _silence_insecure_warnings()
        resp = requests.post(url, json=body, verify=False, timeout=timeout)  # noqa: S501
        resp.raise_for_status()
        return resp.json()
    except ImportError:
        return _http_json_urllib("POST", url, body, timeout)


def http_delete_json(port: int, api_path: str, timeout: float = DEFAULT_TIMEOUT) -> Any:
    """DELETE ``https://localhost:<port>/v1/api<api_path>`` and parse JSON (order cancel).

    On a non-2xx, raise ``ValueError`` carrying the response BODY (the Client
    Portal returns a JSON ``error`` explaining a 400) instead of a bare HTTP
    status, so the caller can surface the actual reason.
    """
    url = f"https://localhost:{port}/v1/api{api_path}"
    try:
        import requests  # type: ignore

        _silence_insecure_warnings()
        resp = requests.delete(url, verify=False, timeout=timeout)  # noqa: S501
        if not resp.ok:
            raise ValueError(f"HTTP {resp.status_code}: {resp.text.strip()[:300]}")
        return resp.json()
    except ImportError:
        return _http_json_urllib("DELETE", url, None, timeout)


def _silence_insecure_warnings() -> None:
    try:
        import urllib3  # type: ignore

        urllib3.disable_warnings()
    except Exception:  # pragma: no cover - cosmetic only
        pass


def _http_json_urllib(method: str, url: str, body: dict | None, timeout: float) -> Any:
    """Fallback GET/POST using only the standard library (no requests installed)."""
    import ssl
    import urllib.request

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    data = None
    headers = {}
    if method == "POST":
        data = json.dumps(body or {}).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


# --------------------------------------------------------------------------- #
# Contract + account resolution
# --------------------------------------------------------------------------- #
def resolve_conid(port: int, ticker: str, timeout: float = DEFAULT_TIMEOUT) -> int:
    """Resolve a US equity ticker to its IB contract id (conid).

    Mirrors the MCP client: ``GET /iserver/secdef/search?symbol=X`` then take the
    first ``STK`` row's conid. Raises ``LookupError`` when nothing matches.
    """
    rows = http_get_json(port, f"/iserver/secdef/search?symbol={ticker}", timeout)
    if not isinstance(rows, list) or not rows:
        raise LookupError(f"No contract found for {ticker!r}")
    stk = [r for r in rows if isinstance(r, dict) and _is_stock_row(r)]
    chosen = stk[0] if stk else rows[0]
    conid = chosen.get("conid") if isinstance(chosen, dict) else None
    if conid is None:
        raise LookupError(f"Contract for {ticker!r} has no conid")
    return int(conid)


def _is_stock_row(row: dict) -> bool:
    """Best-effort 'this search result is a stock' across CP field variants."""
    sec_type = row.get("secType") or row.get("assetClass")
    if isinstance(sec_type, str) and sec_type.upper() == "STK":
        return True
    sections = row.get("sections")
    if isinstance(sections, list):
        return any(
            isinstance(s, dict) and str(s.get("secType", "")).upper() == "STK" for s in sections
        )
    return False


def resolve_account_id(port: int, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Return the first portfolio account id (warns elsewhere if multiple exist)."""
    accounts = http_get_json(port, "/portfolio/accounts", timeout)
    ids = account_ids(accounts)
    if not ids:
        raise LookupError("No portfolio accounts returned by the Gateway")
    return ids[0]


def account_ids(accounts: Any) -> list[str]:
    """Extract account id strings from a ``/portfolio/accounts`` payload."""
    out: list[str] = []
    if isinstance(accounts, list):
        for acc in accounts:
            if isinstance(acc, dict):
                acc_id = acc.get("id") or acc.get("accountId")
                if acc_id:
                    out.append(str(acc_id))
    return out


# --------------------------------------------------------------------------- #
# Bracket construction (pure — fully unit-testable, no network)
# --------------------------------------------------------------------------- #
def split_targets_3(shares: int) -> tuple[int, int, int] | None:
    """50/25/25 integer split (remainder to T1); None when too small to split.

    159 -> (80, 40, 39). Requires every tranche >= 1 share, so < 4 shares (or any
    zero tranche) returns None and the caller falls back to a single target.
    """
    q1 = round(shares * 0.5)
    q2 = round(shares * 0.25)
    q3 = shares - q1 - q2
    if min(q1, q2, q3) < 1:
        return None
    return q1, q2, q3


def build_bracket_orders(
    side: str,
    conid: int,
    shares: float,
    pivot: float,
    stop: float,
    target: float,
    coid: str,
    *,
    entry_tif: str = "GTC",
) -> list[dict]:
    """Build ONE native 3-leg bracket ``orders`` array (parent + stop + take).

      LONG  -> parent BUY  STP @ pivot, child SELL STP @ stop, child SELL LMT @ target.
      SHORT -> parent SELL STP @ pivot, child BUY  STP @ stop, child BUY  LMT @ target.

    Both children carry ``parentId == cOID``; IB makes them a native OCA pair (one
    fills -> the other cancels) once the parent fills. There is deliberately NO
    explicit ``ocaGroup`` — that is what collapsed a multi-tranche scale-out into a
    single group and left it stuck "Pending Submit". A scale-out is instead built
    as several of these standalone brackets by ``build_sub_brackets``. Entry is a
    plain STP, GTC by default (rests across sessions until the breakdown/breakout
    trigger fires — pass ``entry_tif="DAY"`` to expire it at the close instead) —
    a gap can fill past any chase band.
    """
    _validate_geometry(side, shares, pivot, stop, target)
    side = side.lower()
    if side == "long":
        entry_action, exit_action = "BUY", "SELL"
    elif side == "short":
        entry_action, exit_action = "SELL", "BUY"
    else:
        raise ValueError(f"side must be 'long' or 'short', got {side!r}")

    # NOTE: the IBKR Client Portal Web API carries a plain STP order's stop
    # trigger in ``price`` (``auxPrice`` is for STOP_LIMIT / trailing only — a TWS
    # API concept). Sending a STP with ``auxPrice`` and no ``price`` is rejected
    # with "Invalid order price fields".
    parent = {
        "conid": int(conid),
        "orderType": "STP",
        "side": entry_action,
        "quantity": shares,
        "price": pivot,
        "tif": _normalize_tif(entry_tif),
        "cOID": coid,
    }
    stop_leg = {
        "conid": int(conid),
        "orderType": "STP",
        "side": exit_action,
        "quantity": shares,
        "price": stop,
        "tif": "GTC",
        "parentId": coid,
    }
    take_leg = {
        "conid": int(conid),
        "orderType": "LMT",
        "side": exit_action,
        "quantity": shares,
        "price": target,
        "tif": "GTC",
        "parentId": coid,
    }
    return [parent, stop_leg, take_leg]


def sub_coid(coid: str, i: int) -> str:
    """Per-tranche client order id — each scale-out sub-bracket needs a unique cOID.

    ``wl-th_x-2026-06-18`` -> ``wl-th_x-2026-06-18-t1`` / ``-t2`` / ``-t3``. The
    base ``coid`` stays the per-(thesis, day) anchor; idempotency / live-bracket
    detection match it as a PREFIX (every tranche ref starts with the base).
    """
    return f"{coid}-t{i}"


def build_sub_brackets(
    side: str,
    conid: int,
    shares: float,
    pivot: float,
    stop: float,
    target: float,
    coid: str,
    *,
    target2: float | None = None,
    target3: float | None = None,
    entry_tif: str = "GTC",
) -> list[list[dict]]:
    """Build N INDEPENDENT native brackets — one POST each — for a candidate.

    Single target (T2/T3 absent): one standalone bracket ``[entry, stop, take]``.

    Scale-out (BOTH T2 and T3 given, and shares split into >= 1 each): the size is
    split 50/25/25 across T1/T2/T3 and EACH tranche becomes its own standalone
    native bracket (entry at the same pivot, its own stop + take, a unique cOID
    ``{coid}-t{i}``). IB then manages every bracket's stop/take as a native OCA
    pair — avoiding the single-POST multi-OCA collapse where IB merged all the
    tranches into one group and the order stuck "Pending Submit". Falls back to a
    single full-size bracket when T2/T3 are not both present or the shares are too
    few to split.

    Returns a list of ``orders`` arrays (one per ``submit_brackets`` POST).
    """
    _validate_geometry(side, shares, pivot, stop, target)
    tranches: list[tuple[float, int]] = [(target, int(shares))]
    if target2 is not None and target3 is not None:
        _validate_scale_targets(side, pivot, target, target2, target3)
        split = split_targets_3(int(shares))
        if split is not None:
            tranches = list(zip((target, target2, target3), split))
    return [
        build_bracket_orders(
            side, conid, qty, pivot, stop, tp, sub_coid(coid, i), entry_tif=entry_tif
        )
        for i, (tp, qty) in enumerate(tranches, start=1)
    ]


def _normalize_tif(tif: str) -> str:
    t = (tif or "DAY").upper()
    if t not in {"DAY", "GTC", "IOC", "OPG"}:
        raise ValueError(f"unsupported tif {tif!r}")
    return t


def _validate_geometry(side: str, shares: float, pivot: float, stop: float, target: float) -> None:
    if shares is None or shares <= 0:
        raise ValueError("shares must be > 0")
    for name, val in (("pivot", pivot), ("stop", stop), ("target", target)):
        if val is None or val <= 0:
            raise ValueError(f"{name} must be a positive price")
    if side.lower() == "long":
        if not (stop < pivot < target):
            raise ValueError(
                f"long bracket requires stop < pivot < target ({stop}/{pivot}/{target})"
            )
    elif side.lower() == "short":
        if not (target < pivot < stop):
            raise ValueError(
                f"short bracket requires target < pivot < stop ({target}/{pivot}/{stop})"
            )


def _validate_scale_targets(side: str, pivot: float, t1: float, t2: float, t3: float) -> None:
    """Targets must be positive and ordered away from entry in the profit direction.

    LONG  -> pivot < t1 <= t2 <= t3.   SHORT -> pivot > t1 >= t2 >= t3.
    """
    for name, val in (("T2", t2), ("T3", t3)):
        if val is None or val <= 0:
            raise ValueError(f"{name} must be a positive price")
    if side.lower() == "long":
        if not (pivot < t1 <= t2 <= t3):
            raise ValueError(
                f"long scale-out requires pivot < T1 <= T2 <= T3 ({pivot}/{t1}/{t2}/{t3})"
            )
    elif side.lower() == "short":
        if not (pivot > t1 >= t2 >= t3):
            raise ValueError(
                f"short scale-out requires pivot > T1 >= T2 >= T3 ({pivot}/{t1}/{t2}/{t3})"
            )


def coid_prefix(thesis_id: str) -> str:
    """Date-agnostic cOID prefix for a thesis — ``wl-<thesis_id>-``.

    Live-order idempotency must span sessions. With GTC entries (the default) an
    unfilled bracket placed on an earlier day keeps resting under
    ``wl-<id>-<earlier-date>-…``; a same-day anchor (what ``coid_for`` builds)
    would not match it and the next run would place a DUPLICATE. Detecting on this
    date-stripped prefix catches the thesis's bracket regardless of which day it
    was submitted. The trailing ``-`` stops one thesis id from matching a longer
    sibling that shares its leading characters.
    """
    return f"wl-{thesis_id}-"


def coid_for(thesis_id: str, date_str: str) -> str:
    """Deterministic client order id base — the idempotency anchor per (thesis, day).

    Built as ``coid_prefix(thesis_id) + date_str``. The actual cOIDs submitted to
    IB append a per-attempt nonce + tranche suffix (see ``attempt_nonce`` /
    ``sub_coid``) so a re-place never reuses a cancelled order's Local order ID.
    Detection / dedupe matches the date-agnostic ``coid_prefix`` (so a GTC entry
    resting from an earlier session is still recognized); ``includes(thesis.id)``
    is used in the UI.
    """
    return f"{coid_prefix(thesis_id)}{date_str}"


def attempt_nonce() -> str:
    """Short per-attempt token to keep each placement's cOIDs unique.

    IB rejects reusing a Local order ID (cOID) within a session — even after the
    order is cancelled ("… is already registered."). Appending this nonce to the
    stable base cOID gives every placement attempt fresh cOIDs while leaving the
    base intact as the detection/idempotency prefix. Hex of the current time at
    ~ms resolution: always differs between manual retries seconds apart.
    """
    return format(int(time.time() * 1000) & 0xFFFFFFFFF, "x")


# --------------------------------------------------------------------------- #
# Submission + confirmation reply loop
# --------------------------------------------------------------------------- #
def _is_terminal_order_response(resp: Any) -> bool:
    """True when the POST/reply response is an accepted-orders array."""
    if isinstance(resp, list) and resp:
        first = resp[0]
        return isinstance(first, dict) and ("order_id" in first or "order_status" in first)
    return False


def _confirmation_reply_id(resp: Any) -> tuple[str | None, list[str]]:
    """Extract (replyId, messageIds) from a confirmation-prompt response, else (None, [])."""
    if isinstance(resp, list) and resp:
        first = resp[0]
        if isinstance(first, dict) and "id" in first and "order_id" not in first:
            return first.get("id"), first.get("messageIds", []) or []
    return None, []


def place_with_confirmations(
    port: int,
    account_id: str,
    body: dict,
    timeout: float = DEFAULT_TIMEOUT,
    max_replies: int = MAX_REPLIES,
) -> Any:
    """POST a bracket and walk the confirmation reply chain to a terminal response.

    The order POST usually returns a chain of ``{id, message, messageIds}``
    confirmation prompts (precedence / size / far-price precautionary warnings);
    each is auto-answered "Yes" via ``POST /iserver/reply/{id}`` with body
    ``{"confirmed": true}`` (the reply id is in the URL — the message ids are NOT
    part of the reply body), and a reply can itself yield another prompt. Loops
    until an accepted-orders array, an error/unknown shape, or ``max_replies``.
    """
    resp = http_post_json(port, f"/iserver/account/{account_id}/orders", body, timeout)
    for round_no in range(max_replies):
        if _is_terminal_order_response(resp):
            return resp
        reply_id, _message_ids = _confirmation_reply_id(resp)
        if not reply_id:
            return resp  # error envelope / unexpected shape — caller inspects
        prompt = ""
        if isinstance(resp, list) and resp and isinstance(resp[0], dict):
            msg = resp[0].get("message")
            prompt = " / ".join(msg) if isinstance(msg, list) else str(msg or "")
        log.info("auto-confirming IB prompt %d (%s): %s", round_no + 1, reply_id, prompt[:160])
        resp = http_post_json(port, f"/iserver/reply/{reply_id}", {"confirmed": True}, timeout)
    log.warning("confirmation chain not terminal after %d replies", max_replies)
    return resp


def extract_order_ids(resp: Any) -> list[str]:
    """Pull submitted order ids out of a terminal response (parent first)."""
    out: list[str] = []
    if isinstance(resp, list):
        for row in resp:
            if isinstance(row, dict):
                oid = row.get("order_id") or row.get("orderId")
                if oid is not None:
                    out.append(str(oid))
    return out


def reject_reason(resp: Any) -> str | None:
    """Best-effort human reason from a rejected / non-terminal order response.

    The Client Portal signals failure in several shapes: a bare ``{"error": ...}``
    envelope, a list of rows each carrying ``error`` / ``text`` / ``message``
    (warning text it expected a reply to), or a row whose ``order_status`` is a
    reject. Pull whatever message is there so the caller can surface IB's actual
    reason instead of a generic "rejected".
    """

    def _from_dict(d: dict) -> str | None:
        for k in ("error", "text", "message"):
            v = d.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, list) and v:
                joined = "; ".join(str(x).strip() for x in v if str(x).strip())
                if joined:
                    return joined
        st = d.get("order_status") or d.get("status")
        if isinstance(st, str) and "reject" in st.lower():
            return f"order_status={st}"
        return None

    if isinstance(resp, dict):
        return _from_dict(resp)
    if isinstance(resp, list):
        reasons = [_from_dict(r) for r in resp if isinstance(r, dict)]
        reasons = [r for r in reasons if r]
        if reasons:
            return " | ".join(reasons)
    return None


def submit_bracket(
    port: int,
    account_id: str,
    orders: list[dict],
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Submit a pre-built bracket and return a normalized result envelope."""
    resp = place_with_confirmations(port, account_id, {"orders": orders}, timeout)
    order_ids = extract_order_ids(resp)
    ok = _is_terminal_order_response(resp) and bool(order_ids)
    return {
        "ok": ok,
        "order_ids": order_ids,
        "entry_order_id": order_ids[0] if order_ids else None,
        "reason": None if ok else (reject_reason(resp) or "broker rejected order"),
        "raw": resp,
    }


def submit_brackets(
    port: int,
    account_id: str,
    brackets: list[list[dict]],
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Submit N independent native sub-brackets (one POST each) and aggregate.

    Each bracket is its own parent/OCO group, so IB manages every OCA pair
    natively (vs the single-POST multi-OCA collapse). Partial success is
    possible: the envelope reports EVERY placed order id across all sub-brackets
    (so even a partial fan-out can be fully torn down), the first tranche's parent
    as ``entry_order_id`` (the fill-watch anchor) plus all parents in
    ``entry_order_ids``, and fails overall (``ok`` False) if any sub-bracket was
    rejected — joining each rejection reason.
    """
    results = [submit_bracket(port, account_id, orders, timeout) for orders in brackets]
    all_ids = [oid for r in results for oid in r["order_ids"]]
    entry_ids = [r["entry_order_id"] for r in results if r["entry_order_id"]]
    reasons = [r["reason"] for r in results if not r["ok"] and r.get("reason")]
    ok_all = bool(results) and all(r["ok"] for r in results)
    return {
        "ok": ok_all,
        "order_ids": all_ids,
        "entry_order_id": entry_ids[0] if entry_ids else None,
        "entry_order_ids": entry_ids,
        "reason": None if ok_all else ("; ".join(reasons) or "broker rejected order"),
        "raw": [r["raw"] for r in results],
    }


# --------------------------------------------------------------------------- #
# Fill detection (ENTRY_READY -> ACTIVE happens on a real fill)
# --------------------------------------------------------------------------- #
def order_fill_status(port: int, entry_order_id: str, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Return {status, filled, avg_price} for the entry order, reading live orders.

    Used by the daemon to detect when a pending buy-stop entry actually fills, so
    it can transition the thesis ENTRY_READY -> ACTIVE with the real fill price.
    """
    payload = http_get_json(port, "/iserver/account/orders", timeout)
    rows: list = []
    if isinstance(payload, dict) and isinstance(payload.get("orders"), list):
        rows = payload["orders"]
    elif isinstance(payload, list):
        rows = payload
    for row in rows:
        if not isinstance(row, dict):
            continue
        oid = row.get("orderId") or row.get("order_id")
        if oid is not None and str(oid) == str(entry_order_id):
            status = str(row.get("status") or row.get("order_ccp_status") or "").strip()
            avg = _to_float(row.get("avgPrice")) or _to_float(row.get("avg_price"))
            filled = status.lower() in {"filled", "complete", "completed"}
            return {"status": status, "filled": filled, "avg_price": avg}
    return {"status": None, "filled": False, "avg_price": None}


# Order statuses that will never execute again — a cancelled/inactive/rejected
# row lingers in /iserver/account/orders but must NOT count as a live bracket.
_DEAD_ORDER_STATUSES = frozenset({"cancelled", "canceled", "inactive", "rejected"})


def live_order_refs(port: int, timeout: float = DEFAULT_TIMEOUT) -> set[str]:
    """Best-effort set of client-order refs that are still WORKING (idempotency guard).

    The Client Portal echoes the submitted ``cOID`` back as ``order_ref`` on some
    builds; we collect any such field so the daemon can avoid double-placing the
    same bracket after a crash/restart.

    Crucially, DEAD rows (Cancelled / Inactive / Rejected) are skipped: the Client
    Portal keeps them in ``/iserver/account/orders`` after they're cancelled, and
    counting their cOID as "live" would wrongly block a re-place of a bracket the
    trader already tore down. A Filled entry IS still counted (a real position
    exists — don't re-arm over it).
    """
    payload = http_get_json(port, "/iserver/account/orders", timeout)
    rows: list = []
    if isinstance(payload, dict) and isinstance(payload.get("orders"), list):
        rows = payload["orders"]
    elif isinstance(payload, list):
        rows = payload
    refs: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or row.get("order_ccp_status") or "").strip().lower()
        if status in _DEAD_ORDER_STATUSES:
            continue
        for key in ("order_ref", "cOID", "orderRef"):
            val = row.get(key)
            if isinstance(val, str) and val:
                refs.add(val)
    return refs


def _to_float(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# Scale-out / close helpers (+2R: sell half MKT, move the stop to breakeven)
# --------------------------------------------------------------------------- #
def exit_action_for(side: str) -> str:
    """The order side that REDUCES/closes a position: SELL a long, BUY a short."""
    return "SELL" if str(side or "long").lower() == "long" else "BUY"


def place_market_close(
    port: int,
    account_id: str,
    conid: int,
    action: str,
    qty: float,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Place a single MKT order (used to scale out / close). Normalized envelope."""
    orders = [
        {"conid": int(conid), "orderType": "MKT", "side": action, "quantity": qty, "tif": "DAY"}
    ]
    resp = place_with_confirmations(port, account_id, {"orders": orders}, timeout)
    ids = extract_order_ids(resp)
    return {"ok": _is_terminal_order_response(resp) and bool(ids), "order_ids": ids, "raw": resp}


def place_stop(
    port: int,
    account_id: str,
    conid: int,
    action: str,
    qty: float,
    stop_price: float,
    tif: str = "GTC",
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Place a single protective STP order (e.g. the new breakeven stop)."""
    orders = [
        {
            "conid": int(conid),
            "orderType": "STP",
            "side": action,
            "quantity": qty,
            "price": stop_price,  # CP carries the STP trigger in `price` (see build_bracket_orders)
            "tif": _normalize_tif(tif),
        }
    ]
    resp = place_with_confirmations(port, account_id, {"orders": orders}, timeout)
    ids = extract_order_ids(resp)
    return {"ok": _is_terminal_order_response(resp) and bool(ids), "order_ids": ids, "raw": resp}


def cancel_order(
    port: int, account_id: str, order_id: str, timeout: float = DEFAULT_TIMEOUT
) -> Any:
    """Cancel one working order by id (DELETE /iserver/account/{id}/order/{orderId})."""
    return http_delete_json(port, f"/iserver/account/{account_id}/order/{order_id}", timeout)


_WORKING_DONE_STATUSES = frozenset({"filled", "cancelled", "canceled", "inactive", "rejected"})


def working_exit_orders(
    port: int, conid: int, exit_action: str, timeout: float = DEFAULT_TIMEOUT
) -> list[str]:
    """Order ids of WORKING protective orders (stop/target) for a conid + exit side.

    Used to tear down the old bracket children before re-arming a breakeven stop.
    """
    payload = http_get_json(port, "/iserver/account/orders", timeout)
    rows: list = []
    if isinstance(payload, dict) and isinstance(payload.get("orders"), list):
        rows = payload["orders"]
    elif isinstance(payload, list):
        rows = payload
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_conid = row.get("conid")
        if row_conid is None or int(row_conid) != int(conid):
            continue
        side = str(row.get("side", "")).upper()
        if side != exit_action.upper():
            continue
        status = str(row.get("status") or row.get("order_ccp_status") or "").strip().lower()
        if status in _WORKING_DONE_STATUSES:
            continue
        oid = row.get("orderId") or row.get("order_id")
        if oid is not None:
            out.append(str(oid))
    return out


# --------------------------------------------------------------------------- #
# Safety: the two-lock placement guard + mode helpers
# --------------------------------------------------------------------------- #
def is_paper() -> bool:
    return cic.bool_env("IB_PAPER_TRADING", default=True)


def mode_badge() -> str:
    return "📝 PAPER" if is_paper() else "🔴 LIVE"


def order_placement_status(live_flag: bool) -> tuple[bool, str]:
    """Return (allowed, reason). BOTH ``--live`` and the env flag are required.

    Neither a stray env var nor a stray flag alone can place an order — this is
    the safety net that replaces the MCP's ``IB_READ_ONLY_MODE`` (which a direct
    REST POST bypasses).
    """
    if not live_flag:
        return False, "preview mode (--live not set)"
    if not cic.bool_env("IB_ALLOW_ORDER_PLACEMENT", default=False):
        return False, "IB_ALLOW_ORDER_PLACEMENT is not enabled"
    return True, "placement enabled"


# --------------------------------------------------------------------------- #
# Connection helper
# --------------------------------------------------------------------------- #
def connect(runtime_dir: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> int:
    """Discover the Gateway session, verify auth, and return its port.

    Raises ``ConnectionError`` with a human reason on any failure so callers
    (daemon / CLI) can degrade gracefully without touching theses.
    """
    dirs = cic.candidate_runtime_dirs(runtime_dir)
    session_path = cic.find_session_file(dirs)
    if session_path is None:
        searched = ", ".join(str(d) for d in dirs)
        raise ConnectionError(f"IB Gateway session file not found (searched: {searched})")
    session = cic.load_session(session_path)
    port = session.get("port")
    if not isinstance(port, int):
        raise ConnectionError("Gateway session file has no usable 'port'")
    authenticated, detail = cic.probe_auth(port, timeout=timeout)
    if not authenticated:
        raise ConnectionError(f"IB Gateway is not authenticated ({detail})")
    return port


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--side", choices=["long", "short"], default="long")
    parser.add_argument("--shares", type=float, required=True)
    parser.add_argument("--pivot", type=float, required=True, help="entry buy/sell-stop trigger")
    parser.add_argument("--stop", type=float, required=True, help="protective stop-loss")
    parser.add_argument("--target", type=float, required=True, help="take-profit target (T1)")
    parser.add_argument(
        "--target2", type=float, default=None, help="T2 (with T3 → 50/25/25 scale-out)"
    )
    parser.add_argument("--target3", type=float, default=None, help="T3 take-profit")
    parser.add_argument("--account-id", default=None)
    parser.add_argument("--coid", default=None, help="client order id (idempotency anchor)")
    parser.add_argument(
        "--live", action="store_true", help="actually POST (needs the env flag too)"
    )
    parser.add_argument("--dry-run", action="store_true", help="force preview even with --live")
    parser.add_argument("--runtime-dir", default=None)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)

    # A user-supplied --coid is used verbatim; the default gets a per-attempt
    # nonce so re-running after a cancel doesn't hit "Local order ID … already
    # registered" (IB forbids reusing a cancelled cOID within the session).
    coid = args.coid or f"{coid_for(args.ticker.lower(), 'manual')}-{attempt_nonce()}"

    def _build(conid: int) -> list[list[dict]]:
        return build_sub_brackets(
            args.side,
            conid=conid,
            shares=args.shares,
            pivot=args.pivot,
            stop=args.stop,
            target=args.target,
            coid=coid,
            target2=args.target2,
            target3=args.target3,
        )

    try:
        brackets = _build(conid=0)
    except ValueError as exc:
        print(f"Invalid bracket geometry: {exc}", file=sys.stderr)
        return 1

    allowed, reason = order_placement_status(args.live and not args.dry_run)

    # Preview path: still resolve the conid/account if the Gateway is reachable,
    # but never POST. Degrade to a contract-less preview if discovery fails.
    if not allowed:
        port = None
        try:
            port = connect(args.runtime_dir, args.timeout)
            conid = resolve_conid(port, args.ticker, args.timeout)
            account_id = args.account_id or resolve_account_id(port, args.timeout)
            brackets = _build(conid=conid)
        except (ConnectionError, LookupError) as exc:
            account_id = args.account_id
            print(f"(preview only — Gateway not used: {exc})", file=sys.stderr)
        preview = {
            "mode": "preview",
            "reason": reason,
            "paper": is_paper(),
            "account_id": account_id,
            "cOID": coid,
            "sub_brackets": len(brackets),
            "would_place": brackets,
        }
        json.dump(preview, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    # Live path: discover, resolve, submit each sub-bracket on its own POST.
    try:
        port = connect(args.runtime_dir, args.timeout)
        conid = resolve_conid(port, args.ticker, args.timeout)
        account_id = args.account_id or resolve_account_id(port, args.timeout)
    except (ConnectionError, LookupError) as exc:
        print(f"Cannot place order: {exc}", file=sys.stderr)
        return 2
    brackets = _build(conid=conid)
    result = submit_brackets(port, account_id, brackets, args.timeout)
    out = {"mode": "live", "paper": is_paper(), "account_id": account_id, "cOID": coid, **result}
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
