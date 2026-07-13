#requires -Version 5.1
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent

$removedPaths = @(
    'scripts\build-msi.ps1',
    'scripts\build-and-install.ps1',
    'scripts\install-now.ps1',
    'docs\build-install-guide.md',
    'installer\wix'
)

foreach ($relativePath in $removedPaths) {
    $path = Join-Path $root $relativePath
    if (Test-Path $path) {
        throw "Deprecated installer path still exists: $relativePath"
    }
}

$forbiddenExtensions = Get-ChildItem $root -Recurse -File |
    Where-Object { $_.Extension -in @('.wxs', '.wixproj', '.msi') }
if ($forbiddenExtensions) {
    throw "Installer project or artifact still exists: $($forbiddenExtensions.FullName -join ', ')"
}

$publishScript = Get-Content (Join-Path $PSScriptRoot 'publish.ps1') -Raw
foreach ($required in @('Client.App.csproj', 'PublishSingleFile=true', 'wecom-ops.ps1', 'wecom-ops-lib.ps1')) {
    if ($publishScript -notmatch [regex]::Escape($required)) {
        throw "publish.ps1 is missing required EXE delivery content: $required"
    }
}

$activeDocs = @(
    (Join-Path $root 'README.md'),
    (Join-Path $root '..\..\docs\system\wecom-personal-rpa-design.md'),
    (Join-Path $root '..\..\docs\system\wecom-personal-rpa-portal-binding-design.md'),
    (Join-Path $root '..\..\plans\plan-wecom-personal-rpa.md')
)
foreach ($docPath in $activeDocs) {
    $docContent = Get-Content $docPath -Raw
    if ($docContent -match '(?i)build-msi|build-install-guide|installer[\\/]wix|WiX\s*\/\s*MSIX|MSIX\s*\/\s*WiX') {
        throw "Active documentation still references the deprecated installer flow: $docPath"
    }
}

Write-Host '[OK] Only EXE build/publish delivery paths remain.'
