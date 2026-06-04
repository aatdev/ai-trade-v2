---
name: tv-cli-shadowing-jackson
description: "Global `tv` CLI on PATH points to tradingview-mcp-jackson checkout and shadows the vendored copy — vendor/ edits must be mirrored there"
metadata: 
  node_type: memory
  type: project
  originSessionId: b5c8f593-bf38-4249-ad6f-ed7081d20e4e
---

`_resolve_cli()` in `scripts/lib/tv_client_base.py` prefers the global `tv` on PATH over the vendored copy. On this machine `/opt/homebrew/bin/tv` is an npm link to `/Users/alex/Projects/Repos/tradingview-mcp-jackson/src/cli/index.js`, NOT to `vendor/tradingview-mcp`. So any change to `vendor/tradingview-mcp/src/**` (e.g. scanner field lists in `core/fundamentals.js`) silently has no effect at runtime until the same edit is applied to the jackson checkout (done 2026-06-04 for dividend fields).

**Why:** debugging "scanner field returns null" cost a full S&P 500 run before the shadowing was spotted.

**How to apply:** when editing vendor/tradingview-mcp, either mirror the change into tradingview-mcp-jackson, or run with `TV_CLI=<repo>/vendor/tradingview-mcp/src/cli/index.js`. Also note: TV scanner dividend-history field is `dps_common_stock_prim_issue_fy_h` ("prim", not "primary"). Related: [[vendored-tv-data-layer]].
