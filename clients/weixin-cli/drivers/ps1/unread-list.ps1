# drivers/ps1/unread-list.ps1 — weixin_unread_list 驱动（只读，不开会话不清未读）
# PrintWindow 截主窗口（不要求前台，被遮挡也能出图）→ venv python 跑 RapidOCR
# （drivers/py/unread_list.py，只取窗口宽 29% 以左的会话列表区）→
# 聚合 {name, preview, unread_count} → DRIVER_JSON 输出 unread。
# 单栏模式（无左侧会话列表）→ UI_CHANGED（message 说明需恢复两栏）。
# 注意：本文件含中文，必须以 UTF-8 with BOM 保存。
param()
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$driverDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $driverDir '_common.ps1')

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WeixinCli.UnreadList' -Body {
    # 不激活窗口：只读取会话列表，不需要前台
    $mainHwnd = Resolve-WeixinMainWindowHwnd
    $shotPath = Join-Path $env:TEMP 'weixin-driver-unread-list.png'
    Get-WeixinWindowSnapshot $mainHwnd $shotPath | Out-Null

    # venv 里的 python（rapidocr_onnxruntime 装在其中）；drivers/ps1 上四级为仓库根
    $repoRoot = (Resolve-Path (Join-Path $script:DriverPs1Dir '..\..\..\..')).Path
    $pythonExe = Join-Path $repoRoot 'venv\Scripts\python.exe'
    $ocrScript = Join-Path $script:DriverPs1Dir '..\py\unread_list.py'
    if (-not (Test-Path $pythonExe)) { Throw-DriverError 'INTERNAL_ERROR' ("未找到 venv python：" + $pythonExe) }
    if (-not (Test-Path $ocrScript)) { Throw-DriverError 'INTERNAL_ERROR' ("未找到 OCR 脚本：" + $ocrScript) }

    $out = & $pythonExe $ocrScript $shotPath 2>$null
    if ($LASTEXITCODE -ne 0) { Throw-DriverError 'INTERNAL_ERROR' ("OCR 脚本执行失败（exit=$LASTEXITCODE）") }
    $jsonLine = @($out | Where-Object { $_ -match '^UNREAD_JSON:' }) | Select-Object -Last 1
    if (-not $jsonLine) { Throw-DriverError 'INTERNAL_ERROR' 'OCR 脚本未输出 UNREAD_JSON' }
    $parsed = ($jsonLine -replace '^UNREAD_JSON:\s*', '') | ConvertFrom-Json
    if ($parsed.error -eq 'SINGLE_COLUMN') {
        Throw-DriverError 'UI_CHANGED' '微信主窗口当前为单栏模式（无左侧会话列表），请先手动恢复两栏布局后重试'
    }
    $unread = @($parsed.unread | ForEach-Object {
        @{ name = [string]$_.name; preview = [string]$_.preview; unread_count = [int]$_.unread_count }
    })
    return @{ unread = $unread }
}
