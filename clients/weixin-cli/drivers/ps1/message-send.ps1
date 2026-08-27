# drivers/ps1/message-send.ps1 — weixin_message_send 驱动（写动作）
# 全链路：Open-WeixinChat（搜索目标 → Kimi 定位 → PostMessage 点击 overlay →
# 截主窗口 → Kimi 校验标题 + 输入框无残留草稿，不一致一律 UI_CHANGED 中止）→
# PostMessage 点击输入框 → WM_CHAR 输入 text → PostMessage 回车（Vk 0x0D）→
# 截主窗口 → Kimi 校验最后一条消息 == text，校验不过 → EXECUTION_UNKNOWN 且绝不重试。
# 注意：本文件含中文，必须以 UTF-8 with BOM 保存。
param(
    [Parameter(Mandatory)][string]$TargetName,
    [Parameter(Mandatory)][string]$Text
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$driverDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $driverDir '_common.ps1')

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WeixinCli.MessageSend' -Body {
    $chat = Open-WeixinChat -TargetName $TargetName -RequireEmptyInput
    $mainHwnd = [int64]$chat.MainHwnd

    # 点击输入框聚焦 → WM_CHAR 逐字输入（中文 OK，PostMessage 为 Unicode 版）→ 回车发送。
    # Enter 是微信最稳定的发送路径（视觉定位的"发送按钮"曾被误识为语音通话按钮）。
    Send-WeixinPostMessageClick -Hwnd $mainHwnd -ScreenX ($chat.WinLeft + [int]$chat.InputBoxX) -ScreenY ($chat.WinTop + [int]$chat.InputBoxY)
    Start-Sleep -Milliseconds 300
    Send-WeixinPostMessageText -Hwnd $mainHwnd -Text $Text
    Start-Sleep -Milliseconds 400
    Send-WeixinPostMessageKey -Hwnd $mainHwnd -Vk 0x0D -ScanCode 0x1C
    Start-Sleep -Milliseconds 1200

    # 发送后校验：截主窗口，Kimi 确认最后一条消息与发送文本一致；
    # 校验失败 → EXECUTION_UNKNOWN，绝不重试（写动作 unknown 语义）。
    $afterShot = Join-Path $env:TEMP 'weixin-driver-message-send-after.png'
    Get-WeixinWindowSnapshot $mainHwnd $afterShot | Out-Null
    $verifySys = @'
You are a message verifier for WeChat desktop chat window.
Input image: a screenshot of WeChat main window with a chat conversation open, taken right after sending a message.
Task: look at the LAST (bottom-most) chat message in the conversation and compare its text with the expected text.
Return ONLY a single JSON object, no markdown, no explanation:
{"sent_ok": <true|false>, "last_message": "<text of the last message>"}
sent_ok = true only if the last message text exactly matches the expected text.
'@
    $v = Invoke-KimiVision -ImagePath $afterShot -SystemPrompt $verifySys -UserText ("Expected message text: $Text. Does the last chat message match it exactly? Return JSON.") -ArtifactPrefix 'weixin-driver-message-send-verify'
    if ($v.sent_ok -ne $true) {
        Throw-DriverError 'EXECUTION_UNKNOWN' ("发送后校验失败：最后一条消息（「" + [string]$v.last_message + "」）与待发送文本不一致；消息可能已发出，不会自动重试，请人工核对")
    }
    return @{ target = $TargetName; title = [string]$chat.Title; verified = $true }
}
