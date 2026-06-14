# Interactive Brokers MCP Server Setup Guide

This guide explains how to set up and configure the Interactive Brokers (IBKR) MCP
Server for use with the IB Portfolio Manager skill.

It is based on the unofficial [`interactive-brokers-mcp`](https://github.com/code-rabi/interactive-brokers-mcp)
project (npm package `interactive-brokers-mcp`).

> ⚠️ **Unofficial / alpha software.** `interactive-brokers-mcp` is not affiliated
> with or endorsed by Interactive Brokers. Trading involves substantial risk of
> loss. **Always test with a paper-trading account first.** Run the server only
> on your local machine, never expose it on a public network, and never commit
> your IBKR credentials to version control.

## What is the Interactive Brokers MCP Server?

The Interactive Brokers MCP Server is a Model Context Protocol server that gives
Claude access to your IBKR account through a standardized interface. The package
bundles **IB Gateway and a Java runtime for all platforms**, so you do not have
to install or manage the Gateway yourself. On first use it launches (or attaches
to an already-running) Gateway and proxies the IBKR Client Portal API.

This lets the IB Portfolio Manager skill fetch real-time positions, account
balances, live orders, and (via Flex Queries) historical statements directly
from your account.

## Prerequisites

### 1. Node.js 18+

```bash
node --version   # must be >= 18
```

### 2. Interactive Brokers Account

You need an IBKR account (paper or live):

- **Sign up:** https://www.interactivebrokers.com/
- **Paper Trading:** simulated money (recommended for testing)
- **Live Trading:** real money account (requires funding)

Two-factor authentication (2FA) on the account is supported; the server waits up
to 60 seconds for 2FA completion during login.

## Installation

This repository ships a **vendored, security-hardened** copy of the server under
`vendor/interactive-brokers-mcp/` (see its `VENDORING.md` and `SECURITY.md`).
Prefer it over `npx` — it is reviewable, version-pinned, and carries the security
fixes in `SECURITY.md`.

**Build it once:**

```bash
cd vendor/interactive-brokers-mcp
npm install && npm run build      # produces dist/index.js
```

The build is **not runnable until you supply the IB Gateway + JRE runtime**
(large IBKR/JRE binaries are intentionally not committed). The helper script wires
both up from a gateway you already have:

```bash
cd vendor/interactive-brokers-mcp
# symlinks runtime/<platform> -> Java 11 (Homebrew openjdk@11 by default) and
# copies your Client Portal Gateway into ib-gateway/clientportal.gw/
bin/setup-local-runtime.sh --gateway-src /path/to/your/clientportal.gw
```

It prints the gateway's `listenPort` — set `IB_GATEWAY_PORT` to it (commonly
**5002** on macOS, since AirPlay/Control Center usually holds 5000). The MCP then
launches the in-repo gateway with that JRE and connects over HTTPS on that port.
**Java 11 is required** (the gateway is built for it; Java 17+ denies its
reflective access). Full details: `vendor/interactive-brokers-mcp/VENDORING.md`.

> Upstream alternative: `command: "npx", args: ["-y", "interactive-brokers-mcp"]`
> downloads the package (with the bundled runtime) on demand, but bypasses the
> vendored security fixes. Use only if you accept that trade-off.

### Browser-based authentication (default, recommended for desktop)

On first use a browser window opens for OAuth login with your IBKR credentials.

```json
{
  "mcpServers": {
    "interactive-brokers": {
      "command": "node",
      "args": ["/path/to/claude-trading-skills/vendor/interactive-brokers-mcp/dist/index.js"],
      "env": {
        "IB_PAPER_TRADING": "true",
        "IB_READ_ONLY_MODE": "true",
        "IB_GATEWAY_PORT": "5002"
      }
    }
  }
}
```

For the **portfolio-analysis** use case, set `IB_READ_ONLY_MODE=true`. This
disables `place_order` at the server level so the skill cannot place trades —
analysis still has full read access to positions, balances, and market data.

### Headless authentication (for automated / non-interactive environments)

Provide credentials directly so no browser prompt is needed. Store them via
environment variables or a `.gitignore`d config file — never inline in a
committed file.

```json
{
  "mcpServers": {
    "interactive-brokers": {
      "command": "node",
      "args": ["/path/to/claude-trading-skills/vendor/interactive-brokers-mcp/dist/index.js"],
      "env": {
        "IB_HEADLESS_MODE": "true",
        "IB_USERNAME": "YOUR_IBKR_USERNAME",
        "IB_PASSWORD_AUTH": "YOUR_IBKR_PASSWORD",
        "IB_PAPER_TRADING": "true",
        "IB_READ_ONLY_MODE": "true",
        "IB_GATEWAY_PORT": "5002"
      }
    }
  }
}
```

> 2FA may still be required even in headless mode; the server waits up to 60
> seconds for completion.

## Configuration Variables

| Feature | Environment Variable | Command Argument |
|---------|----------------------|------------------|
| Username | `IB_USERNAME` | `--ib-username` |
| Password | `IB_PASSWORD_AUTH` | `--ib-password-auth` |
| Headless mode | `IB_HEADLESS_MODE` | `--ib-headless-mode` |
| Paper trading | `IB_PAPER_TRADING` | `--ib-paper-trading` |
| Read-only mode | `IB_READ_ONLY_MODE` | `--ib-read-only-mode` |
| Flex token | `IB_FLEX_TOKEN` | N/A |
| Auth timeout | `IB_AUTH_TIMEOUT` | `--ib-auth-timeout` |
| Auth wait seconds | `IB_AUTH_WAIT_SECONDS` | `--ib-auth-wait-seconds` |
| Force standalone gateway | `IB_FORCE_STANDALONE_GATEWAY` | N/A |
| Log directory | `IB_MCP_LOG_DIR` (default `~/.ib-mcp`); `IB_MCP_DISABLE_LOGGING=true` to disable | N/A |
| Allow JRE auto-download (musl) | `IB_ALLOW_RUNTIME_DOWNLOAD` (vendored: **off** by default — security fix #5) | N/A |
| Browser web-security off | `IB_BROWSER_DISABLE_WEB_SECURITY` (vendored: **off** by default — security fix #4) | N/A |

> The last three rows reflect the **vendored, hardened** server. See
> `vendor/interactive-brokers-mcp/SECURITY.md` for what changed and why.

Set the credentials as shell environment variables so they are never written
into a committed config file:

```bash
# macOS / Linux
export IB_USERNAME="YOUR_IBKR_USERNAME"
export IB_PASSWORD_AUTH="YOUR_IBKR_PASSWORD"
export IB_PAPER_TRADING="true"
export IB_READ_ONLY_MODE="true"
```

```powershell
# Windows (PowerShell)
$env:IB_USERNAME="YOUR_IBKR_USERNAME"
$env:IB_PASSWORD_AUTH="YOUR_IBKR_PASSWORD"
$env:IB_PAPER_TRADING="true"
$env:IB_READ_ONLY_MODE="true"
```

## Available MCP Tools

Once configured, the IB Portfolio Manager skill can use these tools.

### Trading & Account Management

| Tool | Purpose |
|------|---------|
| `mcp__interactive-brokers__get_account_info` | Account information and balances (net liquidation, cash, buying power) |
| `mcp__interactive-brokers__get_positions` | Current positions and unrealized P&L |
| `mcp__interactive-brokers__get_market_data` | Real-time market data for specified instruments |
| `mcp__interactive-brokers__get_live_orders` | All open / working orders |
| `mcp__interactive-brokers__get_order_status` | Execution status of a specific order |
| `mcp__interactive-brokers__place_order` | Place market/limit/stop orders (**disabled** when `IB_READ_ONLY_MODE=true`) |

### Flex Queries (require `IB_FLEX_TOKEN`)

| Tool | Purpose |
|------|---------|
| `mcp__interactive-brokers__get_flex_query` | Run a Flex Query (statements, realized P&L, dividends); saved for reuse |
| `mcp__interactive-brokers__list_flex_queries` | List previously used Flex Queries |
| `mcp__interactive-brokers__forget_flex_query` | Remove a saved Flex Query |

The IB Portfolio Manager skill uses the read tools above and **does not** call
`place_order`.

## Flex Query Setup (optional, for historical performance)

Interactive Brokers has no single "portfolio history" endpoint. Historical net
asset value, realized P&L, and dividend income come from **Flex Queries**:

1. Log into IBKR Account Management (Client Portal).
2. Navigate **Settings → Account Settings → Reporting → Flex Web Service**.
3. Generate or retrieve your **Flex Web Service Token**.
4. Set the `IB_FLEX_TOKEN` environment variable to that token.
5. Create custom queries under **Reports → Flex Queries** and note each Query ID.

If no Flex token is configured, the skill still works — it produces a
current-snapshot analysis and notes that time-weighted return / drawdown history
is unavailable.

## Gateway Lifecycle

On startup the MCP server probes for an existing local Gateway. If a healthy one
is found it attaches to it; otherwise it launches the bundled Java Gateway as a
detached process. Session metadata is stored under a `ib-gateway/.runtime/`
directory relative to where the server runs:

- `gateway-session.json` — PID, port, version, and log paths
- `gateway-session.lock` — prevents duplicate Gateway startups
- `gateway.stdout.log` / `gateway.stderr.log` — Gateway process output

Normal shutdown detaches but leaves the Gateway running for reuse. Set
`IB_FORCE_STANDALONE_GATEWAY=true` to skip discovery and always launch a fresh
bundled Gateway.

## Verification and Testing

### Preflight diagnostic script

```bash
python3 skills/ib-portfolio-manager/scripts/check_ib_connection.py
```

The script reports your configured mode (paper/live, headless, read-only), tries
to locate the Gateway runtime session file, and (if found) probes the Client
Portal auth-status endpoint. Pass `--runtime-dir <path>` (or set
`IB_GATEWAY_RUNTIME_DIR`) if your `ib-gateway/.runtime/` lives somewhere
non-default.

### Test via Claude

1. **Account connection:** "Can you get my Interactive Brokers account information?"
2. **Positions:** "What positions do I have in my IBKR account?"
3. **Full analysis:** "Analyze my portfolio" → the skill fetches positions, enriches them, analyzes, and writes a report to `reports/`.

## Troubleshooting

### "Interactive Brokers MCP Server not connected"

- Confirm the server is registered: `claude mcp list`
- Restart Claude to reinitialize MCP servers
- Verify Node.js 18+ is installed (`node --version`)
- Check the Gateway logs under `ib-gateway/.runtime/gateway.stderr.log`

### Gateway reachable but "not authenticated" / session expired

- IBKR Client Portal sessions time out after a period of inactivity — re-login
- Complete the browser OAuth prompt, or the 2FA challenge, then retry
- In headless mode, confirm `IB_USERNAME` / `IB_PASSWORD_AUTH` are set correctly
- Only one active IBKR session is allowed per user — logging in elsewhere (e.g.
  the IBKR mobile app or TWS) can invalidate the Gateway session

### "place_order is disabled"

- Expected when `IB_READ_ONLY_MODE=true`. This skill is analysis-only and does
  not place orders, so read-only mode is the recommended configuration.

### Paper vs live mismatch / no positions found

- Confirm `IB_PAPER_TRADING` matches the account you funded the positions in
- Verify positions exist in the IBKR Client Portal / TWS
- Two-factor or session issues can return an empty position list — re-authenticate

### Port already in use / duplicate Gateway

- A stale `gateway-session.lock` can block startup. Stop any orphaned Gateway
  process, remove the lock file under `ib-gateway/.runtime/`, and restart.
- Set `IB_FORCE_STANDALONE_GATEWAY=true` to bypass discovery of an unhealthy
  external Gateway.

## Security Best Practices

1. **Use paper trading for testing.** Never risk real money until thoroughly tested.
2. **Use read-only mode for analysis.** `IB_READ_ONLY_MODE=true` removes the order-placement surface entirely.
3. **Protect credentials.** Use environment variables or a `.gitignore`d config file; never commit `IB_USERNAME` / `IB_PASSWORD_AUTH` / `IB_FLEX_TOKEN`.
4. **Run locally only.** Do not expose the MCP server or Gateway on a public network.
5. **Rotate the Flex token** periodically and revoke it if exposed.

## Alternative: Manual Data Entry

If the MCP server is unavailable, the skill can analyze a manually exported CSV:

```csv
symbol,quantity,cost_basis,current_price
AAPL,100,150.00,175.50
MSFT,50,280.00,310.25
```

Export positions from the IBKR Client Portal / TWS, provide the CSV to Claude,
and the skill will parse it. Limitations: no real-time updates, no historical
performance, manual refresh required.

## Additional Resources

- `interactive-brokers-mcp` repository: https://github.com/code-rabi/interactive-brokers-mcp
- IBKR Client Portal API docs: https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/
- IBKR Flex Web Service: https://www.interactivebrokers.com/campus/ibkr-api-page/flex-web-service/
- Anthropic MCP docs: https://modelcontextprotocol.io/
