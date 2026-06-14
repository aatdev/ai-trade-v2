#!/usr/bin/env python3
"""Preflight diagnostic for the Interactive Brokers MCP Server connection.

Unlike a simple REST broker, the Interactive Brokers MCP Server
(``interactive-brokers-mcp``) launches a bundled IB Gateway and talks to the
IBKR Client Portal API over a local HTTPS port. There is therefore no single
"API key" to validate. This script instead:

1. Reports the configured mode (paper/live, headless, read-only) from env vars.
2. Validates that headless mode has credentials.
3. Locates the Gateway runtime session file (``gateway-session.json``) and reads
   the port the Gateway is listening on.
4. Best-effort probes the Client Portal auth-status endpoint to confirm the
   session is authenticated.

Usage:
    python3 check_ib_connection.py [--runtime-dir PATH]

Environment Variables:
    IB_PAPER_TRADING        'true' for paper (default), 'false' for live
    IB_HEADLESS_MODE        'true' to use IB_USERNAME / IB_PASSWORD_AUTH
    IB_READ_ONLY_MODE       'true' disables place_order (recommended for analysis)
    IB_USERNAME             IBKR username (headless mode)
    IB_PASSWORD_AUTH        IBKR password (headless mode)
    IB_FLEX_TOKEN           Flex Web Service token (optional, historical data)
    IB_GATEWAY_RUNTIME_DIR  Override the ib-gateway/.runtime directory location
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SESSION_FILENAME = "gateway-session.json"
RUNTIME_SUBPATH = Path("ib-gateway") / ".runtime"

TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})


def bool_env(name: str, default: bool = False) -> bool:
    """Parse a boolean-ish environment variable."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in TRUE_TOKENS


def load_config(env: dict[str, str] | None = None) -> dict[str, object]:
    """Read IB MCP configuration from the environment into a plain dict."""
    if env is not None:
        # Allow tests to inject an isolated environment.
        original = dict(os.environ)
        os.environ.clear()
        os.environ.update(env)
        try:
            return load_config(None)
        finally:
            os.environ.clear()
            os.environ.update(original)

    return {
        "paper": bool_env("IB_PAPER_TRADING", default=True),
        "headless": bool_env("IB_HEADLESS_MODE", default=False),
        "read_only": bool_env("IB_READ_ONLY_MODE", default=False),
        "username": os.environ.get("IB_USERNAME", ""),
        "has_password": bool(os.environ.get("IB_PASSWORD_AUTH")),
        "has_flex_token": bool(os.environ.get("IB_FLEX_TOKEN")),
        "runtime_dir": os.environ.get("IB_GATEWAY_RUNTIME_DIR", ""),
    }


def describe_mode(config: dict[str, object]) -> str:
    """One-line human description of the configured mode."""
    parts = [
        "PAPER TRADING" if config["paper"] else "LIVE TRADING",
        "headless" if config["headless"] else "browser auth",
        "read-only" if config["read_only"] else "trading enabled",
    ]
    if config["has_flex_token"]:
        parts.append("Flex token set")
    return ", ".join(parts)


def candidate_runtime_dirs(explicit: str | None = None) -> list[Path]:
    """Ordered list of directories to search for the Gateway runtime files.

    Priority: explicit CLI/env override, the current working directory, and the
    user's home directory. Each is joined with ``ib-gateway/.runtime``.
    """
    roots: list[Path] = []
    if explicit:
        roots.append(Path(explicit).expanduser())
    env_dir = os.environ.get("IB_GATEWAY_RUNTIME_DIR")
    if env_dir:
        roots.append(Path(env_dir).expanduser())
    roots.append(Path.cwd() / RUNTIME_SUBPATH)
    roots.append(Path.home() / RUNTIME_SUBPATH)

    # De-duplicate while preserving order.
    seen: set[Path] = set()
    result: list[Path] = []
    for r in roots:
        if r not in seen:
            seen.add(r)
            result.append(r)
    return result


def find_session_file(dirs: list[Path]) -> Path | None:
    """Return the first existing gateway-session.json among ``dirs``."""
    for d in dirs:
        candidate = d / SESSION_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_session(path: Path) -> dict:
    """Load and parse the gateway session JSON. Raises on malformed JSON."""
    return json.loads(path.read_text(encoding="utf-8"))


def auth_status_url(port: int) -> str:
    """Client Portal auth-status endpoint for a given Gateway port."""
    return f"https://localhost:{port}/v1/api/iserver/auth/status"


def probe_auth(port: int, timeout: float = 5.0) -> tuple[bool, str]:
    """Best-effort probe of the Client Portal auth-status endpoint.

    Returns (authenticated, detail). The Gateway uses a self-signed certificate
    on localhost, so verification is intentionally disabled. Network/SSL/parse
    errors are swallowed into a (False, reason) result so the caller can degrade
    gracefully when no Gateway is running.
    """
    url = auth_status_url(port)
    try:
        import requests  # type: ignore
    except ImportError:
        return _probe_auth_urllib(url, timeout)

    try:
        import urllib3  # type: ignore

        urllib3.disable_warnings()
    except Exception:  # pragma: no cover - cosmetic only
        pass

    try:
        resp = requests.post(url, verify=False, timeout=timeout)  # noqa: S501
        data = resp.json()
        authenticated = bool(data.get("authenticated"))
        return authenticated, json.dumps(data)
    except Exception as exc:  # noqa: BLE001
        return False, f"probe failed: {exc}"


def _probe_auth_urllib(url: str, timeout: float) -> tuple[bool, str]:
    """Fallback probe using only the standard library (no requests installed)."""
    import ssl
    import urllib.request

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return bool(data.get("authenticated")), json.dumps(data)
    except Exception as exc:  # noqa: BLE001
        return False, f"probe failed: {exc}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose the Interactive Brokers MCP Server connection."
    )
    parser.add_argument(
        "--runtime-dir",
        default=None,
        help="Override the ib-gateway/.runtime directory to search for the session file.",
    )
    args = parser.parse_args(argv)

    print("Interactive Brokers MCP Connection Check")
    print("=" * 60)

    config = load_config()
    print(f"\nMode: {describe_mode(config)}")

    # 1. Config validation -----------------------------------------------------
    if config["headless"] and not (config["username"] and config["has_password"]):
        print("\n✗ Headless mode is enabled but credentials are incomplete.")
        print("  Set IB_USERNAME and IB_PASSWORD_AUTH, or disable IB_HEADLESS_MODE")
        print("  to use browser-based authentication.")
        return 1

    if not config["read_only"]:
        print(
            "\n⚠ IB_READ_ONLY_MODE is not enabled. For portfolio *analysis*, "
            "set\n  IB_READ_ONLY_MODE=true so place_order is disabled at the server."
        )

    if not config["has_flex_token"]:
        print(
            "\nℹ IB_FLEX_TOKEN is not set. Current-snapshot analysis will work, "
            "but\n  historical performance (time-weighted return, drawdown) needs Flex."
        )

    # 2. Locate the Gateway runtime session ------------------------------------
    print("\n" + "=" * 60)
    print("Locating IB Gateway runtime session")
    print("=" * 60)

    dirs = candidate_runtime_dirs(args.runtime_dir)
    session_path = find_session_file(dirs)
    if session_path is None:
        print("✗ No gateway-session.json found. Searched:")
        for d in dirs:
            print(f"  - {d}")
        print("\nThe MCP server creates this file when IB Gateway starts.")
        print("Start a Claude session with the interactive-brokers MCP configured,")
        print("or pass --runtime-dir / set IB_GATEWAY_RUNTIME_DIR to its location.")
        return 1

    print(f"✓ Found session file: {session_path}")
    try:
        session = load_session(session_path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"✗ Could not parse session file: {exc}")
        return 1

    port = session.get("port")
    pid = session.get("pid")
    version = session.get("version")
    print(f"  PID: {pid}  Port: {port}  Version: {version}")

    if not isinstance(port, int):
        print("✗ Session file has no usable 'port'; cannot probe the Gateway.")
        return 1

    # 3. Probe authentication --------------------------------------------------
    print("\n" + "=" * 60)
    print("Probing Client Portal auth status")
    print("=" * 60)

    authenticated, detail = probe_auth(port)
    if authenticated:
        print(f"✓ Gateway authenticated at {auth_status_url(port)}")
        print("\nYour Interactive Brokers MCP connection looks ready.")
        print('Next: ask Claude "Analyze my portfolio".')
        return 0

    print(f"✗ Gateway not authenticated ({detail}).")
    print("\nThe Gateway is running but the IBKR session is not authenticated.")
    print("Complete the browser login / 2FA prompt (or re-run with headless")
    print("credentials), then try again. Note: only one IBKR session is allowed")
    print("per user, so logging in via TWS or the mobile app can invalidate it.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
