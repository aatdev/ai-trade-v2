#!/bin/sh
# Alpine-only: install minimal system deps + chromium, prune assets, and report sizes
set -e

echo "🔧 Installing minimal Chromium runtime (Alpine)"

# --- Config ---
CHROMIUM_BIN="/usr/bin/chromium-browser"
CHROMIUM_DIR="/usr/lib/chromium"

# --- Guard: Alpine only ---
if [ ! -f /etc/alpine-release ]; then
  echo "❌ This script targets Alpine Linux only."
  exit 1
fi

# --- Install system deps + chromium ---
echo "📦 apk add chromium + minimal deps"
apk add --no-cache \
  chromium \
  nss \
  ca-certificates \
  freetype \
  harfbuzz \
  ttf-liberation

update-ca-certificates || true

# --- Size snapshot (pre-prune) ---
echo "📏 Sizes before pruning:"
if [ -x "$CHROMIUM_BIN" ]; then
  echo "  - $(ls -lh "$CHROMIUM_BIN" | awk '{print $5"  "$9}')"
fi
if [ -d "$CHROMIUM_DIR" ]; then
  du -sh "$CHROMIUM_DIR" 2>/dev/null | sed 's/^/  - /'
fi

# --- Prune Chromium payload ---
if [ -d "$CHROMIUM_DIR" ]; then
  echo "🧹 Pruning Chromium assets in $CHROMIUM_DIR"

  # 1) Locales — keep only en-US if available
  if [ -d "$CHROMIUM_DIR/locales" ]; then
    if [ -f "$CHROMIUM_DIR/locales/en-US.pak" ]; then
      mv "$CHROMIUM_DIR/locales/en-US.pak" /tmp/keep-locale.pak
      rm -rf "$CHROMIUM_DIR/locales"/*
      mkdir -p "$CHROMIUM_DIR/locales"
      mv /tmp/keep-locale.pak "$CHROMIUM_DIR/locales/en-US.pak"
      echo "   • kept locale: en-US"
    else
      echo "   • locale en-US not found, skipping locale prune"
    fi
  fi

  # 2) Remove heavy, non-essential directories for headless auth
  rm -rf \
    "$CHROMIUM_DIR/swiftshader" \
    "$CHROMIUM_DIR/extensions" \
    "$CHROMIUM_DIR/MEIPreload" 2>/dev/null || true

  # 3) Drop hi-DPI resource packs; keep defaults
  if [ -d "$CHROMIUM_DIR/resources" ]; then
    find "$CHROMIUM_DIR/resources" -type f -name "*-200-percent.pak" -delete || true
    find "$CHROMIUM_DIR/resources" -type f -name "*-300-percent.pak" -delete || true
  fi

  # 4) Clean any leftover debug/tests if present (defensive)
  find "$CHROMIUM_DIR" -type d -name "test*" -prune -exec rm -rf {} + 2>/dev/null || true
fi

# --- Size snapshot (post-prune) ---
echo "📏 Sizes after pruning:"
if [ -x "$CHROMIUM_BIN" ]; then
  echo "  - $(ls -lh "$CHROMIUM_BIN" | awk '{print $5"  "$9}')"
fi
if [ -d "$CHROMIUM_DIR" ]; then
  du -sh "$CHROMIUM_DIR" 2>/dev/null | sed 's/^/  - /'
fi

# --- Package-level info (useful when debugging layer size) ---
echo "ℹ️ apk package sizes:"
apk info -s chromium 2>/dev/null | sed 's/^/  - /' || true
apk info -s nss 2>/dev/null | sed 's/^/  - /' || true
apk info -s freetype 2>/dev/null | sed 's/^/  - /' || true
apk info -s harfbuzz 2>/dev/null | sed 's/^/  - /' || true
apk info -s ttf-liberation 2>/dev/null | sed 's/^/  - /' || true

echo "✅ Done. Chromium at: $CHROMIUM_BIN"
echo "   Suggested flags: --headless=new --no-sandbox --disable-dev-shm-usage"
