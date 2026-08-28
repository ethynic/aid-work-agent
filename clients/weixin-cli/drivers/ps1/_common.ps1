# drivers/ps1/_common.ps1 — M2 驱动公共底座
# 职责：
#   - dot-source 已真机验证的 experiments/probes/p1-chat-search-group/probe-lib.ps1（只读引用，不改动）
#   - DRIVER_JSON 输出约定与 Invoke-DriverMain 包装（单飞 mutex + 统一错误映射）
#   - Kimi 视觉调用（kimi-k3，429 重试 25s×4，```json 包裹剥离）
#   - 主窗口解析（ClassName/标题校验 + 兜底枚举，规避 MainWindowHandle 被弹窗抢走）
#   - 两栏布局校验（单栏模式下搜索框固定坐标失效，必须 fail closed）
#   - Open-WeixinChat：搜索目标 → Kimi 定位 overlay 结果项 → PostMessage 点击 →
#     截主窗口 Kimi 校验标题/输入框（message-send 与 history_read 的 resolve-open 共用）
#
# 约定：业务失败一律 Throw-DriverError（DRIVER_JSON ok=false + 退出码 0）；
#   只有未预期崩溃才非零退出（TS 侧映射 INTERNAL_ERROR）。
# 注意：本文件含中文，必须以 UTF-8 with BOM 保存（Windows PowerShell 5.1 否则按 GBK 解析报错）。

$script:DriverPs1Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $script:DriverPs1Dir '..\..\experiments\probes\p1-chat-search-group\probe-lib.ps1')

# 视觉模型配置：GLM-5.3-Flash 为默认通道，kimi-k3 保留为备份
# （2026-08-28 评估结论：坐标定位与 kimi-k3 等效、成本约 1/40、快 3-4 倍，
# 见 docs/research/weixin-cli/vision-model-comparison.md）。
# 两种调用路径（与计费设计一致，见 docs/design/weixin/weixin-cli-billing.md）：
# 1) 本机直调（开发调试）：AID_WEIXIN_ZHIPU_API_KEY → GLM；AID_WEIXIN_KIMI_API_KEY → Moonshot；
# 2) 代理模式：TS 层注入 AID_WEIXIN_SERVER_URL + AID_WEIXIN_ACCESS_TOKEN，走服务端集中计费
#    （代理白名单目前仅 kimi-k3，故代理通道固定用 kimi-k3）。
# AID_WEIXIN_VISION_PROVIDER 可强制 'glm' 或 'moonshot'；缺省：有 zhipu key 则 GLM 优先、
# Moonshot（直调或代理）作备份，GLM 调用失败自动降级。
$script:KimiApiKey = [string]$env:AID_WEIXIN_KIMI_API_KEY
$script:KimiApiBase = 'https://api.moonshot.cn/v1/chat/completions'
$script:KimiModel = 'kimi-k3'   # 推理模型：答案在 content，为空则读 reasoning_content；temperature 必须为 1/省略（服务端 provider 统一省略）
$script:ZhipuApiKey = [string]$env:AID_WEIXIN_ZHIPU_API_KEY
$script:ZhipuApiBase = 'https://open.bigmodel.cn/api/paas/v4/chat/completions'
$script:ZhipuModel = 'GLM-5.3-Flash'   # 原生多模态；答案直接在 content

function Write-DriverJson([object]$Payload) {
    # -InputObject 传参：避免管道对单元素数组的解包怪癖
    $json = ConvertTo-Json -InputObject $Payload -Compress -Depth 10
    Write-Output ("DRIVER_JSON: " + $json)
}

function Throw-DriverError([string]$Code, [string]$Message) {
    # 驱动内统一的可识别错误标记，由 Invoke-DriverMain 解析为 DRIVER_JSON ok=false
    throw ("WXDRIVE|" + $Code + "|" + $Message)
}

function Resolve-WeixinMainWindowHwnd {
    # probe-lib 的 Select-WeixinMainWindow 依赖 Process.MainWindowHandle，主窗口句柄
    # 可能被弹窗抢走导致选错；此处逐窗口校验 ClassName=='Qt51514QWindowIcon' 且标题为
    # "微信"，不符则枚举其它 Weixin.exe 可见窗口兜底。
    $valid = @()
    foreach ($w in @(Get-VisibleWindowList)) {
        if ([int64]$w.Hwnd -eq 0) { continue }
        if (-not (Test-WeixinExecutablePath ([string]$w.ProcessPath))) { continue }
        $id = Get-WindowIdentityByHwnd ([int64]$w.Hwnd)
        if ($null -ne $id -and [string]$id.ClassName -eq 'Qt51514QWindowIcon' -and
            [string]$id.Title -eq $script:WeixinTitle) {
            $valid += [int64]$id.Hwnd
        }
    }
    $valid = @($valid | Select-Object -Unique)
    if ($valid.Count -eq 0) { Throw-DriverError 'WEIXIN_NOT_FOUND' '未找到微信主窗口（Weixin.exe 未运行或主窗口不可见）' }
    if ($valid.Count -gt 1) { Throw-DriverError 'WINDOW_AMBIGUOUS' ('找到 ' + $valid.Count + ' 个微信主窗口候选，无法唯一确定（可能开了多个微信）') }
    return [int64]$valid[0]
}

function Invoke-KimiVision {
    # 通用视觉调用（函数名保留历史命名）：base64 PNG data URL，max_tokens=4096；
    # 每通道 429 重试 25s×4；返回可能是 ```json 包裹，正则提取首个 { 到末个 }。
    # 多通道：默认 GLM-5.3-Flash 优先、Moonshot（直调或代理）备份，GLM 失败自动降级；
    # 402/401 属计费/凭据业务性失败，不降级直接报 INSUFFICIENT_CREDIT/CONFIG_MISSING。
    param(
        [Parameter(Mandatory)][string]$ImagePath,
        [Parameter(Mandatory)][string]$SystemPrompt,
        [Parameter(Mandatory)][string]$UserText,
        [string]$ArtifactPrefix = 'weixin-driver-vision'
    )
    # TS 层激活/绑定了服务端但拿不到 token 时，通过 AID_WEIXIN_PROXY_ERROR 传入原因
    if (-not [string]::IsNullOrWhiteSpace($env:AID_WEIXIN_PROXY_ERROR)) {
        Throw-DriverError 'CONFIG_MISSING' ('服务端代理凭据不可用：' + $env:AID_WEIXIN_PROXY_ERROR)
    }

    # 组装候选通道（按优先级）
    $candidates = @()
    $forced = ([string]$env:AID_WEIXIN_VISION_PROVIDER).Trim().ToLower()
    $glmDirect = @{ ApiUrl = $script:ZhipuApiBase; Token = $script:ZhipuApiKey; Model = $script:ZhipuModel; Mode = 'direct'; Tag = 'glm' }
    $moonDirect = @{ ApiUrl = $script:KimiApiBase; Token = $script:KimiApiKey; Model = $script:KimiModel; Mode = 'direct'; Tag = 'moonshot-direct' }
    $serverUrl = ([string]$env:AID_WEIXIN_SERVER_URL).TrimEnd('/')
    $moonProxy = $null
    if (-not [string]::IsNullOrWhiteSpace($serverUrl)) {
        $moonProxy = @{ ApiUrl = ($serverUrl + '/api/client/v1/llm/chat'); Token = [string]$env:AID_WEIXIN_ACCESS_TOKEN; Model = $script:KimiModel; Mode = 'proxy'; Tag = 'moonshot-proxy' }
    }
    if ($forced -eq 'glm') {
        if (-not [string]::IsNullOrWhiteSpace($script:ZhipuApiKey)) { $candidates += $glmDirect }
    } elseif ($forced -eq 'moonshot') {
        if (-not [string]::IsNullOrWhiteSpace($script:KimiApiKey)) { $candidates += $moonDirect }
        elseif ($null -ne $moonProxy) { $candidates += $moonProxy }
    } else {
        if (-not [string]::IsNullOrWhiteSpace($script:ZhipuApiKey)) { $candidates += $glmDirect }
        if (-not [string]::IsNullOrWhiteSpace($script:KimiApiKey)) { $candidates += $moonDirect }
        elseif ($null -ne $moonProxy) { $candidates += $moonProxy }
    }
    if ($candidates.Count -eq 0) {
        Throw-DriverError 'CONFIG_MISSING' '未配置视觉模型调用凭据：请设置 AID_WEIXIN_ZHIPU_API_KEY（GLM-5.3-Flash，推荐）或 AID_WEIXIN_KIMI_API_KEY（kimi-k3 备份）；生产用法也可设 AID_WEIXIN_SERVER_URL + AID_WEIXIN_ACTIVATION_CODE 走服务端代理计费'
    }
    if ($null -ne $moonProxy -and [string]::IsNullOrWhiteSpace([string]$moonProxy.Token) -and
        $candidates.Count -eq 1 -and $candidates[0].Mode -eq 'proxy') {
        Throw-DriverError 'CONFIG_MISSING' '已配置 AID_WEIXIN_SERVER_URL 但缺少 AID_WEIXIN_ACCESS_TOKEN（应由 CLI 自动注入；直接运行 ps1 时请改用 AID_WEIXIN_ZHIPU_API_KEY / AID_WEIXIN_KIMI_API_KEY 降级模式）'
    }

    $b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($ImagePath))
    $dataUrl = 'data:image/png;base64,' + $b64
    $utf8NoBom = New-Object Text.UTF8Encoding($false)
    $lastErr = $null
    foreach ($cand in $candidates) {
        if ([string]::IsNullOrWhiteSpace([string]$cand.Token)) { continue }
        $payloadObj = @{
            model = $cand.Model
            messages = @(
                @{ role = 'system'; content = $SystemPrompt },
                @{ role = 'user'; content = @(
                    @{ type = 'image_url'; image_url = @{ url = $dataUrl } },
                    @{ type = 'text'; text = $UserText }
                )}
            )
            max_tokens = 4096
        }
        if ($cand.Mode -eq 'proxy') { $payloadObj.purpose = 'weixin-vision-' + $ArtifactPrefix }
        $payload = $payloadObj | ConvertTo-Json -Depth 8
        $reqPath = Join-Path $env:TEMP ($ArtifactPrefix + '-req.json')
        [IO.File]::WriteAllText($reqPath, $payload, $utf8NoBom)
        $respPath = Join-Path $env:TEMP ($ArtifactPrefix + '-resp.json')
        $curlArgs = @('-s', '-X', 'POST', [string]$cand.ApiUrl,
            '-H', "Authorization: Bearer $($cand.Token)",
            '-H', 'Content-Type: application/json',
            '--data-binary', "@$reqPath",
            '-o', $respPath, '-w', '%{http_code}', '--max-time', '180')
        $http = $null
        $fatal = $null
        for ($attempt = 1; $attempt -le 4; $attempt++) {
            $http = & curl.exe @curlArgs
            if ($LASTEXITCODE -ne 0) { $fatal = "curl exit=$LASTEXITCODE"; break }
            if ($http -eq '200') { $fatal = $null; break }
            $errBody = if (Test-Path $respPath) { Get-Content $respPath -Raw -Encoding UTF8 } else { '<no body>' }
            # 服务端代理计费的业务性拒绝：402 余额不足 / 401 凭据失效，重试和降级都无意义
            if ($http -eq '402') { Throw-DriverError 'INSUFFICIENT_CREDIT' '积分余额不足，请充值后重试（视觉调用走服务端代理计费）' }
            if ($http -eq '401') { Throw-DriverError 'CONFIG_MISSING' '服务端 access_token 无效或已吊销：请删除 %APPDATA%\aid-weixin\binding.json 并重新设置 AID_WEIXIN_ACTIVATION_CODE 激活' }
            if ($http -eq '429' -and $attempt -lt 4) {
                Write-Host ("[vision] HTTP 429 (attempt {0}/4), retrying in 25s..." -f $attempt)
                Start-Sleep -Seconds 25
                continue
            }
            $fatal = "HTTP $http body=" + $errBody.Substring(0, [Math]::Min(300, $errBody.Length))
            break
        }
        if ($null -ne $fatal) {
            Write-Host ("[vision] 通道 {0} 失败：{1}，尝试下一通道" -f $cand.Tag, $fatal)
            $lastErr = $fatal
            continue
        }
        $resp = (Get-Content $respPath -Raw -Encoding UTF8) | ConvertFrom-Json
        if ($cand.Mode -eq 'proxy') {
            # 代理端点已剥离 choices 包装，直接返回 content（服务端 provider 已做 reasoning_content 兜底）
            $content = [string]$resp.content
        } else {
            $content = [string]$resp.choices[0].message.content
            if ([string]::IsNullOrWhiteSpace($content)) { $content = [string]$resp.choices[0].message.reasoning_content }
        }
        $stripped = $content -replace '(?s)^```(?:json)?\s*', '' -replace '(?s)\s*```$', ''
        $m = [regex]::Match($stripped, '(?s)\{[\s\S]*\}')
        if (-not $m.Success) {
            Write-Host ("[vision] 通道 {0} 响应无 JSON，尝试下一通道" -f $cand.Tag)
            $lastErr = '响应无 JSON：' + $content.Substring(0, [Math]::Min(200, $content.Length))
            continue
        }
        [IO.File]::WriteAllText((Join-Path $env:TEMP ($ArtifactPrefix + '-raw.json')), $content, [Text.Encoding]::UTF8)
        Write-Host ("[vision] 通道 {0} 成功（model={1}）" -f $cand.Tag, $cand.Model)
        return ($m.Value | ConvertFrom-Json)
    }
    Throw-DriverError 'INTERNAL_ERROR' ('视觉调用全部通道失败，最后错误：' + $lastErr)
}

function Assert-WeixinTwoColumnLayout([int64]$MainHwnd) {
    # 单栏模式（无左侧会话列表）下搜索框固定坐标不是搜索框，必须先判布局 fail closed。
    # 判据：截主窗口图，由 Kimi 判断左栏顶部是否存在「搜索」框（两栏布局）。
    $shot = Join-Path $env:TEMP 'weixin-driver-layout-check.png'
    Get-WeixinWindowSnapshot $MainHwnd $shot | Out-Null
    $sys = @'
You are a UI layout inspector for WeChat (Weixin) desktop 4.x main window.
Input image: a capture of the WeChat main window.
Task: determine whether the window is in TWO-COLUMN layout — a left conversation-list column
with a search box (placeholder text 搜索) at the top of that left column, plus a right chat area.
Return ONLY a single JSON object, no markdown, no explanation:
{"two_column": <true|false>}
'@
    $r = Invoke-KimiVision -ImagePath $shot -SystemPrompt $sys -UserText 'Is this WeChat main window in two-column layout with a search box at the top of the left column? Return JSON.' -ArtifactPrefix 'weixin-driver-layout'
    if ($r.two_column -ne $true) {
        Throw-DriverError 'UI_CHANGED' '微信主窗口当前为单栏模式（无左侧会话列表），请先手动恢复两栏布局后重试'
    }
}

function Close-WeixinSearchOverlay([int64]$OverlayHwnd) {
    # 关闭搜索面板（best-effort）：PostMessage ESC 投递未真机验证过，失败则退化
    # keybd_event ESC（脚本已激活过窗口，有前台）；仍关不掉不影响结果正确性。
    if ($OverlayHwnd -eq 0) { return }
    try {
        Send-WeixinPostMessageKey -Hwnd $OverlayHwnd -Vk 0x1B -ScanCode 0x01
        Start-Sleep -Milliseconds 300
    } catch {}
    if ((Get-WeixinSearchOverlayHwnd) -ne 0) {
        try { [WeixinProbeWin32]::keybd_event(0x1B, 0x01, 0, [IntPtr]::Zero); Start-Sleep -Milliseconds 60; [WeixinProbeWin32]::keybd_event(0x1B, 0x01, 2, [IntPtr]::Zero) } catch {}
    }
}

function Open-WeixinChat {
    # 搜索目标名 → Kimi 定位 overlay 结果项 → PostMessage 点击 → 截主窗口
    # Kimi 一次调用返回 {title, input_box, input_empty, found} → 校验。
    # 返回 @{ MainHwnd; WinLeft; WinTop; Title; InputBoxX; InputBoxY }
    param(
        [Parameter(Mandatory)][string]$TargetName,
        [switch]$RequireEmptyInput
    )
    $mainHwnd = Resolve-WeixinMainWindowHwnd
    # 搜索面板必须前台才会打开/保持；Kimi 定位期间用户切走焦点面板会关闭 → 安全中止
    if (-not (Invoke-WeixinActivation $mainHwnd)) { Throw-DriverError 'FOREGROUND_LOST' '无法将微信主窗口激活到前台（搜索面板必须前台才会打开）' }
    Start-Sleep -Milliseconds 300
    Assert-WeixinTwoColumnLayout $mainHwnd

    $rect = New-Object WeixinProbeWin32+RECT
    [void][WeixinProbeWin32]::GetWindowRect([IntPtr]$mainHwnd, [ref]$rect)
    # 搜索框在主窗口左栏顶部，两栏布局下位置相对窗口固定；
    # 点击时微信自动全选残留文本，直接输入即覆盖——绝对不要用退格清空搜索框
    # （清空瞬间面板自动关闭且再输入不重开）。
    Send-WeixinPostMessageClick -Hwnd $mainHwnd -ScreenX ($rect.Left + 335) -ScreenY ($rect.Top + 110)
    Start-Sleep -Milliseconds 800
    Send-WeixinPostMessageText -Hwnd $mainHwnd -Text $TargetName
    Start-Sleep -Milliseconds 1500

    $overlayHwnd = Get-WeixinSearchOverlayHwnd
    if ($overlayHwnd -eq 0) { Throw-DriverError 'UI_CHANGED' '未找到搜索结果面板（前台被切走会关闭面板，或布局已变化）' }
    $ovlShot = Join-Path $env:TEMP 'weixin-driver-open-overlay.png'
    $snap = Get-WeixinWindowSnapshot $overlayHwnd $ovlShot
    $ovlLeft = [int]$snap[0]; $ovlTop = [int]$snap[1]

    $locSys = @'
You are a coordinate locator for WeChat desktop search.
Input image: a capture of the WeChat SEARCH RESULT PANEL window (the dropdown list that appears after typing in the search box). The panel content sits in a rounded-corner area; margins may be black (transparent areas).
Your task: find the item that best matches the given query word — prefer items under sections like 功能/联系人/群聊, NOT the "搜索网络结果" web-search suggestions. Return the center click coordinate of that item.
Coordinate origin = top-left corner of the image. Unit = pixel.
Return ONLY a single JSON object, no markdown, no explanation:
{"x": <int>, "y": <int>, "label": "<matched item text>", "found": <true|false>}
If there is no matching item, return:
{"x": 0, "y": 0, "label": "", "found": false}
'@
    $loc = Invoke-KimiVision -ImagePath $ovlShot -SystemPrompt $locSys -UserText ("Query word: $TargetName. Find the best matching item in the WeChat search result area and return its center coordinate as JSON.") -ArtifactPrefix 'weixin-driver-open-locate'
    if (-not $loc.found -or ([int]$loc.x -eq 0 -and [int]$loc.y -eq 0)) {
        Close-WeixinSearchOverlay $overlayHwnd
        Throw-DriverError 'TARGET_NOT_FOUND' ("搜索结果中未找到与「" + $TargetName + "」匹配的条目")
    }
    # 结果项点击必须投给 overlay（投主窗口会被当作"点击面板外"关面板）
    Send-WeixinPostMessageClick -Hwnd $overlayHwnd -ScreenX ($ovlLeft + [int]$loc.x) -ScreenY ($ovlTop + [int]$loc.y)
    Start-Sleep -Milliseconds 1200

    $mainShot = Join-Path $env:TEMP 'weixin-driver-open-main.png'
    $snap2 = Get-WeixinWindowSnapshot $mainHwnd $mainShot
    $winLeft = [int]$snap2[0]; $winTop = [int]$snap2[1]
    $checkSys = @'
You are a UI inspector for WeChat desktop 4.x main window.
Input image: a screenshot of WeChat main window with a chat conversation open.
Return ONLY a single JSON object, no markdown, no explanation:
{"title": "<conversation title shown at the top of the chat area>", "input_box": {"x": <int>, "y": <int>}, "input_empty": <true|false>, "found": <true|false>}
input_box = CENTER of the text input area at the bottom where you type a message.
input_empty = whether the input box currently contains NO draft text (empty = true).
found = false if no chat conversation is open or the input box cannot be located.
Coordinates are INSIDE the image (origin top-left, unit pixel).
'@
    $check = Invoke-KimiVision -ImagePath $mainShot -SystemPrompt $checkSys -UserText 'Return the conversation title and input box location as JSON.' -ArtifactPrefix 'weixin-driver-open-check'
    if (-not $check.found) { Throw-DriverError 'UI_CHANGED' '打开会话后无法识别聊天窗口结构（未打开会话或找不到输入框）' }
    $title = [string]$check.title
    if ($title -notlike ('*' + $TargetName + '*')) {
        Throw-DriverError 'UI_CHANGED' ("打开的会话标题「" + $title + "」与目标「" + $TargetName + "」不一致，已中止")
    }
    if ($RequireEmptyInput -and $check.input_empty -eq $false) {
        # 输入框有残留草稿时禁止输入（文本会追加到草稿后面），需人工清空后重试
        Throw-DriverError 'UI_CHANGED' '输入框存在残留草稿，为避免串消息已中止；请人工清空后重试'
    }
    return @{
        MainHwnd = $mainHwnd; WinLeft = $winLeft; WinTop = $winTop
        Title = $title
        InputBoxX = [int]$check.input_box.x; InputBoxY = [int]$check.input_box.y
    }
}

function Invoke-DriverMain {
    # 驱动统一入口：命名 mutex 单飞 → Win32/DPI 初始化 → body → DRIVER_JSON 输出。
    # body 返回的 hashtable 作为 data；Throw-DriverError 映射为 ok=false + 稳定 code。
    param(
        [Parameter(Mandatory)][string]$MutexName,
        [Parameter(Mandatory)][scriptblock]$Body
    )
    $mutex = New-Object Threading.Mutex($false, $MutexName, [ref]$null)
    if (-not $mutex.WaitOne(0)) {
        Write-DriverJson @{ ok = $false; code = 'BUSY'; message = '另一个微信自动化驱动正在执行（命名 mutex 单飞），请稍后重试' }
        return
    }
    try {
        Initialize-WeixinProbeWin32
        Set-WeixinProbeDpiContext
        $data = & $Body
        # body 最后一个语句的返回值即 data；脚本块输出可能混入数组，取最后一个 hashtable
        if ($data -is [array]) { $data = ($data | Where-Object { $_ -is [hashtable] } | Select-Object -Last 1) }
        if ($null -eq $data) { $data = @{} }
        Write-DriverJson @{ ok = $true; data = $data }
    } catch {
        $raw = [string]$_.Exception.Message
        if ($raw -match '^WXDRIVE\|([A-Z_]+)\|([\s\S]*)$') {
            Write-DriverJson @{ ok = $false; code = $Matches[1]; message = $Matches[2] }
        } elseif ($raw -match 'DPI_AWARENESS_FAILED') {
            Write-DriverJson @{ ok = $false; code = 'INTERNAL_ERROR'; message = 'DPI 感知上下文设置失败（Per-Monitor V2）' }
        } else {
            if ($raw.Length -gt 300) { $raw = $raw.Substring(0, 300) }
            Write-DriverJson @{ ok = $false; code = 'INTERNAL_ERROR'; message = ('驱动内部错误：' + $raw) }
        }
    } finally {
        [void]$mutex.ReleaseMutex()
        $mutex.Dispose()
    }
}
