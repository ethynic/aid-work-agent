# drivers/ps1/chat-search.ps1 — wecom_chat_search 驱动（只读，不打开会话不读历史）
# 流程（2026-08-31 真机实测修订）：Open-WeComSearchOverlay 全链路（解析主窗口 →
# 点搜索框 → 清空上次查询残留 → 输入 query → OCR 回读验证框内文本 → 等
# SearchResultWindow2 → 轮询等渲染稳定后以稳定帧 OCR 为终态，无结果返回空 items）→
# 关闭 overlay + 清空搜索框残留恢复原状（best-effort，finally）→ DRIVER_JSON 输出 items。
# target_ref 签发在 TS 侧完成（驱动只回 name/subtitle/section）。
# 注意：本文件含中文，必须以 UTF-8 with BOM 保存。
param(
    [Parameter(Mandatory)][string]$Query
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$driverDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $driverDir '_common.ps1')

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WecomCli.ChatSearch' -Body {
    $s = Open-WeComSearchOverlay -Query $Query
    $mainHwnd = [int64]$s.MainHwnd
    $overlayHwnd = [int64]$s.OverlayHwnd
    try {
        $ocr = $s.Ocr
    } finally {
        # 只读操作必须恢复原状：无论成败都尽力关闭搜索 overlay + 清空搜索框残留
        Close-WeComSearchOverlay -OverlayHwnd $overlayHwnd -MainHwnd $mainHwnd
    }
    $items = @($ocr.items | ForEach-Object {
        @{ name = [string]$_.name; subtitle = [string]$_.subtitle; section = [string]$_.section }
    })
    return @{ items = $items }
}
