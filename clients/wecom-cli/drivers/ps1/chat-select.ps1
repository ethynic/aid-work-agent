# drivers/ps1/chat-select.ps1 — wecom_chat_select 驱动（M5：点击 search 结果进入会话）
# 消费 search 留下的 SearchResultWindow2 overlay（M4 起 search 返回后面板保持打开）：
#   1) 解析主窗口（确认在线；select 不搜索，只消费已打开面板）；
#   2) 找**可见的** SearchResultWindow2（类名 + IsWindowVisible 双条件——overlay 关闭后
#      窗口对象以 visible=False 残留，已实测事实，绝不能只按类名）；
#      找不到 → TARGET_REF_STALE（面板已关，请重新 search）；
#   3) 点击前校验（防陈旧面板）：PrintWindow 截 overlay → OCR search 模式 → 在 items 里
#      找 name 归一化 == 目标名（剥 @微信 后缀双向比较）的条目；无 → UI_CHANGED。
#      同名多条（同一人在「联系人」与「聊天记录」分区各一行）取距 payload 坐标最近的一条。
#      坐标信任策略：OCR 复核到的条目自身 x/y 优先，payload X/Y 作对照——两者中心距
#      >40px → UI_CHANGED（面板可能已变）；≤40px 用 OCR 条目坐标（更新鲜）；OCR 条目
#      坐标缺失时用 payload X/Y；
#   4) 屏幕坐标 = overlay 实时 rect.X/Y + 条目 x/y → Send-WeComClick（显式投递 overlay 顶层
#      hwnd，不用 WindowFromPoint 路由——Chromium 嵌入窗口可能盖住 overlay 吞点击）
#      路由，点中结果行实际归属的 HWND）；
#   5) 点击后面板应自动关闭（M2 实测）：轮询 ≤3s 等可见 overlay 消失；未消失 → UI_CHANGED；
#   6) 重新解析主窗口（外部联系人会话会把主窗口撑宽，hwnd 可能不变但 rect 变，已实测）
#      → PrintWindow 截图 → OCR 标题带 → 标题严格校验（复用 message-send 语义：归一化
#      相等，或「名字+@/（」前缀形式；防前缀陷阱：「陆伟」不得匹配「陆伟民」；目标名剥
#      @微信 后缀再比）；不一致 → UI_CHANGED。
# 副作用：进入会话会清除该会话未读角标（企微固有行为）、切换当前会话视图；无出站消息。
# artifact：点击前 overlay 截图 + 点击后主窗口截图 + driver-log.txt（各步耗时 / OCR 摘要 /
# 坐标换算记录；与 search 同款敏感级别，绝不含 key）。
# 注意：本文件含中文，必须以 UTF-8 with BOM 保存。
param(
    [Parameter(Mandatory)][string]$TargetName,
    [string]$Subtitle = '',
    [string]$Section = '',
    [Parameter(Mandatory)][int]$X,
    [Parameter(Mandatory)][int]$Y,
    [Parameter(Mandatory)][string]$ArtifactDir
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$driverDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $driverDir '_common.ps1')

# artifact 目录兜底创建（正式链路由 TS 侧 mkdir；直跑驱动时保证 driver-log 可写）
if (-not (Test-Path -LiteralPath $ArtifactDir)) { New-Item -ItemType Directory -Path $ArtifactDir -Force | Out-Null }

# 诊断日志（同 chat-search.ps1）：各步判定写 artifact 目录 driver-log.txt（真机排障用）
$script:DriverLogPath = Join-Path $ArtifactDir 'driver-log.txt'
function Write-DriverLog([string]$Msg) {
    $line = '[' + ([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')) + '] ' + $Msg
    Add-Content -Path $script:DriverLogPath -Value $line -Encoding UTF8
}

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WecomCli.ChatSelect' -Body {
    $swTotal = [System.Diagnostics.Stopwatch]::StartNew()
    $timing = @{ overlay_check = 0; verify = 0; click = 0; close_wait = 0; title_check = 0; total = 0 }

    # 1) 解析主窗口（确认在线；主窗口本身不用于点击，只为失败语义与日志）
    $mainHwnd = Resolve-WeComMainWindow
    Write-DriverLog ('target=' + $TargetName + ' subtitle=' + $Subtitle + ' section=' + $Section + ' payload_xy=(' + $X + ',' + $Y + ') mainHwnd=' + $mainHwnd)

    # 2) 找可见 SearchResultWindow2（类名 + IsWindowVisible；overlay 关闭后窗口以 visible=False 残留）
    $t0 = $swTotal.ElapsedMilliseconds
    $overlay = $null
    foreach ($w in @(Get-WeComTopLevelWindows)) {
        if ($w.Class -eq 'SearchResultWindow2' -and $w.Visible) { $overlay = $w; break }
    }
    if ($null -eq $overlay) {
        Throw-DriverError 'TARGET_REF_STALE' '搜索结果面板已关闭（SearchResultWindow2 不可见），target_ref 失效，请重新 search 获取新的 target_ref'
    }
    $overlayHwnd = [int64]$overlay.Hwnd
    $timing.overlay_check = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step2 overlay hwnd=' + $overlayHwnd + ' rect=(' + $overlay.X + ',' + $overlay.Y + ',' + $overlay.W + 'x' + $overlay.H + ') 耗时=' + $timing.overlay_check + 'ms')

    # 3) 点击前校验：截 overlay → OCR → 按 name 归一化（剥 @微信 后缀双向比较）找条目
    $t0 = $swTotal.ElapsedMilliseconds
    $shotOverlay = Join-Path $ArtifactDir 'overlay-preclick.png'
    [void](Get-WeComWindowSnapshot -Hwnd $overlayHwnd -Path $shotOverlay)
    $ocr = Invoke-WeComChatOcr -ImagePath $shotOverlay -Mode 'search'
    Write-DriverLog ('OCR[search] ' + ($ocr | ConvertTo-Json -Compress -Depth 10))
    # 目标名剥 @微信 后缀（M2 实测：结果行名称带后缀、搜索词剥后缀；两侧都归一化后双向比较）
    $normTarget = ConvertTo-WeComNormalized ($TargetName -replace '@微信$', '')
    # 同名多条是常见形态（同一人在「联系人」与「聊天记录」分区各一行）：取与 payload 坐标
    # 最近的一条，不取首命中——首命中在同名跨分区场景稳定指向另一行，>40px 护栏会把好面板
    # 误判成已变（假失败循环）；最近条仍受 >40px 护栏约束，fail-closed 语义不变
    $cands = @($ocr.items | Where-Object {
        $n = ConvertTo-WeComNormalized ([string]$_.name)
        ($n -eq $normTarget) -or (($n -replace '@微信$', '') -eq $normTarget)
    })
    $found = $null
    if ($cands.Count -gt 1) {
        $bestDist = [double]::MaxValue
        foreach ($c in $cands) {
            if ($null -eq $c.x -or $null -eq $c.y) { continue }
            $dx = ([int]$c.x - $X); $dy = ([int]$c.y - $Y)
            $d = [Math]::Sqrt($dx * $dx + $dy * $dy)
            if ($d -lt $bestDist) { $bestDist = $d; $found = $c }
        }
        if ($null -ne $found) { Write-DriverLog ('step3 同名条目 ' + $cands.Count + ' 条，取距 payload 坐标最近者 dist=' + [Math]::Round($bestDist, 1) + 'px') }
    }
    if ($null -eq $found -and $cands.Count -gt 0) { $found = $cands[0] }
    if ($null -eq $found) {
        Throw-DriverError 'UI_CHANGED' ('搜索面板当前内容与 target_ref 不符（未找到「' + $TargetName + '」对应的结果条目），面板可能已被更新，请重新 search')
    }
    # 坐标信任策略：OCR 条目自身 x/y 优先，payload X/Y 作对照（中心距 >40px → 面板已变）
    $itemX = $X; $itemY = $Y
    if ($null -ne $found.x -and $null -ne $found.y) {
        $dx = ([int]$found.x - $X); $dy = ([int]$found.y - $Y)
        $dist = [Math]::Sqrt($dx * $dx + $dy * $dy)
        if ($dist -gt 40) {
            Throw-DriverError 'UI_CHANGED' ('OCR 复核坐标与 target_ref 坐标漂移过大（OCR=(' + [int]$found.x + ',' + [int]$found.y + ') payload=(' + $X + ',' + $Y + ') 距离=' + [Math]::Round($dist, 1) + 'px>40px），面板可能已变化，请重新 search')
        }
        $itemX = [int]$found.x; $itemY = [int]$found.y
        Write-DriverLog ('step3 条目命中 ocr_xy=(' + $itemX + ',' + $itemY + ') payload_xy=(' + $X + ',' + $Y + ') 距离=' + [Math]::Round($dist, 1) + 'px（≤40px 用 OCR 坐标）')
    } else {
        Write-DriverLog ('step3 条目命中 OCR 坐标缺失，用 payload_xy=(' + $X + ',' + $Y + ')')
    }
    $timing.verify = [int]($swTotal.ElapsedMilliseconds - $t0)

    # 4) 屏幕坐标 = overlay 实时 rect + 条目 x/y → 显式投递给 overlay 顶层 hwnd。
    #    不用 WindowFromPoint 路由（2026-09-28 真机实测：外部联系人会话的智能总结侧栏等
    #    Chromium 嵌入窗口（Chrome_RenderWidgetHostHWND）Z 序可能盖住 overlay 左半，
    #    自动路由会把点击交给它、被 Chromium 吞掉——M2 message-send 本就显式传 overlay hwnd）。
    $t0 = $swTotal.ElapsedMilliseconds
    $ovl = Get-WeComWindowInfo ([IntPtr]$overlayHwnd)
    $clickX = [int]($ovl.X + $itemX)
    $clickY = [int]($ovl.Y + $itemY)
    $routed = Send-WeComClick -Hwnd $overlayHwnd -ScreenX $clickX -ScreenY $clickY
    $timing.click = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step4 click 结果行 screen=(' + $clickX + ',' + $clickY + ')（overlay rect=(' + $ovl.X + ',' + $ovl.Y + ') + 条目=(' + $itemX + ',' + $itemY + ')）投递 overlay hwnd=' + $routed + ' 耗时=' + $timing.click + 'ms')

    # 5) 等 overlay 自动关闭（≤3s；关闭后窗口以 visible=False 残留属正常）
    $t0 = $swTotal.ElapsedMilliseconds
    $deadline = [DateTime]::UtcNow.AddSeconds(3)
    $closed = $false
    while ([DateTime]::UtcNow -lt $deadline) {
        if (-not [WeComWin32]::IsWindow([IntPtr]$overlayHwnd) -or -not [WeComWin32]::IsWindowVisible([IntPtr]$overlayHwnd)) { $closed = $true; break }
        Start-Sleep -Milliseconds 150
    }
    $timing.close_wait = [int]($swTotal.ElapsedMilliseconds - $t0)
    if (-not $closed) {
        Throw-DriverError 'UI_CHANGED' ('点击搜索结果行后面板未关闭（' + $timing.close_wait + 'ms 内仍可见），点击可能未生效或页面结构已变化')
    }
    Write-DriverLog ('step5 overlay 已关闭 耗时=' + $timing.close_wait + 'ms')

    # 5.5) 清空搜索框查询残留：点击结果行后搜索框仍保留查询词，残留在标题带同高位置、
    #      会污染标题 OCR（2026-09-28 真机实测：标题读到「、搜索文件传输助手」——框内残留
    #      与碎片合并所致，校验误杀）。Ctrl+F 聚焦框 → Ctrl+A+Delete 清空（已验证原语，
    #      清空后框回占位符、overlay 保持关闭，标题带干净）。
    $t0 = $swTotal.ElapsedMilliseconds
    [void](Send-WeComAttachChordKey -Hwnd $mainHwnd -Vk 0x46)
    Start-Sleep -Milliseconds 150
    [void](Clear-WeComSearchBoxV2 -MainHwnd $mainHwnd)
    Start-Sleep -Milliseconds 200
    $timing.box_clear = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step5.5 搜索框残留清空 耗时=' + $timing.box_clear + 'ms')

    # 6) 重新解析主窗口 → 截图 → OCR 标题带 → 严格校验（外部联系人会话会撑宽主窗口）；
    #    点击后 1500ms 渲染等待对齐 message-send 实测节奏（过早 OCR 会读到旧会话标题）
    $t0 = $swTotal.ElapsedMilliseconds
    Start-Sleep -Milliseconds 1500
    $mainHwnd = Resolve-WeComMainWindow
    $shotMain = Join-Path $ArtifactDir 'main-opened.png'
    [void](Get-WeComWindowSnapshot -Hwnd $mainHwnd -Path $shotMain)
    # 标题读取用聊天区带过滤（x0>0.20w，2026-09-28 真机实测修订）：py title 模式的整带拼接
    # 会被搜索框残留/占位符/会话列表首行污染成「搜索文件传输助手」等，几何过滤不再依赖
    # 5.5 清空是否生效（清空仍保留作状态恢复 best-effort）。
    $title = Get-WeComChatAreaTitle -ImagePath $shotMain
    Write-DriverLog ('title(band-filtered)=' + $title)
    # 严格匹配（同 message-send）：归一化后相等，或以「名字 + 分隔符」形式开头（外部联系人
    # 「陆伟 @微信」、群「产品讨论群（13）」）；纯子串不放行（「陆伟」不得落入「陆伟民」）
    $expected = $TargetName -replace '@微信$', ''
    $tn = ConvertTo-WeComNormalized $title
    $en = ConvertTo-WeComNormalized $expected
    $titleOk = ($tn -eq $en) -or $tn.StartsWith($en + '@') -or $tn.StartsWith($en + '（') -or $tn.StartsWith($en + '(')
    if (-not $titleOk) {
        Throw-DriverError 'UI_CHANGED' ('会话标题「' + $title + '」与目标「' + $expected + '」不一致，进入的会话与 target_ref 不符，已判失败')
    }
    $timing.title_check = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step6 会话标题校验通过 title=' + $title + ' 耗时=' + $timing.title_check + 'ms')

    $timing.total = [int]$swTotal.ElapsedMilliseconds
    Write-DriverLog ('done title=' + $title + ' timing=' + ($timing | ConvertTo-Json -Compress))

    # 7) 返回（target 回显 target_ref 身份；clicked 为实际点击的屏幕坐标）
    return @{
        target = @{ name = $TargetName; subtitle = $Subtitle; section = $Section }
        title = $title
        clicked = @{ x = $clickX; y = $clickY }
        timing_ms = $timing
        screenshot_paths = @($shotOverlay, $shotMain)
    }
}
