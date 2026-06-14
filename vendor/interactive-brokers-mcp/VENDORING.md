# Vendored `interactive-brokers-mcp`

This directory is a **vendored, security-hardened fork** of
[`code-rabi/interactive-brokers-mcp`](https://github.com/code-rabi/interactive-brokers-mcp)
(MIT, © 2024 Interactive Brokers MCP Server / Nitay Rabinovich — see `LICENSE`).

It backs the **`ib-portfolio-manager`** skill. Vendoring keeps the implementation
in-repo (supply-chain transparency, reviewable diffs, no `npx` fetch of arbitrary
versions) and lets us apply the security fixes documented in `SECURITY.md`.

## What is and isn't committed

**Committed:** the TypeScript source (`src/`), tests (`test/`), build/runtime
helper scripts (`scripts/`, `install/`), and manifests (`package.json`,
`package-lock.json`, `tsconfig.json`, `vitest.config.ts`, `.oxlintrc.json`,
`LICENSE`, `README.md`).

**NOT committed** (see `.gitignore`):

| Path | Why excluded |
|------|--------------|
| `node_modules/` | Reproducible via `npm install` (mirrors `vendor/tradingview-mcp`) |
| `dist/` | Build output of `npm run build` |
| `runtime/` | ~337 MB of per-platform JREs — third-party redistributable, over the repo's 500 KB file limit |
| `ib-gateway/` | ~12 MB IBKR Client Portal Gateway — IBKR redistributable |
| `IB-MCP.gif` / `.mp4` | 44 MB upstream demo media, not needed |

The upstream HTTP/SSE entrypoint (`src/index-http.ts`) and the `express`/`cors`
dependencies were **removed** — the skill only uses the stdio transport, and the
HTTP server was an unauthenticated network-exposed surface (see `SECURITY.md` #1).

## Differences from upstream

- Removed `src/index-http.ts` (+ `express`, `cors`, `@types/*`, `start:http`/`dev:http`
  scripts) and the now-unused `src/server.ts`.
- Removed `semantic-release` / CI / Docker / smithery release tooling.
- `private: true` in `package.json` (never publish this fork).
- Security hardening — see `SECURITY.md` (§ "Fixes applied").

## Build & run (bring-your-own runtime)

The committed source is **not runnable on its own** — it needs the IB Gateway
distribution and a JRE, which are intentionally not committed.

```bash
cd vendor/interactive-brokers-mcp
npm install        # installs node deps (regenerates node_modules/)
npm run build      # tsc -> dist/index.js
npm test           # vitest (optional, 183 tests)
```

Provide the runtime once (gitignored, stays local). The Gateway + JRE live
relative to this package, as `ib-gateway/clientportal.gw/` (the folder with
`bin/run.sh` + `dist/*.jar` + `root/conf.yaml`) and `runtime/<platform>/`
(e.g. `runtime/darwin-arm64/`).

**Easiest — the helper script** (symlinks the JRE and copies a gateway you
already have):

```bash
# JRE 11 is required (the Client Portal Gateway is built for Java 11).
# Default --java-home is Homebrew openjdk@11; override with --java-home /path/to/jdk11.
bin/setup-local-runtime.sh --gateway-src /path/to/your/clientportal.gw
```

It symlinks `runtime/<platform>` → your Java 11 home and copies the gateway into
`ib-gateway/clientportal.gw/`, then prints the gateway's `listenPort` so you know
what to set `IB_GATEWAY_PORT` to.

**Manual equivalent:**

1. Symlink (or copy) a **Java 11** home to `runtime/<platform>` so
   `runtime/<platform>/bin/java` exists (e.g. `ln -sfn "$(brew --prefix
   openjdk@11)/libexec/openjdk.jdk/Contents/Home" runtime/darwin-arm64`).
2. Put a Client Portal Gateway at `ib-gateway/clientportal.gw/` (copy from an
   existing install, or `npm pack interactive-brokers-mcp` / `npx -y
   interactive-brokers-mcp` once and copy its `ib-gateway/`).

> **Port:** if the gateway's `root/conf.yaml` `listenPort` is not 5000 (on macOS
> port 5000 is usually taken by AirPlay/Control Center, so installs often use
> **5002**), set `IB_GATEWAY_PORT` to that value so the managed launch and the
> client agree. The MCP launches the gateway with the JRE above and connects over
> HTTPS on that port.

> JRE version: **Java 11** is what works. On Java 17+ the gateway's reflective
> access is denied; on Java 11 it is only a warning. `IB_ALLOW_RUNTIME_DOWNLOAD=true`
> re-enables the upstream auto-download of a public musl JRE (Alpine/musl Linux
> only) — **off by default**, not checksum-verified (see `SECURITY.md` #5).

## Wiring into Claude

Point the MCP config at the built entrypoint (run read-only for analysis):

```json
{
  "mcpServers": {
    "interactive-brokers": {
      "command": "node",
      "args": ["/path/to/claude-trading-skills/vendor/interactive-brokers-mcp/dist/index.js"],
      "env": { "IB_PAPER_TRADING": "true", "IB_READ_ONLY_MODE": "true", "IB_GATEWAY_PORT": "5002" }
    }
  }
}
```

## Re-syncing with upstream

When pulling a newer upstream version, re-apply the deltas above. Diff
`SECURITY.md` § "Fixes applied" against the new source so no hardening is lost,
then re-run `npm run build && npm test`.
