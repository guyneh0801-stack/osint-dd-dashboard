#!/usr/bin/env pwsh
#Requires -Version 5.1
<#
.SYNOPSIS
    Start the OSINT DD Dashboard server in the background (no visible window).

.DESCRIPTION
    Launches the FastAPI backend server as a hidden background process with
    full log capture, PID tracking, and automatic stale-PID cleanup.

    The script uses .NET ProcessStartInfo with CreateNoWindow for a truly
    hidden process — no console flicker, no taskbar icon.

.PARAMETER Force
    Kill any existing server instance before starting a new one.

.PARAMETER Debug
    Print verbose diagnostic messages.

.EXAMPLE
    .\start-background.ps1
    # Start server (silently, logs to logs/server.log)

.EXAMPLE
    .\start-background.ps1 -Force
    # Restart: stop existing, then start fresh

.NOTES
    Author: OSINT DD Dashboard
    Version: 1.0
#>
[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$Debug
)

# ============================================================================
# USER CONFIGURATION — Edit these paths if your setup differs
# ============================================================================

$script:PYTHON_PATH = "C:\Users\guyne\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
$script:PROJECT_DIR = "C:\Users\guyne\Documents\osint-dd-dashboard\backend"
$script:LOG_DIR      = "$PROJECT_DIR\logs"
$script:LOG_FILE     = "$LOG_DIR\server.log"
$script:PID_FILE     = "$LOG_DIR\server.pid"
$script:SERVER_PORT  = 8000

# ============================================================================
# Helper Functions
# ============================================================================

function Write-Status {
    param([string]$Message, [string]$Level = "INFO")
    $ts = Get-Date -Format "HH:mm:ss"
    $prefix = switch ($Level) {
        "OK"    { "[OK]" }
        "WARN"  { "[WARN]" }
        "ERROR" { "[ERROR]" }
        default { "[INFO]" }
    }
    $color = switch ($Level) {
        "OK"    { "Green" }
        "WARN"  { "Yellow" }
        "ERROR" { "Red" }
        default { "Cyan" }
    }
    Write-Host "$ts $prefix $Message" -ForegroundColor $color
}

function Test-PythonPath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    if (-not (Test-Path $Path -PathType Leaf)) { return $false }
    try {
        $ver = & $Path --version 2>&1
        return ($ver -match "Python \d+\.\d+")
    } catch { return $false }
}

function Find-Python {
    # 1. Try user-configured path
    if (Test-PythonPath $script:PYTHON_PATH) {
        if ($Debug) { Write-Status "Found Python at configured path: $script:PYTHON_PATH" }
        return $script:PYTHON_PATH
    }
    Write-Status "Configured Python not found at: $script:PYTHON_PATH" "WARN"

    # 2. Try 'py' launcher (Python Windows launcher)
    try {
        $pyPath = (Get-Command "py" -ErrorAction SilentlyContinue).Source
        if (Test-PythonPath $pyPath) {
            Write-Status "Found Python via 'py' launcher: $pyPath"
            return $pyPath
        }
    } catch { }

    # 3. Try 'python' in PATH
    try {
        $pyInPath = (Get-Command "python" -ErrorAction SilentlyContinue).Source
        if (Test-PythonPath $pyInPath) {
            Write-Status "Found Python in PATH: $pyInPath"
            return $pyInPath
        }
    } catch { }

    # 4. Try 'python3' in PATH
    try {
        $py3Path = (Get-Command "python3" -ErrorAction SilentlyContinue).Source
        if (Test-PythonPath $py3Path) {
            Write-Status "Found Python3 in PATH: $py3Path"
            return $py3Path
        }
    } catch { }

    # 5. Common installation paths
    $commonPaths = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python39\python.exe",
        "$env:PROGRAMFILES\Python312\python.exe",
        "$env:PROGRAMFILES\Python311\python.exe",
        "$env:PROGRAMFILES\Python310\python.exe",
        "$env:PROGRAMFILES\Python39\python.exe",
        "$env:PROGRAMFILES(X86)\Python312\python.exe",
        "$env:PROGRAMFILES(X86)\Python311\python.exe",
        "$env:APPDATA\Python\Python311\Scripts\python.exe",
        "$env:APPDATA\Python\Python312\Scripts\python.exe"
    )
    foreach ($p in $commonPaths) {
        if (Test-PythonPath $p) {
            Write-Status "Found Python at common path: $p"
            return $p
        }
    }

    return $null
}

function Test-ServerRunning {
    # Check PID file
    if (Test-Path $script:PID_FILE) {
        try {
            $pidVal = [int](Get-Content $script:PID_FILE -Raw).Trim()
            $proc = Get-Process -Id $pidVal -ErrorAction SilentlyContinue
            if ($proc) {
                # Verify it's actually a Python process
                if ($proc.ProcessName -match "python") {
                    return $true
                }
            }
        } catch { }
    }

    # Check HTTP endpoint
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:$($script:SERVER_PORT)/api/health" `
            -UseBasicParsing -TimeoutSec 3 -ErrorAction SilentlyContinue
        if ($resp.StatusCode -eq 200) {
            return $true
        }
    } catch { }

    return $false
}

function Remove-StalePid {
    if (Test-Path $script:PID_FILE) {
        try {
            Remove-Item $script:PID_FILE -Force
            if ($Debug) { Write-Status "Removed stale PID file" }
        } catch {
            Write-Status "Could not remove PID file: $_" "WARN"
        }
    }
}

function Rotate-Log {
    if (Test-Path $script:LOG_FILE) {
        $size = (Get-Item $script:LOG_FILE).Length
        $maxSize = 10MB
        if ($size -gt $maxSize) {
            $backup = "$script:LOG_FILE.1"
            if (Test-Path $backup) { Remove-Item $backup -Force }
            Move-Item $script:LOG_FILE $backup -Force
            Write-Status "Rotated log file (>10MB)"
        }
    }
}

# ============================================================================
# Main Script
# ============================================================================

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  OSINT DD Dashboard - Background Start" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# --- Step 1: Check if already running ---
if (Test-ServerRunning) {
    if ($Force) {
        Write-Status "Server already running, stopping for restart..." "WARN"
        & "$PSScriptRoot\stop-server.ps1" -Quiet
        Start-Sleep -Seconds 2
    } else {
        Write-Status "Server is already running!" "WARN"
        Write-Status "Use -Force to restart, or run .\stop-server.ps1 first"
        Write-Host ""
        exit 0
    }
}

# --- Step 2: Clean stale PID ---
Remove-StalePid

# --- Step 3: Find Python ---
Write-Status "Locating Python installation..."
$pythonExe = Find-Python
if (-not $pythonExe) {
    Write-Status "Python NOT FOUND!" "ERROR"
    Write-Status "Please install Python or edit the PYTHON_PATH variable in this script" "ERROR"
    Write-Host ""
    Write-Host "Searched locations:" -ForegroundColor Yellow
    Write-Host "  - Configured: $script:PYTHON_PATH"
    Write-Host "  - 'py' launcher"
    Write-Host "  - 'python' / 'python3' in PATH"
    Write-Host "  - Common Windows install directories"
    Write-Host ""
    exit 1
}
Write-Status "Using Python: $pythonExe"

# --- Step 4: Verify project directory ---
if (-not (Test-Path $script:PROJECT_DIR -PathType Container)) {
    Write-Status "Project directory NOT FOUND: $script:PROJECT_DIR" "ERROR"
    Write-Status "Please edit the PROJECT_DIR variable in this script" "ERROR"
    Write-Host ""
    exit 1
}

# --- Step 5: Check main.py exists ---
$mainPy = Join-Path $script:PROJECT_DIR "main.py"
if (-not (Test-Path $mainPy)) {
    Write-Status "main.py not found at: $mainPy" "ERROR"
    Write-Status "Is PROJECT_DIR correct?" "ERROR"
    Write-Host ""
    exit 1
}

# --- Step 6: Validate Python packages ---
Write-Status "Validating Python packages..."
try {
    $pkgCheck = & $pythonExe -c "import fastapi, uvicorn; print('OK')" 2>&1
    if ($pkgCheck -ne "OK") {
        Write-Status "Missing required packages (fastapi, uvicorn)" "WARN"
        Write-Status "Run: pip install -r requirements.txt" "WARN"
    } else {
        Write-Status "Packages validated (fastapi, uvicorn)" "OK"
    }
} catch {
    Write-Status "Could not validate packages: $_" "WARN"
}

# --- Step 7: Ensure log directory ---
if (-not (Test-Path $script:LOG_DIR)) {
    New-Item -ItemType Directory -Path $script:LOG_DIR -Force | Out-Null
    Write-Status "Created log directory: $script:LOG_DIR"
}

# --- Step 8: Rotate log if needed ---
Rotate-Log

# --- Step 9: Start server as hidden process ---
Write-Status "Starting server..."
Write-Status "Log file: $script:LOG_FILE"

# Build the Python command
$pythonCmd = "$pythonExe"
$pythonArgs = "main.py"

# Use .NET ProcessStartInfo for true hidden window
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $pythonCmd
$psi.Arguments = $pythonArgs
$psi.WorkingDirectory = $script:PROJECT_DIR
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true

# Start the process
$process = [System.Diagnostics.Process]::Start($psi)

if (-not $process) {
    Write-Status "Failed to start server process!" "ERROR"
    Write-Host ""
    exit 1
}

# Save PID
$process.Id | Out-File -FilePath $script:PID_FILE -Encoding UTF8 -NoNewline

Write-Status "Server process started (PID: $($process.Id))" "OK"

# --- Step 10: Asynchronous log capture ---
$stdoutJob = {
    param($proc, $logFile)
    $reader = $proc.StandardOutput
    $fs = [System.IO.File]::Open($logFile, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write, [System.IO.FileShare]::ReadWrite)
    $writer = [System.IO.StreamWriter]::new($fs)
    $writer.AutoFlush = $true
    while (-not $proc.HasExited) {
        try {
            $line = $reader.ReadLine()
            if ($null -ne $line) {
                $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
                $writer.WriteLine("[$ts] $line")
            }
        } catch { break }
    }
    $writer.Close()
    $fs.Close()
}

$stderrJob = {
    param($proc, $logFile)
    $reader = $proc.StandardError
    $fs = [System.IO.File]::Open($logFile, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write, [System.IO.FileShare]::ReadWrite)
    $writer = [System.IO.StreamWriter]::new($fs)
    $writer.AutoFlush = $true
    while (-not $proc.HasExited) {
        try {
            $line = $reader.ReadLine()
            if ($null -ne $line) {
                $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
                $writer.WriteLine("[$ts] [STDERR] $line")
            }
        } catch { break }
    }
    $writer.Close()
    $fs.Close()
}

# Start log capture in background
Start-Job -ScriptBlock $stdoutJob -ArgumentList $process, $script:LOG_FILE | Out-Null
Start-Job -ScriptBlock $stderrJob -ArgumentList $process, $script:LOG_FILE | Out-Null

# --- Step 11: Wait for HTTP endpoint ---
Write-Status "Waiting for server to be ready..."
$maxWait = 30
$ready = $false
for ($i = 0; $i -lt $maxWait; $i++) {
    # Check if process died
    if ($process.HasExited) {
        Write-Status "Server process exited early (code: $($process.ExitCode))" "ERROR"
        Write-Status "Check logs: $script:LOG_FILE" "ERROR"
        Remove-StalePid
        Write-Host ""
        exit 1
    }

    # Check HTTP endpoint
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:$($script:SERVER_PORT)/api/health" `
            -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
        if ($resp.StatusCode -eq 200) {
            $ready = $true
            break
        }
    } catch { }

    Start-Sleep -Seconds 1
}

if ($ready) {
    Write-Status "Server is ONLINE!" "OK"
    Write-Status "Dashboard: http://localhost:$($script:SERVER_PORT)"
    Write-Status "API Docs:  http://localhost:$($script:SERVER_PORT)/docs"
    Write-Status "PID:       $($process.Id)"
    Write-Status "Log:       $script:LOG_FILE"
    Write-Host ""
    Write-Host "Commands:" -ForegroundColor Cyan
    Write-Host "  .\status.ps1      - Check server status"
    Write-Host "  .\stop-server.ps1  - Stop the server"
    Write-Host ""
} else {
    Write-Status "Server process is running but HTTP endpoint not responding yet" "WARN"
    Write-Status "This is normal on first start (static sanctions downloading)" "WARN"
    Write-Status "Run .\status.ps1 in 30 seconds to check again" "WARN"
    Write-Host ""
}

# --- Register exit handler to clean up PID file ---
Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action {
    if ($process -and -not $process.HasExited) {
        # Process still running, leave PID file
    } else {
        Remove-StalePid
    }
} | Out-Null

# Keep script running briefly to ensure process is stable, then exit
Start-Sleep -Seconds 2
exit 0
