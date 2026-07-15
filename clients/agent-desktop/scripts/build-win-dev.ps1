[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$ApiBaseUrl
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$desktopRoot = Split-Path -Parent $PSScriptRoot
$repositoryRoot = (Resolve-Path (Join-Path $desktopRoot '..\..')).Path
$frontendRoot = Join-Path $repositoryRoot 'frontend'
$minimumNodeVersion = [Version]'22.12.0'

function Invoke-ExternalCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,

        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory
    )

    Write-Host "`n==> $Command $($Arguments -join ' ')" -ForegroundColor Cyan
    Push-Location $WorkingDirectory
    try {
        & $Command @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed with exit code ${LASTEXITCODE}: $Command $($Arguments -join ' ')"
        }
    }
    finally {
        Pop-Location
    }
}

function Install-LockedDependencies {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectDirectory
    )

    $lockFile = Join-Path $ProjectDirectory 'package-lock.json'
    if (-not (Test-Path -LiteralPath $lockFile -PathType Leaf)) {
        throw "package-lock.json not found: $ProjectDirectory"
    }

    Write-Host "Installing locked dependencies with npm ci: $ProjectDirectory" -ForegroundColor Yellow
    Invoke-ExternalCommand -Command 'npm.cmd' -Arguments @('ci') -WorkingDirectory $ProjectDirectory
}

try {
    if ([string]::IsNullOrWhiteSpace($ApiBaseUrl)) {
        $ApiBaseUrl = $env:AID_AGENT_PACKAGE_API_BASE_URL
    }
    if ([string]::IsNullOrWhiteSpace($ApiBaseUrl)) {
        throw 'API base URL is required. Pass -ApiBaseUrl or set AID_AGENT_PACKAGE_API_BASE_URL.'
    }
    $env:AID_AGENT_PACKAGE_API_BASE_URL = $ApiBaseUrl
    $nodeCommand = Get-Command node.exe -ErrorAction Stop
    $npmCommand = Get-Command npm.cmd -ErrorAction Stop
    $nodeVersionText = (& $nodeCommand.Source --version).Trim().TrimStart('v')
    $nodeVersion = [Version]$nodeVersionText
    if ($nodeVersion -lt $minimumNodeVersion) {
        throw "Node.js $minimumNodeVersion or newer is required; current version is $nodeVersion."
    }

    Write-Host "Node.js $nodeVersion (minimum $minimumNodeVersion)" -ForegroundColor Green
    Write-Host "npm $((& $npmCommand.Source --version).Trim())" -ForegroundColor Green

    Install-LockedDependencies -ProjectDirectory $frontendRoot
    Install-LockedDependencies -ProjectDirectory $desktopRoot

    Invoke-ExternalCommand -Command $npmCommand.Source -Arguments @('run', 'typecheck') -WorkingDirectory $desktopRoot
    Invoke-ExternalCommand -Command $npmCommand.Source -Arguments @('run', 'build') -WorkingDirectory $desktopRoot

    $testDirectory = Join-Path $desktopRoot 'dist\tests'
    $testFiles = @(Get-ChildItem -LiteralPath $testDirectory -Filter '*.test.js' -File -Recurse |
        Sort-Object FullName |
        ForEach-Object { $_.FullName })
    if ($testFiles.Count -eq 0) {
        throw "No compiled test files found in $testDirectory"
    }
    Invoke-ExternalCommand -Command $nodeCommand.Source -Arguments (@('--test') + $testFiles) -WorkingDirectory $desktopRoot

    Invoke-ExternalCommand -Command $nodeCommand.Source -Arguments @('scripts/package-win.mjs', 'dev') -WorkingDirectory $desktopRoot

    $releaseDirectory = Join-Path $desktopRoot 'release'
    Write-Host "`nBuild completed successfully: $releaseDirectory" -ForegroundColor Green
}
catch {
    Write-Error $_
    exit 1
}
