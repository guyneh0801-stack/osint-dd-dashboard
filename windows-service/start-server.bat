@echo off
chcp 65001 >nul
:: ============================================================================
:: OSINT DD Dashboard - Start Server (VISIBLE WINDOW - for debugging)
:: ============================================================================
:: This batch file starts the server with a visible window so you can see
:: all logs in real-time. Use start-background.ps1 for hidden background mode.
::
:: Usage: Double-click this file, or run from CMD/PowerShell.
:: ============================================================================

title OSINT DD Dashboard Server
cls
echo.
echo  ╔══════════════════════════════════════════════════════════════╗
echo  ║          OSINT DD Dashboard - Server Startup                 ║
echo  ║           (Visible Window - Debug Mode)                      ║
echo  ╚══════════════════════════════════════════════════════════════╝
echo.

:: --- Configuration ---
set "PYTHON_PATH=C:\Users\guyne\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
set "PROJECT_DIR=C:\Users\guyne\Documents\osint-dd-dashboard\backend"

:: --- Auto-detect Python if configured path doesn't exist ---
if not exist "%PYTHON_PATH%" (
    echo [WARN] Configured Python not found, trying auto-detect...
    where python >nul 2>&1 && set "PYTHON_PATH=python"
    if not exist "%PYTHON_PATH%" (
        where py >nul 2>&1 && set "PYTHON_PATH=py"
        if not exist "%PYTHON_PATH%" (
            where python3 >nul 2>&1 && set "PYTHON_PATH=python3"
        )
    )
)

:: --- Verify Python ---
if not exist "%PYTHON_PATH%" (
    echo [ERROR] Python not found!
    echo.
    echo Please edit this file and set PYTHON_PATH to your Python installation.
    echo.
    pause
    exit /b 1
)

echo [INFO] Using Python: %PYTHON_PATH%
echo [INFO] Project dir:  %PROJECT_DIR%
echo.

:: --- Check main.py ---
if not exist "%PROJECT_DIR%\main.py" (
    echo [ERROR] main.py not found in %PROJECT_DIR%
    echo.
    echo Please edit this file and set PROJECT_DIR correctly.
    echo.
    pause
    exit /b 1
)

:: --- Start server ---
echo [INFO] Starting server...
echo [INFO] Press Ctrl+C to stop
echo [INFO] Server will be available at http://localhost:8000
echo.
echo ════════════════════════════════════════════════════════════════
echo.

cd /d "%PROJECT_DIR%"
"%PYTHON_PATH%" main.py

:: --- If server exits ---
echo.
echo [WARN] Server has stopped.
echo.
pause
