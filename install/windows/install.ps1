<#
.SYNOPSIS
  Install or update the GroHR Biometric Agent as a Windows service.

.DESCRIPTION
  One command, run from the unzipped agent folder in an *Administrator*
  PowerShell window:

      powershell -ExecutionPolicy Bypass -File .\install\windows\install.ps1 -Customer example

  It installs Python dependencies, copies the agent to C:\GroHR\biometric-agent,
  writes config.json from the customer profile (asking for the API key/secret
  and the attendance-database password - they are typed here, never emailed),
  locks the file down, PROVES the setup with --probe and --once, and only then
  installs the service with automatic start and restart-on-failure.

  Re-running it is an update: the service is stopped, files replaced, and
  config.json, the spool and the logs are kept.

  Works on Windows PowerShell 5.1 (Windows 10 / Server 2016 and later).

.PARAMETER Customer
  A folder under profiles\ (example, ...). Used only when there
  is no config.json yet.

.PARAMETER InstallDir
  Where the agent lives. Default C:\GroHR\biometric-agent. Not a user profile.

.PARAMETER NoService
  Install and test, but do not install or start the service.

.PARAMETER Force
  Install the service even if --probe or --once failed (not recommended).
#>
[CmdletBinding()]
param(
	[string]$Customer = "",
	[string]$InstallDir = "C:\GroHR\biometric-agent",
	[switch]$NoService,
	[switch]$Force
)

$ErrorActionPreference = "Stop"
$ServiceName = "GroHRBiometricAgent"
$Source = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Say($text)  { Write-Host "==> $text" -ForegroundColor Cyan }
function Good($text) { Write-Host "    OK  $text" -ForegroundColor Green }
function Warn($text) { Write-Host "    !!  $text" -ForegroundColor Yellow }
function Die($text)  { Write-Host "    XX  $text" -ForegroundColor Red; exit 1 }

# ------------------------------------------------------------------ checks
$me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
	Die "Run this from an Administrator PowerShell window (right-click > Run as administrator)."
}

Say "Looking for Python 3.10 or newer"
$Python = $null
foreach ($candidate in @(@("py", "-3"), @("python"))) {
	try {
		$exe = $candidate[0]; $args0 = @($candidate | Select-Object -Skip 1)
		$ver = & $exe @args0 -c "import sys;print('%d.%d'%sys.version_info[:2]);print(sys.executable)" 2>$null
		if ($LASTEXITCODE -eq 0 -and $ver) {
			$parts = $ver[0].Split("."); $path = $ver[1]
			if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 10 -and $path -notmatch "WindowsApps") {
				$Python = $path; break
			}
		}
	} catch { }
}
if (-not $Python) {
	if (Get-Command winget -ErrorAction SilentlyContinue) {
		Warn "Python not found. Installing Python 3.12 for all users with winget..."
		winget install -e --id Python.Python.3.12 --scope machine --accept-package-agreements --accept-source-agreements
		$Python = "C:\Program Files\Python312\python.exe"
		if (-not (Test-Path $Python)) { Die "winget finished but $Python is missing. Install Python 3.12 from python.org (tick 'Install for all users' and 'Add to PATH') and re-run." }
	} else {
		Die "Python 3.10+ is not installed. Get it from https://www.python.org/downloads/windows/ - tick 'Install for all users' and 'Add python.exe to PATH' - then re-run this script."
	}
}
Good "Python: $Python"

# ---------------------------------------------------------- stop if running
$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq "Running") {
	Say "Stopping the running service for the update"
	Stop-Service $ServiceName -Force
	Good "stopped"
}

# ------------------------------------------------------------------- files
Say "Copying the agent to $InstallDir"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
if ($Source.TrimEnd("\") -ne (Resolve-Path $InstallDir).Path.TrimEnd("\")) {
	# robocopy exit codes below 8 are success.
	robocopy $Source $InstallDir /E /NFL /NDL /NJH /NJS /NP `
		/XF config.json spool.sqlite3 *.pyc /XD logs __pycache__ .git dist build venv tests | Out-Null
	if ($LASTEXITCODE -ge 8) { Die "robocopy failed ($LASTEXITCODE)." }
}
Good "files in place (config.json, spool and logs untouched)"

Say "Installing Python packages"
& $Python -m pip install --disable-pip-version-check --upgrade -q `
	-r (Join-Path $InstallDir "requirements.txt") -r (Join-Path $InstallDir "requirements-windows.txt")
if ($LASTEXITCODE -ne 0) { Die "pip install failed. Is the machine online (outbound HTTPS)?" }
& $Python -m pywin32_postinstall -install -quiet 2>$null | Out-Null
Good "requests, pyzk, PyMySQL, pyodbc, pywin32"

# ------------------------------------------------------------------ config
$Config = Join-Path $InstallDir "config.json"
if (-not (Test-Path $Config)) {
	$template = Join-Path $InstallDir "config.example.json"
	if ($Customer) {
		$template = Join-Path $InstallDir "profiles\$Customer\config.json"
		if (-not (Test-Path $template)) { Die "No profile '$Customer' under profiles\. Choices: $((Get-ChildItem (Join-Path $InstallDir 'profiles') -Directory).Name -join ', ')" }
	}
	Say "Writing config.json from $template"
	$cfg = Get-Content $template -Raw | ConvertFrom-Json

	function Ask($prompt, $current, [switch]$Secret) {
		if ($current -and "$current" -notmatch "^(|CHANGE-ME.*)$") { return $current }
		if ($Secret) {
			$s = Read-Host $prompt -AsSecureString
			return [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))
		}
		return Read-Host $prompt
	}
	$cfg.site_url   = Ask "GroHR site address (e.g. https://yourcompany.grohrms.cloud)" $cfg.site_url
	$cfg.connector  = Ask "Biometric Connector name (from the site)" $cfg.connector
	$cfg.api_key    = Ask "API key of the Biometric Integration user" $cfg.api_key
	$cfg.api_secret = Ask "API secret" $cfg.api_secret -Secret
	if ($cfg.sources) {
		foreach ($p in $cfg.sources.PSObject.Properties) {
			if ($p.Name.StartsWith("_")) { continue }
			$src = $p.Value
			Write-Host "    Attendance database for source '$($p.Name)' ($($src.engine))"
			$src.host     = Ask "      database host (e.g. 127.0.0.1)" $src.host
			$src.database = Ask "      database name" $src.database
			$src.user     = Ask "      database login (SELECT-only, please)" $src.user
			$src.password = Ask "      database password" $src.password -Secret
		}
	}
	# UTF-8 without BOM: Python's json module rejects a BOM.
	[IO.File]::WriteAllText($Config, ($cfg | ConvertTo-Json -Depth 10), (New-Object Text.UTF8Encoding $false))
	Good "config.json written"
} else {
	Good "keeping the existing config.json"
}

Say "Locking config.json to SYSTEM and Administrators (it holds live credentials)"
icacls $Config /inheritance:r /grant:r "*S-1-5-18:(R)" "*S-1-5-32-544:(F)" | Out-Null
Good "done"

# -------------------------------------------------------------- prove it
Push-Location $InstallDir
try {
	$failed = $false
	$hasSources = (Get-Content $Config -Raw | ConvertFrom-Json).sources
	if ($hasSources -and ($hasSources.PSObject.Properties | Where-Object { -not $_.Name.StartsWith("_") })) {
		Say "Probing the attendance database (read-only)"
		& $Python -m grohr_agent --config $Config --probe
		if ($LASTEXITCODE -ne 0) { $failed = $true; Warn "--probe failed: fix the source in config.json (see above)." }
	}
	Say "One real cycle against the site"
	& $Python -m grohr_agent --config $Config --once --verbose
	if ($LASTEXITCODE -ne 0) { $failed = $true; Warn "--once failed: read the lines above and logs\agent.log." }
} finally { Pop-Location }

if ($failed -and -not $Force) {
	Die "Not installing the service until the checks pass. Fix config.json and re-run (or add -Force)."
}
if ($NoService) { Good "Tests done; service not installed (-NoService)."; exit 0 }

# ----------------------------------------------------------------- service
Say "Installing the Windows service"
Push-Location $InstallDir
try {
	if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
		& $Python agent_win.py --startup auto update | Out-Null
	} else {
		& $Python agent_win.py --startup auto install | Out-Null
	}
	if ($LASTEXITCODE -ne 0) { Die "service registration failed." }
} finally { Pop-Location }
# Restart after 1 minute on each of the first three failures; reset the count daily.
sc.exe failure $ServiceName reset= 86400 actions= restart/60000/restart/60000/restart/60000 | Out-Null
sc.exe description $ServiceName "Reads biometric attendance punches and submits them to GroHR." | Out-Null
Start-Service $ServiceName
Start-Sleep -Seconds 3
$svc = Get-Service $ServiceName
if ($svc.Status -ne "Running") { Die "The service did not start. See $InstallDir\logs\agent.log and the Windows Application event log." }
Good "GroHR Biometric Agent is Running, start type Automatic, restarts on failure"

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "  Log:      $InstallDir\logs\agent.log   (a cycle every 5 minutes)"
Write-Host "  Status:   cd $InstallDir; & '$Python' -m grohr_agent --status"
Write-Host "  On the site: Biometric Connector > Last Check-in should update every cycle."
