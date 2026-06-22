#requires -Version 5.1
<#
.SYNOPSIS
  运行客户端单元测试（StateManagerTests/HmacSignerTests/SqliteSendQueueTests/TokenBucketTests/ActionLocatorTests）。
.EXAMPLE
  powershell scripts\test.ps1
#>
[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')][string]$Configuration = 'Debug'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$proj = Join-Path $root 'src\Client.Tests\Client.Tests.csproj'

Write-Host "==> 单元测试 $proj ($Configuration)"
dotnet test $proj -c $Configuration --nologo --logger "console;verbosity=normal"
if ($LASTEXITCODE -ne 0) { Write-Error "测试失败 (exit $LASTEXITCODE)"; exit $LASTEXITCODE }
Write-Host "[OK] 测试完成"
