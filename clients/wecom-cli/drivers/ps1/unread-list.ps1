# drivers/ps1/unread-list.ps1 — wecom_unread_list 驱动（只读，不开会话不清未读）
# PrintWindow 截主窗口（不要求前台，被遮挡也能出图）→ venv python 跑 chat_ocr.py
# unread 模式（会话列表列 x∈[0.16w,0.38w]，红色角标像素检测 + 裁切放大 OCR 读数）
# → 聚合 {name, preview, unread_count, x, y}（x/y = 名称行中心，图像坐标系，
# watch 直接点会话行用）→ DRIVER_JSON 输出 unread。无未读会话 → 空数组（合法结果）。
# 注意：本文件含中文，必须以 UTF-8 with BOM 保存。
param()
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$driverDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $driverDir '_common.ps1')

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WecomCli.UnreadList' -Body {
    # 不激活窗口：只读取会话列表，不需要前台
    $mainHwnd = Resolve-WeComMainWindow
    $shotPath = Join-Path $env:TEMP 'wecom-driver-unread-list.png'
    Get-WeComWindowSnapshot -Hwnd $mainHwnd -Path $shotPath | Out-Null

    $ocr = Invoke-WeComChatOcr -ImagePath $shotPath -Mode 'unread'
    $unread = @($ocr.unread | ForEach-Object {
        @{
            name = [string]$_.name
            preview = [string]$_.preview
            unread_count = [int]$_.unread_count
            x = [int]$_.x
            y = [int]$_.y
        }
    })
    return @{ unread = $unread }
}
