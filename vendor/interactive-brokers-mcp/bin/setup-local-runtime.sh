#!/usr/bin/env bash
#
# Set up the bring-your-own runtime for the vendored interactive-brokers-mcp.
# Both targets it creates are gitignored (ib-gateway/, runtime/) — re-run this
# after a fresh clone or when the machine's Java/gateway location changes.
#
# It does two things:
#   1) symlinks  runtime/<platform>  ->  a local Java 11 home
#      (the IBKR Client Portal Gateway is built for JRE 11; the MCP's managed
#       launch resolves the JRE at runtime/<platform>/bin/java)
#   2) optionally copies an IBKR Client Portal Gateway into
#      ib-gateway/clientportal.gw/  (the dir containing bin/run.sh, dist/, root/)
#
# Usage:
#   bin/setup-local-runtime.sh [--gateway-src DIR] [--java-home DIR]
#
#   --gateway-src DIR : path to an existing Client Portal Gateway (the folder
#                       that has bin/run.sh + dist/*.jar + root/conf.yaml).
#                       Omit to skip the gateway copy (e.g. it is already in place).
#   --java-home DIR   : a Java 11 home (must contain bin/java + lib/server).
#                       Default: $(brew --prefix openjdk@11)/libexec/openjdk.jdk/Contents/Home
#
# After running, set IB_GATEWAY_PORT in the MCP config to match the gateway's
# root/conf.yaml `listenPort` (commonly 5002 when 5000 is taken on macOS).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # vendor/interactive-brokers-mcp

# node-style platform key (darwin-arm64, linux-x64, ...) — the key getJavaPath() uses.
if command -v node >/dev/null 2>&1; then
  PLATFORM="$(node -e 'process.stdout.write(process.platform + "-" + process.arch)')"
else
  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  arch="$(uname -m)"; arch="${arch/x86_64/x64}"; arch="${arch/aarch64/arm64}"
  PLATFORM="${os}-${arch}"
fi

GATEWAY_SRC=""
JAVA_HOME_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --gateway-src) GATEWAY_SRC="${2:-}"; shift 2 ;;
    --java-home)   JAVA_HOME_ARG="${2:-}"; shift 2 ;;
    -h|--help)     sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# --- resolve a Java 11 home -------------------------------------------------
if [ -z "$JAVA_HOME_ARG" ]; then
  if command -v brew >/dev/null 2>&1 && brew --prefix openjdk@11 >/dev/null 2>&1; then
    JAVA_HOME_ARG="$(brew --prefix openjdk@11)/libexec/openjdk.jdk/Contents/Home"
  else
    echo "No --java-home given and Homebrew openjdk@11 not found." >&2
    echo "Install a JRE/JDK 11 and pass --java-home /path/to/jdk-11-home." >&2
    exit 1
  fi
fi
if [ ! -x "$JAVA_HOME_ARG/bin/java" ]; then
  echo "Not a Java home (missing bin/java): $JAVA_HOME_ARG" >&2
  exit 1
fi

mkdir -p "$HERE/runtime"
ln -sfn "$JAVA_HOME_ARG" "$HERE/runtime/$PLATFORM"
echo "✓ linked runtime/$PLATFORM -> $JAVA_HOME_ARG"
"$HERE/runtime/$PLATFORM/bin/java" -version

# --- optionally copy the gateway -------------------------------------------
if [ -n "$GATEWAY_SRC" ]; then
  if [ ! -f "$GATEWAY_SRC/bin/run.sh" ]; then
    echo "Not a Client Portal Gateway (missing bin/run.sh): $GATEWAY_SRC" >&2
    exit 1
  fi
  DEST="$HERE/ib-gateway/clientportal.gw"
  mkdir -p "$HERE/ib-gateway"
  rsync -a --exclude 'logs/' --exclude '.vertx/' "$GATEWAY_SRC"/ "$DEST"/
  mkdir -p "$DEST/logs"
  echo "✓ copied gateway -> $DEST"
  PORT="$(grep -E '^[[:space:]]*listenPort:' "$DEST/root/conf.yaml" 2>/dev/null | head -1 | grep -oE '[0-9]+' || true)"
  [ -n "$PORT" ] && echo "  gateway conf.yaml listenPort = $PORT  → set IB_GATEWAY_PORT=$PORT"
fi

echo "Done. Point the MCP at: node $HERE/dist/index.js (run 'npm run build' first if needed)."
