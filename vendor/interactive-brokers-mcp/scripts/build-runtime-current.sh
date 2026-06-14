#!/bin/bash

set -e

echo "🔧 Building custom Java runtime for current platform..."

# Essential modules for IB Gateway (based on Vert.x, Netty and networking requirements)
# Including jdk.unsupported for sun.misc.Unsafe and other internal APIs
MODULES="java.base,java.logging,java.net.http,java.desktop,java.management,java.naming,java.security.jgss,java.security.sasl,java.sql,java.xml,java.datatransfer,java.prefs,java.transaction.xa,jdk.crypto.ec,jdk.crypto.cryptoki,jdk.zipfs,jdk.unsupported"

# Detect current platform
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)

if [[ "$ARCH" == "arm64" ]] || [[ "$ARCH" == "aarch64" ]]; then
    PLATFORM_ARCH="arm64"
elif [[ "$ARCH" == "x86_64" ]]; then
    PLATFORM_ARCH="x64"
fi

if [[ "$OS" == "darwin" ]]; then
    PLATFORM="darwin-$PLATFORM_ARCH"
    if [[ "$PLATFORM_ARCH" == "arm64" ]]; then
        JDK_URL="https://github.com/adoptium/temurin11-binaries/releases/download/jdk-11.0.22%2B7/OpenJDK11U-jdk_aarch64_mac_hotspot_11.0.22_7.tar.gz"
        JLINK_PATH="jdk-11.0.22+7/Contents/Home/bin/jlink"
    else
        JDK_URL="https://github.com/adoptium/temurin11-binaries/releases/download/jdk-11.0.22%2B7/OpenJDK11U-jdk_x64_mac_hotspot_11.0.22_7.tar.gz"
        JLINK_PATH="jdk-11.0.22+7/Contents/Home/bin/jlink"
    fi
elif [[ "$OS" == "linux" ]]; then
    PLATFORM="linux-x64"
    JDK_URL="https://github.com/adoptium/temurin11-binaries/releases/download/jdk-11.0.22%2B7/OpenJDK11U-jdk_x64_linux_hotspot_11.0.22_7.tar.gz"
    JLINK_PATH="jdk-11.0.22+7/bin/jlink"
else
    echo "❌ Unsupported platform: $OS"
    exit 1
fi

echo "🖥️  Platform: $PLATFORM"

# Create temp directory
TEMP_DIR="./temp-runtime-build"
mkdir -p "$TEMP_DIR"

# Download JDK
ARCHIVE_NAME=$(basename "$JDK_URL" | cut -d'%' -f1)
ARCHIVE_PATH="$TEMP_DIR/$ARCHIVE_NAME"

echo "⬇️  Downloading JDK..."
curl -L -o "$ARCHIVE_PATH" "$JDK_URL"

# Extract JDK
echo "📦 Extracting JDK..."
tar -xf "$ARCHIVE_PATH" -C "$TEMP_DIR"

# Build custom runtime
JLINK_FULL_PATH="$TEMP_DIR/$JLINK_PATH"
RUNTIME_OUTPUT="./runtime/$PLATFORM"

echo "🔗 Running jlink..."
mkdir -p "./runtime"

"$JLINK_FULL_PATH" \
    --add-modules "$MODULES" \
    --strip-debug \
    --no-man-pages \
    --no-header-files \
    --compress=2 \
    --output "$RUNTIME_OUTPUT"

# Test the runtime
echo "✅ Testing runtime..."
"$RUNTIME_OUTPUT/bin/java" -version

# Show size
SIZE=$(du -sh "$RUNTIME_OUTPUT" | cut -f1)
echo "📏 Runtime size: $SIZE"

# Clean up temp files
rm -rf "$TEMP_DIR"

echo "✅ Custom runtime built successfully!"
echo "📁 Runtime location: $RUNTIME_OUTPUT"
