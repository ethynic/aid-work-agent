#requires -Version 5.1
<#
.SYNOPSIS
  本地启动 Client.App（冒烟：看托盘图标、状态窗口、日志是否正常）。
  Client.App 已注入真实视觉定位实现（QwenVisionLocator + ScreenCapturer + WeComAutomation），
  但仍需要真实企微 PC 客户端 + QWEN_API_KEYS 环境变量才能完整跑通端到端。
  无企微环境时启动仅验证 GUI 骨架健康（托盘图标/状态窗口/日志）。
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
