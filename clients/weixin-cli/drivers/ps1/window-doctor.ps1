param([switch]$RestoreWindow)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
. (Join-Path $PSScriptRoot '_common.ps1')
. (Join-Path $PSScriptRoot 'native-messages.ps1')

function Get-WeixinDiagnosticWindows {
    $matches = @()
    $sessionId = (Get-Process -Id $PID).SessionId
    $processIds = @(Get-Process -Name Weixin -ErrorAction SilentlyContinue | Where-Object {$_.SessionId -eq $sessionId} | ForEach-Object {[uint32]$_.Id})
    if ($processIds.Count -eq 0) { return @() }
    foreach ($window in @(Get-VisibleWindowList -IncludeHidden -ProcessIds $processIds)) {
        if (-not (Test-WeixinExecutablePath $window.ProcessPath)) { continue }
        $identity = Get-WindowIdentityByHwnd $window.Hwnd
        if ((Test-WeixinMainIdentity $identity $window.Hwnd) -and -not (Test-WeixinWhatsNewWindow $identity)) {
            $matches += $identity
        }
    }
    return @($matches | Sort-Object Hwnd -Unique)
}

function Get-WeixinDiagnosticState($Windows) {
    if ($Windows.Count -eq 0) { return @{window_state='no_main_window';login_state='unknown';restored=$false} }
    if ($Windows.Count -ne 1) { return @{window_state='ambiguous';login_state='unknown';restored=$false} }
    $identity = $Windows[0]
    $handle = [IntPtr]$identity.Hwnd
    $state = if (-not [WeixinProbeWin32]::IsWindowVisible($handle)) { 'hidden' }
        elseif ([WeixinProbeWin32]::IsIconic($handle)) { 'minimized' }
        elseif ([WeixinProbeWin32]::GetForegroundWindow() -eq $handle) { 'foreground' } else { 'background' }
    $login = 'unknown'
    try {
        $session = @{Handle=$identity.Hwnd;ProcessId=$identity.ProcessId}
        $null = Get-WeixinNativeList $session 'session_list' 'mmui::XTableView'
        $login = 'online'
    } catch {}
    return @{window_state=$state;login_state=$login;restored=$false}
}

function Assert-WeixinRestoreIdentity($Expected) {
    $windows = @(Get-WeixinDiagnosticWindows)
    if ($windows.Count -ne 1 -or $windows[0].Hwnd -ne $Expected.Hwnd -or
        $windows[0].ProcessId -ne $Expected.ProcessId) {
        Throw-DriverError 'UI_CHANGED' '微信主窗口身份已变化，请手动恢复后重试'
    }
}

function Get-WeixinTrayShellRoots {
    $sessionId = (Get-Process -Id $PID).SessionId
    $processIds = @(Get-Process -Name explorer -ErrorAction SilentlyContinue | Where-Object {$_.SessionId -eq $sessionId} | ForEach-Object {[uint32]$_.Id})
    if ($processIds.Count -eq 0) { return }
    foreach ($window in @(Get-VisibleWindowList -ProcessIds $processIds)) {
        $identity = Get-WindowIdentityByHwnd $window.Hwnd
        if (-not $identity -or $identity.ClassName -cnotin @('Shell_TrayWnd','Shell_SecondaryTrayWnd',
            'TopLevelWindowForOverflowXamlIsland','NotifyIconOverflowWindow')) { continue }
        if (-not [string]::Equals($identity.ProcessPath,(Join-Path $env:WINDIR 'explorer.exe'),[StringComparison]::OrdinalIgnoreCase)) { continue }
        if ((Get-Process -Id $identity.ProcessId -ErrorAction Stop).SessionId -ne $sessionId) { continue }
        $root = [Windows.Automation.AutomationElement]::FromHandle([IntPtr]$identity.Hwnd)
        if ($null -ne $root) { @{Root=$root;ProcessId=$identity.ProcessId} }
    }
}

function Find-WeixinTrayButtons($Roots, [switch]$Expander) {
    $found = @()
    foreach ($shell in $Roots) {
        $condition = [Windows.Automation.PropertyCondition]::new(
            [Windows.Automation.AutomationElement]::ControlTypeProperty,[Windows.Automation.ControlType]::Button)
        foreach ($node in $shell.Root.FindAll([Windows.Automation.TreeScope]::Descendants,$condition)) {
            $item = $node.Current
            if ($item.ProcessId -ne $shell.ProcessId -or $item.IsOffscreen -or
                $item.ClassName -cne 'SystemTray.NormalButton') { continue }
            $match = if ($Expander) {
                $item.AutomationId -ceq 'SystemTrayIcon' -and $item.Name -cin @('显示隐藏的图标','Show hidden icons')
            } else { $item.AutomationId -ceq 'NotifyItemIcon' -and $item.Name -cin @('微信','WeChat') }
            if (-not $match) { continue }
            $invoke = $null
            if ($node.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern,[ref]$invoke)) {
                $found += @{Node=$node;Pattern=$invoke;ProcessId=$shell.ProcessId}
            }
        }
    }
    return $found
}

function Invoke-WeixinTrayRestore($Expected) {
    Assert-WeixinRestoreIdentity $Expected
    if ([WeixinProbeWin32]::IsWindowVisible([IntPtr]$Expected.Hwnd)) { return }
    $icons = @(Find-WeixinTrayButtons @(Get-WeixinTrayShellRoots))
    if ($icons.Count -gt 1) { Throw-DriverError 'WINDOW_AMBIGUOUS' '存在多个微信通知区图标，请手动恢复目标微信' }
    if ($icons.Count -eq 0) {
        $expanders = @(Find-WeixinTrayButtons @(Get-WeixinTrayShellRoots) -Expander)
        if ($expanders.Count -ne 1) { Throw-DriverError 'CONTENT_UNAVAILABLE' '无法唯一确认通知区展开按钮，请手动点击微信托盘图标' }
        Assert-WeixinRestoreIdentity $Expected
        if ([WeixinProbeWin32]::IsWindowVisible([IntPtr]$Expected.Hwnd)) { return }
        $expanders[0].Pattern.Invoke()
        Start-Sleep -Milliseconds 400
        $icons = @(Find-WeixinTrayButtons @(Get-WeixinTrayShellRoots))
    }
    if ($icons.Count -ne 1) { Throw-DriverError 'CONTENT_UNAVAILABLE' '无法唯一确认微信通知区图标，请手动恢复微信' }
    Assert-WeixinRestoreIdentity $Expected
    if ([WeixinProbeWin32]::IsWindowVisible([IntPtr]$Expected.Hwnd)) { return }
    $item = $icons[0].Node.Current
    if ($item.IsOffscreen -or $item.ProcessId -ne $icons[0].ProcessId -or
        $item.ClassName -cne 'SystemTray.NormalButton' -or $item.AutomationId -cne 'NotifyItemIcon' -or
        $item.Name -cnotin @('微信','WeChat')) { Throw-DriverError 'UI_CHANGED' '微信通知区图标已变化' }
    $icons[0].Pattern.Invoke()
    Start-Sleep -Milliseconds 700
}

function Invoke-WeixinWindowDoctor([switch]$Restore) {
    $windows = @(Get-WeixinDiagnosticWindows)
    $state = Get-WeixinDiagnosticState $windows
    if (-not $Restore) { return $state }
    try {
        if ($windows.Count -eq 0) { Throw-DriverError 'WEIXIN_NOT_FOUND' '未找到已核对微信主窗口，请手动打开微信' }
        if ($windows.Count -gt 1) { Throw-DriverError 'WINDOW_AMBIGUOUS' '未能唯一核对微信主窗口，请手动打开目标微信' }
        $expected = $windows[0]
        if ($state.window_state -ceq 'hidden') { Invoke-WeixinTrayRestore $expected }
        Assert-WeixinRestoreIdentity $expected
        if (-not [WeixinProbeWin32]::IsWindowVisible([IntPtr]$expected.Hwnd) -and $state.window_state -ceq 'hidden') {
            Throw-DriverError 'UI_CHANGED' '托盘操作后微信原主窗口仍未显示，请手动恢复微信'
        }
        if (-not (Invoke-WeixinActivation -Hwnd $expected.Hwnd -PreservePlacement)) {
            Throw-DriverError 'FOREGROUND_LOST' '无法将微信原主窗口置于前台'
        }
        Assert-WeixinRestoreIdentity $expected
        $result = Get-WeixinDiagnosticState @(Get-WeixinDiagnosticWindows)
        if ($result.window_state -cne 'foreground' -or $result.login_state -cne 'online') {
            Throw-DriverError 'CONTENT_UNAVAILABLE' '原主窗口已显示，但无法核实已登录会话列表，请手动确认微信'
        }
        $result.restored = $true
        return $result
    } catch {
        $raw = [string]$_.Exception.Message
        $state = Get-WeixinDiagnosticState @(Get-WeixinDiagnosticWindows)
        $state.error_code = if ($raw -match '^WXDRIVE\|([A-Z_]+)\|') { $Matches[1] } else { 'INTERNAL_ERROR' }
        return $state
    }
}

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WeixinCli.NativeObserve' -Body {
    Invoke-WeixinWindowDoctor -Restore:$RestoreWindow
}
