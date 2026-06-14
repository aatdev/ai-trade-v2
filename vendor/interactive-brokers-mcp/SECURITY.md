# Security review — vendored `interactive-brokers-mcp`

Scope: the TypeScript source under `src/` of
[`code-rabi/interactive-brokers-mcp`](https://github.com/code-rabi/interactive-brokers-mcp)
at the time of vendoring. The bundled binaries (`runtime/`, `ib-gateway/`) were
**not** committed and were not audited beyond their role in the source.

This MCP server is sensitive: it holds an IBKR session that can **read account
data and (outside read-only mode) place real orders**, and the headless path
handles IBKR **username/password**. The findings below were assessed with that in
mind, and the fixes are reflected in the committed source.

## Summary

| # | Severity | Finding | Status |
|---|----------|---------|--------|
| 1 | High | HTTP/SSE mode: wildcard CORS + bind on all interfaces, no auth | **Removed** |
| 2 | High | Secrets to on-disk log: Flex token unredacted in config dump; request headers (session Cookie) + bodies logged | **Fixed** |
| 3 | Medium | `rejectUnauthorized:false` everywhere → MITM if `IB_GATEWAY_HOST` is remote | **Fixed** (localhost-scoped) |
| 4 | Medium | Credential-entry browser launched with `--disable-web-security` | **Fixed** (off by default) |
| 5 | Medium | Auto-download of a JRE with no checksum/signature | **Fixed** (opt-in) |
| 6 | Low | Username logged; `exec` in port probe; test socket on `0.0.0.0`; detached tickler holds cookie | Partly fixed / documented |
| — | Info | No telemetry/phone-home; `xml2js` (sax) not XXE-prone; `place_order` gated by `IB_READ_ONLY_MODE`; `spawn` uses arg arrays (no shell injection); no `postinstall` | Confirmed good |

## Findings & fixes applied

### 1. HTTP/SSE mode exposed with no auth (High) — REMOVED
`src/index-http.ts` did `app.use(cors())` (any origin) and `app.listen(PORT)`
with no host (Node binds all interfaces). With `MCP_HTTP_SERVER=true`, the MCP —
able to drive a brokerage account — was reachable from the LAN and callable from
any web origin, with no authentication.
**Fix:** the skill uses only the stdio transport, so `index-http.ts`, the unused
`server.ts`, and the `express`/`cors` dependencies were deleted entirely.

### 2. Secrets written to the on-disk log (High) — FIXED
The logger writes to `~/.ib-mcp/ib-mcp.log` (override `IB_MCP_LOG_DIR`).
- `index.ts`/`server.ts` dumped the "merged config" but only redacted
  `IB_PASSWORD*` — **`IB_FLEX_TOKEN` was logged in plaintext**.
- `ib-client.ts` request interceptor logged full `headers` (the IBKR **session
  Cookie**) and `data` (request bodies, including order payloads) for every call.
**Fix:** `IB_FLEX_TOKEN` and `IB_USERNAME` are now redacted in all config logs;
the request interceptor logs **method + URL only** (no headers, no bodies).

### 3. TLS verification globally disabled (Medium) — FIXED
`rejectUnauthorized:false` appeared in `ib-client.ts` (×3), `gateway-manager.ts`,
and `tickler.ts`. This is necessary for the **localhost** Gateway's self-signed
cert, but it also disabled verification for any remote `IB_GATEWAY_HOST`,
enabling a silent MITM.
**Fix:** added `src/utils/tls-utils.ts` (`rejectUnauthorizedFor(host)`); cert
verification is now disabled **only for local hosts** and enforced otherwise.
(`gateway-manager.ts`'s health check is hard-coded to `hostname:'localhost'` and
is left as-is.)

### 4. Credential-entry browser with web security off (Medium) — FIXED
The Playwright Chromium that types the IBKR password launched with
`--disable-web-security` (plus `--ignore-certificate-errors`, `--no-sandbox`).
**Fix:** `--disable-web-security` is **off by default**; restore only via
`IB_BROWSER_DISABLE_WEB_SECURITY=true` if a specific SSO flow needs it.
`--ignore-certificate-errors` is kept (required for the self-signed **localhost**
Gateway SSO page) and documented inline.

### 5. Unverified JRE download (Medium) — FIXED
On Alpine/musl Linux the Gateway manager `fetch()`ed a JRE from
`download.bell-sw.com` and `tar`-extracted it with no checksum/signature check.
**Fix:** the download is now **opt-in** via `IB_ALLOW_RUNTIME_DOWNLOAD=true`;
otherwise it throws and tells the operator to pre-install the runtime. When
enabled it logs a warning to verify the BellSoft checksum.

### 6. Lower-severity items
- **Username logging** → now redacted (see #2).
- **`port-utils.ts`** used `exec` (shell) for the port-owner probe. The
  interpolated value is always an **integer** port (no injection vector), so the
  command is left intact; the availability probe socket was changed from
  `0.0.0.0` to `127.0.0.1`.
- **Detached "tickler"** (`ib-client.ts` → `scripts/tickler.ts`): a background
  keepalive process that outlives the MCP, holding the session cookie in its env
  and a PID file under the gateway runtime dir. Functional behaviour kept; TLS
  verification scoped (#3). Operators should be aware it self-terminates on auth
  loss but is otherwise long-lived.

## Confirmed-good (no change needed)

- **No telemetry / analytics / phone-home** in the source.
- **`xml2js`** is `sax`-based and does not resolve external entities → not
  XXE-prone; Flex XML is fetched from IBKR over HTTPS.
- **Read-only enforcement:** `place_order` and the other mutating tools are only
  registered when `IB_READ_ONLY_MODE` is unset/false (`src/tools.ts`). For
  analysis, always run with `IB_READ_ONLY_MODE=true`.
- **`spawn`** calls (gateway JRE, `tar`, tickler) use argument arrays, not a
  shell string → no shell-injection surface.
- **No `postinstall`/`preinstall`** scripts in `package.json`.

## Residual risk / operator guidance

- Run **locally only**, with **`IB_READ_ONLY_MODE=true`** for analysis.
- Keep `IB_GATEWAY_HOST` on localhost. A remote host now requires a valid TLS
  cert (no longer silently unverified).
- Store `IB_USERNAME` / `IB_PASSWORD_AUTH` / `IB_FLEX_TOKEN` in the environment or
  a gitignored file — never commit them. The Flex token grants read access to
  account statements.
- `~/.ib-mcp/ib-mcp.log` no longer receives credentials/cookies, but still records
  account activity — protect or disable it (`IB_MCP_DISABLE_LOGGING=true`).
- Supply-chain: `npm install` still pulls `playwright-core`, `axios`, `xml2js`,
  etc. Review `package-lock.json` on updates.

## Verification

`npm run build` (tsc, clean), `npm run lint` (oxlint, no new findings), and
`npm test` (vitest, 183 passed / 1 skipped) all pass on the hardened source.
