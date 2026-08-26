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
$apiKey = 'sk-Eqn3ctEHy5mJWCwmEl9IGxERm9TZiHcq6LKII6xbkglxVsTc'
$apiBase = 'https://api.moonshot.cn/v1/chat/completions'
$model = 'kimi-k3'

$sysPrompt = @'
You are a coordinate locator for WeChat desktop search.
Input image: a screenshot of WeChat search results (searching for a contact or group).
Your task: in the SEARCH RESULT AREA (the list that appears below the search box after typing, NOT the main chat list on the right), find the item that best matches the given query word. Return the center click coordinate of that item.
Coordinate origin = top-left corner of the screenshot. Unit = pixel.
Return ONLY a single JSON object, no markdown, no explanation:
{"x": <int>, "y": <int>, "label": "<matched item text>", "found": <true|false>}
If the search result area has no matching item, return:
{"x": 0, "y": 0, "label": "", "found": false}
The x and y are coordinates INSIDE the screenshot image (not the screen).
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
    if (-not (Invoke-WeixinActivation $mainHwnd)) { throw 'WX_ACTIVATION_FAILED' }
    $mainGuard = { [WeixinProbeWin32]::GetForegroundWindow().ToInt64() -eq $mainHwnd }
    if (-not (& $mainGuard)) { throw 'MAIN_NOT_FOREGROUND' }

    Send-WeixinKeyChord @('CTRL', 'F') $mainGuard
    Start-Sleep -Milliseconds 500
    Send-WeixinKeyChord @('CTRL', 'A') $mainGuard; Start-Sleep -Milliseconds 120
    Send-WeixinKeyChord @('DEL') $mainGuard; Start-Sleep -Milliseconds 200
    Write-Host "[2] Ctrl+F + clear"

    $script:originalClipboard = Get-ClipboardTextSafe
    foreach ($ch in $Query.ToCharArray()) {
        Send-WeixinPasteText ([string]$ch) $mainGuard
        Start-Sleep -Milliseconds $TypeDelayMs
    }
    Write-Host "[3] typed: $Query"
    Start-Sleep -Milliseconds $ResultWaitMs
    if (-not (& $mainGuard)) { throw 'FOREGROUND_LOST' }

    $rect = New-Object WeixinProbeWin32+RECT
    [void][WeixinProbeWin32]::GetWindowRect([IntPtr]$mainHwnd, [ref]$rect)
    $winLeft = $rect.Left; $winTop = $rect.Top
    $w = $rect.Right - $rect.Left; $h = $rect.Bottom - $rect.Top
    Add-Type -AssemblyName System.Drawing
    $bmp = New-Object Drawing.Bitmap $w, $h
    $g = [Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($winLeft, $winTop, 0, 0, (New-Object Drawing.Size $w, $h))
    $shotPath = Join-Path $env:TEMP 'weixin-probe-p1-vision-shot.png'
    $bmp.Save($shotPath, [Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $bmp.Dispose()
    Write-Host "[4] shot: $shotPath rect=($winLeft,$winTop,${w}x${h})"

    $loc = Invoke-KimiVision $shotPath $Query
    Write-Host "[5] vision result: x=$($loc.x) y=$($loc.y) found=$($loc.found) label='$($loc.label)'"
    if (-not $loc.found -or ([int]$loc.x -eq 0 -and [int]$loc.y -eq 0)) {
        throw "VISION_NO_MATCH (found=$($loc.found))"
    }
    $centerPngX = [int]$loc.x
    $centerPngY = [int]$loc.y
    $screenX = $winLeft + $centerPngX
    $screenY = $winTop + $centerPngY
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

    # 2026-08-26 起改用 PostMessage 投递点击：RDP 会话下 SetCursorPos+mouse_event 会被
    # 客户端指针纠偏拆散 down/up；且搜索结果项在 overlay 面板窗口上，必须投给 overlay。
    $overlayHwnd = Get-WeixinSearchOverlayHwnd
    if ($overlayHwnd -eq 0) { throw 'SEARCH_OVERLAY_NOT_FOUND' }
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
    if ($null -ne $script:originalClipboard) { try { Set-ClipboardTextRetry $script:originalClipboard } catch {} }
    [void]$mutex.ReleaseMutex(); $mutex.Dispose()
}
