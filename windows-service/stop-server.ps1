#!/usr/bin/env pwsh
#Requires -Version 5.1
<#
.SYNOPSIS
    Stop the OSINT DD Dashboard background server.

.DESCRIPTION
    Stops the server gracefully using PID file, falls back to process
    name matching, and finally kills any process listening on port 8000.

.PARAMETER Quiet
    Suppress output (for use by other scripts).

.EXAMPLE
    .\stop-server.ps1
    # Stop the server with full output

.EXAMPLE
    .\stop-server.ps1 -Quiet
    # Stop silently (called from start-background.ps1 -Force)
#>
[CmdletBinding()]
param([switch]$Quiet)

$script:PID_FILE     = "C:\Users\guyne\Documents\osint-dd-dashboard\backend\logs\server.pid"
$script:SERVER_PORT  = 8000

function Write-Status {
    param([string]$Message, [string]$Level = "INFO")
    if ($Quiet) { return }
    $ts = Get-Date -Format "HH:mm:ss"
    $color = switch ($Level) {
        "OK"    { "Green" }
        "WARN"  { "Yellow" }
        "ERROR" { "Red" }
        default { "Cyan" }
    }
    Write-Host "$ts $Message" -ForegroundColor $color
}

if (-not $Quiet) {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  OSINT DD Dashboard - Stop Server" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
}

$stopped = $false

# --- Method 1: PID file ---
if (Test-Path $script:PID_FILE) {
    try {
        $pidVal = [int](Get-Content $script:PID_FILE -Raw).Trim()
        $proc = Get-Process -Id $pidVal -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Status "Found server process (PID: $pidVal) — stopping gracefully..."
            $proc.CloseMainWindow() | Out-Null
            Start-Sleep -Seconds 1
            if (-not $proc.HasExited) {
                $proc.Kill()
                $proc.WaitForExit(5000) | Out-Null
            }
            if ($proc.HasExited) {
                Write-Status "Server stopped (PID: $pidVal)" "OK"
                $stopped = $true
            }
        }
    } catch {
        Write-Status "Error stopping via PID: $_" "WARN"
    }
}

# --- Method 2: Find python processes running main.py ---
if (-not $stopped) {
    $pythons = Get-Process | Where-Object {
        $_.ProcessName -match "python" -and
        $_.MainWindowTitle -eq "" -and
        $_.Id -ne $PID
    }
    foreach ($p in $pythons) {
        try {
            # Check command line for main.py
            $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($p.Id)").CommandLine
            if ($cmd -match "main\.py") {
                Write-Status "Found orphaned main.py process (PID: $($p.Id)) — stopping..."
                Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
                $stopped = $true
            }
        } catch { }
    }
}

# --- Method 3: Kill anything on port 8000 ---
if (-not $stopped) {
    try {
        $listener = Get-NetTCPConnection -LocalPort $script:SERVER_PORT -ErrorAction SilentlyContinue |
            Where-Object { $_.State -eq "Listen" }
        if ($listener) {
            foreach ($conn in $listener) {
                try {
                    $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
                    if ($proc -and $proc.ProcessName -match "python") {
                        Write-Status "Killing process on port $($script:SERVER_PORT) (PID: $($proc.Id))..."
                        Stop-Process -Id $proc.Id -Force
                        $stopped = $true
                    }
                } catch { }
            }
        }
    } catch { }
}

# --- Cleanup ---
if (Test-Path $script:PID_FILE) {
    Remove-Item $script:PID_FILE -Force -ErrorAction SilentlyContinue
}

if ($stopped) {
    if (-not $Quiet) {
        Write-Status "Server stopped successfully" "OK"
        Write-Host ""
    }
} else {
    if (-not $Quiet) {
        Write-Status "No running server found" "WARN"
        Write-Host ""
    }
}
