---
name: ib-headless-autologin
description: How to auto-login to Interactive Brokers — just call the authenticate MCP tool; headless config lives in .mcp.json
metadata: 
  node_type: memory
  type: project
  originSessionId: 22407f68-0df1-4812-a10b-ac54ecc530ef
---

To log into Interactive Brokers in this repo, just call the MCP tool `mcp__interactive-brokers__authenticate {confirm:true}` (or any IB tool like `get_account_info`). The vendored interactive-brokers MCP server runs headless auth **lazily on the first IB tool call**, not at gateway startup — so a 401 before any tool call is normal, not a misconfig.

**How the autologin is wired (non-obvious):**
- `IB_HEADLESS_MODE=true` is set in **`.mcp.json`** (the `interactive-brokers` `env` block), NOT in `.env`. A grep of `.env` won't show it. The `.mcp.json` env wins over `.env` because it's set before the process starts.
- Credentials come from `.env`: `IB_USERNAME` + `IB_PASSWORD`. Code reads `IB_PASSWORD_AUTH`, but `config.ts:30` falls back `IB_PASSWORD_AUTH || IB_PASSWORD`, so the plain `IB_PASSWORD` name works.
- Login may require a **mobile/SSO approval** tap in the IBKR app (waits up to 60s). No browser needed — do NOT open `https://localhost:5002` for browser login when headless is configured.
- `check_ib_connection.py` printing "browser auth" is **misleading**: it reads its own process env (no `IB_HEADLESS_MODE`), not the MCP server's. Ignore that label; trust the live `auth/status` probe instead.

**Verify after login:** `curl -sk -X POST -H "User-Agent: Mozilla/5.0" --data "" https://localhost:5002/v1/api/iserver/auth/status` → expect `{"authenticated":true,"connected":true,...}`.

Account is paper (`alexeytrusoff-paper`), port 5002. Note current config is `IB_READ_ONLY_MODE=false` + `IB_ALLOW_ORDER_PLACEMENT=true` — order placement is NOT blocked at the server; switch to read-only for analysis-only sessions. See [[ib-gateway-probe-gotchas]] for the probe mechanics (User-Agent / session-file path).
