#!/usr/bin/env bash
# Sync this repo to /opt/trade on the trading server, and pull generated
# artifacts back.
#
#   push  (local → server):  mirror the repo into /opt/trade with --delete,
#                            skipping caches/venv/logs AND the server-owned
#                            data dirs (trading-data, reports) so a push never
#                            clobbers what the server generates.
#   pull  (server → local):  copy ONLY trading-data/ and reports/ back down
#                            (additive — never deletes local files).
#   both  (default):         push then pull.
#
# Usage:
#   scripts/sync-trade.sh [push|pull|both] [-n|--dry-run]
#
#   scripts/sync-trade.sh -n            # dry-run both directions (preview)
#   scripts/sync-trade.sh push          # deploy code only
#   scripts/sync-trade.sh pull          # fetch trading-data + reports only
#
# ALWAYS dry-run first if unsure — `push` deletes remote files that aren't
# present locally (except the protected/excluded paths below).
#
# Server + paths are overridable via env: TRADE_HOST, TRADE_USER, TRADE_SSH_KEY,
# TRADE_REMOTE_DIR, TRADE_LOCAL_DIR.
set -euo pipefail

# --- Config (env-overridable) ------------------------------------------------
REMOTE_HOST="${TRADE_HOST:-179.237.81.138}"
REMOTE_USER="${TRADE_USER:-ubuntu}"
SSH_KEY="${TRADE_SSH_KEY:-/Users/alex/Etc/ingomaniak_ssh.key}"
REMOTE_DIR="${TRADE_REMOTE_DIR:-/opt/trade}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_DIR="${TRADE_LOCAL_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"

# Never pushed (secrets, caches, virtualenvs, logs, VCS). Unanchored → matched
# at any depth (e.g. node_modules under ui/, any nested __pycache__-style cruft).
EXCLUDES=(
  ".env"          # secrets — keep local, never ship to the server
  ".git/"
  "tmp/"
  "logs/"
  "venv/"
  ".venv/"
  "node_modules/"
)

# Server-authoritative: generated ON the box and pulled back to local. Excluded
# from push (so a --delete push can't touch them) and are the ONLY things pull
# fetches. Anchored with a leading slash → only the repo-root dirs, not any
# same-named subdir. Keep this the single source of truth for both directions.
PULL_DIRS=(trading-data reports)

# --- Args --------------------------------------------------------------------
MODE=both
DRYRUN=""
while [ $# -gt 0 ]; do
  case "$1" in
    push|pull|both) MODE="$1" ;;
    -n|--dry-run)   DRYRUN="--dry-run" ;;
    -h|--help)      awk 'NR==1{next} /^#/{sub(/^# ?/,"");print;next} {exit}' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "sync-trade: unknown argument '$1'" >&2; exit 2 ;;
  esac
  shift
done

log() { printf '\n\033[1;36m[sync-trade] %s\033[0m\n' "$*"; }

SSH_OPTS=(-o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new -i "$SSH_KEY")
RSH="ssh -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new -i $SSH_KEY"
REMOTE="$REMOTE_USER@$REMOTE_HOST"

remote_ssh() { ssh "${SSH_OPTS[@]}" "$REMOTE" "$@"; }

# --- Ensure the remote dir exists and is writable by us ----------------------
# /opt is root-owned; create /opt/trade once and hand it to $REMOTE_USER so all
# rsync (both directions) runs without sudo. No-op once it's already writable.
ensure_remote_dir() {
  if [ -n "$DRYRUN" ]; then
    remote_ssh "[ -w '$REMOTE_DIR' ] && echo '[dry-run] $REMOTE_DIR already writable' || echo '[dry-run] would create $REMOTE_DIR (sudo mkdir + chown $REMOTE_USER)'"
    return 0
  fi
  remote_ssh "[ -w '$REMOTE_DIR' ] || { sudo mkdir -p '$REMOTE_DIR' && sudo chown '$REMOTE_USER':'$REMOTE_USER' '$REMOTE_DIR'; }"
}

do_push() {
  ensure_remote_dir
  local args=(-az --delete --human-readable --info=progress2 --partial)
  [ -n "$DRYRUN" ] && args+=("$DRYRUN")
  local e
  for e in "${EXCLUDES[@]}"; do args+=(--exclude "$e"); done
  for e in "${PULL_DIRS[@]}"; do args+=(--exclude "/$e/"); done
  log "PUSH${DRYRUN:+ (dry-run)}  $LOCAL_DIR/  →  $REMOTE:$REMOTE_DIR/"
  rsync "${args[@]}" -e "$RSH" "$LOCAL_DIR/" "$REMOTE:$REMOTE_DIR/"
}

do_pull() {
  local d
  for d in "${PULL_DIRS[@]}"; do
    if ! remote_ssh "test -d '$REMOTE_DIR/$d'"; then
      log "PULL${DRYRUN:+ (dry-run)}  skip: $REMOTE_DIR/$d not on server yet"
      continue
    fi
    local args=(-az --human-readable --info=progress2 --partial --mkpath)
    [ -n "$DRYRUN" ] && args+=("$DRYRUN")
    log "PULL${DRYRUN:+ (dry-run)}  $REMOTE:$REMOTE_DIR/$d/  →  $LOCAL_DIR/$d/"
    rsync "${args[@]}" -e "$RSH" "$REMOTE:$REMOTE_DIR/$d/" "$LOCAL_DIR/$d/"
  done
}

case "$MODE" in
  push) do_push ;;
  pull) do_pull ;;
  both) do_push; do_pull ;;
esac

log "done (${MODE}${DRYRUN:+, dry-run})"
