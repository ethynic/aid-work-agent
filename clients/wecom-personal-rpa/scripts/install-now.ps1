#requires -Version 5.1
<#
.SYNOPSIS
  Uninstall old WeComRpa MSI and install the new one. Must run as Administrator.
  Right-click PowerShell -> "Run as Administrator" -> execute this script.
#>
[CmdletBinding()]
param(
    [string]$MsiPath = "C:\repos\aid-work-agent\clients\wecom-personal-rpa\publish\WeComRpa-1.0.0.msi"
)
$ErrorActionPreference = 'Stop'

function Write-Step([string]$msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Warn2([string]$msg){ Write-Host "[!] $msg" -ForegroundColor Yellow }
function Die([string]$msg)        { Write-Host "[FAIL] $msg" -ForegroundColor Red; exit 1 }

# --- admin check ---
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Die "This script must run as Administrator. Right-click PowerShell -> 'Run as Administrator', then re-run."
}
Write-Ok "Admin confirmed"

if (-not (Test-Path $MsiPath)) { Die "MSI not found: $MsiPath. Run publish.ps1 + build-msi.ps1 first." }

# --- 1. stop running client processes ---
Write-Step "Stopping running client processes"
Get-Process | Where-Object { $_.ProcessName -like 'Client.App' -or $_.ProcessName -like 'Client.Supervisor' } | ForEach-Object {
    Write-Warn2 "Stopping $($_.ProcessName) (PID $($_.Id))"
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2
Write-Ok "Client processes stopped"

# --- 2. uninstall old version(s) if present ---
# Match by UpgradeCode-related pattern: DisplayName contains "RPA" (covers both English and Chinese names).
# Use @() to force array, since multiple ProductCodes with the same DisplayName can coexist.
Write-Step "Checking for old version(s)"
$uninstalls = @(Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*", "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*" -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like "*RPA*" -and $_.DisplayName -notlike "*DingTalk*" })
if ($uninstalls.Count -gt 0) {
    Write-Warn2 "Found $($uninstalls.Count) old entry(ies). Uninstalling all..."
    foreach ($u in $uninstalls) {
        $pc = $u.PSChildName
        Write-Warn2 "  Uninstalling: $($u.DisplayName) ($pc)"
        $proc = Start-Process -FilePath "msiexec.exe" -ArgumentList @("/x", "$pc", "/qn", "/norestart") -Wait -PassThru
        if ($proc.ExitCode -ne 0) { Die "Uninstall failed for $pc (exit $($proc.ExitCode))" }
    }
    Start-Sleep -Seconds 3
    Write-Ok "All old versions uninstalled"
} else {
    Write-Ok "No old version found, skipping uninstall"
}

# --- 2b. force-clean Program Files\WeComRpa if it still exists ---
# (msiexec sometimes leaves stale exe files when version numbers collide)
$installDir = "C:\Program Files\WeComRpa"
if (Test-Path $installDir) {
    Write-Warn2 "Cleaning leftover install dir: $installDir"
    Remove-Item -Recurse -Force $installDir -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    if (Test-Path $installDir) { Die "Failed to remove $installDir. Manual cleanup required." }
    Write-Ok "Stale install dir removed"
}

# --- 3. install new version ---
Write-Step "Installing new MSI: $MsiPath"
$proc = Start-Process -FilePath "msiexec.exe" -ArgumentList @("/i", "$MsiPath", "/qn", "/norestart") -Wait -PassThru
if ($proc.ExitCode -ne 0) { Die "Install failed (exit $($proc.ExitCode))" }
Start-Sleep -Seconds 3
Write-Ok "Install completed"

# --- 4. verify ---
Write-Step "Verifying installation"
$exePath = "C:\Program Files\WeComRpa\app\Client.App.exe"
$configPath = "C:\Program Files\WeComRpa\app\configs\client.example.yaml"
$assetsPath = "C:\Program Files\WeComRpa\app\assets\wecom_nodes.yaml"

if (-not (Test-Path $exePath))    { Die "Missing: $exePath" }
if (-not (Test-Path $configPath)) { Die "Missing: $configPath" }
if (-not (Test-Path $assetsPath)) { Die "Missing: $assetsPath" }
Write-Ok "All expected files present"

# --- 5. show exe timestamp to confirm new build ---
$exe = Get-Item $exePath
Write-Host ""
Write-Ok "Client.App.exe LastWriteTime: $($exe.LastWriteTime)"
Write-Host "    If this is not within the last hour, the MSI is stale."
Write-Host ""
Write-Host "    Now launch the client from Start Menu to test."
