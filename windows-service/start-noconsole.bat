@echo off
chcp 65001 >nul
:: ============================================================================
:: OSINT DD Dashboard - Start Server (NO CONSOLE WINDOW)
:: ============================================================================
:: This batch file starts the server WITHOUT any visible window.
:: It uses PowerShell's -WindowStyle Hidden to hide the console.
::
:: NOTE: There will be a brief flash of a PowerShell window for ~1 second.
::       For zero-flash startup, use start-background.ps1 instead.
::
:: Usage: Double-click this file.
:: ============================================================================

:: --- Configuration ---
set "PYTHON_PATH=C:\Users\guyne\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
set "PROJECT_DIR=C:\Users\guyne\Documents\osint-dd-dashboard\backend"
set "LOG_DIR=%PROJECT_DIR%\logs"
set "LOG_FILE=%LOG_DIR%\server.log"
set "PID_FILE=%LOG_DIR%\server.pid"

:: --- Ensure log directory exists ---
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:: --- Check if already running ---
if exist "%PID_FILE%" (
    set /p EXISTING_PID=<"%PID_FILE%"
    tasklist /FI "PID eq %EXISTING_PID%" 2>nul | find /I "%EXISTING_PID%" >nul
    if not errorlevel 1 (
        echo [OSINT DD] Server is already running! (PID: %EXISTING_PID%)
        echo [OSINT DD] Run stop-server.ps1 first, or use start-background.ps1 -Force
        timeout /t 3 >nul
        exit /b 0
    )
)

:: --- Find Python if configured path doesn't exist ---
if not exist "%PYTHON_PATH%" (
    where python >nul 2>&1 && set "PYTHON_PATH=python"
    if not exist "%PYTHON_PATH%" (
        where py >nul 2>&1 && set "PYTHON_PATH=py"
        if not exist "%PYTHON_PATH%" (
            where python3 >nul 2>&1 && set "PYTHON_PATH=python3"
        )
    )
)

if not exist "%PYTHON_PATH%" (
    echo [ERROR] Python not found! Please edit this file and set PYTHON_PATH.
    timeout /t 5 >nul
    exit /b 1
)

:: --- Start server using hidden PowerShell window ---
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "& { $psi = New-Object System.Diagnostics.ProcessStartInfo; $psi.FileName = '%PYTHON_PATH%'; $psi.Arguments = 'main.py'; $psi.WorkingDirectory = '%PROJECT_DIR%'; $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true; $psi.WindowStyle = 'Hidden'; $p = [System.Diagnostics.Process]::Start($psi); $p.Id | Out-File -FilePath '%PID_FILE%' -Encoding UTF8 -NoNewline; }"

:: --- Show quick confirmation ---
echo [OSINT DD] Server starting in background...
echo [OSINT DD] Logs: %LOG_FILE%
timeout /t 2 >nul
echo [OSINT DD] Check status: run status.ps1
echo [OSINT DD] Stop server:   run stop-server.ps1
timeout /t 3 >nul
