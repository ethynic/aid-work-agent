# drivers/ps1/history-read.ps1 — wecom_history_read 驱动（只读消息内容；
# 副作用注意：进入会话会清除该会话未读角标，无其它写副作用）
# 全链路（滚动与坐标事实同 .tmp/wecom-probe/probe-wheel.ps1 真机验证）：
#   1) 进会话（-OpenMode）：
#      search — 与 message-send 相同的搜索定位（Open-WeComSearchOverlay → 稳定帧 OCR →
#               name+section+subtitle 精确匹配，歧义 TARGET_AMBIGUOUS → 点结果行，finally 关 overlay）
#      row    — 直接点会话列表行（-RowX/-RowY = unread OCR 给的名称行中心，图像坐标系
#               + 主窗口 rect 换算屏幕坐标）；标题校验不一致 → UI_CHANGED（TS 侧降级 search 重试）
#   2) fail-closed：OCR 会话标题严格匹配目标（同 message-send，防串会话）
#   3) 先下滚到底（WM_MOUSEWHEEL delta=-120 x40）→ 逐屏上滚截图 OCR（chat_ocr.py history 模式）
#      → 页间「旧页后缀 == 已合并前缀」最大重叠去重（算法移植自 weixin-cli p4-history-capture）
#      → -SinceDays>0 时本页最早「M月D日」分割线超龄即停止上翻
#      → 上滚后整页与前一页完全相同视为到顶，停止
#   4) finally 滚回底部恢复原位（无论成败）
# 关键步骤截图存 -ArtifactDir（page-1..N.png）；OCR 原始输出与翻页明细写 driver-log.txt。
# 注意：本文件含中文，必须以 UTF-8 with BOM 保存。
param(
    [Parameter(Mandatory)][string]$TargetName,
    [string]$Subtitle = '',
    [string]$Section = '',
    [int]$MaxPages = 1,
    [int]$SinceDays = 0,
    [ValidateSet('search', 'row')][string]$OpenMode = 'search',
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

function Invoke-HistoryChatOcr([string]$ImagePath, [string]$Mode) {
    # _common 的 OCR 入口 + 原始输出落 driver-log（真机排障）
    $parsed = Invoke-WeComChatOcr -ImagePath $ImagePath -Mode $Mode
    Write-DriverLog ('OCR[' + $Mode + '] ' + ($parsed | ConvertTo-Json -Compress -Depth 10))
    return $parsed
}

function Get-WeComChatTitle([int64]$MainHwnd) {
    # 标题校验为瞬时检查（非关键步骤存档），截图仍走 TEMP；OCR 结果落 driver-log
    $shot = Join-Path $env:TEMP 'wecom-driver-history-title.png'
    Get-WeComWindowSnapshot -Hwnd $MainHwnd -Path $shot | Out-Null
    $ocr = Invoke-HistoryChatOcr -ImagePath $shot -Mode 'title'
    return [string]$ocr.title
}

function Assert-WeComChatTitle([int64]$MainHwnd, [string]$Expected) {
    $title = Get-WeComChatTitle $MainHwnd
    # 严格匹配（防串会话）：归一化后相等，或以「名字 + 后缀分隔符」形式开头
    # （外部联系人「陆伟 @微信」、群「产品讨论群（13）」）。
    # 纯子串匹配会把「陆伟」误放进「陆伟民」的会话。
    $t = ConvertTo-WeComNormalized $title
    $e = ConvertTo-WeComNormalized $Expected
    $ok = ($t -eq $e) -or $t.StartsWith($e + '@') -or $t.StartsWith($e + '（') -or $t.StartsWith($e + '(')
    if (-not $ok) {
        Throw-DriverError 'UI_CHANGED' ("会话标题「" + $title + "」与目标「" + $Expected + "」不一致，已中止（防串会话）")
    }
    return $title
}

function Open-WeComChatBySearch {
    # 与 message-send step1-2 相同的搜索定位链路；返回主窗口 hwnd（点击后 overlay 自动关闭，
    # finally 兜底关 overlay 恢复原状）
    Write-DriverLog ('open-by-search target=' + $TargetName + ' subtitle=' + $Subtitle + ' section=' + $Section)
    $s = Open-WeComSearchOverlay -Query $TargetName
    $mainHwnd = [int64]$s.MainHwnd
    $overlayHwnd = [int64]$s.OverlayHwnd
    try {
        $ocr = $s.Ocr
        Write-DriverLog ('OCR[search] ' + ($ocr | ConvertTo-Json -Compress -Depth 10))
        $sectionMap = @{ contact = '联系人'; group = '群聊' }
        $wantSection = ''
        if (-not [string]::IsNullOrEmpty($Section) -and $sectionMap.ContainsKey($Section)) { $wantSection = $sectionMap[$Section] }
        $all = @($ocr.items)
        # 精确匹配：name + section + subtitle；落空则降级 name + section（subtitle 可能随账号状态变化）
        $cands = @($all | Where-Object {
            $_.name -eq $TargetName -and
            ([string]::IsNullOrEmpty($wantSection) -or $_.section -eq $wantSection) -and
            ([string]::IsNullOrEmpty($Subtitle) -or $_.subtitle -eq $Subtitle)
        })
        if ($cands.Count -eq 0 -and -not [string]::IsNullOrEmpty($Subtitle)) {
            $cands = @($all | Where-Object {
                $_.name -eq $TargetName -and
                ([string]::IsNullOrEmpty($wantSection) -or $_.section -eq $wantSection)
            })
        }
        if ($cands.Count -eq 0) {
            Throw-DriverError 'TARGET_NOT_FOUND' ("搜索结果中未找到与「" + $TargetName + "」匹配的条目（section=" + $Section + "）")
        }
        if ($cands.Count -gt 1) {
            Throw-DriverError 'TARGET_AMBIGUOUS' ("搜索结果中「" + $TargetName + "」有 " + $cands.Count + " 个同名匹配，无法唯一确定（请改用更精确的目标）")
        }
        $item = $cands[0]
        $ovl = Get-WeComWindowInfo ([IntPtr]$overlayHwnd)
        $clickX = [int]($ovl.X + [int]$item.x)
        $clickY = [int]($ovl.Y + [int]$item.y)
        Write-DriverLog ('click 搜索结果行 screen=(' + $clickX + ',' + $clickY + ') overlayHwnd=' + $overlayHwnd)
        [void](Send-WeComClick -Hwnd $overlayHwnd -ScreenX $clickX -ScreenY $clickY)
    } finally {
        Close-WeComSearchOverlay -OverlayHwnd $overlayHwnd -MainHwnd $mainHwnd
    }
    Start-Sleep -Milliseconds 1500
    return $mainHwnd
}

function Open-WeComChatByRow([int64]$MainHwnd) {
    # 直点会话列表行：RowX/RowY 是 unread OCR 名称行中心（图像坐标系，原点=主窗口左上角）
    $main = Get-WeComWindowInfo ([IntPtr]$MainHwnd)
    $clickX = [int]($main.X + $RowX)
    $clickY = [int]($main.Y + $RowY)
    Write-DriverLog ('open-by-row click 会话列表行 screen=(' + $clickX + ',' + $clickY + ')（图像坐标=(' + $RowX + ',' + $RowY + ') + 截图原点=(' + $main.X + ',' + $main.Y + ')）mainHwnd=' + $MainHwnd)
    [void](Send-WeComClick -Hwnd $MainHwnd -ScreenX $clickX -ScreenY $clickY)
    Start-Sleep -Milliseconds 1200
    return $MainHwnd
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

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WecomCli.HistoryRead' -Body {
    if ($MaxPages -lt 1 -or $MaxPages -gt 10) {
        Throw-DriverError 'INVALID_ARGUMENT' ('MaxPages 必须是 1..10（实际：' + $MaxPages + '）')
    }
    if ($OpenMode -eq 'row' -and ($RowX -lt 0 -or $RowY -lt 0)) {
        Throw-DriverError 'INVALID_ARGUMENT' 'OpenMode=row 时必须提供 -RowX/-RowY（unread OCR 名称行中心）'
    }
    $shots = New-Object System.Collections.ArrayList

    # 1) 进会话 + 标题校验（打开外部联系人会话主窗口可能变宽：之后任何坐标使用前重新解析/取 rect）
    $mainHwnd = Resolve-WeComMainWindow
    if ($OpenMode -eq 'row') {
        $mainHwnd = Open-WeComChatByRow $mainHwnd
        $mainHwnd = Resolve-WeComMainWindow
    } else {
        [void](Open-WeComChatBySearch)
        $mainHwnd = Resolve-WeComMainWindow
    }
    $title = Assert-WeComChatTitle $mainHwnd $TargetName
    Write-DriverLog ('会话标题校验通过 title=' + $title)

    # 2) 先下滚到底（也是结束后的恢复位置）
    Send-WeComWheel -Hwnd $mainHwnd -Delta -120 -Count 40
    Start-Sleep -Milliseconds 800

    $pages = New-Object System.Collections.ArrayList   # 每页 = @{ msgs; keys }，pages[0]=最新底部页
    try {
        for ($page = 1; $page -le $MaxPages; $page++) {
            $mainHwnd = Resolve-WeComMainWindow
            $s = Save-StepShot $mainHwnd ('page-' + $page + '.png')
            [void]$shots.Add([string]$s.path)
            $ocr = Invoke-HistoryChatOcr ([string]$s.path) 'history'
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
    if ($pages.Count -eq 0) {
        Throw-DriverError 'CONTENT_UNAVAILABLE' '未抓到任何消息行（消息区为空或 OCR 全部漏检）'
    }

    # 3) 合并：页按新→旧采集（pages[0]=最新底部页），逐页把更旧的页拼到前面，
    #    按「旧页后缀 == 已合并前缀」的最大重叠去重（比较键 side|归一化文本）
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

    return @{
        title = $title
        messages = $mergedMsgs.ToArray()
        pages_read = $pages.Count
        screenshot_paths = $shots.ToArray()
    }
}
