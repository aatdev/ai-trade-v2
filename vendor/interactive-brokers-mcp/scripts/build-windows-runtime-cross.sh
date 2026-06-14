#!/bin/bash

set -e

echo "🔧 Cross-compiling Windows x64 Java runtime from macOS/Linux..."

# Essential modules for IB Gateway
MODULES="java.base,java.logging,java.net.http,java.desktop,java.management,java.naming,java.security.jgss,java.security.sasl,java.sql,java.xml,java.datatransfer,java.prefs,java.transaction.xa,jdk.crypto.ec,jdk.crypto.cryptoki,jdk.zipfs,jdk.unsupported"

PLATFORM="win32-x64"
RUNTIME_OUTPUT="./runtime/$PLATFORM"

# Check if runtime already exists
if [[ -d "$RUNTIME_OUTPUT" ]]; then
    echo "⚠️  Runtime for $PLATFORM already exists at $RUNTIME_OUTPUT"
    read -p "Do you want to rebuild it? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Skipping..."
        exit 0
    fi
    rm -rf "$RUNTIME_OUTPUT"
fi

# Create temp directory
TEMP_DIR="./temp-win32-build"
rm -rf "$TEMP_DIR"
mkdir -p "$TEMP_DIR"

# Download Windows JDK
JDK_URL="https://github.com/adoptium/temurin11-binaries/releases/download/jdk-11.0.22%2B7/OpenJDK11U-jdk_x64_windows_hotspot_11.0.22_7.zip"
ARCHIVE_PATH="$TEMP_DIR/windows-jdk.zip"

echo "⬇️  Downloading Windows JDK..."
curl -L -o "$ARCHIVE_PATH" "$JDK_URL"

echo "📦 Extracting Windows JDK..."
unzip -q "$ARCHIVE_PATH" -d "$TEMP_DIR"

# Find the Windows jmods directory
WIN_JMODS="$TEMP_DIR/jdk-11.0.22+7/jmods"

if [[ ! -d "$WIN_JMODS" ]]; then
    echo "❌ Windows jmods not found at $WIN_JMODS"
    rm -rf "$TEMP_DIR"
    exit 1
fi

echo "✅ Found Windows jmods at $WIN_JMODS"

# We need to use jlink from a Java 11 JDK to match the target
# Download the matching JDK for the current platform to get jlink
echo "⬇️  Downloading Java 11 JDK for jlink (must match target version)..."

if [[ "$(uname)" == "Darwin" ]]; then
    # macOS
    if [[ "$(uname -m)" == "arm64" ]]; then
        HOST_JDK_URL="https://github.com/adoptium/temurin11-binaries/releases/download/jdk-11.0.22%2B7/OpenJDK11U-jdk_aarch64_mac_hotspot_11.0.22_7.tar.gz"
    else
        HOST_JDK_URL="https://github.com/adoptium/temurin11-binaries/releases/download/jdk-11.0.22%2B7/OpenJDK11U-jdk_x64_mac_hotspot_11.0.22_7.tar.gz"
    fi
    HOST_JLINK="$TEMP_DIR/host-jdk/jdk-11.0.22+7/Contents/Home/bin/jlink"
else
    # Linux
    if [[ "$(uname -m)" == "aarch64" ]]; then
        HOST_JDK_URL="https://github.com/adoptium/temurin11-binaries/releases/download/jdk-11.0.22%2B7/OpenJDK11U-jdk_aarch64_linux_hotspot_11.0.22_7.tar.gz"
    else
        HOST_JDK_URL="https://github.com/adoptium/temurin11-binaries/releases/download/jdk-11.0.22%2B7/OpenJDK11U-jdk_x64_linux_hotspot_11.0.22_7.tar.gz"
    fi
    HOST_JLINK="$TEMP_DIR/host-jdk/jdk-11.0.22+7/bin/jlink"
fi

mkdir -p "$TEMP_DIR/host-jdk"
curl -L -o "$TEMP_DIR/host-jdk.tar.gz" "$HOST_JDK_URL"
tar -xf "$TEMP_DIR/host-jdk.tar.gz" -C "$TEMP_DIR/host-jdk"
JLINK_PATH="$HOST_JLINK"
echo "✅ Using Java 11 jlink: $JLINK_PATH"

# Create runtime directory
mkdir -p "./runtime"

echo "🔗 Running jlink to create Windows runtime..."
"$JLINK_PATH" \
    --module-path "$WIN_JMODS" \
    --add-modules "$MODULES" \
    --strip-debug \
    --no-man-pages \
    --no-header-files \
    --compress=2 \
    --output "$RUNTIME_OUTPUT"

# Verify the output
if [[ -f "$RUNTIME_OUTPUT/bin/java.exe" ]]; then
    echo "✅ Windows runtime created successfully!"

    # Show size
    SIZE=$(du -sh "$RUNTIME_OUTPUT" | cut -f1)
    echo "📏 Runtime size: $SIZE"
    echo "📁 Runtime location: $RUNTIME_OUTPUT"

    # List key files
    echo ""
    echo "📋 Key files:"
    ls -la "$RUNTIME_OUTPUT/bin/" | head -10
else
    echo "❌ Failed to create Windows runtime - java.exe not found"
    rm -rf "$TEMP_DIR"
    exit 1
fi

# Clean up
echo "🧹 Cleaning up temporary files..."
rm -rf "$TEMP_DIR"

echo ""
echo "🎉 Windows runtime build complete!"
echo ""
echo "Next steps:"
echo "  git add runtime/win32-x64/"
echo "  git commit -m 'Add Windows x64 runtime'"
echo "  git push"
