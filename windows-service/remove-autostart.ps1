#!/usr/bin/env pwsh
#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Remove the OSINT DD Dashboard auto-start task from Windows Task Scheduler.

.DESCRIPTION
    Removes the scheduled task that starts the server on user logon.
    Requires Administrator privileges.
#>

$taskName = "OSINT-DD-Dashboard"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  OSINT DD Dashboard - Remove Auto-Start" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check if running as admin
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")
if (-not $isAdmin) {
    Write-Host "[ERROR] This script must be run as Administrator!" -ForegroundColor Red
    Write-Host ""
    exit 1
}

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "[INFO] Task '$taskName' does not exist." -ForegroundColor Yellow
    Write-Host ""
    exit 0
}

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
Write-Host "[OK] Auto-start task '$taskName' removed." -ForegroundColor Green
Write-Host ""
Write-Host "The server will no longer start automatically on logon." -ForegroundColor Cyan
Write-Host ""
