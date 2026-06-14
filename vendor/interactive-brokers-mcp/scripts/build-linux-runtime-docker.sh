#!/bin/bash

set -e

echo "🔧 Building Linux runtime using Docker..."

# Check if Docker is available
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed or not in PATH"
    exit 1
fi

# Create a temporary Dockerfile
cat > Dockerfile.runtime-build << 'EOF'
FROM ubuntu:20.04

# Avoid interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install required packages
RUN apt-get update && apt-get install -y \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Copy the current directory into the container
COPY . .

# Make the script executable
RUN chmod +x scripts/build-runtime-current.sh

# Run the build script
CMD ["./scripts/build-runtime-current.sh"]
EOF

echo "🐳 Building Docker image for linux/amd64..."
docker build --platform linux/amd64 -f Dockerfile.runtime-build -t runtime-builder .

echo "🏗️  Running Linux runtime build in container..."
docker run --platform linux/amd64 --rm -v "$(pwd)/runtime:/workspace/runtime" runtime-builder

# Clean up
rm -f Dockerfile.runtime-build

echo "✅ Linux runtime build completed!"

# Check if the runtime was created
if [ -f "runtime/linux-x64/bin/java" ]; then
    SIZE=$(du -sh runtime/linux-x64 | cut -f1)
    echo "📏 Linux runtime size: $SIZE"
    echo "📁 Linux runtime location: runtime/linux-x64/"
else
    echo "❌ Linux runtime build failed - java executable not found"
    exit 1
fi
