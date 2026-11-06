# drivers/ps1/message-send.ps1 — wecom_message_send 驱动（M6：发送阶段驱动，智能分发由 TS 层编排）
# 前提：由 TS 层 messageSend.ts 调用；当前会话是否为目标由本驱动判定——
# 不对时返回 navigate_required=true 交还 TS 编排（search+select 后二次调用本驱动）。
# 流程：
#   1) 截主窗口顶部 → GLM-5.3-Flash 按窗口尺寸一次性标定标题首字中心；
#      群单击 / 单聊双击后由企微 Ctrl+C 复制完整标题，严格与目标比较。
#      底部 OCR 只提供输入点候选，Jev 只选择候选输入点。
#   3) 分支：非目标/无法判定 → 返回 data {navigate_required:true, reason}（驱动层 ok=true，
#      TS 接管）；目标确认 → 点击 input_point → Ctrl+A/Delete
#      清理编辑区 → 单行沿用 Send-WeComText，多行沿用剪贴板 Ctrl+V；发送结果由后续
#      本地库记录优先核验，粘贴后不再凭截图文字决定是否按 Enter。
#      副作用：多行发送会覆盖用户剪贴板且**不恢复**（weixin-cli 先例：paste handler 异步
#      读剪贴板，恢复竞态会粘贴到错的内容；README 已注明）
#   4) 输入后保留诊断截图，不再复核标题；沿用步骤 1 首次确认的目标。
#   5) Send-WeComEnter 发送 → 1.2s → 截图 → OCR（input/bubble/preview 三模式终态证据）
#   6) Jev #2（两问合一）：sent_successfully / failure_mode；no/unclear → EXECUTION_UNKNOWN
#      绝不自动重试。降级：M2 三选二（输入框清空 / 消息区末尾任一行含 text 归一化前缀 12 字 /
#      会话列表含目标名且含前缀），<2 项 → EXECUTION_UNKNOWN
# 返回 data：{ navigate_required:false, title, sent_verification:{method,result,...},
#   input_point:{x,y,source}|null, timing_ms, screenshot_paths }。
# P2 任务 3.4（发送动作与核验分离，设计 §4.3）：-SkipVerify 跳过 step5/6 终态证据与判定，
#   发送完成后直接返回 sent=true/verified=false（TS 层用本地库核验接管）；-VerifyOnly 不做
#   输入/发送（跳过 step1-4），仅执行既有 step5 终态证据 + step6 判定（TS 在本地库核验
#   未命中时补做 OCR 气泡核验），input_point 返回 null；两开关互斥。
# artifact：各步截图 + driver-log.txt（输入带 OCR 摘要、timing；
# 绝不含 TYPESAFE_API_KEY——key 只在 Invoke-WeComJev 内经临时头文件瞬态使用）。
# 注意：本文件含中文，必须以 UTF-8 with BOM 保存。
param(
    [Parameter(Mandatory)][string]$TargetName,
    [string]$Subtitle = '',
    [string]$Section = '',
    [Parameter(Mandatory)][string]$Text,
    [Parameter(Mandatory)][string]$ArtifactDir,
    # P2 任务 3.4（发送动作与核验分离，设计 §4.3）：
    # -SkipVerify：跳过 step5 终态证据与判定（本地库核验由 TS 层接管），发送完成后直接
    #   返回 sent=true/verified=false（不产生 EXECUTION_UNKNOWN）；
    # -VerifyOnly：不做输入/发送，仅执行既有 step5 终态证据（截图 + input/bubble/preview
    #   OCR + 三选二/Jev 判定），供 TS 在本地库核验未命中时补做 OCR 气泡核验；需要
    #   -TargetName/-ExpectedPrefix（核验文本，缺省取 -Text）等既有参数。
    # 两开关互斥；既有默认行为（无开关）零改动。
    [switch]$SkipVerify,
    [switch]$VerifyOnly,
    [string]$ExpectedPrefix = ''
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$driverDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $driverDir '_common.ps1')

# artifact 目录兜底创建（正式链路由 TS 侧 mkdir；直跑驱动时保证 driver-log 可写）
if (-not (Test-Path -LiteralPath $ArtifactDir)) { New-Item -ItemType Directory -Path $ArtifactDir -Force | Out-Null }

# 诊断日志：各阶段判定写 artifact 目录 driver-log.txt（真机排障用，格式同 chat-select）
$script:DriverLogPath = Join-Path $ArtifactDir 'driver-log.txt'
function Write-DriverLog([string]$Msg) {
    $line = '[' + ([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')) + '] ' + $Msg
    Add-Content -Path $script:DriverLogPath -Value $line -Encoding UTF8
}

function Save-StepShot([int64]$Hwnd, [string]$Name) {
    # 关键步骤截图存档；返回 @{ path; left; top; w; h }（截图坐标系原点 = 窗口左上角）。
    # 防御（2026-09-28 真机实测）：企微在普通/外部联系人布局切换时会**销毁重建主窗口**
    # （hwnd 变化），过渡期旧 hwnd 失效——截图前重验窗口存活，失效则按类名重解析（一次）。
    $path = Join-Path $ArtifactDir $Name
    $h = $Hwnd
    if (-not [WeComWin32]::IsWindow([IntPtr]$h)) {
        $h = Resolve-WeComMainWindow
        Write-DriverLog ('Save-StepShot: 窗口已重建，重解析 hwnd=' + $h)
    }
    $snap = Get-WeComWindowSnapshot -Hwnd $h -Path $path
    return @{ path = $path; left = [int]$snap[0]; top = [int]$snap[1]; w = [int]$snap[2]; h = [int]$snap[3] }
}

function Get-WeComTitlePoint([int64]$Hwnd, [string]$ImagePath) {
    # GLM 只定位标题首字；真正的会话名随后由企微自身复制，模型文字不参与身份判断。
    $main = Get-WeComWindowInfo ([IntPtr]$Hwnd)
    $dpi = [int][WeComWin32]::GetDpiForWindow([IntPtr]$Hwnd)
    if ($dpi -le 0) { Throw-DriverError 'UI_CHANGED' '无法取得企业微信窗口 DPI，未触碰输入区' }
    $cacheDir = Join-Path $env:LOCALAPPDATA 'AidWorkAgent\wecom-cli'
    $cachePath = Join-Path $cacheDir 'title-point.json'
    $cached = $null
    if (Test-Path -LiteralPath $cachePath) {
        try { $cached = Get-Content -LiteralPath $cachePath -Raw -Encoding UTF8 | ConvertFrom-Json }
        catch { $cached = $null }
    }
    if ($null -ne $cached -and $cached.schema -eq 1 -and $cached.w -eq $main.W -and
            $cached.h -eq $main.H -and $cached.dpi -eq $dpi -and
            ($cached.x -is [int] -or $cached.x -is [long]) -and
            ($cached.y -is [int] -or $cached.y -is [long]) -and
            ($cached.divider_x -is [int] -or $cached.divider_x -is [long]) -and
            $cached.divider_x -ge [int]($main.W * 0.20) -and $cached.divider_x -le [int]($main.W * 0.70) -and
            $cached.x -ge ($cached.divider_x + [int](8 * $dpi / 96)) -and
            $cached.x -le ($cached.divider_x + [int](100 * $dpi / 96)) -and
            $cached.y -ge [int](12 * $dpi / 96) -and $cached.y -le [int](56 * $dpi / 96) -and
            $cached.x -lt $main.W -and $cached.y -lt $main.H) {
        return @{ x = [int]$cached.x; y = [int]$cached.y; cache_path = $cachePath; source = 'cache' }
    }
    $url = ([string]$env:AID_WECOM_SERVER_URL).TrimEnd('/')
    $token = [string]$env:AID_WECOM_SERVER_TOKEN
    if (-not $url -or -not $token) {
        Throw-DriverError 'CONFIG_MISSING' '缺少 AID_WECOM_SERVER_URL 或 AID_WECOM_SERVER_TOKEN，无法用 GLM 标定会话标题；未触碰输入区'
    }
    Add-Type -AssemblyName System.Drawing
    $source = [Drawing.Image]::FromFile($ImagePath)
    $headerBytes = $null
    try {
        if ($source.Width -ne $main.W -or $source.Height -ne $main.H) {
            Throw-DriverError 'UI_CHANGED' '标题截图与当前窗口尺寸不一致，未触碰输入区'
        }
        $headerH = [Math]::Min($source.Height, [int][Math]::Ceiling(64 * $dpi / 96))
        $header = [Drawing.Bitmap]::new($source.Width, $headerH)
        try {
            $graphics = [Drawing.Graphics]::FromImage($header)
            try { $graphics.DrawImage($source, [Drawing.Rectangle]::new(0, 0, $source.Width, $headerH),
                    [Drawing.Rectangle]::new(0, 0, $source.Width, $headerH), [Drawing.GraphicsUnit]::Pixel) }
            finally { $graphics.Dispose() }
            $memory = [IO.MemoryStream]::new()
            try {
                $header.Save($memory, [Drawing.Imaging.ImageFormat]::Png)
                $headerBytes = $memory.ToArray()
            } finally { $memory.Dispose() }
        } finally { $header.Dispose() }
    } finally { $source.Dispose() }
    $image = 'data:image/png;base64,' + [Convert]::ToBase64String($headerBytes)
    $system = '你只负责定位企业微信主窗口右侧聊天区顶部标题的第一个字中心。左侧搜索框和会话列表不是标题。返回单个 JSON 对象，字段 divider_x、x、y 都是相对这张截图左上角的整数像素坐标；divider_x 是左侧会话列表与右侧聊天区的竖向分界线，x/y 是右侧标题第一个字的中心。没有清楚标题或布局不是双栏时返回 {"error":"title_not_found"}。不要返回文字内容。'
    $request = @{
        model = 'GLM-5.3-Flash'; purpose = 'wecom-title-coordinate'; temperature = 0
        messages = @(
            @{ role = 'system'; content = $system },
            @{ role = 'user'; content = @(
                @{ type = 'text'; text = '请按截图原像素给出 divider_x、x、y；不要缩放坐标。' },
                @{ type = 'image_url'; image_url = @{ url = $image } }
            ) }
        )
    }
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes(($request | ConvertTo-Json -Depth 8 -Compress))
        $response = Invoke-RestMethod -Uri ($url + '/api/client/v1/llm/chat') -Method Post `
            -Headers @{ Authorization = ('Bearer ' + $token) } -ContentType 'application/json' `
            -Body $bytes -TimeoutSec 60
    } catch {
        $status = 0
        if ($null -ne $_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        if ($status -eq 401) { Throw-DriverError 'CONFIG_MISSING' 'GLM 标定鉴权失败（HTTP 401），检查 WeCom 服务端 token；未发送消息' }
        Throw-DriverError 'CONTENT_UNAVAILABLE' ('GLM 标定会话标题坐标失败（HTTP ' + $status + '），未触碰输入区或发送消息')
    }
    $raw = ([string]$response.content).Trim()
    if ($raw.StartsWith('```')) { $raw = ($raw -replace '^```(?:json)?\s*', '' -replace '\s*```$', '').Trim() }
    try { $point = $raw | ConvertFrom-Json -ErrorAction Stop }
    catch { Throw-DriverError 'CONTENT_UNAVAILABLE' 'GLM 返回的标题坐标不是 JSON，未触碰输入区' }
    if ($point.error -or ($point.x -isnot [int] -and $point.x -isnot [long]) -or
            ($point.y -isnot [int] -and $point.y -isnot [long]) -or
            ($point.divider_x -isnot [int] -and $point.divider_x -isnot [long]) -or
            $point.divider_x -lt [int]($main.W * 0.20) -or $point.divider_x -gt [int]($main.W * 0.70) -or
            $point.x -lt ($point.divider_x + [int](8 * $dpi / 96)) -or
            $point.x -gt ($point.divider_x + [int](100 * $dpi / 96)) -or
            $point.y -lt [int](12 * $dpi / 96) -or $point.y -gt [int](56 * $dpi / 96) -or
            $point.x -ge $main.W -or $point.y -ge $main.H) {
        Throw-DriverError 'CONTENT_UNAVAILABLE' 'GLM 标定坐标不在聊天标题区域，未触碰输入区'
    }
    return @{ x = [int]$point.x; y = [int]$point.y; divider_x = [int]$point.divider_x;
        w = $main.W; h = $main.H; dpi = $dpi; cache_path = $cachePath; source = 'glm' }
}

function Save-WeComTitlePoint($Point) {
    if ($Point.source -ne 'glm') { return }
    $dir = Split-Path -Parent $Point.cache_path
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    @{ schema = 1; w = $Point.w; h = $Point.h; dpi = $Point.dpi; divider_x = $Point.divider_x;
        x = $Point.x; y = $Point.y } | ConvertTo-Json -Compress |
        Set-Content -LiteralPath $Point.cache_path -Encoding UTF8
}

function Clear-WeComCachedTitlePointOnCopyFailure($Point, [string]$Failure) {
    # 剪贴板占用/被外部改写属于环境故障，不把可用坐标误判为失效。
    if ($Point.source -ne 'cache' -or
            $Failure -notin @('title_edit_not_focused', 'title_copy_unconfirmed')) { return }
    if (Test-Path -LiteralPath $Point.cache_path) {
        Remove-Item -LiteralPath $Point.cache_path -Force -ErrorAction Stop
    }
}

function Send-WeComTitleDoubleClick([int64]$Hwnd, [int]$ScreenX, [int]$ScreenY) {
    # 两次 WM_LBUTTONDOWN 只是两个单击；企微单聊标题需真正的 WM_LBUTTONDBLCLK。
    $point = New-Object WeComWin32+POINT
    $point.X = $ScreenX; $point.Y = $ScreenY
    [void][WeComWin32]::ScreenToClient([IntPtr]$Hwnd, [ref]$point)
    $lp = [IntPtr](($point.Y -shl 16) -bor ($point.X -band 0xFFFF))
    $ok = [WeComWin32]::PostMessageW([IntPtr]$Hwnd, 0x0201, [IntPtr]1, $lp)
    Start-Sleep -Milliseconds 60
    $ok = [WeComWin32]::PostMessageW([IntPtr]$Hwnd, 0x0202, [IntPtr]::Zero, $lp) -and $ok
    Start-Sleep -Milliseconds 80
    $ok = [WeComWin32]::PostMessageW([IntPtr]$Hwnd, 0x0203, [IntPtr]1, $lp) -and $ok
    Start-Sleep -Milliseconds 60
    $ok = [WeComWin32]::PostMessageW([IntPtr]$Hwnd, 0x0202, [IntPtr]::Zero, $lp) -and $ok
    if (-not $ok) { Throw-DriverError 'UI_CHANGED' '单聊标题双击消息未成功投递，未触碰输入区' }
}

function Get-WeComTitleEditWindow([int64]$Hwnd, [int]$ScreenX, [int]$ScreenY) {
    # 标题编辑框可能已因上一次点击存在；从目标主窗枚举，避免 WindowFromPoint
    # 在企微被其他窗口遮挡时误取到前台应用的 HWND。
    $main = Get-WeComWindowInfo ([IntPtr]$Hwnd)
    $dpi = [int][WeComWin32]::GetDpiForWindow([IntPtr]$Hwnd)
    $headerBottom = $main.Y + [int][Math]::Ceiling(64 * $dpi / 96)
    $edits = New-Object System.Collections.ArrayList
    $callback = [WeComWin32+EnumWindowsProc]{
        param($child, $unused)
        $info = Get-WeComWindowInfo $child
        if ($info.Class -eq 'EditWnd' -and $info.Visible -and $info.Pid -eq $main.Pid -and
                $info.X -le $ScreenX -and $ScreenX -lt ($info.X + $info.W) -and
                $info.Y -le $ScreenY -and $ScreenY -lt ($info.Y + $info.H) -and
                $info.Y -ge $main.Y -and ($info.Y + $info.H) -le $headerBottom) {
            [void]$edits.Add($info.Hwnd)
        }
        return $true
    }
    [void][WeComWin32]::EnumChildWindows([IntPtr]$Hwnd, $callback, [IntPtr]::Zero)
    if ($edits.Count -gt 1) { Throw-DriverError 'UI_CHANGED' '标题位置存在多个编辑框，未触碰输入区' }
    if ($edits.Count -eq 0) { return [IntPtr]::Zero }
    return [IntPtr][int64]$edits[0]
}

function Get-WeComGroupTitleEditWindow([int64]$Hwnd, [int]$ScreenX, [int]$ScreenY) {
    $edit = Get-WeComTitleEditWindow -Hwnd $Hwnd -ScreenX $ScreenX -ScreenY $ScreenY
    if ($edit -ne [IntPtr]::Zero) { return $edit }
    # 一次点击会从未选中态选中完整群名；重复点击可能取消全选。
    [void](Send-WeComClick -Hwnd $Hwnd -ScreenX $ScreenX -ScreenY $ScreenY)
    Start-Sleep -Milliseconds 150
    $edit = Get-WeComTitleEditWindow -Hwnd $Hwnd -ScreenX $ScreenX -ScreenY $ScreenY
    if ($edit -eq [IntPtr]::Zero) { throw 'title_edit_not_focused' }
    return $edit
}

function Get-WeComCopiedTitle([int64]$Hwnd, [string]$Section, $Point) {
    # 保存纯文本剪贴板 → 写唯一 nonce → 选中标题复制 → 按序号 CAS 恢复。
    # 原剪贴板是图片/富格式时不触碰；若外部进程同时改剪贴板，不覆盖其新内容。
    $main = Get-WeComWindowInfo ([IntPtr]$Hwnd)
    $screenX = [int]($main.X + $Point.x)
    $screenY = [int]($main.Y + $Point.y)
    $nonce = 'AidWecomTitle:' + [guid]::NewGuid().ToString('N')
    $window = $null; $original = $null; $touched = $false
    $copied = ''; $copySeq = $null; $nonceSeq = $null; $failure = ''
    $restored = $false
    try {
        $originalSeq = [WeComWin32]::GetClipboardSequenceNumber()
        $original = Get-WeComPlainClipboard
        if ($originalSeq -ne [WeComWin32]::GetClipboardSequenceNumber()) { throw 'clipboard_changed' }
        $window = New-WeComClipboardOwner
        $written = [uint32]0
        $touched = $true
        if (-not [WeComWin32]::ExchangeClipboardText($window.Handle, $originalSeq, $original.text,
                $original.empty, $nonce, $false, [ref]$written)) { throw 'clipboard_changed' }
        $nonceSeq = [WeComWin32]::GetClipboardSequenceNumber()
        $check = Get-WeComPlainClipboard
        if ($check.empty -or $check.text -cne $nonce -or
                [WeComWin32]::GetClipboardOwner() -ne $window.Handle -or
                $nonceSeq -ne [WeComWin32]::GetClipboardSequenceNumber()) { throw 'clipboard_nonce_unconfirmed' }
        if (-not [WeComWin32]::IsWindow([IntPtr]$Hwnd)) { throw 'window_changed' }
        if ($Section -eq 'group') {
            $copyHwnd = Get-WeComGroupTitleEditWindow -Hwnd $Hwnd -ScreenX $screenX -ScreenY $screenY
            if (-not (Send-WeComAttachChordKey -Hwnd $copyHwnd.ToInt64() -Vk 0x41)) { throw 'title_select_failed' }
        } elseif ($Section -eq 'contact' -or $Section -eq 'other') {
            Send-WeComTitleDoubleClick -Hwnd $Hwnd -ScreenX $screenX -ScreenY $screenY
            $copyHwnd = [IntPtr]$Hwnd
        } else {
            throw 'section_missing'
        }
        Start-Sleep -Milliseconds 120
        $current = Get-WeComWindowInfo ([IntPtr]$Hwnd)
        if ($current.Pid -ne $main.Pid -or $current.X -ne $main.X -or $current.Y -ne $main.Y -or
                $current.W -ne $main.W -or $current.H -ne $main.H) { throw 'window_changed' }
        if (-not (Send-WeComAttachChordKey -Hwnd $copyHwnd.ToInt64() -Vk 0x43)) { throw 'title_copy_failed' }
        $deadline = [DateTime]::UtcNow.AddMilliseconds(1500)
        while ([DateTime]::UtcNow -lt $deadline) {
            $seq = [WeComWin32]::GetClipboardSequenceNumber()
            if ($seq -ne $nonceSeq) {
                # 企微复制标题会同时发布 WeWork/HTML 富格式；只读取其 UnicodeText。
                $read = @{ empty = -not [Windows.Forms.Clipboard]::ContainsText(); text = [Windows.Forms.Clipboard]::GetText() }
                $owner = [WeComWin32]::GetClipboardOwner()
                $ownerPid = 0
                [void][WeComWin32]::GetWindowThreadProcessId($owner, [ref]$ownerPid)
                if ($owner -ne $window.Handle -and $ownerPid -ne $main.Pid) {
                    throw 'clipboard_changed'
                }
                if ($seq -eq [WeComWin32]::GetClipboardSequenceNumber() -and
                        $ownerPid -eq $main.Pid -and -not $read.empty -and
                        $read.text -cne $nonce -and $read.text.Length -gt 0) {
                    $copied = $read.text; $copySeq = $seq
                    break
                }
            }
            Start-Sleep -Milliseconds 75
        }
        if ($null -eq $copySeq) { throw 'title_copy_unconfirmed' }
    } catch { $failure = $_.Exception.Message }
    finally {
        if ($touched -and $null -ne $original) {
            try {
                $seq = [WeComWin32]::GetClipboardSequenceNumber()
                $now = @{ empty = -not [Windows.Forms.Clipboard]::ContainsText(); text = [Windows.Forms.Clipboard]::GetText() }
                $stable = $seq -eq [WeComWin32]::GetClipboardSequenceNumber()
                $nonceOwned = $null -ne $window -and -not $now.empty -and
                    [WeComWin32]::GetClipboardOwner() -eq $window.Handle -and $now.text -ceq $nonce
                $copyOwned = $null -ne $copySeq -and $seq -eq $copySeq -and $now.text -ceq $copied
                if ($stable -and ($nonceOwned -or $copyOwned)) {
                    $written = [uint32]0
                    $restored = [WeComWin32]::ExchangeClipboardText($window.Handle, $seq, $now.text,
                        $now.empty, $original.text, $original.empty, [ref]$written)
                }
            } catch { $failure = 'clipboard_restore_failed' }
            if (-not $restored) { $failure = 'clipboard_restore_failed' }
        }
        if ($null -ne $window) { $window.Dispose() }
    }
    if ($failure) {
        try { Clear-WeComCachedTitlePointOnCopyFailure -Point $Point -Failure $failure }
        catch { $failure += ';title_cache_invalidate_failed' }
    }
    if ($failure) { Throw-DriverError 'UI_CHANGED' ('标题复制或剪贴板恢复失败（' + $failure + '），未触碰输入区或发送消息') }
    return $copied
}

function Invoke-SendChatOcr([string]$ImagePath, [string]$Mode) {
    # _common 的 OCR 入口 + 原始输出落 driver-log（真机排障）
    $parsed = Invoke-WeComChatOcr -ImagePath $ImagePath -Mode $Mode
    Write-DriverLog ('OCR[' + $Mode + '] ' + ($parsed | ConvertTo-Json -Compress -Depth 10))
    return $parsed
}

function Get-WeComBottomTokens {
    # boxes 只取底部输入带：y0 >= 0.80h 且 x0 > min(620,0.34w)（含工具栏与输入区，
    #             排除左栏会话列表；右缘 0.95w 截掉窗口边框噪声）
    param(
        [Parameter(Mandatory)]$Boxes,
        [Parameter(Mandatory)][int]$W,
        [Parameter(Mandatory)][int]$H
    )
    $chatXMin = [Math]::Min(620, [int]($W * 0.34))
    return @($Boxes | Where-Object {
        [double]$_.y0 -ge ($H * 0.80) -and
        [double]$_.x0 -gt $chatXMin -and [double]$_.x0 -lt ($W * 0.95)
    } | Sort-Object { [double]$_.y0 }, { [double]$_.x0 })
}

function Test-WeComTextInputToken($Token, [int]$W, [int]$H) {
    # 工具栏约 0.83h；右侧总结/群信息栏也不是编辑区。与 input OCR 判空带一致，
    # 只有编辑区内部 token 可供 Jev 选择；空白框保留既有 0.5w/0.9h 回退坐标。
    $cx = ([double]$Token.x0 + [double]$Token.x1) / 2
    $text = ConvertTo-WeComNormalized ([string]$Token.text)
    return ([double]$Token.y0 -ge $H * 0.855 -and [double]$Token.y1 -le $H * 0.97 -and
        $cx -gt $W * 0.40 -and $cx -lt $W * 0.74 -and -not $text.StartsWith('发送'))
}

function Test-WeComTitleMatch {
    # 群标题使用企微复制的原文全等；单聊沿用 chat-select.ps1 的身份规则：
    # 目标名剥 @微信 后缀，归一化后相等或以「名字 + 分隔符」开头。
    # 纯子串不放行（防「陆伟」落入「陆伟民」前缀陷阱）。
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string]$Title,
        [Parameter(Mandatory)][string]$Expected,
        [switch]$RequireWechat,
        [switch]$Group
    )
    if ($Group) { return [string]::Equals($Title, $Expected, [StringComparison]::Ordinal) }
    $expected = $Expected -replace '@微信$', ''
    $tn = ConvertTo-WeComNormalized $Title
    $en = ConvertTo-WeComNormalized $expected
    if ($RequireWechat) { return $tn -eq ($en + '@微信') }
    return ($tn -eq $en) -or $tn.StartsWith($en + '@') -or $tn.StartsWith($en + '（') -or $tn.StartsWith($en + '(')
}

function Format-WeComTokenLines {
    # token 数组 → Jev state 行（键前缀 T/B + 文本 + 中心坐标，供 input_point choice）
    param(
        [Parameter(Mandatory)]$Tokens,
        [Parameter(Mandatory)][string]$KeyPrefix
    )
    $lines = @()
    for ($i = 0; $i -lt $Tokens.Count; $i++) {
        $tk = $Tokens[$i]
        $cx = [int](([double]$tk.x0 + [double]$tk.x1) / 2)
        $cy = [int](([double]$tk.y0 + [double]$tk.y1) / 2)
        $lines += ('{0}{1}: text={2} center=({3},{4}) x0={5} x1={6} y0={7} y1={8}' -f `
            $KeyPrefix, $i, [string]$tk.text, $cx, $cy, [int][double]$tk.x0, [int][double]$tk.x1, [int][double]$tk.y0, [int][double]$tk.y1)
    }
    return $lines
}

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WecomCli.MessageSend' -Body {
    $swTotal = [System.Diagnostics.Stopwatch]::StartNew()
    $timing = @{ precheck = 0; jev1 = 0; input_click = 0; typing = 0; send_wait = 0; final_ocr = 0; jev2 = 0; total = 0 }
    $shots = New-Object System.Collections.ArrayList
    $requireWechat = $Section -ne 'group' -and (($TargetName -match '@微信$') -or ($Section -eq 'contact' -and $Subtitle -eq '微信联系人'))
    $expectedName = $TargetName -replace '@微信$', ''
    # 核验文本（step5/6 前缀与 Jev 关联证据用）：-ExpectedPrefix 优先，缺省取 -Text 原文
    $verifyText = $Text
    if ($ExpectedPrefix -ne '') { $verifyText = $ExpectedPrefix }
    if ($SkipVerify -and $VerifyOnly) {
        Throw-DriverError 'INVALID_ARGUMENT' '-SkipVerify 与 -VerifyOnly 互斥（前者跳过核验，后者只做核验）'
    }

    if (-not $VerifyOnly) {
    $mainHwnd = Resolve-WeComMainWindow
    $main = Get-WeComWindowInfo ([IntPtr]$mainHwnd)

    # 1) GLM 标定标题首字坐标；OCR 仅用于底部输入点候选。
    Write-DriverLog ('target=' + $TargetName + ' subtitle=' + $Subtitle + ' section=' + $Section + ' mainHwnd=' + $mainHwnd + ' rect=(' + $main.X + ',' + $main.Y + ',' + $main.W + 'x' + $main.H + ')')
    $t0 = $swTotal.ElapsedMilliseconds
    $pre = Save-StepShot $mainHwnd 'step1-precheck.png'
    [void]$shots.Add([string]$pre.path)
    $titlePoint = Get-WeComTitlePoint -Hwnd $mainHwnd -ImagePath $pre.path
    $boxesOcr = Invoke-SendChatOcr ([string]$pre.path) 'boxes'
    $boxes = @($boxesOcr.boxes)
    $bottomTokens = @(Get-WeComBottomTokens -Boxes $boxes -W $pre.w -H $pre.h)
    $timing.precheck = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step1 标题坐标 point=' + $titlePoint.x + ',' + $titlePoint.y + ' source=' + $titlePoint.source + '；底部 OCR ' + $bottomTokens.Count + ' token')

    # 2) Jev 只选底部输入点；等待结束后复制标题作唯一会话门禁。
    $t0 = $swTotal.ElapsedMilliseconds
    $stateLines = @()
    $stateLines += '企业微信主窗口底部输入带 OCR 结果（窗口像素坐标）：'
    $stateLines += '—— 底部输入带（y>0.80h，含工具栏图标行与文本输入区；右侧可能有智能总结侧栏，非输入框）：'
    if ($bottomTokens.Count -gt 0) { $stateLines += (Format-WeComTokenLines -Tokens $bottomTokens -KeyPrefix 'B') }
    else { $stateLines += '（无 token）' }
    $stateLines += '任务：从底部候选中选聊天文本输入框位置；标题随后由本机复制校验，不需要判断会话。'
    $stateText = $stateLines -join "`n"
    $questions = @{}
    $critInput = @{}
    if ($bottomTokens.Count -gt 0) {
        # 输入点选择只在底部带有 token 时可问（Jev 按 token 中心点击）；
        # 「发送(S)」按钮不是输入框（2026-09-28 真机：Jev 曾误选，空框时点它侥幸无害，
        # 但语义错误），按归一化前缀剔除后无可选 token 则不问该题（走比例坐标降级）
        for ($i = 0; $i -lt $bottomTokens.Count; $i++) {
            $tk = $bottomTokens[$i]
            if (-not (Test-WeComTextInputToken -Token $tk -W $pre.w -H $pre.h)) { continue }
            $cx = [int](([double]$tk.x0 + [double]$tk.x1) / 2)
            $cy = [int](([double]$tk.y0 + [double]$tk.y1) / 2)
            $critInput[('B' + $i)] = ('OCR文本「' + [string]$tk.text + '」中心坐标(' + $cx + ',' + $cy + ')')
        }
        if ($critInput.Count -gt 0) {
            $questions.input_point = @{ type = 'choice'; instructions = '点击底部输入带中哪个 token 的位置可以聚焦文本输入框？（输入框是可输入文字的大块空白区域）'; criteria = $critInput }
        }
    }
    $jev1 = @{ used = $false; latency_ms = 0; reason = 'no_input_candidate'; answers = @{} }
    if ($questions.Count -gt 0) { $jev1 = Invoke-WeComJev -StateText $stateText -Questions $questions }
    $timing.jev1 = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step2 jev#1 used=' + $jev1.used + ' latency=' + $jev1.latency_ms + 'ms reason=' + [string]$jev1.reason)
    if ($jev1.used) {
        Write-DriverLog ('step2 jev#1 input point=' + [string]$jev1.answers.input_point.choice)
    }

    $title = Get-WeComCopiedTitle -Hwnd $mainHwnd -Section $Section -Point $titlePoint
    # 复制到当前标题就证明坐标有效，即使当前会话不是目标，导航后仍可复用。
    Save-WeComTitlePoint $titlePoint
    Write-DriverLog ('step2 标题复制 title=' + $title)
    $rightOk = Test-WeComTitleMatch -Title $title -Expected $TargetName -RequireWechat:$requireWechat -Group:($Section -eq 'group')
    $navReason = ''
    if (-not $rightOk) { $navReason = ('当前会话标题「' + $title + '」与目标「' + $expectedName + '」不一致') }
    if ($requireWechat -and -not (Test-WeComTitleMatch -Title $title -Expected $TargetName -RequireWechat)) {
        Throw-DriverError 'UI_CHANGED' '当前标题未确认微信外部联系人，已中止；未输入或发送消息'
    }
    if (-not $rightOk) {
        # 非目标/无法判定：不清理输入区，交还 TS 编排（search+select 后二次调用本驱动）
        $timing.total = [int]$swTotal.ElapsedMilliseconds
        Write-DriverLog ('navigate_required=true reason=' + $navReason + ' timing=' + ($timing | ConvertTo-Json -Compress))
        return @{
            navigate_required = $true
            reason = $navReason
            title = $title
            jev = @{ used = $jev1.used; latency_ms = [int]$jev1.latency_ms; reason = [string]$jev1.reason }
            timing_ms = $timing
            screenshot_paths = @($shots.ToArray())
        }
    }
    $title2 = $title

    # input_point：Jev 选 token 中心；降级比例坐标 (0.500w,0.900h)（M2 标定）
    $ipX = [int]($main.X + $main.W * $script:ChatInputRx)
    $ipY = [int]($main.Y + $main.H * $script:ChatInputRy)
    $ipSrc = 'ratio'
    $jevPoint = ''
    if ($jev1.used) { $jevPoint = [string]$jev1.answers.input_point.choice }
    if ($jevPoint -match '^B(\d+)$') {
        $idx = [int]$Matches[1]
        if ($idx -ge 0 -and $idx -lt $bottomTokens.Count -and $critInput.ContainsKey($jevPoint) -and
                (Test-WeComTextInputToken -Token $bottomTokens[$idx] -W $pre.w -H $pre.h)) {
            $tk = $bottomTokens[$idx]
            $ipX = [int]($main.X + ([double]$tk.x0 + [double]$tk.x1) / 2)
            $ipY = [int]($main.Y + ([double]$tk.y0 + [double]$tk.y1) / 2)
            $ipSrc = 'jev'
        }
    }

    # 3) 首次标题已确认；点击输入框后直接清理并输入。
    #    已确认的窗口若销毁重建，停止本次发送，不能沿用旧窗口的标题结论。
    if (-not [WeComWin32]::IsWindow([IntPtr]$mainHwnd)) {
        Throw-DriverError 'UI_CHANGED' '已确认的主窗口重建，未清理或发送消息'
    }
    $t0 = $swTotal.ElapsedMilliseconds
    Write-DriverLog ('step3 click 输入框 screen=(' + $ipX + ',' + $ipY + ') source=' + $ipSrc)
    [void](Send-WeComClick -Hwnd $mainHwnd -ScreenX $ipX -ScreenY $ipY)
    Start-Sleep -Milliseconds 300
    if (-not [WeComWin32]::IsWindow([IntPtr]$mainHwnd)) {
        Throw-DriverError 'UI_CHANGED' '点击输入框后主窗口已重建，未清理或发送消息'
    }
    $focusedMain = Get-WeComWindowInfo ([IntPtr]$mainHwnd)
    if ($focusedMain.Pid -ne $main.Pid -or $focusedMain.Class -ne $main.Class -or -not $focusedMain.Visible -or
            $focusedMain.X -ne $main.X -or $focusedMain.Y -ne $main.Y -or
            $focusedMain.W -ne $main.W -or $focusedMain.H -ne $main.H) {
        Throw-DriverError 'UI_CHANGED' '点击输入框后主窗口身份或位置变化，未清理或发送消息'
    }
    $cleared = Clear-WeComFocusedInput -Hwnd $mainHwnd
    if (-not $cleared) {
        Throw-DriverError 'UI_CHANGED' '目标输入框清理键未成功投递，已中止且未输入或发送消息'
    }
    Write-DriverLog 'step3 目标输入框已投递 Ctrl+A/Delete，准备输入本次消息'
    $timing.input_click = [int]($swTotal.ElapsedMilliseconds - $t0)

    $t0 = $swTotal.ElapsedMilliseconds
    # 多行沿用剪贴板通道；单行沿用 Send-WeComText。两者都输入到已清理的目标输入框。
    $isMultiline = ($Text -match "[`r`n]")
    if ($isMultiline) {
        # 副作用：覆盖用户剪贴板且不恢复，避免异步 paste handler 读到恢复后的旧内容。
        $textPreview = $Text.Substring(0, [Math]::Min(12, $Text.Length)) -replace "[`r`n]", '⏎'
        $clipOk = $false
        $clipTries = 0
        for ($i = 1; $i -le 5; $i++) {
            $clipTries = $i
            try { Set-Clipboard -Value $Text; $clipOk = $true; break }
            catch { if ($i -lt 5) { Start-Sleep -Milliseconds 150 } }
        }
        if (-not $clipOk) {
            Throw-DriverError 'CONFIG_MISSING' ('剪贴板写入失败（重试 ' + $clipTries + ' 次均失败），无法经剪贴板粘贴通道输入多行文本（text 前 12 字=「' + $textPreview + '」共 ' + $Text.Length + ' 字）')
        }
        Write-DriverLog ('step3 多行粘贴通道：剪贴板写入第 ' + $clipTries + ' 次成功；text 前 12 字=「' + $textPreview + '」共 ' + $Text.Length + ' 字（多行文本可能敏感，不记全文）')
        $pasted = Send-WeComAttachChordKey -Hwnd $mainHwnd -Vk 0x56
        Write-DriverLog ('step3 attachstate Ctrl+V 发出=' + $pasted)
        if (-not $pasted) {
            Throw-DriverError 'UI_CHANGED' 'Ctrl+V 未能投递到目标窗口，已中止且未按 Enter 发送'
        }
        Start-Sleep -Milliseconds 500
    } else {
        Send-WeComText -Hwnd $mainHwnd -Text $Text
        Start-Sleep -Milliseconds 400
    }
    $typed = Save-StepShot $mainHwnd 'step2-typed.png'
    [void]$shots.Add([string]$typed.path)

    # typing 覆盖逐字输入或剪贴板写入与粘贴
    $timing.typing = [int]($swTotal.ElapsedMilliseconds - $t0)

    # 4) 输入后的标题 OCR 不再作为发送门禁；沿用清理前确认的目标会话。
    } else {
        # —— -VerifyOnly 模式（任务 3.4）：不做输入/发送（跳过 step1-4 的全部输入动作，
        #    不点输入框、不碰草稿、不按 Enter），仅准备既有 step5 终态证据的输入：
        #    当前窗口截图 + 复制标题 + 核验文本前缀（-ExpectedPrefix 优先，缺省 -Text）
        $mainHwnd = Resolve-WeComMainWindow
        $main = Get-WeComWindowInfo ([IntPtr]$mainHwnd)
        Write-DriverLog ('verify-only target=' + $TargetName + ' section=' + $Section + ' mainHwnd=' + $mainHwnd + ' rect=(' + $main.X + ',' + $main.Y + ',' + $main.W + 'x' + $main.H + ')')
        $after = Save-StepShot $mainHwnd 'step3-after.png'
        [void]$shots.Add([string]$after.path)
        $afterPoint = Get-WeComTitlePoint -Hwnd $mainHwnd -ImagePath $after.path
        $title2 = Get-WeComCopiedTitle -Hwnd $mainHwnd -Section $Section -Point $afterPoint
        Write-DriverLog ('verify-only 当前标题 title=' + $title2)
        # 第八轮评审 P2：核验证据一致性——当前标题与目标严格匹配（使用
        # Test-WeComTitleMatch 规则，含 @微信 后缀/「名+@/（」前缀形态），且必须先于 §5-6
        # 三选二/Jev 判定。否则「输入框空 + 气泡/列表含前缀」可能在非目标会话凑满两项，
        # 把别的会话的证据当成目标会话发送成功；不一致 → 无法确认（EXECUTION_UNKNOWN，
        # 绝不 ocr-bubble 成功），消息可能已发出，不自动重试
        if (-not (Test-WeComTitleMatch -Title $title2 -Expected $TargetName -RequireWechat:$requireWechat -Group:($Section -eq 'group'))) {
            Throw-DriverError 'EXECUTION_UNKNOWN' ('发送后校验无法确认：当前会话标题「' + $title2 + '」与目标「' + $expectedName + '」不一致，核验证据可能来自其他会话（不会自动重试，请人工核对）')
        }
        Save-WeComTitlePoint $afterPoint
        # step5/6 的核验前缀与 Jev 关联文本统一由下方 §5-6 按 $verifyText 计算（-ExpectedPrefix 优先，缺省 -Text）
        # step6 Jev 状态文本复用：多行脱敏预览变量与发送路径同源
        $isMultiline = ($verifyText -match "[`r`n]")
        $textPreview = $verifyText.Substring(0, [Math]::Min(12, $verifyText.Length)) -replace "[`r`n]", '⏎'
    }

    # 5) Enter 发送 → 1.2s 渲染等待 → 终态证据 OCR（input/bubble/preview 三模式）
    #    发送前重验主窗口存活（Enter 打在已销毁窗口上=静默未发，却会走 unknown 语义，必须防）；
    #    -VerifyOnly 跳过发送子段（截图已在上方分支取得，绝不二次 Enter——防重复发送）
    if (-not $VerifyOnly) {
        if (-not [WeComWin32]::IsWindow([IntPtr]$mainHwnd)) {
            $mainHwnd = Resolve-WeComMainWindow
            Write-DriverLog ('step5 主窗口已重建，重解析 hwnd=' + $mainHwnd)
        }
        Write-DriverLog 'step5 PostMessage Enter 发送'
        $t0 = $swTotal.ElapsedMilliseconds
        Send-WeComEnter -Hwnd $mainHwnd
        Start-Sleep -Milliseconds 1200
        $mainHwnd = Resolve-WeComMainWindow
        $after = Save-StepShot $mainHwnd 'step3-after.png'
        [void]$shots.Add([string]$after.path)
        $timing.send_wait = [int]($swTotal.ElapsedMilliseconds - $t0)
    }

    if ($SkipVerify) {
        # —— -SkipVerify 模式（任务 3.4）：跳过 step5 终态证据与判定（本地库核验由 TS 层
        #    接管），发送完成后直接返回 sent=true/verified=false，不产生 EXECUTION_UNKNOWN
        $timing.total = [int]$swTotal.ElapsedMilliseconds
        Write-DriverLog ('done skip-verify timing=' + ($timing | ConvertTo-Json -Compress))
        return @{
            navigate_required = $false
            title = $title2
            sent = $true
            verified = $false
            sent_verification = @{ method = 'skipped'; result = 'unverified' }
            input_point = @{ x = $ipX; y = $ipY; source = $ipSrc }
            timing_ms = $timing
            screenshot_paths = @($shots.ToArray())
        }
    }

    $t0 = $swTotal.ElapsedMilliseconds
    # 核验前缀统一取 $verifyText（-ExpectedPrefix 优先，缺省取 -Text；任务 3.4 修正：
    # 旧实现此处取 $Text，-ExpectedPrefix 被静默覆盖失效）
    $prefix = ConvertTo-WeComNormalized $verifyText
    # 前缀取 8 字（2026-09-28 真机实测修订：会话列表预览列只显示约 10 字 +「..」截断，
    # 12 字前缀永远匹配不上截断预览；8 字对本次输入文本的区分度已足够）
    if ($prefix.Length -gt 8) { $prefix = $prefix.Substring(0, 8) }
    # ① 输入框已清空（input 模式：占位符/按钮/侧栏/图标碎字均已剔除）
    $inputAfter = Invoke-SendChatOcr ([string]$after.path) 'input'
    $inputEmptyHit = ($inputAfter.input_empty -eq $true)
    # ② 消息区气泡区（boxes 直读）：y∈[0.55h,0.80h]、x∈[chatXMin,0.95w] 的 token 归一化后
    #    含 text 前缀即命中（2026-09-28 真机实测：py bubble 模式在 1280 宽窗口/应用类会话
    #    返回空 last_messages，布局解析不稳导致终态误判 EXECUTION_UNKNOWN——消息实际已发。
    #    boxes 直读更鲁棒；长消息换行的气泡靠全带拼接匹配兜底）
    $chatXMin2 = [Math]::Max([int]($after.w * 0.10), 620)
    $afterBoxes = Invoke-SendChatOcr ([string]$after.path) 'boxes'
    $bubbleTokens = @($afterBoxes.boxes | Where-Object {
        [double]$_.y0 -ge ($after.h * 0.55) -and [double]$_.y0 -lt ($after.h * 0.80) -and
        [double]$_.x0 -gt $chatXMin2 -and [double]$_.x0 -lt ($after.w * 0.95)
    } | Sort-Object { [double]$_.y0 }, { [double]$_.x0 })
    $bubbleHit = $false
    $bubbleTexts = @()
    $bubbleHitTexts = @()
    foreach ($bt in $bubbleTokens) {
        $nbt = ConvertTo-WeComNormalized ([string]$bt.text)
        if ($nbt -and $nbt.Contains($prefix)) { $bubbleHit = $true; $bubbleHitTexts += [string]$bt.text }
        if ($bubbleTexts.Count -lt 8) { $bubbleTexts += [string]$bt.text }
    }
    $allBubbleNorm = ConvertTo-WeComNormalized (($bubbleTokens | ForEach-Object { [string]$bt.text }) -join '')
    if ($allBubbleNorm.Contains($prefix)) { $bubbleHit = $true }
    # Jev#2 证据视图必须包含命中前缀的 token（2026-09-29 真机实测修订：旧文件气泡占满
    # 8-token 上限把新发文本气泡挤出 Jev 视野 → 好证据被遮蔽误判 not_sent；命中项置顶必含）
    $bubbleTexts = @($bubbleHitTexts | Select-Object -First 4) + @($bubbleTexts | Where-Object { $bubbleHitTexts -notcontains $_ } | Select-Object -First 6)
    # ③ 会话列表：任一行含 text 前缀即命中（2026-09-28 真机实测修订：应用类会话（文件传输
    #    助手等）的聊天区为 CEF 渲染，PrintWindow 截出纯白（实测 mean=247 stddev=0），气泡
    #    证据结构性不可得，终态只能靠 ①+③；旧「含目标名 且 含前缀」的 AND 在 preview 列
    #    错位时全灭——12 字归一化前缀本身区分度足够，目标名降为记录项不参与判定）
    $preview = Invoke-SendChatOcr ([string]$after.path) 'preview'
    $normTarget = ConvertTo-WeComNormalized $expectedName
    $nameHit = $false
    $prefixHit = $false
    foreach ($t in @($preview.texts_norm)) {
        $nt = [string]$t
        if ($nt.Contains($normTarget)) { $nameHit = $true }
        if ($nt.Contains($prefix)) { $prefixHit = $true }
    }
    $previewPass = $prefixHit
    $ruleChecks = 0
    if ($inputEmptyHit) { $ruleChecks++ }
    if ($bubbleHit) { $ruleChecks++ }
    if ($previewPass) { $ruleChecks++ }
    $timing.final_ocr = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step5 终态规则三选二：①输入框清空=' + $inputEmptyHit + ' ②气泡含前缀「' + $prefix + '」=' + $bubbleHit + ' ③会话列表（含目标名=' + $nameHit + ' 含前缀=' + $prefixHit + '）=' + $previewPass + ' → ' + $ruleChecks + '/3')

    # 6) Jev #2 两问合一（sent_successfully / failure_mode）；降级 → 规则三选二
    $t0 = $swTotal.ElapsedMilliseconds
    $stateTextForText = $verifyText
    if ($isMultiline) {
        # 多行脱敏（与 step3 日志/README 契约一致）：state 会整段落 driver-log，
        # 多行正文只给 Jev 前 12 字预览 + 总字数，不落 120 字（气泡/预览 OCR 证据行
        # 本就携带屏幕内容，前缀足以让 Jev 关联证据做终态判定）
        $stateTextForText = ($textPreview + '…（多行，共 ' + $verifyText.Length + ' 字，脱敏只记前 12 字）')
    } elseif ($stateTextForText.Length -gt 120) { $stateTextForText = $stateTextForText.Substring(0, 120) + '…（截断，共 ' + $verifyText.Length + ' 字）' }
    $previewHits = @()
    foreach ($row in @($preview.texts)) {
        $nt = ConvertTo-WeComNormalized ([string]$row)
        if ($nt.Contains($normTarget) -or $nt.Contains($prefix)) { $previewHits += [string]$row }
        if ($previewHits.Count -ge 5) { break }
    }
    $state2Lines = @()
    if ($VerifyOnly) {
        $state2Lines += '企业微信消息发送后的 OCR 校验证据（本轮仅核验，未执行输入/发送；截图为当前窗口实时状态）：'
    } else {
        $state2Lines += '企业微信消息发送后的 OCR 校验证据（PostMessage Enter 后 1.2s 截图）：'
    }
    $state2Lines += ('—— 输入区 token：' + $(if (@($inputAfter.texts).Count -gt 0) { (@($inputAfter.texts) -join ' | ') } else { '（空）' }))
    $state2Lines += ('—— 消息区气泡 token（底部带，最多 8 个）：' + $(if ($bubbleTexts.Count -gt 0) { ($bubbleTexts -join ' / ') } else { '（空）' }))
    $state2Lines += '（证据说明：应用类会话（如文件传输助手/微信客服等官方应用）的消息区为内嵌网页渲染，截图可能整片空白——气泡 token 为空不等于未发送；此时「输入区已清空 + 会话列表预览出现该文本 + 『刚刚』时间戳」即为已成功发送的有效证据）'
    $state2Lines += ('—— 会话列表含目标名/前缀的行：' + $(if ($previewHits.Count -gt 0) { ($previewHits -join ' / ') } else { '（无）' }))
    $state2Lines += ('目标会话名：' + $expectedName)
    if ($VerifyOnly) {
        $state2Lines += ('待核验的已发送文本（本轮未执行输入/发送）：' + $stateTextForText)
    } else {
        $state2Lines += ('已输入并按 Enter 发送的文本：' + $stateTextForText)
    }
    $state2Lines += '任务：判断该文本是否已成功发送到目标会话。'
    $state2Text = $state2Lines -join "`n"
    $jev2 = Invoke-WeComJev -StateText $state2Text -Questions @{
        sent_successfully = @{ type = 'choice'; instructions = '刚才输入的文本是否已成功发送到目标会话？（注意证据说明：应用类会话气泡区截图空白属正常渲染限制）'; criteria = @{
            yes = '已成功发送'; no = '未发送成功'; unclear = '证据不足无法判断' } }
        failure_mode = @{ type = 'choice'; instructions = '若未成功，失败形态最接近哪种？（已成功时选 unclear 即可）'; criteria = @{
            not_sent = '没有发出去（文本仍在输入框，或输入框已空且会话列表预览也无该文本）'; sent_elsewhere = '可能发到了别的会话'; unclear = '证据不足' } }
    }
    $timing.jev2 = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step6 jev#2 used=' + $jev2.used + ' latency=' + $jev2.latency_ms + 'ms reason=' + [string]$jev2.reason)
    Write-DriverLog ('step6 jev#2 state: ' + ($state2Text -replace "`r?`n", ' / '))
    $method = ''
    if ($jev2.used) {
        $sentChoice = [string]$jev2.answers.sent_successfully.choice
        Write-DriverLog ('step6 jev#2 answers: sent=' + $sentChoice + ' failure_mode=' + [string]$jev2.answers.failure_mode.choice)
        if ($sentChoice -eq 'yes') {
            $method = 'jev'
        } elseif ($sentChoice -eq 'no' -or $sentChoice -eq 'unclear') {
            $fm = [string]$jev2.answers.failure_mode.choice
            if ($fm -ne 'not_sent' -and $fm -ne 'sent_elsewhere') { $fm = 'unclear' }
            Throw-DriverError 'EXECUTION_UNKNOWN' ('发送后校验失败（Jev 判定 ' + $sentChoice + '，failure_mode=' + $fm + '）：消息可能已发出，不会自动重试，请人工核对（详见 driver-log.txt）')
        }
        # 答案非法 → 降级规则三选二（下方继续）
    }
    if ($method -eq '') {
        # 降级链终态：M2 三选二，<2 项 → EXECUTION_UNKNOWN 绝不自动重试
        $method = 'rule_2of3'
        if ($ruleChecks -lt 2) {
            Throw-DriverError 'EXECUTION_UNKNOWN' ('发送后校验失败（三选二仅过 ' + $ruleChecks + '/3 项）：消息可能已发出，不会自动重试，请人工核对（详见 driver-log.txt）')
        }
    }
    $timing.total = [int]$swTotal.ElapsedMilliseconds
    Write-DriverLog ('done method=' + $method + ' rule_checks=' + $ruleChecks + '/3 timing=' + ($timing | ConvertTo-Json -Compress))

    # 7) 返回（navigate_required=false = 已在目标会话完成发送与校验；-VerifyOnly 无输入动作，
    #    input_point 返回 null——TS 侧归一为 null，不下发坐标）
    $sentVerification = @{ method = $method; result = 'sent' }
    if ($method -eq 'rule_2of3') { $sentVerification.rule_checks_passed = $ruleChecks }
    return @{
        navigate_required = $false
        title = $title2
        sent_verification = $sentVerification
        input_point = $(if ($VerifyOnly) { $null } else { @{ x = $ipX; y = $ipY; source = $ipSrc } })
        timing_ms = $timing
        screenshot_paths = @($shots.ToArray())
    }
}
