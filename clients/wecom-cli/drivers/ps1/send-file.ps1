# drivers/ps1/send-file.ps1 — wecom_send_file 驱动（M8：发送阶段驱动，智能分发由 TS 层编排）
# 前提：由 TS 层 sendFile.ts 调用（编排骨架与 send-image.ps1 同源，Jev state/question
# 措辞保持稳定，修改前先核对 send-image.ps1 同段）；当前会话是否为目标由本驱动判定——
# 不对时返回 navigate_required=true 交还 TS 编排（search+select 后二次调用本驱动）。
# 流程（真机验证 2026-09-28 experiments/probes/e6-file：Clipboard.SetFileDropList
# (StringCollection 绝对路径) + attachstate Ctrl+V → 无确认弹窗，输入区直接出现内联
# 文件卡片（OCR 可读文件名 + 大小标签，y≈0.86h）→ Send-WeComEnter 后卡片消失 +
# 会话列表预览出现「[文件名]」，文件真实送达）：
#   1) 解析主窗口 → PrintWindow 截图 → OCR 两带（boxes 模式拿 token+坐标）：
#      标题带（y<0.07h）+ 底部输入带（y>0.80h，含工具栏图标行与输入区）
#   2) Jev #1（三问合一，措辞同 send-image）：right_conversation / input_point / has_draft；
#      降级链：标题归一化规则匹配 / 比例坐标 (0.500w,0.900h) / input 模式判空。
#      非目标/无法判定 → 返回 data {navigate_required:true, reason}；输入区有用户草稿 →
#      UI_CHANGED 中止（**绝不动用户草稿**）
#   3) 粘贴文件：点击 input_point → 截图取输入区（y≥0.75h、x∈(聊天区左界,0.74w]，E3
#      标定带 + chat_ocr 同款侧栏右界排除）OCR 干净基线 → Clipboard.SetFileDropList
#      （StringCollection + 绝对路径，5 次重试×150ms，全败 CONFIG_MISSING）→ attachstate
#      Ctrl+V → 1500ms → 粘贴校验：输入区出现**基线没有的新 token** 且归一化 contains 文件名
#      主干（去扩展名防 OCR 把 .txt 误读，取前 12 字；主干归一化后 <3 字符时回退用完整
#      文件名做匹配键，防「a」/「test」这类通用主干撞上无关 token）——不含 → UI_CHANGED
#      「文件卡片未出现（粘贴可能未生效）」——此时**尚未按 Enter，无发送副作用**；
#      副作用：剪贴板被覆盖为该文件列表且**不恢复**（同 send-image 粘贴通道契约）
#   4) 发送前会话复核（规则，同 send-image 步骤 4）：粘贴后截图 OCR 标题带严格匹配目标名；
#      不一致 → UI_CHANGED fail-closed。**已知限制**：文件卡片不是文本草稿，Ctrl+A 清不掉，
#      ESC 禁用（最小化企微），无法自动清理——message 注明输入区可能残留文件卡片需人工处理
#   5) Send-WeComEnter → 1800ms → 终态证据双判据（均为**基线差分**——只统计基线里没有的
#      新 token，防占位符/侧栏/其他会话预览等既有 token 假命中）：①输入区文件名 token 消失
#      （复测粘贴校验同带）②会话列表（左栏 x0<0.40w）token 归一化含「[+文件名主干」或
#      文件名主干（列表预览格式实测「[e3-test-file.txt]」，容忍括号误读）；Jev #2（两问合一：
#      sent_successfully / failure_mode）可用以其判定为准（method=jev），no/unclear →
#      EXECUTION_UNKNOWN 绝不自动重试；Jev 不可用/答案非法降级规则双判据须 2/2 全过
#      （method=rule_2of2），<2/2 → EXECUTION_UNKNOWN
# 返回 data：{ navigate_required:false, target, title, sent_verification:{method,result,...},
#   paste_check:{stem,paste_hit,input_gone,list_hit}（stem=实际匹配键：主干；主干归一化
#   后 <3 字符时为完整文件名）, input_point:{x,y,source}, timing_ms, screenshot_paths }。
# artifact：各步截图 + driver-log.txt（记文件名/sha256/大小/判据/Jev 摘要/OCR 摘要；
# **不复制文件本体**；绝不含 TYPESAFE_API_KEY）。
# 注意：本文件含中文，必须以 UTF-8 with BOM 保存。
param(
    [Parameter(Mandatory)][string]$TargetName,
    [string]$Subtitle = '',
    [string]$Section = '',
    [Parameter(Mandatory)][string]$FilePath,
    [string]$FileHash = '',
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

# 诊断日志：各阶段判定写 artifact 目录 driver-log.txt（真机排障用，格式同 send-image）
$script:DriverLogPath = Join-Path $ArtifactDir 'driver-log.txt'
function Write-DriverLog([string]$Msg) {
    $line = '[' + ([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')) + '] ' + $Msg
    Add-Content -Path $script:DriverLogPath -Value $line -Encoding UTF8
}

function Save-StepShot([int64]$Hwnd, [string]$Name) {
    # 关键步骤截图存档（窗口重建防御同 send-image：截图前重验窗口存活，失效则重解析一次）
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
    # boxes 模式产物按带过滤（坐标 = 窗口像素坐标；阈值与 send-image.ps1 同源标定）
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

function Get-WeComFileInputTokens {
    # 输入区带过滤（E3 真机标定：文件卡片文件名 + 大小标签出现在 y≥0.75h，y≈0.86h 实测；
    # 粘贴校验与终态「文件名消失」判据复测同一带）。左右界：
    #   左 = 420 像素锚定（2026-09-28 真机实测修订：输入框比聊天区宽——文件卡片实测
    #        x0≈497，旧 max(0.10w,620) 按聊天区左界推理会把卡片整段漏掉、粘贴校验恒 false；
    #        420 > 左栏文本列（名称 x0≈226 / 时间列 x1≈400）排除列表噪声，< 卡片 497；
    #        宽窗口输入框只会更宽、卡片 x0 只会更大）；
    #   右 = 0.74w——外部联系人「智能总结」侧栏文本 x0≥0.76w（chat_ocr.py input 模式同
    #        阈值），无条件排除：侧栏内容会在操作中途刷新，基线差分防不住中途「新出现」的
    #        侧栏 token；同时排除 self 文件气泡（右对齐，实测 x0≈1018@1280w）。
    param(
        [Parameter(Mandatory)]$Boxes,
        [Parameter(Mandatory)][int]$W,
        [Parameter(Mandatory)][int]$H
    )
    $xMin = 420
    return @($Boxes | Where-Object {
        [double]$_.y0 -ge ($H * 0.75) -and
        [double]$_.x0 -gt $xMin -and [double]$_.x0 -le ($W * 0.74)
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
    # 标题严格匹配（同 send-image.ps1 / chat-select.ps1：归一化相等或「名字+分隔符」前缀）
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

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WecomCli.SendFile' -Body {
    $swTotal = [System.Diagnostics.Stopwatch]::StartNew()
    $timing = @{ precheck = 0; jev1 = 0; input_click = 0; clipboard = 0; paste = 0; title_recheck = 0; send_wait = 0; final_ocr = 0; jev2 = 0; total = 0 }
    $shots = New-Object System.Collections.ArrayList
    $sectionMap = @{ contact = '联系人'; group = '群聊' }
    $sectionDesc = $Section
    if ($sectionMap.ContainsKey($Section)) { $sectionDesc = $sectionMap[$Section] + '（' + $Section + '）' }
    $expectedName = $TargetName -replace '@微信$', ''

    # 0) 文件预检（fail-fast，先于任何 UI 交互）：TS 侧已校验存在/类型/大小，驱动侧重验
    #    存在性与可读性（文件可能在 TS 校验后、驱动执行前被删）；hash 由 TS 计算传入仅作日志
    if (-not (Test-Path -LiteralPath $FilePath)) {
        Throw-DriverError 'CONFIG_MISSING' ('文件不存在或不可读：' + $FilePath)
    }
    $fileItem = Get-Item -LiteralPath $FilePath
    if ($fileItem.PSIsContainer) {
        Throw-DriverError 'CONFIG_MISSING' ('目标路径是目录而非文件：' + $FilePath)
    }
    # 文件名主干（去扩展名取前 12 字 + 归一化）：粘贴/终态判据的匹配键（paste_check.stem
    # 返回的就是它）。去扩展名防 OCR 把 .txt 误读（.txt → .Lxl 等）；主干为空（如纯扩展名
    # 文件 .gitignore）或归一化后 <3 字符（a.txt→a、报表.txt→报表——contains 匹配过宽，
    # 任意新 token 都可能撞上）时回退用**完整文件名**（含扩展名）做匹配键：E3 实测卡片/
    # 列表预览的扩展名可直读，误读时按 fail-closed 中止。文件名去空白后不足 3 字符的极端名
    # 由 TS 侧预先拒绝（INVALID_ARGUMENT，不 spawn 驱动）。
    $stemRaw = $fileItem.Name -replace '\.[^\.]+$', ''
    if ($stemRaw.Length -gt 12) { $stemRaw = $stemRaw.Substring(0, 12) }
    $stemN = ConvertTo-WeComNormalized $stemRaw
    if ([string]::IsNullOrEmpty($stemN) -or $stemN.Length -lt 3) {
        $stemN = ConvertTo-WeComNormalized $fileItem.Name
    }
    Write-DriverLog ('file: path=' + $FilePath + ' name=' + $fileItem.Name + ' size=' + $fileItem.Length + 'B sha256=' + $FileHash + ' stem=' + $stemN)

    # 0.5) 搜索框残留防御（best-effort，同 send-image）：Ctrl+F 聚焦 → Ctrl+A+Delete 清空
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

    # 2) Jev #1 三问合一（right_conversation / input_point / has_draft；措辞与 send-image 一致）
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
    $stateLines += '任务：准备在目标会话的文本输入框中粘贴一个文件并发送。当前窗口可能已打开目标会话，也可能停留在别的会话。'
    $stateText = $stateLines -join "`n"
    $questions = @{
        right_conversation = @{ type = 'choice'; instructions = '当前主窗口打开的会话是否就是目标会话？（按标题带内容与目标 name 判断）'; criteria = @{
            yes = '标题带显示的就是目标会话'; no = '显示的是别的会话'; unclear = '证据不足无法判断' } }
        has_draft = @{ type = 'choice'; instructions = '底部文本输入区是否已有输入的草稿文本？（灰色占位符（如「发送消息」）不算草稿；工具栏图标行的碎字不算草稿）'; criteria = @{
            yes = '输入区有已输入的草稿文本'; no = '输入区干净（无草稿）' } }
    }
    if ($bottomTokens.Count -gt 0) {
        # 输入点选择只在底部带有 token 时可问；「发送(S)」按钮不是输入框，按归一化前缀剔除
        # （逻辑照搬 send-image.ps1 step2）
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
        # 降级：input 模式判空（占位符/按钮/侧栏/图标碎字均已剔除；文件卡片有文件名文本
        # 同样会判为非空 → 中止，正好拦住上次失败尝试残留的文件卡片）
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

    # 3) 点击输入框聚焦 → baseline 输入区 OCR（干净基线）→ SetFileDropList → Ctrl+V → 粘贴校验
    #    动作前重验主窗口存活（同 send-image step3）
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
    $baseBoxes = Invoke-SendChatOcr ([string]$base.path) 'boxes'
    $baseInputTokens = @(Get-WeComFileInputTokens -Boxes @($baseBoxes.boxes) -W $base.w -H $base.h)
    Write-DriverLog ('step3 baseline 输入区 OCR（文件卡片出现前干净基线）：' + $baseInputTokens.Count + ' token：' + (($baseInputTokens | ForEach-Object { [string]$_.text }) -join '|'))

    # 基线差分集（粘贴/终态判据只认基线之后**新出现**的 token，防既有 token 假命中）：
    # ①输入区带归一化集——占位符「发送消息」含「消息」、工具栏/侧栏碎字等基线 token 一律
    #   不算粘贴证据；上次失败尝试残留的文件卡片也在基线里，二次粘贴同文件 token 不变即
    #   不算新 → 粘贴门拦下（防「旧卡片还在 + 本次粘贴未生效」被误判为已粘贴）；
    # ②左栏会话列表（x0<0.40w，与终态判据②同区域）归一化集——其他会话名/预览含主干
    #   不算发送证据（重发同名文件时列表预览不变 → 差分不命中，按 fail-closed 走人工核对）
    $baseInputNorms = @{}
    foreach ($t in $baseInputTokens) { $baseInputNorms[(ConvertTo-WeComNormalized ([string]$t.text))] = $true }
    $baseListNorms = @{}
    foreach ($t in @($baseBoxes.boxes | Where-Object { [double]$_.x0 -lt ($base.w * 0.40) })) {
        $baseListNorms[(ConvertTo-WeComNormalized ([string]$t.text))] = $true
    }

    # 剪贴板写入文件列表（System.Windows.Forms Clipboard.SetFileDropList + StringCollection
    # 绝对路径；5 次重试×150ms，全败 CONFIG_MISSING）。副作用：覆盖用户剪贴板且不恢复
    # （刻意的，同 send-image 粘贴通道：paste handler 异步读剪贴板，恢复竞态会粘贴到错的内容）
    $clipOk = $false
    $clipTries = 0
    for ($i = 1; $i -le 5; $i++) {
        $clipTries = $i
        try {
            $dropList = New-Object 'System.Collections.Specialized.StringCollection'
            [void]$dropList.Add($fileItem.FullName)
            [System.Windows.Forms.Clipboard]::SetFileDropList($dropList)
            $clipOk = $true
            break
        } catch {
            if ($i -lt 5) { Start-Sleep -Milliseconds 150 }
        }
    }
    if (-not $clipOk) {
        Throw-DriverError 'CONFIG_MISSING' ('剪贴板写入文件列表失败（重试 ' + $clipTries + ' 次均失败），无法经剪贴板粘贴通道输入文件：' + $FilePath)
    }
    $timing.clipboard = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step3 Clipboard.SetFileDropList 第 ' + $clipTries + ' 次成功')

    $t0 = $swTotal.ElapsedMilliseconds
    $pasted = Send-WeComAttachChordKey -Hwnd $mainHwnd -Vk 0x56
    Start-Sleep -Milliseconds 1500
    $pastedSnap = Save-StepShot $mainHwnd 'step3-pasted.png'
    [void]$shots.Add([string]$pastedSnap.path)
    # 粘贴校验：输入区（y≥0.75h、x∈(聊天区左界,0.74w]，E3 标定带）出现基线没有的**新
    # token** 且归一化 contains 文件名主干
    $pastedBoxesOcr = Invoke-SendChatOcr ([string]$pastedSnap.path) 'boxes'
    $pastedInputTokens = @(Get-WeComFileInputTokens -Boxes @($pastedBoxesOcr.boxes) -W $pastedSnap.w -H $pastedSnap.h)
    $pasteHit = $false
    $pasteEvidence = @()
    foreach ($t in $pastedInputTokens) {
        $raw = [string]$t.text
        $nt = ConvertTo-WeComNormalized $raw
        if ($stemN -and -not $baseInputNorms.ContainsKey($nt) -and $nt.Contains($stemN)) {
            $pasteHit = $true
            if ($pasteEvidence.Count -lt 5) { $pasteEvidence += $raw }
        }
    }
    $timing.paste = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step3 attachstate Ctrl+V attach=' + $pasted + '；粘贴校验（输入区新出现 token 含主干「' + $stemN + '」，基线差分）=' + $pasteHit + '；命中 token：' + $(if ($pasteEvidence.Count -gt 0) { ($pasteEvidence -join ' / ') } else { '（无）' }) + '；输入区 token：[' + (($pastedInputTokens | ForEach-Object { [string]$_.text }) -join '|') + ']')
    if (-not $pasteHit) {
        # 文件卡片未出现：尚未按 Enter，无发送副作用（effect=none 由 TS 处理）。
        # 残留清理（2026-09-28 真机实测：Ctrl+A+Delete 可清除输入区的文件卡片）：
        # 粘贴可能部分生效（卡片存在但基线差分不认，如上次失败残留同名卡片），
        # 中止前尽力清掉自己粘贴产生的卡片，防残留累积干扰后续运行。
        if ($pasted) {
            [void](Clear-WeComFocusedInput -MainHwnd $mainHwnd)
            Start-Sleep -Milliseconds 300
            Write-DriverLog 'step3 粘贴校验失败，已尝试 Ctrl+A+Delete 清理自贴卡片'
        }
        Throw-DriverError 'UI_CHANGED' ('文件卡片未出现（粘贴可能未生效）：输入区 OCR 未读到新出现的文件名主干「' + $stemN + '」（基线差分：基线里已有的 token 不算粘贴证据——若输入区已有同名残留卡片，本判定拒绝叠加发送；attach=' + $pasted + '，输入区 token=[' + (($pastedInputTokens | ForEach-Object { [string]$_.text }) -join '|') + ']），已中止且未按 Enter 发送，并已尝试清理自贴卡片（剪贴板仍保留该文件列表且不恢复，请知悉）')
    }

    # 4) 发送前会话复核（规则，同 send-image step4）：粘贴后截图 OCR 标题带严格匹配
    #    （复用粘贴校验已取的 boxes，不重复跑 OCR）；不一致 → UI_CHANGED fail-closed。
    #    文件卡片残留：Ctrl+A+Delete 可清除（2026-09-28 真机实测，修正早期「清不掉」的
    #    误判），失败路径同样清理
    $t0 = $swTotal.ElapsedMilliseconds
    $title2 = Get-WeComTitleFromTokens -Tokens @(Get-WeComBandTokens -Boxes @($pastedBoxesOcr.boxes) -W $pastedSnap.w -H $pastedSnap.h -Band 'title')
    $timing.title_recheck = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step4 发送前复核 title=' + $title2)
    if (-not (Test-WeComTitleMatch -Title $title2 -Expected $TargetName)) {
        [void](Clear-WeComFocusedInput -MainHwnd $mainHwnd)
        Start-Sleep -Milliseconds 300
        Write-DriverLog 'step4 标题复核失败，已尝试 Ctrl+A+Delete 清理自贴文件卡片'
        Throw-DriverError 'UI_CHANGED' ('发送前复核：会话标题「' + $title2 + '」与目标「' + $expectedName + '」不一致，已中止且未按 Enter 发送（防串消息），并已尝试清理输入区自贴的文件卡片（Ctrl+A+Delete，2026-09-28 真机实测可清除）')
    }

    # 5) Enter 发送 → 1800ms 渲染等待 → 终态证据：输入区文件名 token 消失 + 会话列表含文件名
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
    $afterBoxes = Invoke-SendChatOcr ([string]$after.path) 'boxes'
    # ① 输入区文件名 token 消失（复测粘贴校验同带 y≥0.75h、x∈(聊天区左界,0.74w]；
    #    残留判定只认基线之后新出现的 token——基线里就有的含主干 token 不算卡片残留）
    $afterInputTokens = @(Get-WeComFileInputTokens -Boxes @($afterBoxes.boxes) -W $after.w -H $after.h)
    $inputGone = $true
    $inputResidue = @()
    foreach ($t in $afterInputTokens) {
        $raw = [string]$t.text
        $nt = ConvertTo-WeComNormalized $raw
        if ($stemN -and -not $baseInputNorms.ContainsKey($nt) -and $nt.Contains($stemN)) {
            $inputGone = $false
            if ($inputResidue.Count -lt 5) { $inputResidue += $raw }
        }
    }
    # ② 会话列表（左栏 x0<0.40w，沿用 M7 阈值）出现基线没有的新 token 且归一化含
    #    「[+主干」或主干（列表预览格式实测「[e3-test-file.txt]」，容忍括号误读）
    $listTokens = @($afterBoxes.boxes | Where-Object { [double]$_.x0 -lt ($after.w * 0.40) } | Sort-Object { [double]$_.y0 }, { [double]$_.x0 })
    $listHit = $false
    $fileEvidence = @()
    $listEvidence = @()
    foreach ($t in $listTokens) {
        $raw = [string]$t.text
        if ($listEvidence.Count -lt 8) { $listEvidence += $raw }
        $nt = ConvertTo-WeComNormalized $raw
        if ($stemN -and -not $baseListNorms.ContainsKey($nt) -and ($nt.Contains('[' + $stemN) -or $nt.Contains($stemN))) {
            $listHit = $true
            if ($fileEvidence.Count -lt 5) { $fileEvidence += $raw }
        }
    }
    $ruleChecks = 0
    if ($inputGone) { $ruleChecks++ }
    if ($listHit) { $ruleChecks++ }
    $timing.final_ocr = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step5 终态规则双判据（基线差分，只认新 token）：①输入区文件名消失（残留=' + (-not $inputGone) + $(if (-not $inputGone) { '，' + ($inputResidue -join ' / ') } else { '' }) + '）②会话列表新出现文件名=' + $listHit + ' → ' + $ruleChecks + '/2')

    # 6) Jev #2 两问合一（sent_successfully / failure_mode）：可用以其判定为准（yes →
    #    method=jev，即使规则双判据已 2/2 也如此）；Jev 不可用/答案非法降级规则须 2/2 全过
    $t0 = $swTotal.ElapsedMilliseconds
    $state2Lines = @()
    $state2Lines += '企业微信文件消息发送后的 OCR 校验证据（PostMessage Enter 后 1.8s 截图）：'
    $state2Lines += ('—— 输入区文件名判定（粘贴校验同带 OCR + 基线差分，匹配键=文件名主干「' + $stemN + '」）：粘贴后读到文件名=' + $pasteHit + '，发送后已消失=' + $inputGone + $(if (-not $inputGone) { '（残留：' + ($inputResidue -join ' / ') + '）' } else { '' }))
    $state2Lines += ('—— 会话列表新出现且含文件名的 token（基线差分，发送前已存在的不算）：' + $(if ($fileEvidence.Count -gt 0) { ($fileEvidence -join ' / ') } else { '（无）' }))
    $state2Lines += ('—— 会话列表 token（左栏，最多 8 个）：' + $(if ($listEvidence.Count -gt 0) { ($listEvidence -join ' / ') } else { '（无）' }))
    $state2Lines += ('—— 文件：' + $fileItem.Name + '（sha256=' + $FileHash + '，' + $fileItem.Length + ' 字节）')
    $state2Lines += '（证据说明：文件消息没有文本气泡，消息区 OCR 读不到该文件的内容文字属正常，不是未发送的证据；「输入区文件名消失 + 会话列表预览出现 [文件名]」即为已成功发送的有效证据。若该文件此前刚发送过（重发同名文件），会话列表预览可能保持不变、差分不命中属正常——此时「粘贴后文件名出现 + 发送后输入区文件名消失」仍可佐证已发送。）'
    $state2Lines += ('目标会话名：' + $expectedName)
    $state2Lines += '任务：判断该文件是否已成功发送到目标会话。'
    $state2Text = $state2Lines -join "`n"
    $jev2 = Invoke-WeComJev -StateText $state2Text -Questions @{
        sent_successfully = @{ type = 'choice'; instructions = '刚才粘贴的文件是否已成功发送到目标会话？（注意证据说明：文件消息无文本气泡，消息区读不到文件内容属正常）'; criteria = @{
            yes = '已成功发送'; no = '未发送成功'; unclear = '证据不足无法判断' } }
        failure_mode = @{ type = 'choice'; instructions = '若未成功，失败形态最接近哪种？（已成功时选 unclear 即可）'; criteria = @{
            not_sent = '没有发出去（输入区仍有文件卡片，或输入区已清空且会话列表预览也无文件名——重发同名文件预览不变除外，见证据说明）'; sent_elsewhere = '可能发到了别的会话'; unclear = '证据不足' } }
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
            Throw-DriverError 'EXECUTION_UNKNOWN' ('发送后校验失败（Jev 判定 ' + $sentChoice + '，failure_mode=' + $fm + '）：文件可能已发出，不会自动重试，请人工核对（详见 driver-log.txt）')
        }
        # 答案非法 → 降级规则双判据（下方继续）
    }
    if ($method -eq '') {
        # 降级链终态：规则双判据须 2/2 全过，任一不过 → EXECUTION_UNKNOWN 绝不自动重试
        $method = 'rule_2of2'
        if ($ruleChecks -lt 2) {
            Throw-DriverError 'EXECUTION_UNKNOWN' ('发送后校验失败（双判据仅过 ' + $ruleChecks + '/2 项）：文件可能已发出，不会自动重试，请人工核对（详见 driver-log.txt）')
        }
    }
    $timing.total = [int]$swTotal.ElapsedMilliseconds
    Write-DriverLog ('done method=' + $method + ' rule_checks=' + $ruleChecks + '/2 stem=' + $stemN + ' paste_hit=' + $pasteHit + ' input_gone=' + $inputGone + ' list_hit=' + $listHit + ' timing=' + ($timing | ConvertTo-Json -Compress))

    # 7) 返回（navigate_required=false = 已在目标会话完成文件发送与校验）
    $sentVerification = @{ method = $method; result = 'sent' }
    if ($method -eq 'rule_2of2') { $sentVerification.rule_checks_passed = $ruleChecks }
    return @{
        navigate_required = $false
        target = @{ name = $TargetName; subtitle = $Subtitle; section = $Section }
        title = $title2
        sent_verification = $sentVerification
        paste_check = @{ stem = $stemN; paste_hit = $pasteHit; input_gone = $inputGone; list_hit = $listHit }
        input_point = @{ x = $ipX; y = $ipY; source = $ipSrc }
        timing_ms = $timing
        screenshot_paths = @($shots.ToArray())
    }
}
