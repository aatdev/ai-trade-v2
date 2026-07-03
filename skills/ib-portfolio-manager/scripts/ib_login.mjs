#!/usr/bin/env node
/**
 * Scripted Interactive Brokers login.
 *
 * There is no standalone IB "login" CLI: in this repo logging in means calling
 * the vendored interactive-brokers MCP server's `authenticate` tool. That tool
 * boots the bundled IB Gateway and (in headless mode) drives the SSO login with
 * Playwright using IB_USERNAME / IB_PASSWORD(_AUTH), then waits for mobile/2FA
 * approval. This script is a tiny MCP stdio client that spawns that exact server
 * (mirroring the `.mcp.json` `interactive-brokers` entry) and calls the tool —
 * i.e. it does programmatically what Claude Code does when you type "ib login".
 *
 * Credentials are NOT read here: the server loads the repo-root `.env` itself
 * (see vendor/interactive-brokers-mcp/src/config.ts). This script only forwards
 * the non-secret mode flags (IB_HEADLESS_MODE, IB_GATEWAY_PORT, ...) taken from
 * `.mcp.json` so the spawned server matches the interactive session.
 *
 * Usage:
 *   node skills/ib-portfolio-manager/scripts/ib_login.mjs
 *   IB_GATEWAY_PORT=5002 IB_HEADLESS_MODE=true node .../ib_login.mjs
 *
 * Exit codes: 0 = authenticated (SUCCESS), 2 = waiting for user 2FA/mobile
 * approval, 1 = failure/error.
 */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
// scripts -> ib-portfolio-manager -> skills -> repo root
const repoRoot = resolve(here, "..", "..", "..");
const vendorRoot = resolve(repoRoot, "vendor", "interactive-brokers-mcp");
const sdkEsm = resolve(vendorRoot, "node_modules", "@modelcontextprotocol", "sdk", "dist", "esm");

// Import the vendored MCP SDK by file path so we don't depend on this script's
// own node_modules resolution (the SDK only lives under the vendored package).
const { Client } = await import(pathToFileURL(resolve(sdkEsm, "client", "index.js")).href);
const { StdioClientTransport } = await import(pathToFileURL(resolve(sdkEsm, "client", "stdio.js")).href);

/** Read the interactive-brokers server entry from .mcp.json (command/args/env). */
function loadServerConfig() {
  const fallback = {
    command: "node",
    args: [resolve(vendorRoot, "dist", "index.js")],
    env: { IB_HEADLESS_MODE: "true", IB_GATEWAY_PORT: "5002" },
  };
  try {
    const mcp = JSON.parse(readFileSync(resolve(repoRoot, ".mcp.json"), "utf8"));
    const srv = mcp?.mcpServers?.["interactive-brokers"];
    if (!srv?.command || !Array.isArray(srv.args)) return fallback;
    return { command: srv.command, args: srv.args, env: srv.env ?? {} };
  } catch {
    return fallback;
  }
}

async function main() {
  const srv = loadServerConfig();

  // The server loads repo-root .env for credentials; we forward process.env plus
  // the .mcp.json mode flags (and allow shell overrides to win).
  const env = { ...process.env, ...srv.env };
  for (const [k, v] of Object.entries(process.env)) {
    if (v !== undefined && k.startsWith("IB_")) env[k] = v; // explicit shell IB_* override .mcp.json
  }

  const transport = new StdioClientTransport({
    command: srv.command,
    args: srv.args,
    env,
    cwd: repoRoot,
    stderr: "inherit", // surface gateway/Playwright/auth logs live
  });

  const client = new Client({ name: "ib-login-script", version: "1.0.0" }, { capabilities: {} });

  console.error("→ Spawning interactive-brokers MCP server and calling authenticate …");
  await client.connect(transport);

  let result;
  try {
    result = await client.callTool({ name: "authenticate", arguments: { confirm: true } });
  } finally {
    await client.close().catch(() => {});
  }

  const text = result?.content?.find((c) => c.type === "text")?.text ?? "";
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    parsed = { raw: text };
  }

  console.log(JSON.stringify(parsed, null, 2));

  if (parsed.status === "SUCCESS" || parsed.success === true) return 0;
  if (parsed.status === "WAITING_FOR_USER_2FA" || parsed.waitingFor2FA) {
    console.error("⏳ Waiting for mobile/2FA approval — approve the push on your IBKR app.");
    return 2;
  }
  return 1;
}

main()
  .then((code) => process.exit(code))
  .catch((err) => {
    console.error("✗ ib_login failed:", err?.stack || err);
    process.exit(1);
  });
