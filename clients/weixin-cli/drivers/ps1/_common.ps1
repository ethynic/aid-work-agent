# drivers/ps1/_common.ps1 — M2 驱动公共底座
# 职责：
#   - dot-source 包内 win32-lib.ps1（由原已验证 Win32/UIA 底座迁入）
#   - DRIVER_JSON 输出约定与 Invoke-DriverMain 包装（单飞 mutex + 统一错误映射）
#   - 视觉调用（服务端 GLM-5.3-Flash，429 重试 25s×4，```json 包裹剥离）
#   - 主窗口解析（ClassName/标题校验 + 兜底枚举，规避 MainWindowHandle 被弹窗抢走）
#   - 两栏布局校验（单栏模式下搜索框固定坐标失效，必须 fail closed）
#
# 约定：业务失败一律 Throw-DriverError（DRIVER_JSON ok=false + 退出码 0）；
#   只有未预期崩溃才非零退出（TS 侧映射 INTERNAL_ERROR）。
# 注意：本文件含中文，必须以 UTF-8 with BOM 保存（Windows PowerShell 5.1 否则按 GBK 解析报错）。

$script:DriverPs1Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $script:DriverPs1Dir 'win32-lib.ps1')

# 代理模式由 TS 注入 AID_WEIXIN_SERVER_URL + AID_WEIXIN_ACCESS_TOKEN，
# 通过服务端 GLM-5.3-Flash 白名单集中计费。
# 开发调试可用本机 GLM 或 Moonshot 密钥及 AID_WEIXIN_VISION_PROVIDER 选择。
$script:KimiApiKey = [string]$env:AID_WEIXIN_KIMI_API_KEY
$script:KimiApiBase = 'https://api.moonshot.cn/v1/chat/completions'
$script:KimiModel = 'kimi-k3'   # 推理模型：答案在 content，为空则读 reasoning_content；temperature 必须为 1/省略（服务端 provider 统一省略）
$script:ZhipuApiKey = [string]$env:AID_WEIXIN_ZHIPU_API_KEY
$script:ZhipuApiBase = 'https://open.bigmodel.cn/api/paas/v4/chat/completions'
$script:ZhipuModel = 'GLM-5.3-Flash'   # 原生多模态；答案直接在 content

function Write-DriverJson([object]$Payload) {
    # -InputObject 传参：避免管道对单元素数组的解包怪癖
    $json = ConvertTo-Json -InputObject $Payload -Compress -Depth 10
    # Keep the protocol ASCII-only: Windows hosts can reset the child console
    # codepage during WinForms initialization. JSON escapes preserve exact text.
    $json = [regex]::Replace($json, '[^\x00-\x7F]', {
        param($match)
        '\u{0:x4}' -f [int][char]$match.Value
    })
    Write-Output ("DRIVER_JSON: " + $json)
}

function Throw-DriverError([string]$Code, [string]$Message) {
    # 驱动内统一的可识别错误标记，由 Invoke-DriverMain 解析为 DRIVER_JSON ok=false
    throw ("WXDRIVE|" + $Code + "|" + $Message)
}

function Test-WeixinWhatsNewWindow($Identity) {
    try {
        $root = [Windows.Automation.AutomationElement]::FromHandle([IntPtr]$Identity.Hwnd)
        $item = $root.Current
        return $item.ProcessId -eq $Identity.ProcessId -and $item.ClassName -ceq 'mmui::WhatsNewWindow'
    } catch { return $false }
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
            [string]$id.Title -eq $script:WeixinTitle -and -not (Test-WeixinWhatsNewWindow $id)) {
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
    # 代理使用 GLM-5.3-Flash。兼容既有本机直调配置，ProxyOnly 仅允许计费代理。
    # 402/401 直接报 INSUFFICIENT_CREDIT/CONFIG_MISSING。
    param(
        [Parameter(Mandatory, ParameterSetName = 'Path')][string]$ImagePath,
        [Parameter(Mandatory, ParameterSetName = 'Bytes')][byte[]]$ImageBytes,
        [Parameter(Mandatory)][string]$SystemPrompt,
        [Parameter(Mandatory)][string]$UserText,
        [string]$ArtifactPrefix = 'weixin-driver-vision',
        [switch]$ProxyOnly,
        [string]$RuntimeFriendStep
    )
    $runtimeServer = ([string]$env:AIDWORK_RUNTIME_SERVER).Trim().TrimEnd('/')
    $runtimeToken = ([string]$env:AIDWORK_RUNTIME_DEVICE_TOKEN).Trim()
    $runtimeFriend = $RuntimeFriendStep -and ($runtimeServer -or $runtimeToken)
    # TS 层激活/绑定了服务端但拿不到 token 时，通过 AID_WEIXIN_PROXY_ERROR 传入原因
    if (-not $runtimeFriend -and -not [string]::IsNullOrWhiteSpace($env:AID_WEIXIN_PROXY_ERROR)) {
        Throw-DriverError 'CONFIG_MISSING' ('服务端代理凭据不可用：' + $env:AID_WEIXIN_PROXY_ERROR)
    }

    # 组装候选通道（按优先级）
    $candidates = @()
    $forced = ([string]$env:AID_WEIXIN_VISION_PROVIDER).Trim().ToLower()
    $glmDirect = @{ ApiUrl = $script:ZhipuApiBase; Token = $script:ZhipuApiKey; Model = $script:ZhipuModel; Mode = 'direct'; Tag = 'glm' }
    $moonDirect = @{ ApiUrl = $script:KimiApiBase; Token = $script:KimiApiKey; Model = $script:KimiModel; Mode = 'direct'; Tag = 'moonshot-direct' }
    $serverUrl = ([string]$env:AID_WEIXIN_SERVER_URL).TrimEnd('/')
    $billingProxy = $null
    if (-not [string]::IsNullOrWhiteSpace($serverUrl)) {
        $billingProxy = @{ ApiUrl = ($serverUrl + '/api/client/v1/llm/chat'); Token = [string]$env:AID_WEIXIN_ACCESS_TOKEN; Model = $script:ZhipuModel; Mode = 'proxy'; Tag = 'glm-proxy' }
    }
    if ($runtimeFriend) {
        if (-not $ProxyOnly -or -not $runtimeServer -or -not $runtimeToken) {
            Throw-DriverError 'CONFIG_MISSING' '已配对 Runtime 的微信视觉凭证不完整'
        }
        $candidates += @{ ApiUrl = ($runtimeServer + '/api/local-tools/runtime/weixin/visual-locate'); Token = $runtimeToken; Model = $script:ZhipuModel; Mode = 'proxy'; Tag = 'glm-runtime' }
    } elseif ($ProxyOnly) {
        if ($null -eq $billingProxy -or [string]::IsNullOrWhiteSpace([string]$billingProxy.Token)) {
            Throw-DriverError 'CONFIG_MISSING' '需要启用微信 CLI 服务端计费代理，请配置服务端地址并完成客户端激活'
        }
        $candidates += $billingProxy
    } elseif ($forced -eq 'glm') {
        if (-not [string]::IsNullOrWhiteSpace($script:ZhipuApiKey)) { $candidates += $glmDirect }
        elseif ($null -ne $billingProxy) { $candidates += $billingProxy }
    } elseif ($forced -eq 'moonshot') {
        if (-not [string]::IsNullOrWhiteSpace($script:KimiApiKey)) { $candidates += $moonDirect }
        elseif ($null -ne $billingProxy) { $candidates += $billingProxy }
    } else {
        if (-not [string]::IsNullOrWhiteSpace($script:ZhipuApiKey)) { $candidates += $glmDirect }
        if (-not [string]::IsNullOrWhiteSpace($script:KimiApiKey)) { $candidates += $moonDirect }
        elseif ($null -ne $billingProxy) { $candidates += $billingProxy }
    }
    if ($candidates.Count -eq 0) {
        Throw-DriverError 'CONFIG_MISSING' '未配置视觉模型调用凭据：请设置 AID_WEIXIN_ZHIPU_API_KEY（GLM-5.3-Flash，推荐）或 AID_WEIXIN_KIMI_API_KEY（kimi-k3 备份）；生产用法也可设 AID_WEIXIN_SERVER_URL + AID_WEIXIN_ACTIVATION_CODE 走服务端代理计费'
    }
    if ($null -ne $billingProxy -and [string]::IsNullOrWhiteSpace([string]$billingProxy.Token) -and
        $candidates.Count -eq 1 -and $candidates[0].Mode -eq 'proxy') {
        Throw-DriverError 'CONFIG_MISSING' '已配置 AID_WEIXIN_SERVER_URL 但缺少 AID_WEIXIN_ACCESS_TOKEN（应由 CLI 自动注入；直接运行 ps1 时请改用 AID_WEIXIN_ZHIPU_API_KEY / AID_WEIXIN_KIMI_API_KEY 降级模式）'
    }

    if ($PSCmdlet.ParameterSetName -eq 'Path') { $ImageBytes = [IO.File]::ReadAllBytes($ImagePath) }
    $b64 = [Convert]::ToBase64String($ImageBytes)
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
        if ($runtimeFriend) {
            $payloadObj = @{ image_url = $dataUrl; step = $RuntimeFriendStep }
        } elseif ($cand.Mode -eq 'proxy') {
            $payloadObj.purpose = 'weixin-vision-' + $ArtifactPrefix
        }
        $payload = $payloadObj | ConvertTo-Json -Depth 8
        if (-not $ProxyOnly) {
            $reqPath = Join-Path $env:TEMP ($ArtifactPrefix + '-req.json')
            [IO.File]::WriteAllText($reqPath, $payload, $utf8NoBom)
            $respPath = Join-Path $env:TEMP ($ArtifactPrefix + '-resp.json')
            $curlArgs = @('-s', '-X', 'POST', [string]$cand.ApiUrl,
                '-H', "Authorization: Bearer $($cand.Token)",
                '-H', 'Content-Type: application/json',
                '--data-binary', "@$reqPath",
                '-o', $respPath, '-w', '%{http_code}', '--max-time', '180')
        }
        $http = $null
        $fatal = $null
        for ($attempt = 1; $attempt -le 4; $attempt++) {
            $errBody = ''
            if ($ProxyOnly) {
                # Keep the token, private screenshot and model response in this process.
                try {
                    $response = Invoke-WebRequest -UseBasicParsing -Uri $cand.ApiUrl -Method Post `
                        -Headers @{ Authorization = "Bearer $($cand.Token)" } -ContentType 'application/json; charset=utf-8' `
                        -Body $utf8NoBom.GetBytes($payload) -MaximumRedirection 0 -TimeoutSec 180 -ErrorAction Stop
                    $http = [string][int]$response.StatusCode
                } catch {
                    if ($null -eq $_.Exception.Response) { $fatal = '服务端视觉请求失败'; break }
                    $http = [string][int]$_.Exception.Response.StatusCode
                }
            } else {
                $http = & curl.exe @curlArgs
                if ($LASTEXITCODE -ne 0) { $fatal = "curl exit=$LASTEXITCODE"; break }
                if ($http -ne '200' -and (Test-Path $respPath)) {
                    $errBody = Get-Content $respPath -Raw -Encoding UTF8
                }
            }
            if ($http -eq '200') { $fatal = $null; break }
            # 服务端代理计费的业务性拒绝：402 余额不足 / 401 凭据失效，重试和降级都无意义
            if ($http -eq '402') { Throw-DriverError 'INSUFFICIENT_CREDIT' '积分余额不足，请充值后重试（视觉调用走服务端代理计费）' }
            if ($runtimeFriend -and $http -in @('401', '403')) {
                Throw-DriverError 'CONFIG_MISSING' '已配对 Runtime 的设备或租户无权调用微信好友视觉'
            }
            if ($runtimeFriend -and $http -eq '404') {
                Throw-DriverError 'CONFIG_MISSING' '当前服务端未提供 Runtime 微信好友视觉入口'
            }
            if ($http -eq '401') { Throw-DriverError 'CONFIG_MISSING' '服务端 access_token 无效或已吊销：请删除 %APPDATA%\aid-weixin\binding.json 并重新设置 AID_WEIXIN_ACTIVATION_CODE 激活' }
            if (-not $runtimeFriend -and $http -eq '429' -and $attempt -lt 4) {
                Write-Host ("[vision] HTTP 429 (attempt {0}/4), retrying in 25s..." -f $attempt)
                Start-Sleep -Seconds 25
                continue
            }
            $fatal = "HTTP $http body=" + $errBody.Substring(0, [Math]::Min(300, $errBody.Length))
            break
        }
        if ($null -ne $fatal) {
            if ($runtimeFriend) { Throw-DriverError 'INTERNAL_ERROR' ('Runtime 微信好友视觉请求失败：' + $fatal) }
            Write-Host ("[vision] 通道 {0} 失败：{1}，尝试下一通道" -f $cand.Tag, $fatal)
            $lastErr = $fatal
            continue
        }
        if ($ProxyOnly) {
            try { $resp = ConvertFrom-Json -InputObject $response.Content }
            catch { Throw-DriverError 'UI_CHANGED' '服务端视觉响应格式无效' }
        } else {
            $resp = (Get-Content $respPath -Raw -Encoding UTF8) | ConvertFrom-Json
        }
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
            if ($runtimeFriend) { Throw-DriverError 'UI_CHANGED' 'Runtime 微信好友视觉响应格式无效' }
            Write-Host ("[vision] 通道 {0} 响应无 JSON，尝试下一通道" -f $cand.Tag)
            $lastErr = if ($ProxyOnly) { '响应无 JSON' } else {
                '响应无 JSON：' + $content.Substring(0, [Math]::Min(200, $content.Length))
            }
            continue
        }
        if (-not $ProxyOnly) {
            [IO.File]::WriteAllText((Join-Path $env:TEMP ($ArtifactPrefix + '-raw.json')), $content, [Text.Encoding]::UTF8)
        }
        Write-Host ("[vision] 通道 {0} 成功（model={1}）" -f $cand.Tag, $cand.Model)
        if ($ProxyOnly) {
            try { return (ConvertFrom-Json -InputObject $m.Value) }
            catch { Throw-DriverError 'UI_CHANGED' '服务端视觉响应格式无效' }
        }
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
