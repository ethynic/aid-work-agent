# drivers/ps1/chat-search.ps1 — weixin_chat_search 驱动（只读）
# 激活微信主窗口 → PostMessage 点击搜索框 → WM_CHAR 输入 query → 等 1.5s →
# 找搜索结果 overlay 并 PrintWindow 截图到 %TEMP% → Kimi 视觉返回结果列表 →
# 关闭面板 → DRIVER_JSON 输出 items[{label, section}]。
# 无结果 → TARGET_NOT_FOUND；找不到 overlay / 单栏布局 → UI_CHANGED。
# 注意：本文件含中文，必须以 UTF-8 with BOM 保存。
param(
    [Parameter(Mandatory)][string]$Query,
    [ValidateSet('friend', 'group', 'any')][string]$Type = 'any',
    [ValidateRange(1, 20)][int]$Limit = 10
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$driverDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $driverDir '_common.ps1')

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WeixinCli.ChatSearch' -Body {
    $mainHwnd = Resolve-WeixinMainWindowHwnd
    # 搜索面板必须前台才会打开/保持；Kimi 抽取期间用户切走焦点面板会关闭 → UI_CHANGED 安全中止
    if (-not (Invoke-WeixinActivation $mainHwnd)) { Throw-DriverError 'FOREGROUND_LOST' '无法将微信主窗口激活到前台（搜索面板必须前台才会打开）' }
    Start-Sleep -Milliseconds 300
    Assert-WeixinTwoColumnLayout $mainHwnd

    $rect = New-Object WeixinProbeWin32+RECT
    [void][WeixinProbeWin32]::GetWindowRect([IntPtr]$mainHwnd, [ref]$rect)
    # 搜索框固定相对位置（两栏布局下）；点击时微信自动全选残留文本，直接输入即覆盖；
    # 绝对不要用退格清空搜索框（清空瞬间面板自动关闭且再输入不重开）。
    Send-WeixinPostMessageClick -Hwnd $mainHwnd -ScreenX ($rect.Left + 335) -ScreenY ($rect.Top + 110)
    Start-Sleep -Milliseconds 800
    Send-WeixinPostMessageText -Hwnd $mainHwnd -Text $Query
    Start-Sleep -Milliseconds 1500

    $overlayHwnd = Get-WeixinSearchOverlayHwnd
    if ($overlayHwnd -eq 0) { Throw-DriverError 'UI_CHANGED' '未找到搜索结果面板（前台被切走会关闭面板，或布局已变化）' }
    $shotPath = Join-Path $env:TEMP 'weixin-driver-chat-search-overlay.png'
    Get-WeixinWindowSnapshot $overlayHwnd $shotPath | Out-Null

    # 注意：「最常使用」分组同时可能含好友和群（2026-08-27 实测哈尼群在该分组），
    # 类型不能按分组名过滤，要靠头像形态（拼图头像=群，单头像=好友）逐项判断。
    $listSys = @"
You are a result-list extractor for WeChat desktop search.
Input image: a capture of the WeChat SEARCH RESULT PANEL window (the dropdown list that appears after typing in the search box). Margins may be black (transparent areas).
Task: list all OPENABLE CHAT TARGETS in the panel, in top-to-bottom order. Include items under sections such as 最常使用/功能/联系人/群聊. EXCLUDE everything under 聊天记录 (history matches) and 搜索网络结果 (web-search suggestions).
For each item return:
- label: the item display text
- section: the section header it belongs to (最常使用/功能/联系人/群聊/other)
- kind: "group" if the avatar is a grid of multiple small avatars (a group chat), "friend" if it is a single-person avatar, "other" otherwise (e.g. a function icon like 文件传输助手)
Return ONLY a single JSON object, no markdown, no explanation:
{"items": [{"label": "<item text>", "section": "<section>", "kind": "group|friend|other"}]}
If the panel has no such items, return {"items": []}.
"@
    $r = Invoke-KimiVision -ImagePath $shotPath -SystemPrompt $listSys -UserText ("Query word: $Query. Extract the full result list as JSON.") -ArtifactPrefix 'weixin-driver-chat-search'

    # 关闭面板（best-effort，不影响结果正确性）
    Close-WeixinSearchOverlay $overlayHwnd

    $items = @($r.items)
    if ($Type -eq 'friend') { $items = @($items | Where-Object { [string]$_.kind -eq 'friend' }) }
    elseif ($Type -eq 'group') { $items = @($items | Where-Object { [string]$_.kind -eq 'group' }) }
    if ($items.Count -eq 0) { Throw-DriverError 'TARGET_NOT_FOUND' ("搜索「" + $Query + "」没有匹配结果") }
    if ($items.Count -gt $Limit) { $items = @($items[0..($Limit - 1)]) }
    $out = @($items | ForEach-Object { @{ label = [string]$_.label; section = [string]$_.section; kind = [string]$_.kind } })
    return @{ query = $Query; type = $Type; items = $out }
}
