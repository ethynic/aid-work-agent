# drivers/ps1/message-send.ps1 — wecom_message_send 驱动（M6：发送阶段驱动，智能分发由 TS 层编排）
# 前提：由 TS 层 messageSend.ts 调用；当前会话是否为目标由本驱动判定——
# 不对时返回 navigate_required=true 交还 TS 编排（search+select 后二次调用本驱动）。
# 流程：
#   1) 解析主窗口 → PrintWindow 截图 → OCR 两带（boxes 模式拿 token+坐标）：
#      标题带（y<0.07h，聊天区顶部）+ 底部输入带（y>0.80h，含工具栏图标行与输入区）
#   2) Jev #1（三问合一）：right_conversation（当前会话是否目标）/ input_point（点哪
#      聚焦文本输入框）/ has_draft（输入区是否有草稿）。降级链：标题归一化规则匹配 /
#      比例坐标 (0.500w,0.900h)（M2 标定）/ input 模式判空（占位符与图标碎字剔除）
#   3) 分支：非目标/无法判定 → 返回 data {navigate_required:true, reason}（驱动层 ok=true，
#      TS 接管）；输入区有草稿 → UI_CHANGED 中止（**绝不动用户草稿**，防串消息）；
#      目标且干净 → 点击 input_point → 输入：单行 Send-WeComText 逐字；多行（含 CR/LF）
#      经剪贴板粘贴通道——Set-Clipboard 重试 → attachstate Ctrl+V → OCR 回读校验输入带
#      含 text 归一化前 8 字（不符 → UI_CHANGED fail-closed，不按 Enter）。
#      副作用：多行发送会覆盖用户剪贴板且**不恢复**（weixin-cli 先例：paste handler 异步
#      读剪贴板，恢复竞态会粘贴到错的内容；README 已注明）
#   4) 发送前会话复核（规则，不发 Jev——快）：重新截图 OCR 标题带，归一化严格匹配目标名
#      （剥 @微信 双向、相等或「名+@/（」前缀、防「陆伟」落入「陆伟民」前缀陷阱）；
#      不一致 → **清空自己刚输入的草稿**（Clear-WeComFocusedInput，同 Clear-WeComSearchBoxV2
#      原语；自己输入的可以清）→ UI_CHANGED（防把文字留给错误会话）
#   5) Send-WeComEnter 发送 → 1.2s → 截图 → OCR（input/bubble/preview 三模式终态证据）
#   6) Jev #2（两问合一）：sent_successfully / failure_mode；no/unclear → EXECUTION_UNKNOWN
#      绝不自动重试。降级：M2 三选二（输入框清空 / 消息区末尾任一行含 text 归一化前缀 12 字 /
#      会话列表含目标名且含前缀），<2 项 → EXECUTION_UNKNOWN
# 返回 data：{ navigate_required:false, title, sent_verification:{method,result,...},
#   input_point:{x,y,source}, timing_ms, screenshot_paths }。
# artifact：各步截图 + driver-log.txt（Jev state/answer 摘要、OCR 摘要、timing；
# 绝不含 TYPESAFE_API_KEY——key 只在 Invoke-WeComJev 内经临时头文件瞬态使用）。
# 注意：本文件含中文，必须以 UTF-8 with BOM 保存。
param(
    [Parameter(Mandatory)][string]$TargetName,
    [string]$Subtitle = '',
    [string]$Section = '',
    [Parameter(Mandatory)][string]$Text,
    [Parameter(Mandatory)][string]$ArtifactDir
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

function Invoke-SendChatOcr([string]$ImagePath, [string]$Mode) {
    # _common 的 OCR 入口 + 原始输出落 driver-log（真机排障）
    $parsed = Invoke-WeComChatOcr -ImagePath $ImagePath -Mode $Mode
    Write-DriverLog ('OCR[' + $Mode + '] ' + ($parsed | ConvertTo-Json -Compress -Depth 10))
    return $parsed
}

function Get-WeComBandTokens {
    # boxes 模式产物按带过滤（坐标 = 窗口像素坐标，与 py 侧 band 语义一致）：
    #   title  — y0 < 0.07h 且 x0 > 0.20w（聊天区顶部标题带；0.20w 阈值 2026-09-28 真机修订：
    #             旧 0.15w 会放进搜索框查询残留（x0≈0.13-0.17w）与会话列表首行（≈0.17w），
    #             与聊天区标题拼接成「文件传输助手文件传输助手」致复核误杀；标题带实测
    #             x0≥0.22w（外部联系人）/0.296w（普通），0.20w 两侧安全排除）
    #   bottom — y0 >= 0.80h 且 x0 > max(0.10w,620)（含工具栏图标行 ≈0.83h 与文本输入区，
    #             排除左栏会话列表；右缘 0.95w 截掉窗口边框噪声）
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
        [double]$_.y0 -ge ($H * 0.80) -and
        [double]$_.x0 -gt $chatXMin -and [double]$_.x0 -lt ($W * 0.95)
    } | Sort-Object { [double]$_.y0 }, { [double]$_.x0 })
}

function Get-WeComTitleFromTokens {
    # 标题带 token → 标题行文本：按 y 中心聚类成视觉行（同行 = y 中心差 ≤14px，标题
    # token 高约 20-24px，同 py cluster_lines 的 0.7 行高语义），取首行按 x 序拼接
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
    # 标题严格匹配（同 chat-select.ps1 步骤 6 / M2 语义）：目标名剥 @微信 后缀，
    # 归一化后相等，或以「名字 + 分隔符」开头（外部联系人「陆伟 @微信」、群「产品讨论群（13）」）；
    # 纯子串不放行（防「陆伟」落入「陆伟民」前缀陷阱）。Title 允许空串
    # （OCR 读不到标题 = 不匹配 → navigate_required，是合法分支而非错误）。
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
    $timing = @{ precheck = 0; jev1 = 0; input_click = 0; typing = 0; title_recheck = 0; send_wait = 0; final_ocr = 0; jev2 = 0; total = 0 }
    $shots = New-Object System.Collections.ArrayList
    $sectionMap = @{ contact = '联系人'; group = '群聊' }
    $sectionDesc = $Section
    if ($sectionMap.ContainsKey($Section)) { $sectionDesc = $sectionMap[$Section] + '（' + $Section + '）' }
    $expectedName = $TargetName -replace '@微信$', ''

    # 0.5) 搜索框残留防御（best-effort）：上游 search 后框内会保留查询词（M4 设计：overlay
    #     保持打开），agent 若 search→send 跳过 select，残留会污染标题带 OCR。Ctrl+F 聚焦
    #     搜索框 → Ctrl+A+Delete 清空（已验证原语；对空框是幂等空操作；清空同时关闭残留
    #     overlay；焦点先落在搜索框，绝不触碰聊天输入框里的用户草稿）。
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
    # 调用侧必须 @() 包裹：PS 函数返回单元素数组会被解包成标量（.Count/索引失效）
    $titleTokens = @(Get-WeComBandTokens -Boxes $boxes -W $pre.w -H $pre.h -Band 'title')
    $bottomTokens = @(Get-WeComBandTokens -Boxes $boxes -W $pre.w -H $pre.h -Band 'bottom')
    $title = Get-WeComTitleFromTokens -Tokens $titleTokens
    $timing.precheck = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step1 两带 OCR：标题带 ' + $titleTokens.Count + ' token（title=' + $title + '）；底部带 ' + $bottomTokens.Count + ' token：' + (($bottomTokens | ForEach-Object { [string]$_.text }) -join '|'))

    # 2) Jev #1 三问合一（right_conversation / input_point / has_draft；措辞保持稳定）
    $t0 = $swTotal.ElapsedMilliseconds
    $stateLines = @()
    $stateLines += '企业微信主窗口 OCR 两带结果（窗口像素坐标）：'
    $stateLines += '—— 标题带（y<0.07h，聊天区顶部会话标题区）：'
    if ($titleTokens.Count -gt 0) { $stateLines += (Format-WeComTokenLines -Tokens $titleTokens -KeyPrefix 'T') }
    else { $stateLines += '（无 token）' }
    $stateLines += '—— 底部输入带（y>0.80h，含工具栏图标行与文本输入区；右侧可能有智能总结侧栏，非输入框）：'
    if ($bottomTokens.Count -gt 0) { $stateLines += (Format-WeComTokenLines -Tokens $bottomTokens -KeyPrefix 'B') }
    else { $stateLines += '（无 token）' }
    $stateLines += ('目标会话：name=' + $TargetName + ' subtitle=' + $Subtitle + ' section=' + $sectionDesc)
    $stateLines += '任务：准备在目标会话的文本输入框中输入一条消息并发送。当前窗口可能已打开目标会话，也可能停留在别的会话。'
    $stateText = $stateLines -join "`n"
    $questions = @{
        right_conversation = @{ type = 'choice'; instructions = '当前主窗口打开的会话是否就是目标会话？（按标题带内容与目标 name 判断）'; criteria = @{
            yes = '标题带显示的就是目标会话'; no = '显示的是别的会话'; unclear = '证据不足无法判断' } }
        has_draft = @{ type = 'choice'; instructions = '底部文本输入区是否已有输入的草稿文本？（灰色占位符（如「发送消息」）不算草稿；工具栏图标行的碎字不算草稿）'; criteria = @{
            yes = '输入区有已输入的草稿文本'; no = '输入区干净（无草稿）' } }
    }
    if ($bottomTokens.Count -gt 0) {
        # 输入点选择只在底部带有 token 时可问（Jev 按 token 中心点击）；
        # 「发送(S)」按钮不是输入框（2026-09-28 真机：Jev 曾误选，空框时点它侥幸无害，
        # 但语义错误），按归一化前缀剔除后无可选 token 则不问该题（走比例坐标降级）
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
        # 降级：标题归一化规则匹配（同步骤 5 语义）
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
        # 降级：input 模式判空（py 侧已剔除占位符「发送消息/输入消息/聊点什么」、发送按钮
        # 标签、右侧栏与工具栏图标行碎字——band 卡 0.855-0.97h 排除 ≈0.83h 图标行）
        $inputPre = Invoke-SendChatOcr ([string]$pre.path) 'input'
        $draftYes = ($inputPre.input_empty -ne $true)
        Write-DriverLog ('step2 has_draft 降级规则：input_empty=' + $inputPre.input_empty + ' texts=[' + (@($inputPre.texts) -join '|') + ']')
    }
    Write-DriverLog ('step2 判定：right=' + $rightOk + '（' + $rightSrc + '） draft=' + $draftYes + '（' + $draftSrc + '）')
    if ($draftYes) {
        Throw-DriverError 'UI_CHANGED' '输入框存在残留草稿，已中止防串消息（可能是用户未发送的文字，未做任何清除/输入）；请人工清空后重试'
    }

    # ③ input_point：Jev 选 token 中心；降级比例坐标 (0.500w,0.900h)（M2 标定）
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

    # 3) 点击输入框聚焦 → Send-WeComText 逐字输入（中文 WM_CHAR）
    #    动作前重验主窗口存活：布局切换会销毁重建窗口（hwnd 变化），失效则重解析
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
    # 多行文本（含 CR/LF）走剪贴板粘贴通道：逐字 WM_CHAR 的换行会提前触发发送或被静默
    # 丢弃（2026-09-28 真机验证：输入框已聚焦时 attachstate Ctrl+V 粘贴可用、换行保留）。
    # 副作用：覆盖用户剪贴板且不恢复——不备份恢复是刻意为之（weixin-cli 先例：目标程序
    # paste handler 异步读剪贴板，恢复竞态会粘贴到错的内容）。
    $isMultiline = ($Text -match "[`r`n]")
    if ($isMultiline) {
        # 日志只记前 12 字（换行替换为 ⏎）+ 总字数，多行文本可能敏感，不落全文
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
        Start-Sleep -Milliseconds 500
    } else {
        Send-WeComText -Hwnd $mainHwnd -Text $Text
        Start-Sleep -Milliseconds 400
    }
    $typed = Save-StepShot $mainHwnd 'step2-typed.png'
    [void]$shots.Add([string]$typed.path)

    if ($isMultiline) {
        # 粘贴回读校验（fail-closed）：粘贴是间接输入（剪贴板写入与 Ctrl+V 之间内容可能被
        # 其他程序/用户改写），OCR 回读输入带确认文本开头真的落在输入框，防把错内容带进
        # 发送与终态校验。输入带过滤（窗口像素，2026-09-28 真机标定）：y0≥0.80h 且
        # 300<x0<0.97w——x>300 排除左栏会话列表（1280 宽下列内 token x≈218），0.97w 截掉
        # 右缘噪声。多行输入框会向上生长、带内混入工具栏/发送按钮等噪声 token：按 (y,x)
        # 序拼接整带后归一化，噪声只出现在前缀之外，不影响 Contains 前缀比对（多行首行
        # 开头 = 归一化全文开头，与 step5 终态前缀同源）。
        $readbackOcr = Invoke-SendChatOcr ([string]$typed.path) 'boxes'
        $inputBand = @($readbackOcr.boxes | Where-Object {
            [double]$_.y0 -ge ($typed.h * 0.80) -and
            [double]$_.x0 -gt 300 -and [double]$_.x0 -lt ($typed.w * 0.97)
        } | Sort-Object { [double]$_.y0 }, { [double]$_.x0 })
        $bandNorm = ConvertTo-WeComNormalized (($inputBand | ForEach-Object { [string]$_.text }) -join '')
        $readbackPrefix = ConvertTo-WeComNormalized $Text
        if ($readbackPrefix.Length -gt 8) { $readbackPrefix = $readbackPrefix.Substring(0, 8) }
        $readbackHit = $bandNorm.Contains($readbackPrefix)
        Write-DriverLog ('step3 粘贴回读校验：输入带 ' + $inputBand.Count + ' token，拼接归一化含前缀「' + $readbackPrefix + '」=' + $readbackHit)
        if (-not $readbackHit) {
            Throw-DriverError 'UI_CHANGED' ('粘贴回读校验不符：OCR 输入带未读到文本开头（前缀「' + $readbackPrefix + '」），粘贴可能未落地或落到非预期位置，已中止未按 Enter 发送；输入框可能有残留，请人工检查')
        }
    }
    # typing 覆盖完整输入阶段（逐字或 剪贴板写入+粘贴+回读校验）
    $timing.typing = [int]($swTotal.ElapsedMilliseconds - $t0)

    # 4) 发送前会话复核（规则）：重新截图 OCR 标题带严格匹配；不一致 → 清自己刚输入的
    #    草稿（这是我们自己输入的，可以清）→ UI_CHANGED（防把文字留给错误会话）
    $t0 = $swTotal.ElapsedMilliseconds
    $boxes2 = Invoke-SendChatOcr ([string]$typed.path) 'boxes'
    $title2 = Get-WeComTitleFromTokens -Tokens @(Get-WeComBandTokens -Boxes @($boxes2.boxes) -W $typed.w -H $typed.h -Band 'title')
    $timing.title_recheck = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step4 发送前复核 title=' + $title2)
    if (-not (Test-WeComTitleMatch -Title $title2 -Expected $TargetName)) {
        $cleared = Clear-WeComFocusedInput -Hwnd $mainHwnd
        Write-DriverLog ('step4 标题不一致，已清理自己刚输入的草稿（attach=' + $cleared + '）')
        Throw-DriverError 'UI_CHANGED' ('发送前复核：会话标题「' + $title2 + '」与目标「' + $expectedName + '」不一致，已清空自己刚输入的草稿并中止（防串消息，未按 Enter 发送）')
    }

    # 5) Enter 发送 → 1.2s 渲染等待 → 终态证据 OCR（input/bubble/preview 三模式）
    #    发送前重验主窗口存活（Enter 打在已销毁窗口上=静默未发，却会走 unknown 语义，必须防）
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

    $t0 = $swTotal.ElapsedMilliseconds
    $prefix = ConvertTo-WeComNormalized $Text
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
    $stateTextForText = $Text
    if ($isMultiline) {
        # 多行脱敏（与 step3 日志/README 契约一致）：state 会整段落 driver-log，
        # 多行正文只给 Jev 前 12 字预览 + 总字数，不落 120 字（气泡/预览 OCR 证据行
        # 本就携带屏幕内容，前缀足以让 Jev 关联证据做终态判定）
        $stateTextForText = ($textPreview + '…（多行，共 ' + $Text.Length + ' 字，脱敏只记前 12 字）')
    } elseif ($stateTextForText.Length -gt 120) { $stateTextForText = $stateTextForText.Substring(0, 120) + '…（截断，共 ' + $Text.Length + ' 字）' }
    $previewHits = @()
    foreach ($row in @($preview.texts)) {
        $nt = ConvertTo-WeComNormalized ([string]$row)
        if ($nt.Contains($normTarget) -or $nt.Contains($prefix)) { $previewHits += [string]$row }
        if ($previewHits.Count -ge 5) { break }
    }
    $state2Lines = @()
    $state2Lines += '企业微信消息发送后的 OCR 校验证据（PostMessage Enter 后 1.2s 截图）：'
    $state2Lines += ('—— 输入区 token：' + $(if (@($inputAfter.texts).Count -gt 0) { (@($inputAfter.texts) -join ' | ') } else { '（空）' }))
    $state2Lines += ('—— 消息区气泡 token（底部带，最多 8 个）：' + $(if ($bubbleTexts.Count -gt 0) { ($bubbleTexts -join ' / ') } else { '（空）' }))
    $state2Lines += '（证据说明：应用类会话（如文件传输助手/微信客服等官方应用）的消息区为内嵌网页渲染，截图可能整片空白——气泡 token 为空不等于未发送；此时「输入区已清空 + 会话列表预览出现该文本 + 『刚刚』时间戳」即为已成功发送的有效证据）'
    $state2Lines += ('—— 会话列表含目标名/前缀的行：' + $(if ($previewHits.Count -gt 0) { ($previewHits -join ' / ') } else { '（无）' }))
    $state2Lines += ('目标会话名：' + $expectedName)
    $state2Lines += ('已输入并按 Enter 发送的文本：' + $stateTextForText)
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

    # 7) 返回（navigate_required=false = 已在目标会话完成发送与校验）
    $sentVerification = @{ method = $method; result = 'sent' }
    if ($method -eq 'rule_2of3') { $sentVerification.rule_checks_passed = $ruleChecks }
    return @{
        navigate_required = $false
        title = $title2
        sent_verification = $sentVerification
        input_point = @{ x = $ipX; y = $ipY; source = $ipSrc }
        timing_ms = $timing
        screenshot_paths = @($shots.ToArray())
    }
}
