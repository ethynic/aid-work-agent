# drivers/ps1/send-image.ps1 — wecom_send_image 驱动（M7：发送阶段驱动，智能分发由 TS 层编排）
# 前提：由 TS 层 sendImage.ts 调用（编排骨架与 message-send.ps1 同源，Jev state/question
# 措辞保持稳定，修改前先核对 message-send.ps1 同段）；当前会话是否为目标由本驱动判定——
# 不对时返回 navigate_required=true 交还 TS 编排（search+select 后二次调用本驱动）。
# 流程（真机验证 2026-09-28 experiments/probes/e5-image：Clipboard.SetImage + attachstate
# Ctrl+V → 输入区缩略图使灰度方差 9.8→36.5，Send-WeComEnter 后回落 + 会话列表出现「[图片]」）：
#   1) 解析主窗口 → PrintWindow 截图 → OCR 两带（boxes 模式拿 token+坐标）：
#      标题带（y<0.07h）+ 底部输入带（y>0.72h，含工具栏图标行与输入区）
#   2) Jev #1（三问合一，措辞同 message-send）：right_conversation / input_point / has_draft；
#      降级链：标题归一化规则匹配 / 比例坐标 (0.500w,0.900h) / input 模式判空。
#      非目标/无法判定 → 返回 data {navigate_required:true, reason}；输入区有用户草稿 →
#      UI_CHANGED 中止（**绝不动用户草稿**）
#   3) 粘贴图片：点击 input_point → 截图取输入区（x∈[0.35w,0.90w]、y∈[0.82h,0.95h]）
#      灰度方差基线 std0 → Clipboard.SetImage（Bitmap FromFile + 5 次重试×150ms，全败
#      CONFIG_MISSING）→ attachstate Ctrl+V → 1500ms → 方差复测 std1：std1 ≤ std0+8 →
#      UI_CHANGED「图片预览未出现（粘贴可能未生效）」——此时**尚未按 Enter，无发送副作用**；
#      副作用：剪贴板被覆盖为该图片且**不恢复**（同 message-send 多行粘贴通道契约）
#   4) 发送前会话复核（规则，同 message-send 步骤 4）：重新 OCR 标题带严格匹配目标名；
#      不一致 → UI_CHANGED fail-closed。**已知限制**：图片预览不是文本草稿，Ctrl+A 清不掉，
#      ESC 禁用（最小化企微），无法自动清理——message 注明输入区可能残留图片预览需人工处理
#   5) Send-WeComEnter → 1800ms → 终态证据双判据：①输入区方差回落（std2 < std0+8）
#      ②会话列表 boxes token（x0<0.40w 左栏）归一化含「[图片]」；Jev #2（两问合一：
#      sent_successfully / failure_mode）可用以其判定为准（method=jev），no/unclear →
#      EXECUTION_UNKNOWN 绝不自动重试；Jev 不可用/答案非法降级规则双判据须 2/2 全过
#      （method=rule_2of2），<2/2 → EXECUTION_UNKNOWN
# 返回 data：{ navigate_required:false, target, title, sent_verification:{method,result,...},
#   input_stddev:{before,paste,after}, input_point:{x,y,source}, timing_ms, screenshot_paths }。
# artifact：各步截图 + driver-log.txt（记图片文件名/sha256/大小/方差/Jev 摘要/OCR 摘要；
# **不复制图片本体**；绝不含 TYPESAFE_API_KEY）。
# 注意：本文件含中文，必须以 UTF-8 with BOM 保存。
param(
    [Parameter(Mandatory)][string]$TargetName,
    [string]$Subtitle = '',
    [string]$Section = '',
    [Parameter(Mandatory)][string]$ImagePath,
    [string]$ImageHash = '',
    [Parameter(Mandatory)][string]$ArtifactDir
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$driverDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $driverDir '_common.ps1')

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

# artifact 目录兜底创建（正式链路由 TS 侧 mkdir；直跑驱动时保证 driver-log 可写）
if (-not (Test-Path -LiteralPath $ArtifactDir)) { New-Item -ItemType Directory -Path $ArtifactDir -Force | Out-Null }

# 诊断日志：各阶段判定写 artifact 目录 driver-log.txt（真机排障用，格式同 message-send）
$script:DriverLogPath = Join-Path $ArtifactDir 'driver-log.txt'
function Write-DriverLog([string]$Msg) {
    $line = '[' + ([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')) + '] ' + $Msg
    Add-Content -Path $script:DriverLogPath -Value $line -Encoding UTF8
}

function Save-StepShot([int64]$Hwnd, [string]$Name) {
    # 关键步骤截图存档（窗口重建防御同 message-send：截图前重验窗口存活，失效则重解析一次）
    $path = Join-Path $ArtifactDir $Name
    $h = $Hwnd
    if (-not [WeComWin32]::IsWindow([IntPtr]$h)) {
        $h = Resolve-WeComMainWindow
        Write-DriverLog ('Save-StepShot: 窗口已重建，重解析 hwnd=' + $h)
    }
    $snap = Get-WeComWindowSnapshot -Hwnd $h -Path $path
    return @{ path = $path; left = [int]$snap[0]; top = [int]$snap[1]; w = [int]$snap[2]; h = [int]$snap[3] }
}

function Invoke-SendChatOcr([string]$ImagePath, [string]$Mode) {
    # _common 的 OCR 入口 + 原始输出落 driver-log（真机排障）
    $parsed = Invoke-WeComChatOcr -ImagePath $ImagePath -Mode $Mode
    Write-DriverLog ('OCR[' + $Mode + '] ' + ($parsed | ConvertTo-Json -Compress -Depth 10))
    return $parsed
}

function Get-WeComBandTokens {
    # boxes 模式产物按带过滤（坐标 = 窗口像素坐标；阈值与 message-send.ps1 同源标定）
    param(
        [Parameter(Mandatory)]$Boxes,
        [Parameter(Mandatory)][int]$W,
        [Parameter(Mandatory)][int]$H,
        [Parameter(Mandatory)][string]$Band
    )
    if ($Band -eq 'title') {
        return @($Boxes | Where-Object {
            [double]$_.y0 -lt ($H * 0.07) -and [double]$_.x0 -gt ($W * 0.20)
        } | Sort-Object { [double]$_.y0 }, { [double]$_.x0 })
    }
    $chatXMin = [Math]::Max([int]($W * 0.10), 620)
    return @($Boxes | Where-Object {
        [double]$_.y0 -ge ($H * 0.72) -and
        [double]$_.x0 -gt $chatXMin -and [double]$_.x0 -lt ($W * 0.95)
    } | Sort-Object { [double]$_.y0 }, { [double]$_.x0 })
}

function Get-WeComTitleFromTokens {
    # 标题带 token → 标题行文本：y 中心聚类成视觉行（≤14px 同行），取首行按 x 序拼接
    param([Parameter(Mandatory)]$Tokens)
    if ($Tokens.Count -eq 0) { return '' }
    $anchor = $Tokens[0]
    $anchorCy = ([double]$anchor.y0 + [double]$anchor.y1) / 2
    $line = @($Tokens | Where-Object {
        $cy = ([double]$_.y0 + [double]$_.y1) / 2
        [Math]::Abs($cy - $anchorCy) -le 14
    } | Sort-Object { [double]$_.x0 })
    return (($line | ForEach-Object { [string]$_.text }) -join '')
}

function Test-WeComTitleMatch {
    # 标题严格匹配（同 message-send.ps1 / chat-select.ps1：归一化相等或「名字+分隔符」前缀）
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string]$Title,
        [Parameter(Mandatory)][string]$Expected
    )
    $expected = $Expected -replace '@微信$', ''
    $tn = ConvertTo-WeComNormalized $Title
    $en = ConvertTo-WeComNormalized $expected
    return ($tn -eq $en) -or $tn.StartsWith($en + '@') -or $tn.StartsWith($en + '（') -or $tn.StartsWith($en + '(')
}

function Format-WeComTokenLines {
    # token 数组 → Jev state 行（键前缀 + 文本 + 中心坐标，供 input_point choice）
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

function Get-InputRegionStddev($Snap) {
    # 输入区（窗口像素 x∈[0.35w,0.90w]、y∈[0.82h,0.95h]）灰度标准差；区域按各快照自身
    # 尺寸计算（防窗口中途变宽导致区域漂移）
    return Get-WeComRegionStddev -ImagePath ([string]$Snap.path) `
        -X0 ([int]($Snap.w * 0.35)) -Y0 ([int]($Snap.h * 0.82)) `
        -X1 ([int]($Snap.w * 0.90)) -Y1 ([int]($Snap.h * 0.95))
}

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WecomCli.SendImage' -Body {
    $swTotal = [System.Diagnostics.Stopwatch]::StartNew()
    $timing = @{ precheck = 0; jev1 = 0; input_click = 0; clipboard = 0; paste = 0; title_recheck = 0; send_wait = 0; final_ocr = 0; jev2 = 0; total = 0 }
    $shots = New-Object System.Collections.ArrayList
    $sectionMap = @{ contact = '联系人'; group = '群聊' }
    $sectionDesc = $Section
    if ($sectionMap.ContainsKey($Section)) { $sectionDesc = $sectionMap[$Section] + '（' + $Section + '）' }
    $expectedName = $TargetName -replace '@微信$', ''

    # 0) 图片文件预检（fail-fast，先于任何 UI 交互）：TS 侧已校验存在/类型/大小，驱动侧重验
    #    存在性与可读性（文件可能在 TS 校验后、驱动执行前被删）；hash 由 TS 计算传入仅作日志
    if (-not (Test-Path -LiteralPath $ImagePath)) {
        Throw-DriverError 'CONFIG_MISSING' ('图片文件不存在或不可读：' + $ImagePath)
    }
    $imgItem = Get-Item -LiteralPath $ImagePath
    Write-DriverLog ('image: path=' + $ImagePath + ' name=' + $imgItem.Name + ' size=' + $imgItem.Length + 'B sha256=' + $ImageHash)

    # 0.5) 搜索框残留防御（best-effort，同 message-send）：Ctrl+F 聚焦 → Ctrl+A+Delete 清空
    $mainHwnd = Resolve-WeComMainWindow
    $main = Get-WeComWindowInfo ([IntPtr]$mainHwnd)
    $t0 = $swTotal.ElapsedMilliseconds
    [void](Send-WeComAttachChordKey -Hwnd $mainHwnd -Vk 0x46)
    Start-Sleep -Milliseconds 150
    [void](Clear-WeComSearchBoxV2 -MainHwnd $mainHwnd)
    Start-Sleep -Milliseconds 200
    $timing.precheck = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step0.5 搜索框残留清空（防御性，best-effort）耗时=' + $timing.precheck + 'ms')

    # 1) 主窗口 + 截图 + boxes OCR 两带（标题带 + 底部输入带）
    Write-DriverLog ('target=' + $TargetName + ' subtitle=' + $Subtitle + ' section=' + $Section + ' mainHwnd=' + $mainHwnd + ' rect=(' + $main.X + ',' + $main.Y + ',' + $main.W + 'x' + $main.H + ')')
    $t0 = $swTotal.ElapsedMilliseconds
    $pre = Save-StepShot $mainHwnd 'step1-precheck.png'
    [void]$shots.Add([string]$pre.path)
    $boxesOcr = Invoke-SendChatOcr ([string]$pre.path) 'boxes'
    $boxes = @($boxesOcr.boxes)
    $titleTokens = @(Get-WeComBandTokens -Boxes $boxes -W $pre.w -H $pre.h -Band 'title')
    $bottomTokens = @(Get-WeComBandTokens -Boxes $boxes -W $pre.w -H $pre.h -Band 'bottom')
    $title = Get-WeComTitleFromTokens -Tokens $titleTokens
    $timing.precheck = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step1 两带 OCR：标题带 ' + $titleTokens.Count + ' token（title=' + $title + '）；底部带 ' + $bottomTokens.Count + ' token：' + (($bottomTokens | ForEach-Object { [string]$_.text }) -join '|'))

    # 2) Jev #1 三问合一（right_conversation / input_point / has_draft；措辞与 message-send 一致）
    $t0 = $swTotal.ElapsedMilliseconds
    $stateLines = @()
    $stateLines += '企业微信主窗口 OCR 两带结果（窗口像素坐标）：'
    $stateLines += '—— 标题带（y<0.07h，聊天区顶部会话标题区）：'
    if ($titleTokens.Count -gt 0) { $stateLines += (Format-WeComTokenLines -Tokens $titleTokens -KeyPrefix 'T') }
    else { $stateLines += '（无 token）' }
    $stateLines += '—— 底部输入带（y>0.72h，含工具栏图标行与文本输入区；右侧可能有智能总结侧栏，非输入框）：'
    if ($bottomTokens.Count -gt 0) { $stateLines += (Format-WeComTokenLines -Tokens $bottomTokens -KeyPrefix 'B') }
    else { $stateLines += '（无 token）' }
    $stateLines += ('目标会话：name=' + $TargetName + ' subtitle=' + $Subtitle + ' section=' + $sectionDesc)
    $stateLines += '任务：准备在目标会话的文本输入框中粘贴一张图片并发送。当前窗口可能已打开目标会话，也可能停留在别的会话。'
    $stateText = $stateLines -join "`n"
    $questions = @{
        right_conversation = @{ type = 'choice'; instructions = '当前主窗口打开的会话是否就是目标会话？（按标题带内容与目标 name 判断）'; criteria = @{
            yes = '标题带显示的就是目标会话'; no = '显示的是别的会话'; unclear = '证据不足无法判断' } }
        has_draft = @{ type = 'choice'; instructions = '底部文本输入区是否已有输入的草稿文本？（灰色占位符（如「发送消息」）不算草稿；工具栏图标行的碎字不算草稿）'; criteria = @{
            yes = '输入区有已输入的草稿文本'; no = '输入区干净（无草稿）' } }
    }
    if ($bottomTokens.Count -gt 0) {
        # 输入点选择只在底部带有 token 时可问；「发送(S)」按钮不是输入框，按归一化前缀剔除
        # （逻辑照搬 message-send.ps1 step2）
        $critInput = @{}
        for ($i = 0; $i -lt $bottomTokens.Count; $i++) {
            $tk = $bottomTokens[$i]
            $nText = ConvertTo-WeComNormalized ([string]$tk.text)
            if ($nText.StartsWith('发送')) { continue }
            $cx = [int](([double]$tk.x0 + [double]$tk.x1) / 2)
            $cy = [int](([double]$tk.y0 + [double]$tk.y1) / 2)
            $critInput[('B' + $i)] = ('OCR文本「' + [string]$tk.text + '」中心坐标(' + $cx + ',' + $cy + ')')
        }
        if ($critInput.Count -gt 0) {
            $questions.input_point = @{ type = 'choice'; instructions = '点击底部输入带中哪个 token 的位置可以聚焦文本输入框？（输入框是可输入文字的大块空白区域）'; criteria = $critInput }
        }
    }
    $jev1 = Invoke-WeComJev -StateText $stateText -Questions $questions
    $timing.jev1 = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step2 jev#1 used=' + $jev1.used + ' latency=' + $jev1.latency_ms + 'ms reason=' + [string]$jev1.reason)
    Write-DriverLog ('step2 jev#1 state: ' + ($stateText -replace "`r?`n", ' / '))
    if ($jev1.used) {
        Write-DriverLog ('step2 jev#1 answers: right=' + [string]$jev1.answers.right_conversation.choice + ' draft=' + [string]$jev1.answers.has_draft.choice + ' point=' + [string]$jev1.answers.input_point.choice)
    }

    # 三问逐一取值（Jev 不可用/单问答案非法 → 该问走规则降级，其余仍可用 Jev 结论）
    # ① right_conversation：no/unclear → navigate_required=true 交还 TS 编排
    $rightOk = $false
    $rightSrc = 'rule'
    $navReason = ''
    $jevRight = ''
    if ($jev1.used) { $jevRight = [string]$jev1.answers.right_conversation.choice }
    if ($jevRight -eq 'yes') {
        $rightOk = $true; $rightSrc = 'jev'
    } elseif ($jevRight -eq 'no' -or $jevRight -eq 'unclear') {
        $rightSrc = 'jev'
        $navReason = ('Jev 判定当前会话非目标（' + $jevRight + '，标题带 title=' + $title + '）')
    } else {
        # 降级：标题归一化规则匹配
        if (Test-WeComTitleMatch -Title $title -Expected $TargetName) {
            $rightOk = $true
        } else {
            $navReason = ('标题归一化规则未命中（当前标题「' + $title + '」≠ 目标「' + $expectedName + '」）')
        }
        Write-DriverLog ('step2 right_conversation 降级规则：title=' + $title + ' 匹配=' + $rightOk)
    }
    if (-not $rightOk) {
        # 非目标/无法判定：不做任何输入，交还 TS 编排（search+select 后二次调用本驱动）
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

    # ② has_draft：yes → UI_CHANGED 中止（可能是用户未发送的文字，绝不清除）
    $draftYes = $false
    $draftSrc = 'rule'
    $jevDraft = ''
    if ($jev1.used) { $jevDraft = [string]$jev1.answers.has_draft.choice }
    if ($jevDraft -eq 'yes' -or $jevDraft -eq 'no') {
        $draftSrc = 'jev'
        $draftYes = ($jevDraft -eq 'yes')
    } else {
        # 降级：input 模式判空（占位符/按钮/侧栏/图标碎字均已剔除）
        $inputPre = Invoke-SendChatOcr ([string]$pre.path) 'input'
        $draftYes = ($inputPre.input_empty -ne $true)
        Write-DriverLog ('step2 has_draft 降级规则：input_empty=' + $inputPre.input_empty + ' texts=[' + (@($inputPre.texts) -join '|') + ']')
    }
    Write-DriverLog ('step2 判定：right=' + $rightOk + '（' + $rightSrc + '） draft=' + $draftYes + '（' + $draftSrc + '）')
    if ($draftYes) {
        Throw-DriverError 'UI_CHANGED' '输入框存在残留草稿，已中止防串消息（可能是用户未发送的文字，未做任何清除/输入）；请人工清空后重试'
    }

    # ③ input_point：Jev 选 token 中心；降级比例坐标 (0.500w,0.900h)
    $ipX = [int]($main.X + $main.W * $script:ChatInputRx)
    $ipY = [int]($main.Y + $main.H * $script:ChatInputRy)
    $ipSrc = 'ratio'
    $jevPoint = ''
    if ($jev1.used) { $jevPoint = [string]$jev1.answers.input_point.choice }
    if ($jevPoint -match '^B(\d+)$') {
        $idx = [int]$Matches[1]
        if ($idx -ge 0 -and $idx -lt $bottomTokens.Count) {
            $tk = $bottomTokens[$idx]
            $ipX = [int]($main.X + ([double]$tk.x0 + [double]$tk.x1) / 2)
            $ipY = [int]($main.Y + ([double]$tk.y0 + [double]$tk.y1) / 2)
            $ipSrc = 'jev'
        }
    }

    # 3) 点击输入框聚焦 → baseline 方差 → Clipboard.SetImage → attachstate Ctrl+V → 方差复测
    #    动作前重验主窗口存活（同 message-send step3）
    if (-not [WeComWin32]::IsWindow([IntPtr]$mainHwnd)) {
        $mainHwnd = Resolve-WeComMainWindow
        Write-DriverLog ('step3 主窗口已重建，重解析 hwnd=' + $mainHwnd)
    }
    $t0 = $swTotal.ElapsedMilliseconds
    Write-DriverLog ('step3 click 输入框 screen=(' + $ipX + ',' + $ipY + ') source=' + $ipSrc)
    [void](Send-WeComClick -Hwnd $mainHwnd -ScreenX $ipX -ScreenY $ipY)
    Start-Sleep -Milliseconds 300
    $timing.input_click = [int]($swTotal.ElapsedMilliseconds - $t0)

    $t0 = $swTotal.ElapsedMilliseconds
    $base = Save-StepShot $mainHwnd 'step2-baseline.png'
    [void]$shots.Add([string]$base.path)
    $std0 = Get-InputRegionStddev $base
    Write-DriverLog ('step3 baseline 输入区方差 std0=' + ([Math]::Round($std0, 1)))

    # 剪贴板写入图片（System.Windows.Forms Clipboard.SetImage；FromFile 加载 Bitmap；
    # 5 次重试×150ms，全败 CONFIG_MISSING）。副作用：覆盖用户剪贴板且不恢复（刻意的，
    # 同 message-send 多行粘贴通道：paste handler 异步读剪贴板，恢复竞态会粘贴到错的内容）
    $clipOk = $false
    $clipTries = 0
    for ($i = 1; $i -le 5; $i++) {
        $clipTries = $i
        $clipBmp = $null
        try {
            $clipBmp = [System.Drawing.Bitmap]::FromFile($ImagePath)
            [System.Windows.Forms.Clipboard]::SetImage($clipBmp)
            $clipOk = $true
            break
        } catch {
            if ($i -lt 5) { Start-Sleep -Milliseconds 150 }
        } finally {
            if ($null -ne $clipBmp) { $clipBmp.Dispose() }
        }
    }
    if (-not $clipOk) {
        Throw-DriverError 'CONFIG_MISSING' ('剪贴板写入图片失败（重试 ' + $clipTries + ' 次均失败），无法经剪贴板粘贴通道输入图片：' + $ImagePath)
    }
    $timing.clipboard = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step3 Clipboard.SetImage 第 ' + $clipTries + ' 次成功')

    $t0 = $swTotal.ElapsedMilliseconds
    $pasted = Send-WeComAttachChordKey -Hwnd $mainHwnd -Vk 0x56
    Start-Sleep -Milliseconds 1500
    $pastedSnap = Save-StepShot $mainHwnd 'step3-pasted.png'
    [void]$shots.Add([string]$pastedSnap.path)
    $std1 = Get-InputRegionStddev $pastedSnap
    $timing.paste = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step3 attachstate Ctrl+V attach=' + $pasted + '；方差 std0=' + ([Math]::Round($std0, 1)) + ' std1=' + ([Math]::Round($std1, 1)) + '（判据 std1 > std0+8）')
    if ($std1 -le ($std0 + 8)) {
        # 预览未出现：尚未按 Enter，无发送副作用（effect=none 由 TS 处理）
        Throw-DriverError 'UI_CHANGED' ('图片预览未出现（粘贴可能未生效）：输入区方差未上抬（std1=' + ([Math]::Round($std1, 1)) + ' ≤ std0+8=' + ([Math]::Round($std0 + 8, 1)) + '，attach=' + $pasted + '），已中止且未按 Enter 发送（无发送副作用；剪贴板仍保留该图片且不恢复，请知悉）')
    }

    # 4) 发送前会话复核（规则，同 message-send step4）：粘贴后截图 OCR 标题带严格匹配；
    #    不一致 → UI_CHANGED fail-closed。已知限制：图片预览非文本草稿，Ctrl+A 清不掉、
    #    ESC 禁用（最小化企微），无法自动清理，message 注明需人工处理
    $t0 = $swTotal.ElapsedMilliseconds
    $boxes2 = Invoke-SendChatOcr ([string]$pastedSnap.path) 'boxes'
    $title2 = Get-WeComTitleFromTokens -Tokens @(Get-WeComBandTokens -Boxes @($boxes2.boxes) -W $pastedSnap.w -H $pastedSnap.h -Band 'title')
    $timing.title_recheck = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step4 发送前复核 title=' + $title2)
    if (-not (Test-WeComTitleMatch -Title $title2 -Expected $TargetName)) {
        Throw-DriverError 'UI_CHANGED' ('发送前复核：会话标题「' + $title2 + '」与目标「' + $expectedName + '」不一致，已中止且未按 Enter 发送（防串消息）。已知限制：输入区可能残留图片预览（图片非文本草稿，无法自动清除，ESC 会最小化企微已禁用），需人工处理后再重试')
    }

    # 5) Enter 发送 → 1800ms 渲染等待 → 终态证据：输入区方差回落 + 会话列表「[图片]」
    #    发送前重验主窗口存活（Enter 打在已销毁窗口上=静默未发，必须防）
    if (-not [WeComWin32]::IsWindow([IntPtr]$mainHwnd)) {
        $mainHwnd = Resolve-WeComMainWindow
        Write-DriverLog ('step5 主窗口已重建，重解析 hwnd=' + $mainHwnd)
    }
    Write-DriverLog 'step5 PostMessage Enter 发送'
    $t0 = $swTotal.ElapsedMilliseconds
    Send-WeComEnter -Hwnd $mainHwnd
    Start-Sleep -Milliseconds 1800
    $mainHwnd = Resolve-WeComMainWindow
    $after = Save-StepShot $mainHwnd 'step4-after.png'
    [void]$shots.Add([string]$after.path)
    $timing.send_wait = [int]($swTotal.ElapsedMilliseconds - $t0)

    $t0 = $swTotal.ElapsedMilliseconds
    $std2 = Get-InputRegionStddev $after
    $varianceCleared = ($std2 -lt ($std0 + 8))
    # 会话列表（左栏 x0<0.40w）boxes token 归一化后含「[图片]」即命中（全角括号容忍）
    $afterBoxes = Invoke-SendChatOcr ([string]$after.path) 'boxes'
    $listTokens = @($afterBoxes.boxes | Where-Object { [double]$_.x0 -lt ($after.w * 0.40) } | Sort-Object { [double]$_.y0 }, { [double]$_.x0 })
    $imageTokenHit = $false
    $imageEvidence = @()
    $listEvidence = @()
    foreach ($t in $listTokens) {
        $raw = [string]$t.text
        if ($listEvidence.Count -lt 8) { $listEvidence += $raw }
        $nt = ConvertTo-WeComNormalized $raw
        if ($nt -and ($nt.Contains('[图片]') -or $nt.Contains('【图片】'))) {
            $imageTokenHit = $true
            if ($imageEvidence.Count -lt 5) { $imageEvidence += $raw }
        }
    }
    $ruleChecks = 0
    if ($varianceCleared) { $ruleChecks++ }
    if ($imageTokenHit) { $ruleChecks++ }
    $timing.final_ocr = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step5 终态规则双判据：①输入区方差回落（std2=' + ([Math]::Round($std2, 1)) + ' < std0+8=' + ([Math]::Round($std0 + 8, 1)) + '）=' + $varianceCleared + ' ②会话列表含[图片]=' + $imageTokenHit + ' → ' + $ruleChecks + '/2')

    # 6) Jev #2 两问合一（sent_successfully / failure_mode）：可用以其判定为准（yes →
    #    method=jev，即使规则双判据已 2/2 也如此）；Jev 不可用/答案非法降级规则须 2/2 全过
    $t0 = $swTotal.ElapsedMilliseconds
    $state2Lines = @()
    $state2Lines += '企业微信图片消息发送后的 OCR 校验证据（PostMessage Enter 后 1.8s 截图）：'
    $state2Lines += ('—— 输入区像素灰度标准差：粘贴前=' + ([Math]::Round($std0, 1)) + '，粘贴后=' + ([Math]::Round($std1, 1)) + '，发送后=' + ([Math]::Round($std2, 1)) + '（粘贴后明显上抬=输入区出现图片缩略图；发送成功时「发送后」应回落到接近「粘贴前」基线）')
    $state2Lines += ('—— 会话列表含「[图片]」的 token：' + $(if ($imageEvidence.Count -gt 0) { ($imageEvidence -join ' / ') } else { '（无）' }))
    $state2Lines += ('—— 会话列表 token（左栏，最多 8 个）：' + $(if ($listEvidence.Count -gt 0) { ($listEvidence -join ' / ') } else { '（无）' }))
    $state2Lines += ('—— 图片文件：' + $imgItem.Name + '（sha256=' + $ImageHash + '，' + $imgItem.Length + ' 字节）')
    $state2Lines += '（证据说明：图片消息没有文本气泡，消息区 OCR 读不到该图片的任何文字内容属正常，不是未发送的证据；「输入区方差回落 + 会话列表预览出现[图片]」即为已成功发送的有效证据）'
    $state2Lines += ('目标会话名：' + $expectedName)
    $state2Lines += '任务：判断该图片是否已成功发送到目标会话。'
    $state2Text = $state2Lines -join "`n"
    $jev2 = Invoke-WeComJev -StateText $state2Text -Questions @{
        sent_successfully = @{ type = 'choice'; instructions = '刚才粘贴的图片是否已成功发送到目标会话？（注意证据说明：图片消息无文本气泡，消息区读不到图片内容属正常）'; criteria = @{
            yes = '已成功发送'; no = '未发送成功'; unclear = '证据不足无法判断' } }
        failure_mode = @{ type = 'choice'; instructions = '若未成功，失败形态最接近哪种？（已成功时选 unclear 即可）'; criteria = @{
            not_sent = '没有发出去（输入区仍有图片预览，或输入区已清空且会话列表预览也无[图片]）'; sent_elsewhere = '可能发到了别的会话'; unclear = '证据不足' } }
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
            Throw-DriverError 'EXECUTION_UNKNOWN' ('发送后校验失败（Jev 判定 ' + $sentChoice + '，failure_mode=' + $fm + '）：图片可能已发出，不会自动重试，请人工核对（详见 driver-log.txt）')
        }
        # 答案非法 → 降级规则双判据（下方继续）
    }
    if ($method -eq '') {
        # 降级链终态：规则双判据须 2/2 全过，任一不过 → EXECUTION_UNKNOWN 绝不自动重试
        $method = 'rule_2of2'
        if ($ruleChecks -lt 2) {
            Throw-DriverError 'EXECUTION_UNKNOWN' ('发送后校验失败（双判据仅过 ' + $ruleChecks + '/2 项）：图片可能已发出，不会自动重试，请人工核对（详见 driver-log.txt）')
        }
    }
    $timing.total = [int]$swTotal.ElapsedMilliseconds
    Write-DriverLog ('done method=' + $method + ' rule_checks=' + $ruleChecks + '/2 std0=' + ([Math]::Round($std0, 1)) + ' std1=' + ([Math]::Round($std1, 1)) + ' std2=' + ([Math]::Round($std2, 1)) + ' timing=' + ($timing | ConvertTo-Json -Compress))

    # 7) 返回（navigate_required=false = 已在目标会话完成图片发送与校验）
    $sentVerification = @{ method = $method; result = 'sent' }
    if ($method -eq 'rule_2of2') { $sentVerification.rule_checks_passed = $ruleChecks }
    return @{
        navigate_required = $false
        target = @{ name = $TargetName; subtitle = $Subtitle; section = $Section }
        title = $title2
        sent_verification = $sentVerification
        input_stddev = @{ before = [Math]::Round($std0, 1); paste = [Math]::Round($std1, 1); after = [Math]::Round($std2, 1) }
        input_point = @{ x = $ipX; y = $ipY; source = $ipSrc }
        timing_ms = $timing
        screenshot_paths = @($shots.ToArray())
    }
}
