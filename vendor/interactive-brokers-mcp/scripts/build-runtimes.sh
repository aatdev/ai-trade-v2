#!/bin/bash

set -e

echo "🔧 Building custom Java runtimes for all platforms..."

# Essential modules for IB Gateway (based on Vert.x and networking requirements)
MODULES="java.base,java.logging,java.net.http,java.desktop,java.management,java.naming,java.security.jgss,java.sql,java.xml,jdk.crypto.ec,jdk.crypto.cryptoki,jdk.zipfs"

# Create temp directory
TEMP_DIR="./temp-runtime-build"
mkdir -p "$TEMP_DIR"

# Function to download file
download_file() {
    local url="$1"
    local output="$2"
    echo "⬇️  Downloading: $(basename "$output")"
    curl -L -o "$output" "$url"
}

# Function to extract archive
extract_archive() {
    local archive="$1"
    local target_dir="$2"

    if [[ "$archive" == *.zip ]]; then
        unzip -q "$archive" -d "$target_dir"
    else
        tar -xf "$archive" -C "$target_dir"
    fi
}

# Function to build runtime for a platform
build_runtime() {
    local platform="$1"
    local jdk_url="${JDK_URLS[$platform]}"
    local jlink_path="${JLINK_PATHS[$platform]}"

    echo ""
    echo "🏗️  Building runtime for $platform..."

    # Create platform-specific temp dir
    local platform_temp="$TEMP_DIR/$platform"
    mkdir -p "$platform_temp"

    # Download JDK
    local archive_name=$(basename "$jdk_url" | cut -d'%' -f1)
    local archive_path="$platform_temp/$archive_name"
    download_file "$jdk_url" "$archive_path"

    # Extract JDK
    echo "📦 Extracting JDK..."
    extract_archive "$archive_path" "$platform_temp"

    # Build custom runtime
    local jlink_full_path="$platform_temp/$jlink_path"
    local runtime_output="./runtime/$platform"

    echo "🔗 Running jlink..."
    mkdir -p "./runtime"

    "$jlink_full_path" \
        --add-modules "$MODULES" \
        --strip-debug \
        --no-man-pages \
        --no-header-files \
        --compress=2 \
        --output "$runtime_output"

    # Test the runtime
    local java_exe="java"
    if [[ "$platform" == "win32-x64" ]]; then
        java_exe="java.exe"
    fi

    echo "✅ Testing runtime..."
    "$runtime_output/bin/$java_exe" -version

    # Show size
    local size=$(du -sh "$runtime_output" | cut -f1)
    echo "📏 Runtime size: $size"

    # Clean up temp files
    rm -rf "$platform_temp"

    echo "✅ $platform runtime complete!"
}

# Build runtimes for all platforms
for platform in "${!JDK_URLS[@]}"; do
    build_runtime "$platform"
done

# Clean up
rm -rf "$TEMP_DIR"

echo ""
echo "🎉 All custom runtimes built successfully!"
echo "📁 Runtimes are in: ./runtime/"
echo ""
du -sh ./runtime/*
