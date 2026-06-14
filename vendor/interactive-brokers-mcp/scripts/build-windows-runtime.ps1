# PowerShell script to build custom Java runtime for Windows x64

Write-Host "🔧 Building custom Java runtime for Windows x64..." -ForegroundColor Cyan

# Essential modules for IB Gateway (based on Vert.x, Netty and networking requirements)
# Including jdk.unsupported for sun.misc.Unsafe and other internal APIs
$MODULES = "java.base,java.logging,java.net.http,java.desktop,java.management,java.naming,java.security.jgss,java.security.sasl,java.sql,java.xml,java.datatransfer,java.prefs,java.transaction.xa,jdk.crypto.ec,jdk.crypto.cryptoki,jdk.zipfs,jdk.unsupported"

# Platform-specific settings
$PLATFORM = "win32-x64"
$JDK_URL = "https://github.com/adoptium/temurin11-binaries/releases/download/jdk-11.0.22%2B7/OpenJDK11U-jdk_x64_windows_hotspot_11.0.22_7.zip"
$JDK_FILENAME = "OpenJDK11U-jdk_x64_windows_hotspot_11.0.22_7.zip"
$JLINK_PATH = "jdk-11.0.22+7\bin\jlink.exe"

Write-Host "🖥️  Platform: $PLATFORM" -ForegroundColor Yellow

# Create temp directory
$TEMP_DIR = "temp-runtime-build"
if (Test-Path $TEMP_DIR) {
    Remove-Item -Recurse -Force $TEMP_DIR
}
New-Item -ItemType Directory -Path $TEMP_DIR | Out-Null

try {
    # Download JDK
    Write-Host "⬇️  Downloading JDK..." -ForegroundColor Green
    $ProgressPreference = 'SilentlyContinue'  # Disable progress bar for faster download
    Invoke-WebRequest -Uri $JDK_URL -OutFile "$TEMP_DIR\$JDK_FILENAME"
    $ProgressPreference = 'Continue'

    if (-not (Test-Path "$TEMP_DIR\$JDK_FILENAME")) {
        throw "Download failed"
    }

    # Extract JDK
    Write-Host "📦 Extracting JDK..." -ForegroundColor Green
    Expand-Archive -Path "$TEMP_DIR\$JDK_FILENAME" -DestinationPath $TEMP_DIR -Force

    # Build custom runtime
    $JLINK_FULL_PATH = Join-Path $TEMP_DIR $JLINK_PATH
    $RUNTIME_OUTPUT = "runtime\$PLATFORM"

    Write-Host "🔗 Running jlink..." -ForegroundColor Green
    if (-not (Test-Path "runtime")) {
        New-Item -ItemType Directory -Path "runtime" | Out-Null
    }

    & $JLINK_FULL_PATH --add-modules $MODULES --strip-debug --no-man-pages --no-header-files --compress=2 --output $RUNTIME_OUTPUT

    if (-not (Test-Path "$RUNTIME_OUTPUT\bin\java.exe")) {
        throw "Runtime build failed - java.exe not found"
    }

    # Test the runtime
    Write-Host "✅ Testing runtime..." -ForegroundColor Green
    $testResult = & "$RUNTIME_OUTPUT\bin\java.exe" -version 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Runtime test failed: $testResult"
    }

    # Show size
    $size = (Get-ChildItem -Recurse $RUNTIME_OUTPUT | Measure-Object -Property Length -Sum).Sum
    $sizeMB = [math]::Round($size / 1MB, 1)
    Write-Host "📏 Runtime size: ${sizeMB}MB" -ForegroundColor Yellow

    Write-Host "✅ Windows runtime built successfully!" -ForegroundColor Green
    Write-Host "📁 Runtime location: $RUNTIME_OUTPUT" -ForegroundColor Cyan

    Write-Host "`n🎉 Windows runtime build complete!" -ForegroundColor Magenta
    Write-Host "`nNext steps:" -ForegroundColor Yellow
    Write-Host "1. Copy this runtime folder to your main project" -ForegroundColor White
    Write-Host "2. Ensure the runtime/win32-x64/ directory is committed to git" -ForegroundColor White

} catch {
    Write-Host "❌ Error: $_" -ForegroundColor Red
    exit 1
} finally {
    # Clean up temp files
    if (Test-Path $TEMP_DIR) {
        Remove-Item -Recurse -Force $TEMP_DIR
    }
}
