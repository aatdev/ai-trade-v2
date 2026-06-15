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
import sys
from typing import Any

# Sibling import: Python puts this script's own dir on sys.path[0], so the
# preflight-check module resolves whether invoked from the repo root or scripts/.
import check_ib_connection as cic

DEFAULT_TIMEOUT = 20.0
MAX_REPLIES = 8  # bound the order-confirmation reply chain


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
def build_bracket_orders(
    side: str,
    conid: int,
    shares: float,
    pivot: float,
    stop: float,
    target: float,
    coid: str,
    *,
    entry_tif: str = "DAY",
) -> list[dict]:
    """Build the native-bracket ``orders`` array for one watchlist candidate.

    LONG  -> parent BUY  STP @ pivot, child SELL STP @ stop, child SELL LMT @ target.
    SHORT -> parent SELL STP @ pivot, child BUY  STP @ stop, child BUY  LMT @ target.

    The parent carries ``cOID``; both children carry ``parentId == cOID`` so they
    arm on the parent fill and form an OCA pair. The entry is a plain STP (DAY):
    a gap can fill worse than any chase band, which the caller surfaces to the
    human in the Telegram card.
    """
    _validate_geometry(side, shares, pivot, stop, target)
    side = side.lower()
    qty = shares
    if side == "long":
        entry_action, exit_action = "BUY", "SELL"
    elif side == "short":
        entry_action, exit_action = "SELL", "BUY"
    else:
        raise ValueError(f"side must be 'long' or 'short', got {side!r}")

    parent = {
        "conid": int(conid),
        "orderType": "STP",
        "side": entry_action,
        "quantity": qty,
        "auxPrice": pivot,
        "tif": _normalize_tif(entry_tif),
        "cOID": coid,
    }
    stop_leg = {
        "conid": int(conid),
        "orderType": "STP",
        "side": exit_action,
        "quantity": qty,
        "auxPrice": stop,
        "tif": "GTC",
        "parentId": coid,
    }
    target_leg = {
        "conid": int(conid),
        "orderType": "LMT",
        "side": exit_action,
        "quantity": qty,
        "price": target,
        "tif": "GTC",
        "parentId": coid,
    }
    return [parent, stop_leg, target_leg]


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


def coid_for(thesis_id: str, date_str: str) -> str:
    """Deterministic client order id — the idempotency anchor per (thesis, day)."""
    return f"wl-{thesis_id}-{date_str}"


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
    confirmation prompts (precedence / size / whatif warnings); each must be
    replied to with ``{confirmed: true}`` and a reply can itself yield another.
    Loops until an accepted-orders array, an error object, or ``max_replies``.
    """
    resp = http_post_json(port, f"/iserver/account/{account_id}/orders", body, timeout)
    for _ in range(max_replies):
        if _is_terminal_order_response(resp):
            return resp
        reply_id, message_ids = _confirmation_reply_id(resp)
        if not reply_id:
            return resp  # error envelope / unexpected shape — caller inspects
        resp = http_post_json(
            port,
            f"/iserver/reply/{reply_id}",
            {"confirmed": True, "messageIds": message_ids},
            timeout,
        )
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
        "raw": resp,
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


def live_order_refs(port: int, timeout: float = DEFAULT_TIMEOUT) -> set[str]:
    """Best-effort set of client-order refs currently live (idempotency guard).

    The Client Portal echoes the submitted ``cOID`` back as ``order_ref`` on some
    builds; we collect any such field so the daemon can avoid double-placing the
    same bracket after a crash/restart.
    """
    payload = http_get_json(port, "/iserver/account/orders", timeout)
    rows: list = []
    if isinstance(payload, dict) and isinstance(payload.get("orders"), list):
        rows = payload["orders"]
    elif isinstance(payload, list):
        rows = payload
    refs: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
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
    parser.add_argument("--target", type=float, required=True, help="take-profit target")
    parser.add_argument("--account-id", default=None)
    parser.add_argument("--coid", default=None, help="client order id (idempotency anchor)")
    parser.add_argument(
        "--live", action="store_true", help="actually POST (needs the env flag too)"
    )
    parser.add_argument("--dry-run", action="store_true", help="force preview even with --live")
    parser.add_argument("--runtime-dir", default=None)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)

    coid = args.coid or coid_for(args.ticker.lower(), "manual")
    try:
        orders = build_bracket_orders(
            args.side,
            conid=0,
            shares=args.shares,
            pivot=args.pivot,
            stop=args.stop,
            target=args.target,
            coid=coid,
        )
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
            orders = build_bracket_orders(
                args.side,
                conid=conid,
                shares=args.shares,
                pivot=args.pivot,
                stop=args.stop,
                target=args.target,
                coid=coid,
            )
        except (ConnectionError, LookupError) as exc:
            account_id = args.account_id
            print(f"(preview only — Gateway not used: {exc})", file=sys.stderr)
        preview = {
            "mode": "preview",
            "reason": reason,
            "paper": is_paper(),
            "account_id": account_id,
            "cOID": coid,
            "would_place": orders,
        }
        json.dump(preview, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    # Live path: discover, resolve, submit.
    try:
        port = connect(args.runtime_dir, args.timeout)
        conid = resolve_conid(port, args.ticker, args.timeout)
        account_id = args.account_id or resolve_account_id(port, args.timeout)
    except (ConnectionError, LookupError) as exc:
        print(f"Cannot place order: {exc}", file=sys.stderr)
        return 2
    orders = build_bracket_orders(
        args.side,
        conid=conid,
        shares=args.shares,
        pivot=args.pivot,
        stop=args.stop,
        target=args.target,
        coid=coid,
    )
    result = submit_bracket(port, account_id, orders, args.timeout)
    out = {"mode": "live", "paper": is_paper(), "account_id": account_id, "cOID": coid, **result}
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
