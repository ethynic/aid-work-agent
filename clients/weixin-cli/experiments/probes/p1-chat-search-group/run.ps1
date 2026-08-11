# p1-chat-search-group probe 主脚本
# 流程：找主窗口 → 激活 → Ctrl+F → 逐字输入查询 → UIA 识别群聊匹配项 → 点击进入会话 → 验证终态
param(
    [Parameter(Mandatory)][string]$Query,  # 运行时传入真实查询词（明文真实联系人姓名不入库）
    [int]$TypeDelayMs = 350,
    [int]$ResultWaitMs = 900,
    [switch]$ScreenshotOnly,  # 输入完成后截图并停止，不识别不点击
    [int]$ClickX = -1,        # 物理屏幕坐标（由截图分析得出），-1 = 不点击
    [int]$ClickY = -1,
    [switch]$DryRun   # 只到识别候选为止，不点击
)

$ErrorActionPreference = 'Stop'
$probeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $probeDir 'probe-lib.ps1')

$mutex = New-Object Threading.Mutex($false, 'Local\AidWorkAgent.WeixinCliProbe.ChatSearchGroup', [ref]$null)
if (-not $mutex.WaitOne(0)) { throw 'PROBE_BUSY' }
try {
    Initialize-WeixinProbeWin32
    Set-WeixinProbeDpiContext

    # 1. 定位并激活主窗口
    $windows = @(Get-VisibleWindowList)
    $main = Select-WeixinMainWindow $windows
    $mainHwnd = [int64]$main.Hwnd
    Write-Host "[1] 主窗口 hwnd=$mainHwnd"
    if (-not (Invoke-WeixinActivation $mainHwnd)) { throw 'WX_ACTIVATION_FAILED' }
    $mainIdentity = Get-WindowIdentityByHwnd $mainHwnd
    if (-not (Test-WeixinMainIdentity $mainIdentity $mainHwnd)) { throw 'MAIN_WINDOW_UNTRUSTED' }
    if ([WeixinProbeWin32]::GetForegroundWindow().ToInt64() -ne $mainHwnd) { throw 'MAIN_NOT_FOREGROUND' }
    $mainGuard = { [WeixinProbeWin32]::GetForegroundWindow().ToInt64() -eq $mainHwnd }
    Write-Host "[1] 激活并验证前台身份 OK（$($mainIdentity.ClassName) / $($mainIdentity.Title)）"

    # 2. Ctrl+F 打开搜索（用户确认：打开后搜索框必然聚焦，直接输入）
    Send-WeixinKeyChord @('CTRL', 'F') $mainGuard
    Start-Sleep -Milliseconds 500
    if (-not (& $mainGuard)) { throw 'FOREGROUND_LOST' }
    Write-Host "[2] Ctrl+F 已发送"

    # 2a. 清空可能残留的查询文本（上次实验未关面板时搜索框会保留旧文本）
    Send-WeixinKeyChord @('CTRL', 'A') $mainGuard
    Start-Sleep -Milliseconds 120
    Send-WeixinKeyChord @('DEL') $mainGuard
    Start-Sleep -Milliseconds 200
    Write-Host "[2a] 搜索框已清空"

    # 3. 逐字输入（Qt 搜索框不收 KEYEVENTF_UNICODE，改用剪贴板逐字粘贴；结束后恢复原剪贴板）
    $script:originalClipboard = Get-ClipboardTextSafe
    foreach ($ch in $Query.ToCharArray()) {
        Send-WeixinPasteText ([string]$ch) $mainGuard
        Write-Host "[3] 已输入字符: $ch"
        Start-Sleep -Milliseconds $TypeDelayMs
    }
    Start-Sleep -Milliseconds $ResultWaitMs
    if (-not (& $mainGuard)) { throw 'FOREGROUND_LOST' }

    # 4. UIA 导出 + 全量 dump 落本地 TEMP（不进仓库）
    $dump = @(Get-WeixinUiaDump $mainHwnd)
    $tempPath = Join-Path $env:TEMP 'weixin-probe-p1-uia.json'
    $dump | ConvertTo-Json -Depth 4 | Set-Content -Path $tempPath -Encoding UTF8
    Write-Host "[4] UIA 节点数=$($dump.Count)，全量 dump: $tempPath"

    if ($ScreenshotOnly) {
        # 4a. 枚举所有可见顶层窗口（确认搜索面板是否独立 HWND）
        Write-Host "[4a] 当前可见顶层窗口："
        foreach ($w in @(Get-VisibleWindowList)) {
            $id = Get-WindowIdentityByHwnd ([int64]$w.Hwnd)
            if ($null -eq $id) { continue }
            Write-Host ("    hwnd={0} class={1} title={2} proc={3}" -f $id.Hwnd, $id.ClassName, $id.Title, [IO.Path]::GetFileName([string]$id.ProcessPath))
        }
        # 4b. 截取主窗口区域（物理坐标，Per-Monitor V2 已固定）
        $rect = New-Object WeixinProbeWin32+RECT
        if (-not [WeixinProbeWin32]::GetWindowRect([IntPtr]$mainHwnd, [ref]$rect)) { throw 'WINDOW_RECT_FAILED' }
        $w = $rect.Right - $rect.Left; $h = $rect.Bottom - $rect.Top
        $bmp = New-Object Drawing.Bitmap $w, $h
        $g = [Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($rect.Left, $rect.Top, 0, 0, (New-Object Drawing.Size $w, $h))
        $shotPath = Join-Path $env:TEMP 'weixin-probe-p1-search.png'
        $bmp.Save($shotPath, [Drawing.Imaging.ImageFormat]::Png)
        $g.Dispose(); $bmp.Dispose()
        Write-Host "[4b] 窗口 rect=($($rect.Left),$($rect.Top),$($rect.Right),$($rect.Bottom)) ${w}x${h}"
        Write-Host "[4b] 截图: $shotPath"
        return
    }

    if ($ClickX -ge 0 -and $ClickY -ge 0) {
        # 5c. 物理坐标点击（截图分析得出的目标中心点），带前台守卫的 down/up 防撕裂
        if (-not (& $mainGuard)) { throw 'FOREGROUND_LOST' }
        [void][WeixinProbeWin32]::SetCursorPos($ClickX, $ClickY)
        Start-Sleep -Milliseconds 120
        if (-not (& $mainGuard)) { throw 'FOREGROUND_LOST' }
        $downFailure = $null
        $downDone = $false
        try {
            [WeixinProbeWin32]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)  # LEFTDOWN
            $downDone = $true
            if (-not (& $mainGuard)) { throw 'FOREGROUND_LOST' }
        } catch { $downFailure = $_ } finally {
            if ($downDone) { [WeixinProbeWin32]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero) }  # LEFTUP
        }
        if ($downFailure) { throw $downFailure }
        Write-Host "[5c] 已点击 ($ClickX, $ClickY)"
        Start-Sleep -Milliseconds 900
        if (-not (& $mainGuard)) { throw 'FOREGROUND_LOST' }

        # 5d. 点击后验证：搜索面板窗口消失 + 截图
        $overlayGone = $true
        foreach ($w in @(Get-VisibleWindowList)) {
            $id = Get-WindowIdentityByHwnd ([int64]$w.Hwnd)
            if ($null -ne $id -and [string]$id.ClassName -eq 'Qt51514QWindowToolSaveBits' -and
                (Test-WeixinExecutablePath ([string]$id.ProcessPath))) { $overlayGone = $false }
        }
        $rect2 = New-Object WeixinProbeWin32+RECT
        [void][WeixinProbeWin32]::GetWindowRect([IntPtr]$mainHwnd, [ref]$rect2)
        $w2 = $rect2.Right - $rect2.Left; $h2 = $rect2.Bottom - $rect2.Top
        $bmp2 = New-Object Drawing.Bitmap $w2, $h2
        $g2 = [Drawing.Graphics]::FromImage($bmp2)
        $g2.CopyFromScreen($rect2.Left, $rect2.Top, 0, 0, (New-Object Drawing.Size $w2, $h2))
        $afterShot = Join-Path $env:TEMP 'weixin-probe-p1-after-click.png'
        $bmp2.Save($afterShot, [Drawing.Imaging.ImageFormat]::Png)
        $g2.Dispose(); $bmp2.Dispose()
        Write-Host "[5d] 搜索面板已关闭: $overlayGone；点击后截图: $afterShot"
        if ($overlayGone) { Write-Host "PROBE_RESULT: CLICK_DONE" } else { Write-Host "PROBE_RESULT: OVERLAY_STILL_OPEN" }
        return
    }

    # 5. 识别“群聊”分类与候选
    $groupHeaders = @($dump | Where-Object { $_.name -eq '群聊' })
    Write-Host "[5] '群聊' 分类头数量=$($groupHeaders.Count)"
    $candidates = @()
    if ($groupHeaders.Count -ge 1) {
        $header = $groupHeaders[0]
        # 分类头下方、下一个分类头之前的可点击项
        $nextHeaders = @($dump | Where-Object {
            $_.control_type -eq $header.control_type -and
            $_.top -gt $header.top -and
            $_.name -match '^(联系人|聊天记录|公众号|小程序|收藏|表情)$'
        } | Sort-Object top)
        $sectionBottom = if ($nextHeaders.Count -gt 0) { [double]$nextHeaders[0].top } else { [double]::PositiveInfinity }
        $candidates = @($dump | Where-Object {
            $_.top -gt $header.top -and $_.top -lt $sectionBottom -and
            $_.has_invoke -and -not $_.is_offscreen -and
            $_.control_type -in @('Button', 'ListItem') -and
            $_.name -and $_.name.IndexOf($Query, [StringComparison]::Ordinal) -ge 0
        } | Sort-Object top, left)
    }
    # 兜底：不限分类，任何可点击且名字含查询词的项（标记 section=unknown）
    if ($candidates.Count -eq 0) {
        Write-Host "[5] 群聊分类内未找到含 '$Query' 的可点击项，尝试全局兜底匹配"
        $candidates = @($dump | Where-Object {
            $_.has_invoke -and -not $_.is_offscreen -and
            $_.control_type -in @('Button', 'ListItem') -and
            $_.name -and $_.name.IndexOf($Query, [StringComparison]::Ordinal) -ge 0
        } | Sort-Object top, left)
    }
    Write-Host "[5] 候选数=$($candidates.Count)"
    $i = 0
    foreach ($c in $candidates) {
        $i++
        Write-Host ("    [{0}] type={1} name={2} rect=({3},{4},{5}x{6})" -f $i, $c.control_type, $c.name, [int]$c.left, [int]$c.top, [int]$c.width, [int]$c.height)
    }
    if ($candidates.Count -eq 0) { throw 'TARGET_NOT_FOUND' }

    if ($DryRun) {
        Write-Host "[6] DryRun：不点击。实验到候选识别为止。"
        return
    }

    # 6. 点击第一个候选（重新取活元素，Chromium/Qt 元素可能失效）
    $target = $candidates[0]
    $targetNameNormalized = [regex]::Replace(([string]$target.name).Trim(), '\s+', '')
    $root = [Windows.Automation.AutomationElement]::FromHandle([IntPtr]$mainHwnd)
    $liveNodes = $root.FindAll(
        [Windows.Automation.TreeScope]::Descendants,
        [Windows.Automation.Condition]::TrueCondition)
    $liveTarget = $null
    foreach ($node in $liveNodes) {
        try {
            $cur = $node.Current
            if ([string]$cur.ControlType.ProgrammaticName.Replace('ControlType.', '') -ne $target.control_type) { continue }
            if ([regex]::Replace(([string]$cur.Name).Trim(), '\s+', '') -ne $targetNameNormalized) { continue }
            $ip = $null
            if (-not $node.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern, [ref]$ip)) { continue }
            $b = $cur.BoundingRectangle
            if ([math]::Abs([double]$b.Top - [double]$target.top) -gt 20) { continue }
            $liveTarget = @{ Node = $node; Invoke = $ip }
            break
        } catch { continue }
    }
    if ($null -eq $liveTarget) { throw 'TARGET_ELEMENT_STALE' }
    if (-not (& $mainGuard)) { throw 'FOREGROUND_LOST' }
    $liveTarget.Invoke.Invoke()
    Write-Host "[6] 已 Invoke 点击: $($target.name)"
    Start-Sleep -Milliseconds 800

    # 7. 终态验证：搜索面板关闭 + 目标名出现在主区
    $afterDump = @(Get-WeixinUiaDump $mainHwnd)
    $searchEditGone = @($afterDump | Where-Object {
        $_.control_type -eq 'Edit' -and $_.top -lt 200 -and -not $_.is_offscreen
    }).Count -eq 0
    $targetVisible = @($afterDump | Where-Object {
        $_.name -and [regex]::Replace(([string]$_.name).Trim(), '\s+', '') -eq $targetNameNormalized
    }).Count -gt 0
    $afterPath = Join-Path $env:TEMP 'weixin-probe-p1-uia-after-click.json'
    $afterDump | ConvertTo-Json -Depth 4 | Set-Content -Path $afterPath -Encoding UTF8
    Write-Host "[7] 终态: search_panel_closed=$searchEditGone target_name_visible=$targetVisible"
    Write-Host "[7] 点击后 dump: $afterPath"
    if (-not $searchEditGone) { Write-Host "[7] 警告：搜索面板可能仍开着" }
    if ($searchEditGone -and $targetVisible) {
        Write-Host "PROBE_RESULT: SUCCESS"
    } else {
        Write-Host "PROBE_RESULT: INCONCLUSIVE"
    }
} finally {
    # 恢复原剪贴板（仅当原内容是文本时）
    if ($null -ne $script:originalClipboard) {
        try { Set-ClipboardTextRetry $script:originalClipboard } catch {}
    }
    [void]$mutex.ReleaseMutex()
    $mutex.Dispose()
}
