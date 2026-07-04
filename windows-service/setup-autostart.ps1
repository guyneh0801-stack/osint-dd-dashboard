#!/usr/bin/env pwsh
#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Register the OSINT DD Dashboard to start automatically on Windows boot.

.DESCRIPTION
    Creates a Windows Task Scheduler task that starts the server silently
    when the user logs on. Requires Administrator privileges.

.EXAMPLE
    # Run as Administrator:
    .\setup-autostart.ps1
#>

$taskName = "OSINT-DD-Dashboard"
$psPath = (Get-Command powershell).Source
$scriptPath = Resolve-Path "$PSScriptRoot\start-background.ps1"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  OSINT DD Dashboard - Auto-Start Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check if running as admin
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")
if (-not $isAdmin) {
    Write-Host "[ERROR] This script must be run as Administrator!" -ForegroundColor Red
    Write-Host ""
    Write-Host "Right-click PowerShell and select 'Run as Administrator', then run:" -ForegroundColor Yellow
    Write-Host "  cd '$PSScriptRoot'" -ForegroundColor Gray
    Write-Host "  .\setup-autostart.ps1" -ForegroundColor Gray
    Write-Host ""
    exit 1
}

# Check if task already exists
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Task '$taskName' already exists." -ForegroundColor Yellow
    Write-Host ""
    $choice = Read-Host "Recreate it? (y/n)"
    if ($choice -ne 'y') {
        Write-Host "Cancelled." -ForegroundColor Cyan
        exit 0
    }
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed old task." -ForegroundColor Green
}

# Create the task action
$action = New-ScheduledTaskAction -Execute $psPath -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`""

# Trigger: at user logon
$trigger = New-ScheduledTaskTrigger -AtLogOn

# Settings: run whether user is logged on or not, highest privileges
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

# Register
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

Write-Host ""
Write-Host "[OK] Auto-start task created successfully!" -ForegroundColor Green
Write-Host ""
Write-Host "Task Details:" -ForegroundColor Cyan
Write-Host "  Name:  $taskName"
Write-Host "  Run:   At logon"
Write-Host "  User:  $env:USERNAME"
Write-Host "  Start: $scriptPath"
Write-Host ""
Write-Host "The server will start automatically when you log in." -ForegroundColor Green
Write-Host ""
Write-Host "To remove auto-start, run: .\remove-autostart.ps1" -ForegroundColor Gray
Write-Host ""
