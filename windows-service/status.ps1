#!/usr/bin/env pwsh
#Requires -Version 5.1
<#
.SYNOPSIS
    Check the OSINT DD Dashboard server status.

.DESCRIPTION
    Shows whether the server is running, process details, HTTP health,
    and the last lines of the log file.
#>

$script:PID_FILE     = "C:\Users\guyne\Documents\osint-dd-dashboard\backend\logs\server.pid"
$script:LOG_FILE     = "C:\Users\guyne\Documents\osint-dd-dashboard\backend\logs\server.log"
$script:SERVER_PORT  = 8000

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  OSINT DD Dashboard - Server Status" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$running = $false
$pidVal = $null
$proc = $null

# --- Check PID file ---
if (Test-Path $script:PID_FILE) {
    try {
        $pidVal = [int](Get-Content $script:PID_FILE -Raw).Trim()
        $proc = Get-Process -Id $pidVal -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "Status:     " -NoNewline; Write-Host "RUNNING" -ForegroundColor Green
            Write-Host "PID:        $pidVal"
            Write-Host "Name:       $($proc.ProcessName)"
            Write-Host "Started:    $($proc.StartTime)"
            Write-Host "Uptime:     $((Get-Date) - $proc.StartTime | Select-Object -ExpandProperty TotalMinutes | ForEach-Object { [math]::Round($_,1) }) minutes"
            Write-Host "Memory:     $([math]::Round($proc.WorkingSet64 / 1MB, 1)) MB"
            Write-Host "Threads:    $($proc.Threads.Count)"
            $running = $true
        } else {
            Write-Host "Status:     " -NoNewline; Write-Host "STOPPED (stale PID file)" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "Status:     " -NoNewline; Write-Host "UNKNOWN (PID file error)" -ForegroundColor Yellow
    }
} else {
    Write-Host "Status:     " -NoNewline; Write-Host "STOPPED (no PID file)" -ForegroundColor Red
}

# --- Check HTTP endpoint ---
Write-Host ""
Write-Host "HTTP Check:" -ForegroundColor Cyan
$endpoints = @(
    "http://localhost:$($script:SERVER_PORT)/api/health",
    "http://localhost:$($script:SERVER_PORT)/",
    "http://localhost:$($script:SERVER_PORT)/docs"
)
$httpUp = $false
foreach ($ep in $endpoints) {
    try {
        $resp = Invoke-WebRequest -Uri $ep -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        Write-Host "  $ep " -NoNewline
        Write-Host "OK ($($resp.StatusCode))" -ForegroundColor Green
        $httpUp = $true
        break
    } catch {
        Write-Host "  $ep " -NoNewline
        Write-Host "FAILED" -ForegroundColor Red
    }
}

if ($httpUp) {
    Write-Host ""
    Write-Host "Dashboard URL: http://localhost:$($script:SERVER_PORT)" -ForegroundColor Green
}

# --- Show recent log lines ---
Write-Host ""
Write-Host "Recent Log Entries:" -ForegroundColor Cyan
if (Test-Path $script:LOG_FILE) {
    $lines = Get-Content $script:LOG_FILE -Tail 10
    foreach ($line in $lines) {
        if ($line -match "ERROR|CRITICAL|FATAL") {
            Write-Host "  $line" -ForegroundColor Red
        } elseif ($line -match "WARN") {
            Write-Host "  $line" -ForegroundColor Yellow
        } elseif ($line -match "OK|running|started|complete") {
            Write-Host "  $line" -ForegroundColor Green
        } else {
            Write-Host "  $line" -ForegroundColor Gray
        }
    }
} else {
    Write-Host "  (no log file yet)" -ForegroundColor Gray
}

Write-Host ""
