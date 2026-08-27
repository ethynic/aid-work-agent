# p4-history-capture probe 主脚本
# 流程：滚到底 -> 逐页上滚 + PrintWindow 截图 -> RapidOCR 识别 -> 按坐标区分发言人
#       -> 翻页重叠去重合并 -> 存文本 -> 滚回底部恢复原位
# 全程 PostMessage + PrintWindow：不占光标/剪贴板，窗口被遮挡也能工作，RDP 会话适用。
param(
    [string]$PeerName = '对方',        # 对方显示名（仅用于输出行标签，不参与定位）
    [int]$MaxPages = 3,                # 最多向上翻几页（含底部当前页）
    [int]$UntilDaysAgo = 0,            # >0 时：某页最早时间分割线早于该天数则停止继续上翻
    [string]$OutFile = '',             # 输出文本路径，默认 %TEMP%\weixin-chat-<peer>-<时间戳>.txt
    [int]$PageScrollNotches = 8        # 每翻一页的滚轮格数（120/格）
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$probeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $probeDir '..\p1-chat-search-group\probe-lib.ps1')

# venv 里的 python（rapidocr_onnxruntime 装在其中）；probe 在 clients/weixin-cli/experiments/probes/p4 下，仓库根在上五级
$repoRoot = (Resolve-Path (Join-Path $probeDir '..\..\..\..\..')).Path
$pythonExe = Join-Path $repoRoot 'venv\Scripts\python.exe'
$ocrScript = Join-Path $probeDir 'ocr_chat.py'
if (-not (Test-Path $pythonExe)) { throw "PYTHON_NOT_FOUND: $pythonExe" }
if ($OutFile -eq '') { $OutFile = Join-Path $env:TEMP ("weixin-chat-{0}-{1:yyyyMMdd-HHmmss}.txt" -f $PeerName, (Get-Date)) }

function Invoke-OcrPage([string]$ImgPath) {
    $out = & $pythonExe $ocrScript $ImgPath $PeerName 2>$null
    @($out | Where-Object { $_ -and $_.Trim() -ne '' })
}

function Get-PageFirstDate([string[]]$Lines) {
    # 取本页第一条 [时间] 行并解析为日期；无年份的按月日推断（月份晚于当前月则视为去年）
    foreach ($ln in $Lines) {
        if ($ln -notmatch '^\[时间\]') { continue }
        $m = [regex]::Match($ln, '(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日')
        if (-not $m.Success) { continue }
        $year = if ($m.Groups[1].Success) { [int]$m.Groups[1].Value } else {
            $now = Get-Date
            if ([int]$m.Groups[2].Value -gt $now.Month) { $now.Year - 1 } else { $now.Year }
        }
        return Get-Date -Year $year -Month ([int]$m.Groups[2].Value) -Day ([int]$m.Groups[3].Value) -Hour 0 -Minute 0 -Second 0
    }
    return $null
}

function Send-WeixinWheel([int64]$Hwnd, [int]$Delta, [int]$Count, [int]$GapMs = 40) {
    # WM_MOUSEWHEEL：wParam 高字=delta（正=上滚看历史，负=下滚），lParam=屏幕坐标
    $sx = $script:WheelScreenX; $sy = $script:WheelScreenY
    $lp = [IntPtr](($sy -shl 16) -bor ($sx -band 0xFFFF))
    foreach ($i in 1..$Count) {
        [void][WeixinProbeWin32]::PostMessage([IntPtr]$Hwnd, 0x020A, [IntPtr]($Delta -shl 16), $lp)
        Start-Sleep -Milliseconds $GapMs
    }
}

$mutex = New-Object Threading.Mutex($false, 'Local\AidWorkAgent.WeixinCliProbe.HistoryCapture', [ref]$null)
if (-not $mutex.WaitOne(0)) { throw 'PROBE_BUSY' }
try {
    Initialize-WeixinProbeWin32
    Set-WeixinProbeDpiContext
    Add-Type -AssemblyName System.Drawing

    $windows = @(Get-VisibleWindowList)
    $main = Select-WeixinMainWindow $windows
    $mainHwnd = [int64]$main.Hwnd
    Write-Host "[1] main hwnd=$mainHwnd （目标=当前打开的会话，请确认）"

    # 滚轮落点：聊天区中心
    $rect = New-Object WeixinProbeWin32+RECT
    [void][WeixinProbeWin32]::GetWindowRect([IntPtr]$mainHwnd, [ref]$rect)
    $script:WheelScreenX = $rect.Left + [int](($rect.Right - $rect.Left) * 0.63)
    $script:WheelScreenY = $rect.Top + [int](($rect.Bottom - $rect.Top) * 0.55)

    # 1. 先滚到底（也是结束后的恢复位置）
    Send-WeixinWheel $mainHwnd -120 40
    Start-Sleep -Milliseconds 800
    Write-Host "[2] 已滚到底部"

    # 2. 自底向上逐页截图 + OCR
    $pages = New-Object Collections.Generic.List[object]   # 每个元素 = string[] 行
    for ($page = 1; $page -le $MaxPages; $page++) {
        $shotPath = Join-Path $env:TEMP "weixin-probe-p4-page$page.png"
        $snap = Get-WeixinWindowSnapshot $mainHwnd $shotPath
        $lines = @(Invoke-OcrPage $shotPath)
        Write-Host "[3] page$page : $($lines.Count) 行 (printwindow=$($snap[4]))"
        if ($lines.Count -eq 0) { Write-Host "[3] 本页无内容，停止"; break }
        $pages.Add([string[]]$lines)
        if ($UntilDaysAgo -gt 0) {
            $firstDate = Get-PageFirstDate $lines
            if ($null -ne $firstDate) {
                $age = ((Get-Date).Date - $firstDate).Days
                Write-Host "[3] 本页最早时间线: $($firstDate.ToString('yyyy-MM-dd')) (${age}天前)"
                if ($age -gt $UntilDaysAgo) { Write-Host "[3] 已超出 $UntilDaysAgo 天范围，停止上翻"; break }
            }
        }
        if ($page -lt $MaxPages) {
            Send-WeixinWheel $mainHwnd 120 $PageScrollNotches
            Start-Sleep -Milliseconds 800
        }
    }

    # 3. 合并：页按新->旧采集（pages[0]=最新的底部页），逐页把更旧的页拼到前面，
    #    按"旧页后缀 == 已合并前缀"的最大重叠去重
    $merged = New-Object 'Collections.Generic.List[string]'
    $merged.AddRange([string[]]($pages[0]))
    for ($i = 1; $i -lt $pages.Count; $i++) {
        $older = [string[]]($pages[$i])
        $maxOverlap = [Math]::Min($older.Count, $merged.Count)
        $overlap = 0
        for ($k = $maxOverlap; $k -ge 1; $k--) {
            $match = $true
            for ($j = 0; $j -lt $k; $j++) {
                if ($older[$older.Count - $k + $j] -ne $merged[$j]) { $match = $false; break }
            }
            if ($match) { $overlap = $k; break }
        }
        $prependCount = $older.Count - $overlap
        if ($prependCount -gt 0) {
            $prepend = $older[0..($prependCount - 1)]
            $merged.InsertRange(0, [string[]]$prepend)
        }
        Write-Host "[4] page$($i+1) 重叠 $overlap 行，前插 $prependCount 行"
    }

    # 4. 存文本
    [IO.File]::WriteAllLines($OutFile, [string[]]$merged, [Text.Encoding]::UTF8)
    Write-Host "[5] 已保存 $($merged.Count) 行 -> $OutFile"
    Write-Host "PROBE_RESULT: CAPTURED pages=$($pages.Count) lines=$($merged.Count) file=$OutFile"
} finally {
    # 恢复：滚回底部
    if ($null -ne $mainHwnd -and $mainHwnd -ne 0) {
        try { Send-WeixinWheel $mainHwnd -120 40 } catch {}
    }
    [void]$mutex.ReleaseMutex(); $mutex.Dispose()
}
