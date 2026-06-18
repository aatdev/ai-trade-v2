import { config as dotenvConfig } from "dotenv";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

// Load environment variables. dotenv's default reads `.env` from the process
// cwd, but Claude Code may launch this server with an arbitrary cwd, so also
// load the repo-root `.env` — this vendored MCP lives at
// <repo>/vendor/interactive-brokers-mcp/dist, hence ../../.. up from here.
// The cwd load runs first and wins on conflicts (and the `.mcp.json` `env`
// block, set before the process starts, wins over both since dotenv never
// overrides already-set vars); the repo-root load only fills vars neither set
// — e.g. IB_USERNAME / IB_PASSWORD for headless auth.
dotenvConfig();
try {
  const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
  dotenvConfig({ path: resolve(repoRoot, ".env") });
} catch {
  // import.meta.url unavailable (non-ESM context) — the cwd load above suffices.
}

export const config = {
  IB_GATEWAY_HOST: process.env.IB_GATEWAY_HOST || "localhost",
  IB_GATEWAY_PORT: parseInt(process.env.IB_GATEWAY_PORT || "5000"),
  IB_FORCE_STANDALONE_GATEWAY: process.env.IB_FORCE_STANDALONE_GATEWAY === "true",
  IB_ACCOUNT: process.env.IB_ACCOUNT || "",
  IB_PASSWORD: process.env.IB_PASSWORD || "",

  // Headless authentication configuration
  IB_USERNAME: process.env.IB_USERNAME || "",
  IB_PASSWORD_AUTH: process.env.IB_PASSWORD_AUTH || process.env.IB_PASSWORD || "",
  IB_AUTH_TIMEOUT: parseInt(process.env.IB_AUTH_TIMEOUT || "300000"),
  IB_AUTH_WAIT_SECONDS: parseInt(process.env.IB_AUTH_WAIT_SECONDS || "60"),
  IB_AUTH_POLL_SECONDS: parseInt(process.env.IB_AUTH_POLL_SECONDS || "5"),
  IB_HEADLESS_MODE: process.env.IB_HEADLESS_MODE === "true",

  // Paper trading configuration
  IB_PAPER_TRADING: process.env.IB_PAPER_TRADING === "true",

  // Read-only mode configuration
  IB_READ_ONLY_MODE: process.env.IB_READ_ONLY_MODE === "true",

  // Flex Query configuration
  IB_FLEX_TOKEN: process.env.IB_FLEX_TOKEN || "",

};
