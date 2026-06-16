@echo off
setlocal enabledelayedexpansion

echo ============================================
echo  CivAdvisor - Build Script
echo ============================================
echo.

REM Resolve absolute paths relative to this script's directory
set "SCRIPT_DIR=%~dp0"
set "OVERLAY_DIR=%SCRIPT_DIR%..\overlay"
set "MAIN_PY=%OVERLAY_DIR%\main.py"
set "DIST_DIR=%OVERLAY_DIR%\dist"

REM Sanity check — make sure main.py exists before doing anything
if not exist "%MAIN_PY%" (
    echo [ERROR] Could not find main.py at:
    echo         %MAIN_PY%
    echo.
    echo Make sure you are running build.bat from inside the build\ folder.
    pause
    exit /b 1
)

echo Paths confirmed:
echo   Source : %MAIN_PY%
echo   Output : %DIST_DIR%\CivAdvisor.exe
echo.

REM Step 1 — dependencies
echo [1/3] Installing dependencies...
pip install -r "%OVERLAY_DIR%\requirements.txt"
if errorlevel 1 (
    echo.
    echo [ERROR] pip install failed. Is Python on your PATH?
    pause
    exit /b 1
)
echo.

REM Step 2 — PyInstaller
REM   --onefile        : single exe
REM   --console        : keep console visible so errors are readable
REM   --distpath       : put CivAdvisor.exe in overlay\dist\
REM   --workpath       : temp build files go in overlay\build_tmp\
REM   --specpath       : .spec file next to main.py
REM   --clean          : wipe previous build artifacts first
echo [2/3] Packaging with PyInstaller...
pyinstaller ^
    --onefile ^
    --console ^
    --name "CivAdvisor" ^
    --distpath "%DIST_DIR%" ^
    --workpath "%OVERLAY_DIR%\build_tmp" ^
    --specpath "%OVERLAY_DIR%" ^
    --clean ^
    "%MAIN_PY%"

if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller failed. Scroll up to read the error.
    pause
    exit /b 1
)
echo.

REM Step 3 — confirm output
if exist "%DIST_DIR%\CivAdvisor.exe" (
    echo [3/3] Done^^!
    echo.
    echo   Output: %DIST_DIR%\CivAdvisor.exe
    echo.
    echo Next steps:
    echo   1. Copy CivAdvisor.exe somewhere convenient
    echo   2. Run CivAdvisor.exe before or during a game
    echo.
    echo   No API key needed — advice is generated locally, offline.
) else (
    echo [ERROR] Build appeared to succeed but CivAdvisor.exe was not found.
    echo         Check the PyInstaller output above for clues.
)

echo.
pause
