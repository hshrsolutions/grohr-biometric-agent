<#
.SYNOPSIS
  Stop and remove the GroHR Biometric Agent service. Files are KEPT.

      powershell -ExecutionPolicy Bypass -File .\install\windows\uninstall.ps1

  config.json, the spool (punches not yet delivered) and the logs stay in the
  install folder. Delete that folder by hand only after `--status` shows
  pending: 0 - otherwise undelivered punches are lost.
#>
param([string]$InstallDir = "C:\GroHR\biometric-agent")
$ErrorActionPreference = "Stop"
$ServiceName = "GroHRBiometricAgent"

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if (-not $svc) { Write-Host "Service $ServiceName is not installed."; exit 0 }
if ($svc.Status -eq "Running") { Stop-Service $ServiceName -Force }
sc.exe delete $ServiceName | Out-Null
Write-Host "Service removed. Files kept in $InstallDir." -ForegroundColor Green
Write-Host "Check undelivered punches before deleting it:  python -m grohr_agent --status"
