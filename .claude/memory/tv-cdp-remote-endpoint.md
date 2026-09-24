---
name: tv-cdp-remote-endpoint
description: "TradingView Desktop CDP is remote (TV_CDP_HOST, port 9222) (VPN), set via TV_CDP_HOST/TV_CDP_PORT in gitignored repo .env; not installed on the Mac"
metadata:
  node_type: memory
  type: project
  originSessionId: 57a28be4-0731-4164-a8fd-4ffac0b9fbc4
  modified: 2026-09-24T16:09:28.558Z
---

TradingView Desktop is NOT installed on the user's Mac. Since 2026-09-24 the CDP endpoint is the remote host from `TV_CDP_HOST` (port 9222) (reachable over VPN, utun). Configured via `TV_CDP_HOST=<remote-ip>` / `TV_CDP_PORT=9222` in the repo's gitignored `.env`.

Resolution order (env > nearest `.env` walking up from cwd, then module dir > localhost:9222) lives in `vendor/tradingview-mcp/src/cdp_config.js` (JS) and `scripts/lib/tv_alerts.py::cdp_endpoint` (Python). Mirrored into the jackson checkout (global `tv`), see [[tv-cli-shadowing-jackson]].

Gotchas:
- jackson's `.env` is git-TRACKED (remote aatdev/ai-trade) → never put the IP there; global `tv` run with cwd outside this repo falls back to localhost (export TV_CDP_HOST in that case).
- `tv launch` / `health.launch` still assume a local app on localhost — irrelevant for the remote setup.
- Old LaunchAgent `ssh-tunnel-9222` in `~/Library/LaunchAgents` forwards localhost:9222 → an old Linux server, where TV has been dead since 2026-07-08 (no X/Xvfb). That was the cause of "CDP connection failed"; the tunnel is now unused.

- Screenshots: `tv screenshot -r chart` captures only the main price pane (no RSI/MACD/Stoch). Remote window is 1920×898 with the alerts panel on the right → take `-r full` and crop: `sips -c 820 1360 --cropOffset 42 54`. Set `tv range --from/--to` first (weekly otherwise shows full history), wait ~5s after timeframe/symbol switch before `tv values` (earlier reads return half-loaded values). Files land in the jackson checkout's `screenshots/`.
- The user's chart has EMA63 + EMA200 (not 20/50) — don't add indicators to their layout; compute EMA20/50 from OHLCV. Restore the original symbol/TF afterwards (was AMEX:XLV 1D).

**Why:** the user redirected CDP to a new remote host instead of fixing the dead server.
**How to apply:** if TV is "unavailable", first `curl http://$TV_CDP_HOST:9222/json/version` and check VPN; don't try `tv launch` on the Mac.
