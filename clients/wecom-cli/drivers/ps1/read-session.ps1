# drivers/ps1/read-session.ps1 — wecom_read_session 驱动（M9，前身 M3 history-read.ps1
# 改名重写；只读消息内容。副作用注意：进入会话会清除该会话未读角标，无其它写副作用）
# 导航迁移到共享智能分发（与 send/send-image/send-file 同款，TS 层 navigate.ts 编排）：
#   0.5) 搜索框残留防御（best-effort，同 send-file）：attachstate Ctrl+F 聚焦 →
#        Ctrl+A+Delete 清空（顺带关闭可能残留的搜索 overlay，防其遮挡会话列表/消息区）。
#        **旧 M2 搜索链（驱动内打开搜索面板：固定像素点击/固定带残留检查/× 清空/ESC
#        关闭）自 M9 起从本驱动全部退役**——旧链在窄窗口有残留误判 bug + ESC 最小化
#        风险（README M4 段），会话切换统一交 TS 编排 chatSearch+chatSelect 完成，
#        本驱动只判定不导航。
#   1) row 快路径（可选，watch 依赖）：-RowX/-RowY 均 >0 时先直点会话列表行
#      （unread OCR 给的名称行中心，图像坐标系 + 主窗口 rect 换算屏幕坐标），再走同一
#      会话判定做校验兜底（点击未命中/列表已滚动 → 判定不过 → navigate_required 分发）。
#   2) 截图 + boxes OCR 标题带（y<0.07h 且 x0>0.20w，阈值与 send-file/_common 同源标定；
#      0.20w 几何排除搜索框内容与会话列表首行，不依赖搜索框是否已清空）→ 拼接标题行。
#   3) Jev #1 单问 right_conversation（read 是只读动作：不点输入框、不输入文字，
#      输入点选择不需要；**草稿判定不适用——读消息绝不碰输入框，绝不因草稿中止**）：
#      yes → 继续；no/unclear → 返回 navigate_required=true 交 TS 编排（chatSearch +
#      chatSelect 后二次调用本驱动）；Jev 不可用/答案非法 → 降级标题归一化规则匹配。
#   4) 标题严格校验（防串会话，同现有 Assert 语义：归一化相等或「名字+@/（」前缀；
#      Jev 判 yes 但规则不过 → 以规则为准 fail-closed UI_CHANGED）。
#   5) 滚动抓取核心（自 history-read.ps1 原样迁入，坐标事实同 .tmp/wecom-probe/
#      probe-wheel.ps1 真机验证）：先下滚到底（WM_MOUSEWHEEL delta=-120 x40）→ 逐屏上滚
#      截图 OCR（chat_ocr.py history 模式）→ 页间「旧页后缀 == 已合并前缀」最大重叠去重
#      → -SinceDays>0 时本页最早「M月D日」分割线超龄即停止上翻 → 上滚后整页与前一页
#      完全相同视为到顶停止 → finally 滚回底部恢复原位（无论成败）。
#   6) 时间戳沿袭（M9）：页合并完成后沿合并消息流（旧→新）遍历，side=timeline 的条目
#      记住其文本（如「7月16日 09:01」「08:23」），后续 self/peer 条目带 time=<最近
#      分割线原文>；首条分割线之前的消息无 time 字段。timeline 条目保留在输出中
#      （watch 的页间去重/水位逻辑依赖 side|text 键，不受影响）。time 语义 = 近似时间。
# side 语义（锁定，勿改）：气泡左缘锚定=peer（对方发的）、右缘锚定=self（自己发的），
#   见 chat_ocr.py classify_side（OCR 启发式，长行可能误判，不得依赖 side 做安全判定）。
# 关键步骤截图存 -ArtifactDir（step1-precheck / page-1..N.png）；OCR 原始输出与翻页明细
# 写 driver-log.txt。注意：本文件含中文，必须以 UTF-8 with BOM 保存。
param(
    [Parameter(Mandatory)][string]$TargetName,
    [string]$Subtitle = '',
    [string]$Section = '',
    [int]$MaxPages = 1,
    [int]$SinceDays = 0,
    [int]$RowX = -1,
    [int]$RowY = -1,
    [Parameter(Mandatory)][string]$ArtifactDir
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$driverDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $driverDir '_common.ps1')

# 诊断日志：点击目标坐标 / OCR 原始输出 / 翻页去重明细，写 artifact 目录 driver-log.txt（真机排障用）
$script:DriverLogPath = Join-Path $ArtifactDir 'driver-log.txt'
function Write-DriverLog([string]$Msg) {
    $line = '[' + ([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')) + '] ' + $Msg
    Add-Content -Path $script:DriverLogPath -Value $line -Encoding UTF8
}

function Save-StepShot([int64]$Hwnd, [string]$Name) {
    # 关键步骤截图存档；返回 @{ path; left; top; w; h }（截图坐标系原点 = 窗口左上角）
    $path = Join-Path $ArtifactDir $Name
    $snap = Get-WeComWindowSnapshot -Hwnd $Hwnd -Path $path
    return @{ path = $path; left = [int]$snap[0]; top = [int]$snap[1]; w = [int]$snap[2]; h = [int]$snap[3] }
}

function Invoke-ReadSessionOcr([string]$ImagePath, [string]$Mode) {
    # _common 的 OCR 入口 + 原始输出落 driver-log（真机排障）
    $parsed = Invoke-WeComChatOcr -ImagePath $ImagePath -Mode $Mode
    Write-DriverLog ('OCR[' + $Mode + '] ' + ($parsed | ConvertTo-Json -Compress -Depth 10))
    return $parsed
}

function Get-WeComTitleBandTokens {
    # boxes 模式产物按标题带过滤（y<0.07h 且 x0>0.20w；阈值与 send-file.ps1/_common
    # Get-WeComChatAreaTitle 同源标定：0.20w 几何排除搜索框内容 x0≈0.13-0.17w 与
    # 会话列表首行 ≈0.17w，聊天区标题实测 ≥0.22w 外部联系人 / 0.296w 普通会话）
    param(
        [Parameter(Mandatory)]$Boxes,
        [Parameter(Mandatory)][int]$W,
        [Parameter(Mandatory)][int]$H
    )
    return @($Boxes | Where-Object {
        [double]$_.y0 -lt ($H * 0.07) -and [double]$_.x0 -gt ($W * 0.20)
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
    # 标题严格匹配（同 send-file.ps1 / chat-select.ps1：归一化相等或「名字+分隔符」前缀）。
    # 纯子串匹配会把「陆伟」误放进「陆伟民」的会话（防串会话 fail-closed 的核心）
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
    # token 数组 → Jev state 行（键前缀 + 文本 + 中心坐标）
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

function Get-PageFirstDate($Msgs) {
    # 取本页第一条「M月D日」时间分割线并解析为日期；无年份的按月日推断（月份晚于当前月则视为去年）
    foreach ($m in $Msgs) {
        if ([string]$m.side -ne 'timeline') { continue }
        $mm = [regex]::Match([string]$m.text, '(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日')
        if (-not $mm.Success) { continue }
        $year = if ($mm.Groups[1].Success) { [int]$mm.Groups[1].Value } else {
            $now = Get-Date
            if ([int]$mm.Groups[2].Value -gt $now.Month) { $now.Year - 1 } else { $now.Year }
        }
        return Get-Date -Year $year -Month ([int]$mm.Groups[2].Value) -Day ([int]$mm.Groups[3].Value) -Hour 0 -Minute 0 -Second 0
    }
    return $null
}

function Get-PageKey($Msg) {
    # 页间去重比较键：side + 归一化文本（OCR 空白/全半角抖动不破坏重叠匹配）
    return ([string]$Msg.side) + '|' + (ConvertTo-WeComNormalized ([string]$Msg.text))
}

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WecomCli.ReadSession' -Body {
    $swTotal = [System.Diagnostics.Stopwatch]::StartNew()
    $timing = @{ precheck = 0; jev1 = 0; scroll = 0; merge = 0; total = 0 }
    $shots = New-Object System.Collections.ArrayList
    $sectionMap = @{ contact = '联系人'; group = '群聊' }
    $sectionDesc = $Section
    if ($sectionMap.ContainsKey($Section)) { $sectionDesc = $sectionMap[$Section] + '（' + $Section + '）' }
    $expectedName = $TargetName -replace '@微信$', ''

    if ($MaxPages -lt 1 -or $MaxPages -gt 10) {
        Throw-DriverError 'INVALID_ARGUMENT' ('MaxPages 必须是 1..10（实际：' + $MaxPages + '）')
    }
    if ((($RowX -gt 0) -or ($RowY -gt 0)) -and -not (($RowX -gt 0) -and ($RowY -gt 0))) {
        Throw-DriverError 'INVALID_ARGUMENT' 'RowX/RowY 必须成对提供（unread OCR 名称行中心，图像坐标系）'
    }

    # 0.5) 搜索框残留防御（best-effort，同 send-file）：Ctrl+F 聚焦 → Ctrl+A+Delete 清空
    $t0 = $swTotal.ElapsedMilliseconds
    $mainHwnd = Resolve-WeComMainWindow
    [void](Send-WeComAttachChordKey -Hwnd $mainHwnd -Vk 0x46)
    Start-Sleep -Milliseconds 150
    [void](Clear-WeComSearchBoxV2 -MainHwnd $mainHwnd)
    Start-Sleep -Milliseconds 200
    Write-DriverLog ('step0.5 搜索框残留清空（防御性，best-effort）耗时=' + [int]($swTotal.ElapsedMilliseconds - $t0) + 'ms')

    # 1) row 快路径（可选）：直点会话列表行（RowX/RowY = unread OCR 名称行中心，图像坐标系，
    #    原点=主窗口左上角）；点击后重解析主窗口（打开外部联系人会话主窗口可能变宽）
    if ($RowX -gt 0 -and $RowY -gt 0) {
        $main = Get-WeComWindowInfo ([IntPtr]$mainHwnd)
        $clickX = [int]($main.X + $RowX)
        $clickY = [int]($main.Y + $RowY)
        Write-DriverLog ('open-by-row click 会话列表行 screen=(' + $clickX + ',' + $clickY + ')（图像坐标=(' + $RowX + ',' + $RowY + ') + 截图原点=(' + $main.X + ',' + $main.Y + ')）mainHwnd=' + $mainHwnd)
        [void](Send-WeComClick -Hwnd $mainHwnd -ScreenX $clickX -ScreenY $clickY)
        Start-Sleep -Milliseconds 1200
        $mainHwnd = Resolve-WeComMainWindow
    }

    # 2) 截图 + 标题带 OCR → 拼接标题（判定与严格校验共用同一来源）
    Write-DriverLog ('target=' + $TargetName + ' subtitle=' + $Subtitle + ' section=' + $Section + ' mainHwnd=' + $mainHwnd + ' row=(' + $RowX + ',' + $RowY + ') maxPages=' + $MaxPages + ' sinceDays=' + $SinceDays)
    $t0 = $swTotal.ElapsedMilliseconds
    $pre = Save-StepShot $mainHwnd 'step1-precheck.png'
    [void]$shots.Add([string]$pre.path)
    $boxesOcr = Invoke-ReadSessionOcr ([string]$pre.path) 'boxes'
    $titleTokens = @(Get-WeComTitleBandTokens -Boxes @($boxesOcr.boxes) -W $pre.w -H $pre.h)
    $title = Get-WeComTitleFromTokens -Tokens $titleTokens
    $timing.precheck = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step2 标题带 OCR：' + $titleTokens.Count + ' token（title=' + $title + '）')

    # 3) Jev #1 单问 right_conversation（read 只读：不问输入点、不问草稿——
    #    读消息不碰输入框，草稿绝不构成读取中止条件；措辞与 send-file 同款保持稳定）
    $t0 = $swTotal.ElapsedMilliseconds
    $stateLines = @()
    $stateLines += '企业微信主窗口 OCR 标题带结果（窗口像素坐标）：'
    $stateLines += '—— 标题带（y<0.07h，聊天区顶部会话标题区）：'
    if ($titleTokens.Count -gt 0) { $stateLines += (Format-WeComTokenLines -Tokens $titleTokens -KeyPrefix 'T') }
    else { $stateLines += '（无 token）' }
    $stateLines += ('目标会话：name=' + $TargetName + ' subtitle=' + $Subtitle + ' section=' + $sectionDesc)
    $stateLines += '任务：准备读取目标会话的历史消息（只读，不输入任何内容）。当前窗口可能已打开目标会话，也可能停留在别的会话。'
    $stateText = $stateLines -join "`n"
    $jev1 = Invoke-WeComJev -StateText $stateText -Questions @{
        right_conversation = @{ type = 'choice'; instructions = '当前主窗口打开的会话是否就是目标会话？（按标题带内容与目标 name 判断）'; criteria = @{
            yes = '标题带显示的就是目标会话'; no = '显示的是别的会话'; unclear = '证据不足无法判断' } }
    }
    $timing.jev1 = [int]($swTotal.ElapsedMilliseconds - $t0)
    Write-DriverLog ('step3 jev#1 used=' + $jev1.used + ' latency=' + $jev1.latency_ms + 'ms reason=' + [string]$jev1.reason)
    Write-DriverLog ('step3 jev#1 state: ' + ($stateText -replace "`r?`n", ' / '))
    if ($jev1.used) {
        Write-DriverLog ('step3 jev#1 answers: right=' + [string]$jev1.answers.right_conversation.choice)
    }

    # 判定（同 send-file 语义）：Jev yes → 过；no/unclear → navigate_required 交还 TS 编排；
    # Jev 不可用/答案非法 → 降级标题归一化规则匹配（Get-WeComChatAreaTitle 同款语义）
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
        if (Test-WeComTitleMatch -Title $title -Expected $TargetName) {
            $rightOk = $true
        } else {
            $navReason = ('标题归一化规则未命中（当前标题「' + $title + '」≠ 目标「' + $expectedName + '」）')
        }
        Write-DriverLog ('step3 right_conversation 降级规则：title=' + $title + ' 匹配=' + $rightOk)
    }
    if (-not $rightOk) {
        # 非目标/无法判定：不做任何输入（row 快路径点击未命中也走这里），交还 TS 编排
        # （chatSearch + chatSelect 后二次调用本驱动）
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

    # 4) 标题严格校验（防串会话，同现有 Assert 语义）：Jev 判 yes 但规则不过 → 以规则为准
    #    fail-closed（UI_CHANGED）；降级规则路径判定过 = 同一谓词，此处必过（仅日志）
    if (-not (Test-WeComTitleMatch -Title $title -Expected $TargetName)) {
        Throw-DriverError 'UI_CHANGED' ('会话标题「' + $title + '」与目标「' + $expectedName + '」不一致，已中止（防串会话）')
    }
    Write-DriverLog ('会话标题校验通过 title=' + $title + '（判定来源=' + $rightSrc + '）')

    # 5) 滚动抓取核心（自 history-read.ps1 原样迁入）：先下滚到底（也是结束后的恢复位置）
    $t0 = $swTotal.ElapsedMilliseconds
    Send-WeComWheel -Hwnd $mainHwnd -Delta -120 -Count 40
    Start-Sleep -Milliseconds 800

    $pages = New-Object System.Collections.ArrayList   # 每页 = @{ msgs; keys }，pages[0]=最新底部页
    try {
        for ($page = 1; $page -le $MaxPages; $page++) {
            $mainHwnd = Resolve-WeComMainWindow
            $s = Save-StepShot $mainHwnd ('page-' + $page + '.png')
            [void]$shots.Add([string]$s.path)
            $ocr = Invoke-ReadSessionOcr ([string]$s.path) 'history'
            $msgs = @($ocr.messages | ForEach-Object {
                @{ side = [string]$_.side; text = [string]$_.text }
            } | Where-Object { $_.text.Length -gt 0 })
            Write-DriverLog ('page' + $page + ' 行数=' + $msgs.Count)
            if ($msgs.Count -eq 0) { Write-DriverLog '本页无内容，停止'; break }
            $keys = @($msgs | ForEach-Object { Get-PageKey $_ })
            # 到顶检测：上滚后整页与前一页完全相同（滚动未生效），丢弃本页停止
            if ($pages.Count -gt 0) {
                $prevKeys = [string[]]$pages[$pages.Count - 1].keys
                if (($prevKeys -join "`n") -eq ($keys -join "`n")) {
                    Write-DriverLog '上滚后页面未变化（已到顶），停止'
                    break
                }
            }
            $pages.Add(@{ msgs = $msgs; keys = $keys }) | Out-Null
            if ($SinceDays -gt 0) {
                $firstDate = Get-PageFirstDate $msgs
                if ($null -ne $firstDate) {
                    $age = ((Get-Date).Date - $firstDate).Days
                    Write-DriverLog ('本页最早时间线: ' + $firstDate.ToString('yyyy-MM-dd') + '（' + $age + ' 天前）')
                    if ($age -gt $SinceDays) { Write-DriverLog ('已超出 ' + $SinceDays + ' 天范围，停止上翻'); break }
                }
            }
            if ($page -lt $MaxPages) {
                Send-WeComWheel -Hwnd $mainHwnd -Delta 120 -Count 8
                Start-Sleep -Milliseconds 800
            }
        }
    } finally {
        # 恢复原位：滚回底部（无论成败；hwnd 动态，失败仅记录不掩盖主错误）
        try {
            $hw = Resolve-WeComMainWindow
            Send-WeComWheel -Hwnd $hw -Delta -120 -Count 40
        } catch {
            Write-DriverLog ('恢复原位滚回底部失败（不影响已抓取结果）：' + [string]$_.Exception.Message)
        }
    }
    $timing.scroll = [int]($swTotal.ElapsedMilliseconds - $t0)
    if ($pages.Count -eq 0) {
        Throw-DriverError 'CONTENT_UNAVAILABLE' '未抓到任何消息行（消息区为空或 OCR 全部漏检）'
    }

    # 6) 合并：页按新→旧采集（pages[0]=最新底部页），逐页把更旧的页拼到前面，
    #    按「旧页后缀 == 已合并前缀」的最大重叠去重（比较键 side|归一化文本）
    $t0 = $swTotal.ElapsedMilliseconds
    $mergedMsgs = New-Object 'Collections.Generic.List[object]'
    $mergedKeys = New-Object 'Collections.Generic.List[string]'
    foreach ($m in [object[]]$pages[0].msgs) { $mergedMsgs.Add($m) }
    foreach ($k in [string[]]$pages[0].keys) { $mergedKeys.Add($k) }
    for ($i = 1; $i -lt $pages.Count; $i++) {
        $olderMsgs = [object[]]$pages[$i].msgs
        $olderKeys = [string[]]$pages[$i].keys
        $maxOverlap = [Math]::Min($olderKeys.Count, $mergedKeys.Count)
        $overlap = 0
        for ($k = $maxOverlap; $k -ge 1; $k--) {
            $match = $true
            for ($j = 0; $j -lt $k; $j++) {
                if ($olderKeys[$olderKeys.Count - $k + $j] -ne $mergedKeys[$j]) { $match = $false; break }
            }
            if ($match) { $overlap = $k; break }
        }
        $prependCount = $olderMsgs.Count - $overlap
        if ($prependCount -gt 0) {
            $mergedMsgs.InsertRange(0, [object[]]$olderMsgs[0..($prependCount - 1)])
            $mergedKeys.InsertRange(0, [string[]]$olderKeys[0..($prependCount - 1)])
        }
        Write-DriverLog ('page' + ($i + 1) + ' 重叠 ' + $overlap + ' 行，前插 ' + $prependCount + ' 行')
    }
    $timing.merge = [int]($swTotal.ElapsedMilliseconds - $t0)

    # 7) 时间戳沿袭（M9）：合并消息流（旧→新）遍历，timeline 条目记住文本，后续 self/peer
    #    带 time=<最近分割线原文>；首条分割线之前的消息无 time 字段；timeline 条目保留输出
    $outMsgs = New-Object 'Collections.Generic.List[object]'
    $lastTimeline = ''
    foreach ($m in [object[]]$mergedMsgs.ToArray()) {
        $side = [string]$m.side
        $text = [string]$m.text
        if ($side -eq 'timeline') {
            $lastTimeline = $text
            $outMsgs.Add(@{ side = $side; text = $text })
        } elseif ($lastTimeline -ne '') {
            $outMsgs.Add(@{ side = $side; text = $text; time = $lastTimeline })
        } else {
            $outMsgs.Add(@{ side = $side; text = $text })
        }
    }
    $timing.total = [int]$swTotal.ElapsedMilliseconds
    Write-DriverLog ('done pages=' + $pages.Count + ' msgs=' + $outMsgs.Count + ' timing=' + ($timing | ConvertTo-Json -Compress))

    # 8) 返回（navigate_required=false = 已在目标会话完成抓取；navigated 由 TS 层编排补充）
    return @{
        navigate_required = $false
        title = $title
        messages = $outMsgs.ToArray()
        pages_read = $pages.Count
        timing_ms = $timing
        screenshot_paths = @($shots.ToArray())
    }
}
