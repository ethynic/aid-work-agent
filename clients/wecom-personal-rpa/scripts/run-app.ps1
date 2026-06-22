#requires -Version 5.1
<#
.SYNOPSIS
  本地启动 Client.App（冒烟：看托盘图标、状态窗口、日志是否正常）。
  注意：当前 Client.App 注入的是 Stubs/AutomationStubs.cs 桩，仅验证骨架健康，
  不会真实操作企业微信。见 STATUS.md ③。
.EXAMPLE
  powershell scripts\run-app.ps1
#>
[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')][string]$Configuration = 'Debug'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent

Write-Host "==> 启动 Client.App ($Configuration)"
Write-Host "    预期：系统托盘出现图标；状态窗口可打开。"
Write-Host "    日志：%LOCALAPPDATA%\WeComPersonalRpa\Client.App\logs\"
Write-Host "    [!] 当前自动化为桩，不会真实操作企业微信（STATUS.md ③）。"
dotnet run --project (Join-Path $root 'src\Client.App\Client.App.csproj') -c $Configuration --nologo
