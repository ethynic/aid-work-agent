$ErrorActionPreference = 'Stop'
$nativeRoot = $PSScriptRoot
$compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework/v4.0.30319/csc.exe'
}
if (-not (Test-Path -LiteralPath $compiler)) { throw 'NET_FRAMEWORK_COMPILER_MISSING' }
$outputRoot = Join-Path $nativeRoot 'build'
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$outputExe = Join-Path $outputRoot 'RuntimeProcessSupervisor.exe'
$source = Join-Path $nativeRoot 'RuntimeProcessSupervisor.cs'
& $compiler /nologo /target:exe /platform:anycpu /optimize+ /reference:System.Security.dll "/out:$outputExe" $source
if ($LASTEXITCODE -ne 0) { throw 'SUPERVISOR_BUILD_FAILED' }
