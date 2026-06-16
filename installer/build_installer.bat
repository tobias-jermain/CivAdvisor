@echo off
setlocal enabledelayedexpansion

echo ============================================
echo  CivAdvisor - Build Installer
echo ============================================
echo.

set "SCRIPT_DIR=%~dp0"
set "ROOT_DIR=%SCRIPT_DIR%.."
set "OVERLAY_DIR=%ROOT_DIR%\overlay"
set "MAIN_PY=%OVERLAY_DIR%\main.py"
set "DIST_EXE=%OVERLAY_DIR%\dist\CivAdvisor.exe"
set "ISS_FILE=%SCRIPT_DIR%civadvisor.iss"
set "OUTPUT_DIR=%SCRIPT_DIR%Output"

REM Locate Inno Setup compiler (checks both 32-bit and 64-bit install paths)
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe"       set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"

if not exist "%MAIN_PY%" (
    echo [ERROR] overlay\main.py not found. Run from inside the installer\ folder.
    pause & exit /b 1
)

REM ── Step 1: PyInstaller ──────────────────────────────────────────────────────
echo [1/3] Installing Python dependencies...
pip install -r "%OVERLAY_DIR%\requirements.txt"
if errorlevel 1 ( echo [ERROR] pip install failed. & pause & exit /b 1 )
echo.

echo [2/3] Building CivAdvisor.exe with PyInstaller...
pyinstaller ^
    --onefile ^
    --noconsole ^
    --name "CivAdvisor" ^
    --distpath "%OVERLAY_DIR%\dist" ^
    --workpath "%OVERLAY_DIR%\build_tmp" ^
    --specpath "%OVERLAY_DIR%" ^
    --clean ^
    "%MAIN_PY%"

if errorlevel 1 ( echo [ERROR] PyInstaller failed. & pause & exit /b 1 )

if not exist "%DIST_EXE%" (
    echo [ERROR] CivAdvisor.exe was not produced. Check PyInstaller output above.
    pause & exit /b 1
)
echo   OK: %DIST_EXE%
echo.

REM ── Step 2: Inno Setup ───────────────────────────────────────────────────────
if not defined ISCC (
    echo [ERROR] Inno Setup 6 not found.
    echo         Download from https://jrsoftware.org/isdownload.php
    echo         Install it, then re-run this script.
    pause & exit /b 1
)

echo [3/3] Compiling installer with Inno Setup...
"%ISCC%" "%ISS_FILE%"
if errorlevel 1 ( echo [ERROR] Inno Setup compile failed. & pause & exit /b 1 )

REM ── Done ────────────────────────────────────────────────────────────────────
if exist "%OUTPUT_DIR%\CivAdvisor_Setup.exe" (
    echo.
    echo ============================================
    echo  Done!
    echo  Installer: %OUTPUT_DIR%\CivAdvisor_Setup.exe
    echo ============================================
    echo.
    echo Upload CivAdvisor_Setup.exe as a GitHub Release asset.
) else (
    echo [ERROR] CivAdvisor_Setup.exe not found after build.
)

echo.
pause
