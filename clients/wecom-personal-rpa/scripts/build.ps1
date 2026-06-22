#requires -Version 5.1
<#
.SYNOPSIS
  编译 WeComPersonalRpaClient 解决方案。
.EXAMPLE
  powershell scripts\build.ps1 -Configuration Release
#>
[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')][string]$Configuration = 'Debug'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$solution = Join-Path $root 'WeComPersonalRpaClient.sln'

Write-Host "==> 编译 $solution ($Configuration)"
dotnet build $solution -c $Configuration --nologo
if ($LASTEXITCODE -ne 0) { Write-Error "构建失败 (exit $LASTEXITCODE)"; exit $LASTEXITCODE }
Write-Host "[OK] 构建成功 ($Configuration)"
