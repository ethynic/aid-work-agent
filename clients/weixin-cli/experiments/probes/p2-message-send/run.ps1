param(
    [string]$Message = '你好',
    [switch]$DryRun,
    [switch]$LocateOnly
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$probeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $probeDir '..\p1-chat-search-group\probe-lib.ps1')

# Kimi (Moonshot) vision config
$apiKey = [string]$env:AID_WEIXIN_KIMI_API_KEY
if (-not $apiKey) { throw '未设置 AID_WEIXIN_KIMI_API_KEY（与 _common.ps1 一致的环境变量）；密钥不得写入脚本' }
$apiBase = 'https://api.moonshot.cn/v1/chat/completions'
$model = 'kimi-k3'

$sysPrompt = @'
You are a coordinate locator for WeChat desktop chat window.
Input image: a screenshot of WeChat main window with a chat conversation open.
Locate TWO elements and return their CENTER click coordinates:
1. "input_box": the text input area at the bottom where you type a message
2. "send_button": the send button (usually bottom-right, labeled "发送" / Send)
Coordinate origin = top-left corner of the screenshot. Unit = pixel.
Return ONLY a single JSON object, no markdown, no explanation:
{"input_box": {"x": <int>, "y": <int>}, "send_button": {"x": <int>, "y": <int>}, "input_empty": <true|false>, "found": <true|false>}
"input_empty": whether the input box currently contains NO draft text (empty = true).
If either element cannot be located, return {"input_box":{"x":0,"y":0},"send_button":{"x":0,"y":0},"input_empty":false,"found":false}.
Coordinates are INSIDE the screenshot image (not the screen).
'@

function Invoke-KimiVision([string]$ImagePath) {
    $b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($ImagePath))
    $dataUrl = 'data:image/png;base64,' + $b64
    $payload = @{
        model = $model
        messages = @(
            @{ role = 'system'; content = $sysPrompt },
            @{ role = 'user'; content = @(
                @{ type = 'image_url'; image_url = @{ url = $dataUrl } },
                @{ type = 'text'; text = 'Locate the input box and send button in this WeChat chat window. Return JSON.' }
            )}
        )
        max_tokens = 4096
    } | ConvertTo-Json -Depth 8
    $reqPath = Join-Path $env:TEMP 'weixin-probe-p2-vision-req.json'
    $utf8NoBom = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($reqPath, $payload, $utf8NoBom)
    $respPath = Join-Path $env:TEMP 'weixin-probe-p2-vision-resp.json'
    $curlArgs = @('-s', '-X', 'POST', $apiBase,
        '-H', "Authorization: Bearer $apiKey",
        '-H', 'Content-Type: application/json',
        '--data-binary', "@$reqPath",
        '-o', $respPath, '-w', '%{http_code}', '--max-time', '180')
    $http = $null
    for ($attempt = 1; $attempt -le 4; $attempt++) {
        $http = & curl.exe @curlArgs
        if ($LASTEXITCODE -ne 0) { throw "VISION_CURL_FAILED exit=$LASTEXITCODE http=$http" }
        if ($http -eq '200') { break }
        $errBody = if (Test-Path $respPath) { Get-Content $respPath -Raw -Encoding UTF8 } else { '<no body>' }
        if ($http -eq '429' -and $attempt -lt 4) {
            Write-Host ("[vision] HTTP 429 (attempt {0}/4), retrying in 25s..." -f $attempt)
            Start-Sleep -Seconds 25
            continue
        }
        throw "VISION_HTTP_$http body=$($errBody.Substring(0, [Math]::Min(300, $errBody.Length)))"
    }
    $resp = (Get-Content $respPath -Raw -Encoding UTF8) | ConvertFrom-Json
    $content = [string]$resp.choices[0].message.content
    if ([string]::IsNullOrWhiteSpace($content)) { $content = [string]$resp.choices[0].message.reasoning_content }
    $m = [regex]::Match($content, '\{[^{}]*"input_box"[^{}]*\}')
    if (-not $m.Success) {
        $stripped = $content -replace '(?s)^```(?:json)?\s*', '' -replace '(?s)\s*```$', ''
        $m = [regex]::Match($stripped, '\{[\s\S]*\}')
    }
    if (-not $m.Success) { throw "VISION_NO_JSON in: $content" }
    [IO.File]::WriteAllText((Join-Path $env:TEMP 'weixin-probe-p2-vision-raw.json'), $content, [Text.Encoding]::UTF8)
    $m.Value | ConvertFrom-Json
}

function Click-ScreenPoint([int]$pngX, [int]$pngY, [int]$winLeft, [int]$winTop, $mainGuard) {
    # 2026-08-26 起改用 PostMessage 投递点击（Send-WeixinPostMessageClick）：
    # RDP 会话下客户端指针位置是权威值，SetCursorPos+mouse_event 的 down/up 会被拆到
    # 不同窗口导致点击落空/误点；PostMessage 光标无关、不要求前台，正常桌面同样适用。
    # mainGuard 参数保留仅为兼容调用方，不再需要前台守卫。
    $screenX = $winLeft + $pngX
    $screenY = $winTop + $pngY
    if ($script:WeixinMainHwndForClick -eq 0) { throw 'MAIN_HWND_NOT_SET' }
    Send-WeixinPostMessageClick -Hwnd $script:WeixinMainHwndForClick -ScreenX $screenX -ScreenY $screenY
    Write-Host "      postmessage-clicked screen ($screenX, $screenY)"
    Start-Sleep -Milliseconds 300
}

$mutex = New-Object Threading.Mutex($false, 'Local\AidWorkAgent.WeixinCliProbe.MessageSend', [ref]$null)
if (-not $mutex.WaitOne(0)) { throw 'PROBE_BUSY' }
try {
    Initialize-WeixinProbeWin32
    Set-WeixinProbeDpiContext

    $windows = @(Get-VisibleWindowList)
    $main = Select-WeixinMainWindow $windows
    $mainHwnd = [int64]$main.Hwnd
    $script:WeixinMainHwndForClick = $mainHwnd
    Write-Host "[1] main hwnd=$mainHwnd"
    if (-not (Invoke-WeixinActivation $mainHwnd)) { throw 'WX_ACTIVATION_FAILED' }
    $mainGuard = { [WeixinProbeWin32]::GetForegroundWindow().ToInt64() -eq $mainHwnd }
    if (-not (& $mainGuard)) { throw 'MAIN_NOT_FOREGROUND' }
    Write-Host "[1] 当前微信打开的会话即测试目标（请确认目标正确）"

    # 截图（PrintWindow 优先，窗口被遮挡/非前台也能出图；失败回退 CopyFromScreen）
    Add-Type -AssemblyName System.Drawing
    $shotPath = Join-Path $env:TEMP 'weixin-probe-p2-shot.png'
    $snap = Get-WeixinWindowSnapshot $mainHwnd $shotPath
    $winLeft = [int]$snap[0]; $winTop = [int]$snap[1]; $w = [int]$snap[2]; $h = [int]$snap[3]
    Write-Host ("[2] shot: {0} rect=({1},{2},{3}x{4}) printwindow={5}" -f $shotPath,$winLeft,$winTop,$w,$h,$snap[4])

    # Kimi 定位输入框 + 发送按钮
    $loc = Invoke-KimiVision $shotPath
    Write-Host "[3] vision found=$($loc.found) input_empty=$($loc.input_empty)"
    if (-not $loc.found) { throw 'VISION_NOT_FOUND' }
    # 输入框有残留草稿时禁止直接输入（文本会追加到草稿后面），需人工清空后重跑
    if ($loc.input_empty -eq $false) { throw 'INPUT_NOT_EMPTY' }
    $inboxX = [int]$loc.input_box.x; $inboxY = [int]$loc.input_box.y
    $sendX = [int]$loc.send_button.x; $sendY = [int]$loc.send_button.y
    Write-Host ("[3] input_box png=({0},{1}) screen=({2},{3})" -f $inboxX,$inboxY,($winLeft+$inboxX),($winTop+$inboxY))
    Write-Host ("[3] send_button png=({0},{1}) screen=({2},{3})" -f $sendX,$sendY,($winLeft+$sendX),($winTop+$sendY))

    # 标注图：两个目标都画十字
    $bmp2 = [Drawing.Image]::FromFile($shotPath)
    $g2 = [Drawing.Graphics]::FromImage($bmp2)
    $redPen = New-Object Drawing.Pen ([Drawing.Color]::Red, 3)
    $bluePen = New-Object Drawing.Pen ([Drawing.Color]::Blue, 3)
    foreach ($p in @(@($inboxX,$inboxY,$redPen,'input'), @($sendX,$sendY,$bluePen,'send'))) {
        $px=$p[0]; $py=$p[1]; $pen=$p[2]
        $g2.DrawEllipse($pen, $px-10, $py-10, 20, 20)
        $g2.DrawLine($pen, $px-15, $py, $px+15, $py)
        $g2.DrawLine($pen, $px, $py-15, $px, $py+15)
    }
    $annotated = Join-Path $env:TEMP 'weixin-probe-p2-annotated.png'
    $bmp2.Save($annotated, [Drawing.Imaging.ImageFormat]::Png)
    $g2.Dispose(); $bmp2.Dispose()
    Write-Host ("[4] annotated (red=input_box, blue=send_button): {0}" -f $annotated)

    if ($LocateOnly -or $DryRun) { Write-Host "PROBE_RESULT: LOCATE_DONE"; return }

    Write-Host "[5] 即将执行：点击输入框 -> 粘贴 '$Message' -> 回车发送。3秒后开始，Ctrl+C 取消"
    Start-Sleep -Seconds 3

    # 5a 点击输入框聚焦
    Write-Host "[5a] 点击输入框"
    Click-ScreenPoint $inboxX $inboxY $winLeft $winTop $mainGuard

    # 5b 文本输入：PostMessage WM_CHAR 逐字投递，不经剪贴板/物理键盘，
    # 不要求微信是前台窗口（RDP 会话、用户正在其他窗口打字均不受影响）。
    # 前置保障：步骤 [3] 已用视觉确认输入框无残留草稿（input_empty=true）。
    Send-WeixinPostMessageText -Hwnd $mainHwnd -Text $Message
    Start-Sleep -Milliseconds 400
    Write-Host "[5b] 已投递文本 '$Message'"

    # 5c 回车发送：PostMessage WM_KEYDOWN/UP Return（视觉定位的"发送按钮"曾被误识为
    # 语音通话按钮，点击不可靠；Enter 是微信最稳定的发送路径）。
    Write-Host "[5c] 回车发送（PostMessage Enter）"
    Send-WeixinPostMessageKey -Hwnd $mainHwnd -Vk 0x0D -ScanCode 0x1C
    Start-Sleep -Milliseconds 800

    # 6 发送后截图，Kimi 二次确认最后一条消息是否为 $Message
    $afterPath = Join-Path $env:TEMP 'weixin-probe-p2-after-send.png'
    Get-WeixinWindowSnapshot $mainHwnd $afterPath | Out-Null
    Write-Host "[6] after-send shot: $afterPath"
    Write-Host "PROBE_RESULT: SENT (请人工核对 $afterPath 最后一条消息是否为 '$Message')"
} finally {
    [void]$mutex.ReleaseMutex(); $mutex.Dispose()
}
