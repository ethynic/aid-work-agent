param(
    [Parameter(Mandatory)][string]$Query,
    [int]$TypeDelayMs = 350,
    [int]$ResultWaitMs = 1200,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$probeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $probeDir 'probe-lib.ps1')

# Kimi (Moonshot) vision config — kimi-k3 is a reasoning model: answer in content, temp must be 1/omitted
$apiKey = [string]$env:AID_WEIXIN_KIMI_API_KEY
if (-not $apiKey) { throw '未设置 AID_WEIXIN_KIMI_API_KEY（与 _common.ps1 一致的环境变量）；密钥不得写入脚本' }
$apiBase = 'https://api.moonshot.cn/v1/chat/completions'
$model = 'kimi-k3'

$sysPrompt = @'
You are a coordinate locator for WeChat desktop search.
Input image: a capture of the WeChat SEARCH RESULT PANEL window (the dropdown list that appears after typing in the search box). The panel content sits in a rounded-corner area; margins may be black (transparent areas).
Your task: find the item that best matches the given query word — prefer items under sections like 功能/联系人/群聊, NOT the "搜索网络结果" web-search suggestions. Return the center click coordinate of that item.
Coordinate origin = top-left corner of the image. Unit = pixel.
Return ONLY a single JSON object, no markdown, no explanation:
{"x": <int>, "y": <int>, "label": "<matched item text>", "found": <true|false>}
If there is no matching item, return:
{"x": 0, "y": 0, "label": "", "found": false}
'@

function Invoke-KimiVision([string]$ImagePath, [string]$Query) {
    $b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($ImagePath))
    $dataUrl = 'data:image/png;base64,' + $b64
    $userText = "Query word: $Query. Find the best matching item in the WeChat search result area and return its center coordinate as JSON."
    $payload = @{
        model = $model
        messages = @(
            @{ role = 'system'; content = $sysPrompt },
            @{ role = 'user'; content = @(
                @{ type = 'image_url'; image_url = @{ url = $dataUrl } },
                @{ type = 'text'; text = $userText }
            )}
        )
        max_tokens = 4096
    } | ConvertTo-Json -Depth 8
    $reqPath = Join-Path $env:TEMP 'weixin-probe-p1-vision-req.json'
    $utf8NoBom = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($reqPath, $payload, $utf8NoBom)
    $respPath = Join-Path $env:TEMP 'weixin-probe-p1-vision-resp.json'
    $curlArgs = @('-s', '-X', 'POST', $apiBase,
        '-H', "Authorization: Bearer $apiKey",
        '-H', 'Content-Type: application/json',
        '--data-binary', "@$reqPath",
        '-o', $respPath, '-w', '%{http_code}',
        '--max-time', '180')
    $http = & curl.exe @curlArgs
    if ($LASTEXITCODE -ne 0) { throw "VISION_CURL_FAILED exit=$LASTEXITCODE http=$http" }
    if ($http -ne '200') {
        $errBody = if (Test-Path $respPath) { Get-Content $respPath -Raw -Encoding UTF8 } else { '<no body>' }
        $errSnippet = $errBody.Substring(0, [Math]::Min(300, $errBody.Length))
        throw "VISION_HTTP_$http body=$errSnippet"
    }
    $respJson = Get-Content $respPath -Raw -Encoding UTF8
    $resp = $respJson | ConvertFrom-Json
    $content = [string]$resp.choices[0].message.content
    if ([string]::IsNullOrWhiteSpace($content)) {
        $content = [string]$resp.choices[0].message.reasoning_content
    }
    $m = [regex]::Match($content, '\{[^{}]*"x"[^{}]*\}')
    if (-not $m.Success) {
        $stripped = $content -replace '(?s)^```(?:json)?\s*', '' -replace '(?s)\s*```$', ''
        $m = [regex]::Match($stripped, '\{[^{}]*\}')
    }
    if (-not $m.Success) { throw "VISION_NO_JSON in: $content" }
    $rawPath = Join-Path $env:TEMP 'weixin-probe-p1-vision-raw.json'
    [IO.File]::WriteAllText($rawPath, $content, [Text.Encoding]::UTF8)
    Write-Host "[vision] raw response saved: $rawPath"
    $m.Value | ConvertFrom-Json
}

$mutex = New-Object Threading.Mutex($false, 'Local\AidWorkAgent.WeixinCliProbe.ChatSearchGroup', [ref]$null)
if (-not $mutex.WaitOne(0)) { throw 'PROBE_BUSY' }
try {
    Initialize-WeixinProbeWin32
    Set-WeixinProbeDpiContext

    $windows = @(Get-VisibleWindowList)
    $main = Select-WeixinMainWindow $windows
    $mainHwnd = [int64]$main.Hwnd
    Write-Host "[1] main hwnd=$mainHwnd"
    # 搜索面板（overlay）需要微信前台才会打开/保持，激活一次；
    # 之后 Kimi 定位期间若用户切走焦点导致面板关闭，会以 SEARCH_OVERLAY_NOT_FOUND 安全中止。
    if (-not (Invoke-WeixinActivation $mainHwnd)) { throw 'WX_ACTIVATION_FAILED' }
    Start-Sleep -Milliseconds 300

    # 2026-08-26 起全 PostMessage 化：点击搜索框（主窗口内固定位置）→ WM_CHAR 输入查询词
    # → PrintWindow 截 overlay 面板 → Kimi 在面板图上定位 → PostMessage 点击面板项。
    # 除激活动作外不占剪贴板、不依赖系统光标，RDP/用户正常用电脑均不受影响。
    $rect = New-Object WeixinProbeWin32+RECT
    [void][WeixinProbeWin32]::GetWindowRect([IntPtr]$mainHwnd, [ref]$rect)
    $winLeft = $rect.Left; $winTop = $rect.Top
    # 搜索框在主窗口左栏顶部，位置相对窗口固定
    Send-WeixinPostMessageClick -Hwnd $mainHwnd -ScreenX ($winLeft + 335) -ScreenY ($winTop + 110)
    Start-Sleep -Milliseconds 800
    Write-Host "[2] 已点击搜索框"

    # 清空残留查询词：点击搜索框时微信会自动全选残留文本，直接输入即覆盖；
    # 不要用退格清空——实测文本清空瞬间面板会自动关闭，且再输入不会重开。
    Send-WeixinPostMessageText -Hwnd $mainHwnd -Text $Query
    Write-Host "[3] typed: $Query"
    Start-Sleep -Milliseconds $ResultWaitMs

    # 搜索结果由独立 overlay 窗口渲染，直接截 overlay（PrintWindow，遮挡/非前台均可）
    $overlayHwnd = Get-WeixinSearchOverlayHwnd
    if ($overlayHwnd -eq 0) { throw 'SEARCH_OVERLAY_NOT_FOUND' }
    Add-Type -AssemblyName System.Drawing
    $shotPath = Join-Path $env:TEMP 'weixin-probe-p1-vision-shot.png'
    $snap = Get-WeixinWindowSnapshot $overlayHwnd $shotPath
    $ovlLeft = [int]$snap[0]; $ovlTop = [int]$snap[1]
    Write-Host "[4] shot(overlay): $shotPath rect=($ovlLeft,$ovlTop,$($snap[2])x$($snap[3])) printwindow=$($snap[4])"

    $loc = Invoke-KimiVision $shotPath $Query
    Write-Host "[5] vision result: x=$($loc.x) y=$($loc.y) found=$($loc.found) label='$($loc.label)'"
    if (-not $loc.found -or ([int]$loc.x -eq 0 -and [int]$loc.y -eq 0)) {
        throw "VISION_NO_MATCH (found=$($loc.found))"
    }
    $centerPngX = [int]$loc.x
    $centerPngY = [int]$loc.y
    # 截图是 overlay 窗口，坐标换算基于 overlay 的屏幕位置
    $screenX = $ovlLeft + $centerPngX
    $screenY = $ovlTop + $centerPngY
    Write-Host "[6] png=($centerPngX,$centerPngY) -> screen=($screenX,$screenY)"

    $bmp2 = [Drawing.Image]::FromFile($shotPath)
    $g2 = [Drawing.Graphics]::FromImage($bmp2)
    $pen = New-Object Drawing.Pen ([Drawing.Color]::Red, 3)
    $g2.DrawEllipse($pen, $centerPngX-10, $centerPngY-10, 20, 20)
    $g2.DrawLine($pen, $centerPngX-15, $centerPngY, $centerPngX+15, $centerPngY)
    $g2.DrawLine($pen, $centerPngX, $centerPngY-15, $centerPngX, $centerPngY+15)
    $annotated = Join-Path $env:TEMP 'weixin-probe-p1-vision-annotated.png'
    $bmp2.Save($annotated, [Drawing.Imaging.ImageFormat]::Png)
    $g2.Dispose(); $bmp2.Dispose()
    Write-Host "[7] annotated: $annotated"

    if ($DryRun) { Write-Host "PROBE_RESULT: DRY_RUN"; return }

    # PostMessage 投递点击到 overlay 面板窗口（光标无关、不要求前台）
    Send-WeixinPostMessageClick -Hwnd $overlayHwnd -ScreenX $screenX -ScreenY $screenY
    Write-Host "[8] postmessage-clicked ($screenX, $screenY) overlay=$overlayHwnd"
    Start-Sleep -Milliseconds 1000

    $overlayGone = $true
    foreach ($wd in @(Get-VisibleWindowList)) {
        $id = Get-WindowIdentityByHwnd ([int64]$wd.Hwnd)
        if ($null -ne $id -and [string]$id.ClassName -eq 'Qt51514QWindowToolSaveBits' -and
            (Test-WeixinExecutablePath ([string]$id.ProcessPath))) { $overlayGone = $false }
    }
    Write-Host "[9] overlay_gone: $overlayGone"
    if ($overlayGone) { Write-Host "PROBE_RESULT: SUCCESS" } else { Write-Host "PROBE_RESULT: OVERLAY_STILL_OPEN" }
} finally {
    [void]$mutex.ReleaseMutex(); $mutex.Dispose()
}
