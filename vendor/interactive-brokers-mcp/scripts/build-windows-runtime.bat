@echo off
setlocal enabledelayedexpansion

echo 🔧 Building custom Java runtime for Windows x64...

REM Essential modules for IB Gateway (based on Vert.x, Netty and networking requirements)
REM Including jdk.unsupported for sun.misc.Unsafe and other internal APIs
set MODULES=java.base,java.logging,java.net.http,java.desktop,java.management,java.naming,java.security.jgss,java.security.sasl,java.sql,java.xml,java.datatransfer,java.prefs,java.transaction.xa,jdk.crypto.ec,jdk.crypto.cryptoki,jdk.zipfs,jdk.unsupported

REM Platform-specific settings
set PLATFORM=win32-x64
set JDK_URL=https://github.com/adoptium/temurin11-binaries/releases/download/jdk-11.0.22%%2B7/OpenJDK11U-jdk_x64_windows_hotspot_11.0.22_7.zip
set JDK_FILENAME=OpenJDK11U-jdk_x64_windows_hotspot_11.0.22_7.zip
set JLINK_PATH=jdk-11.0.22+7\bin\jlink.exe

echo 🖥️  Platform: %PLATFORM%

REM Create temp directory
set TEMP_DIR=temp-runtime-build
if exist "%TEMP_DIR%" rmdir /s /q "%TEMP_DIR%"
mkdir "%TEMP_DIR%"

REM Download JDK
echo ⬇️  Downloading JDK...
powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%JDK_URL%' -OutFile '%TEMP_DIR%\%JDK_FILENAME%'}"

if not exist "%TEMP_DIR%\%JDK_FILENAME%" (
    echo ❌ Download failed
    goto cleanup
)

REM Extract JDK
echo 📦 Extracting JDK...
powershell -Command "Expand-Archive -Path '%TEMP_DIR%\%JDK_FILENAME%' -DestinationPath '%TEMP_DIR%' -Force"

REM Build custom runtime
set JLINK_FULL_PATH=%TEMP_DIR%\%JLINK_PATH%
set RUNTIME_OUTPUT=runtime\%PLATFORM%

echo 🔗 Running jlink...
if not exist "runtime" mkdir "runtime"

"%JLINK_FULL_PATH%" --add-modules %MODULES% --strip-debug --no-man-pages --no-header-files --compress=2 --output "%RUNTIME_OUTPUT%"

if not exist "%RUNTIME_OUTPUT%\bin\java.exe" (
    echo ❌ Runtime build failed
    goto cleanup
)

REM Test the runtime
echo ✅ Testing runtime...
"%RUNTIME_OUTPUT%\bin\java.exe" -version

if errorlevel 1 (
    echo ❌ Runtime test failed
    goto cleanup
)

REM Show size
for /f "tokens=3" %%a in ('dir "%RUNTIME_OUTPUT%" /-c ^| find "File(s)"') do set size=%%a
set /a size_mb=!size! / 1024 / 1024
echo 📏 Runtime size: !size_mb!MB

REM Clean up temp files
:cleanup
if exist "%TEMP_DIR%" rmdir /s /q "%TEMP_DIR%"

if exist "%RUNTIME_OUTPUT%\bin\java.exe" (
    echo ✅ Windows runtime built successfully!
    echo 📁 Runtime location: %RUNTIME_OUTPUT%
) else (
    echo ❌ Failed to build Windows runtime
    exit /b 1
)

echo.
echo 🎉 Windows runtime build complete!
echo.
echo Next steps:
echo 1. Copy this runtime folder to your main project
echo 2. Ensure the runtime/win32-x64/ directory is committed to git
