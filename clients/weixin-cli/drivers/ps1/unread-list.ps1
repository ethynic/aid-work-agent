# drivers/ps1/unread-list.ps1 — weixin_unread_list 驱动（只读，不开会话不清未读）
# 从 UI Automation 读取原生文本与计数，并在本机核对候选行的红色角标。
# 注意：本文件含中文，必须以 UTF-8 with BOM 保存。
param()
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$driverDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $driverDir '_common.ps1')

function ConvertFrom-WeixinUnreadSessionName([string]$AccessibleName) {
    $lines = $AccessibleName -split '\r\n|\n|\r'
    if ($lines.Count -lt 2 -or [string]::IsNullOrWhiteSpace($lines[0])) { return }

    # 微信只在会话名后的首行开头提供 [n条] 角标，后续预览正文不作计数依据。
    $badge = [regex]::Match($lines[1], '^\[(?<count>[1-9][0-9]*)条\](?:[ \t]+(?<preview>.*))?$')
    if (-not $badge.Success) { return }
    $count = 0
    if (-not [int]::TryParse($badge.Groups['count'].Value, [ref]$count)) { return }
    $preview = $badge.Groups['preview'].Value
    # 独立角标后的预览与时间各有一行。无预览时不把时间当成消息。
    if ([string]::IsNullOrWhiteSpace($preview) -and $lines.Count -gt 4) { $preview = $lines[2] }
    return @{ name = $lines[0]; preview = $preview; unread_count = $count }
}

function Read-WeixinUnreadSessions($Lists, [int]$WeixinProcessId) {
    $sessionLists = @(foreach ($list in $Lists) {
        $current = $list.Current
        if ($current.ProcessId -eq $WeixinProcessId -and -not $current.IsOffscreen -and
            $current.ClassName -ceq 'mmui::XTableView' -and $current.ControlType -eq [Windows.Automation.ControlType]::List) {
            $list
        }
    })
    if ($sessionLists.Count -ne 1) {
        Throw-DriverError 'UI_CHANGED' '无法读取当前微信会话列表，请先打开会话列表并确认当前微信版本支持原生读取'
    }

    $rows = $sessionLists[0].FindAll([Windows.Automation.TreeScope]::Children, [Windows.Automation.Condition]::TrueCondition)
    foreach ($row in $rows) {
        $current = $row.Current
        if ($current.ProcessId -eq $WeixinProcessId -and -not $current.IsOffscreen -and
            $current.ClassName -ceq 'mmui::ChatSessionCell') {
            $entry = ConvertFrom-WeixinUnreadSessionName $current.Name
            if ($entry -and $current.AutomationId -ceq ('session_item_' + $entry.name)) {
                @{ entry = $entry; bounds = $current.BoundingRectangle }
            }
        }
    }
}

function Select-WeixinUnreadSessionsWithBadge($Candidates, [Drawing.Bitmap]$Bitmap, $WindowRect, [double]$Scale) {
    foreach ($candidate in $Candidates) {
        $left = [int]($candidate.bounds.Left - $WindowRect.Left)
        $top = [int]($candidate.bounds.Top - $WindowRect.Top)
        # 微信 4.1.15.13 的红标上沿在头像上方，避免将头像本身的红色当作未读。
        $x0 = $left + [int][Math]::Ceiling(44 * $Scale)
        $x1 = $left + [int][Math]::Floor(54 * $Scale)
        $y0 = $top + [int][Math]::Ceiling(7 * $Scale)
        $y1 = $top + [int][Math]::Floor(13 * $Scale)
        if ($x0 -lt 0 -or $y0 -lt 0 -or $x1 -ge $Bitmap.Width -or $y1 -ge $Bitmap.Height) { continue }
        $redPixels = 0
        for ($y = $y0; $y -le $y1; $y++) {
            for ($x = $x0; $x -le $x1; $x++) {
                $color = $Bitmap.GetPixel($x, $y)
                if ($color.R -eq 250 -and $color.G -eq 81 -and $color.B -eq 81) { $redPixels++ }
            }
        }
        if ($redPixels -ge 5 * $Scale * $Scale) { $candidate.entry }
    }
}

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WeixinCli.UnreadList' -Body {
    $mainHwnd = Resolve-WeixinMainWindowHwnd
    $identity = Get-WindowIdentityByHwnd $mainHwnd
    if ($null -eq $identity) { Throw-DriverError 'UI_CHANGED' '微信主窗口已变化' }
    $rect = New-Object WeixinProbeWin32+RECT
    $dpi = [WeixinProbeWin32]::GetDpiForWindow([IntPtr]$mainHwnd)
    if ($dpi -eq 0 -or -not [WeixinProbeWin32]::GetWindowRect([IntPtr]$mainHwnd, [ref]$rect)) {
        Throw-DriverError 'UI_CHANGED' '无法读取微信窗口位置'
    }
    $root = [Windows.Automation.AutomationElement]::FromHandle([IntPtr]$mainHwnd)
    if ($null -eq $root) { Throw-DriverError 'UI_CHANGED' '无法读取微信主窗口' }
    $condition = [Windows.Automation.PropertyCondition]::new(
        [Windows.Automation.AutomationElement]::AutomationIdProperty, 'session_list'
    )
    $lists = $root.FindAll([Windows.Automation.TreeScope]::Descendants, $condition)
    $candidates = @(Read-WeixinUnreadSessions -Lists $lists -WeixinProcessId $identity.ProcessId)
    if (-not $candidates.Count) { return @{ unread = @() } }

    $stream = $null
    $bitmap = $null
    try {
        try {
            $snapshot = Get-WeixinWindowSnapshot -Hwnd $mainHwnd
            $stream = [IO.MemoryStream]::new($snapshot.Image)
            $bitmap = [Drawing.Bitmap]::new($stream)
        } catch {
            Throw-DriverError 'UI_CHANGED' '无法核对微信未读角标'
        }
        if (-not $rect.Equals($snapshot.WindowRect)) { Throw-DriverError 'UI_CHANGED' '微信窗口位置已变化' }
        return @{ unread = @(Select-WeixinUnreadSessionsWithBadge $candidates $bitmap $rect ($dpi / 96.0)) }
    } finally {
        if ($bitmap) { $bitmap.Dispose() }
        if ($stream) { $stream.Dispose() }
    }
}
