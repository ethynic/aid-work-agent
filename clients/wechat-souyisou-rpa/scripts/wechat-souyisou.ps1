[CmdletBinding()]
param(
    [ValidateSet('probe','open','search','collect')][string]$Command = 'probe',
    [string]$AssociationName,
    [string]$PersonName,
    [string]$InputJson,
    [switch]$ReadStdin,
    [switch]$Execute,
    [ValidateRange(1,10)][int]$Limit = 3,
    [string]$JudgeCommand,
    [switch]$UseProjectLlm,
    [string]$CliExe,
    [string]$OcrCommand,
    [switch]$DisableOcr,
    [switch]$VerifyInputOnly,
    [string]$ArtifactDirectory = (Join-Path $env:LOCALAPPDATA 'AidWorkAgent\wechat-souyisou-rpa\artifacts'),
    [ValidateRange(500,30000)][int]$WaitMilliseconds = 2500,
    [ValidateRange(10000,60000)][int]$SearchReadyTimeoutMilliseconds = 15000
)

$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = [Text.Encoding]::UTF8
. (Join-Path $PSScriptRoot 'wechat-souyisou-lib.ps1')

function Write-Result([hashtable]$Value) {
    Add-Content -Path "$env:TEMP\wechat_diag.log" -Value "[$([DateTimeOffset]::Now.ToString('HH:mm:ss'))] Write-Result: ok=$($Value.ok) status=$($Value.status) error_code=$($Value.error_code) stage=$($Value.stage) checked=$($Value.checked) person=$PersonName assoc=$AssociationName"
    [Console]::Out.WriteLine(($Value | ConvertTo-Json -Depth 12 -Compress))
}
function Fail(
    [string]$Code,
    [string]$Message,
    [AllowNull()][string]$ArtifactRef,
    [AllowNull()][string]$Stage,
    [AllowNull()][string]$ResultStatus
) {
    $result = @{
        ok=$false; executed=[bool]$Execute; error_code=$Code
        message=(Get-RedactedSummary $Message)
        session_closed=[bool]$script:sessionCleanupCompleted
    }
    if ($ArtifactRef) { $result.artifact_ref = $ArtifactRef }
    if ($Stage) { $result.stage = $Stage }
    if ($ResultStatus) { $result.result_status = $ResultStatus }
    Write-Result $result
    exit 1
}

try {
    $stage = 'enum'
    $llmUsages = @()
    $raw = $InputJson
    if ($ReadStdin) { $raw = [Console]::In.ReadToEnd() }
    if ($raw) {
        $inputObject = $raw | ConvertFrom-Json
        if ($inputObject.command) { $Command = [string]$inputObject.command }
        if ($inputObject.association_name) { $AssociationName = [string]$inputObject.association_name }
        if ($inputObject.person_name) { $PersonName = [string]$inputObject.person_name }
        if ($inputObject.verify_input_only -eq $true) { $VerifyInputOnly = $true }
        if ($inputObject.limit) { $Limit = [math]::Min(10, [int]$inputObject.limit) }
        if ($null -ne $inputObject.search_ready_timeout_milliseconds) {
            try {
                $stdinSearchReadyTimeout = [int]$inputObject.search_ready_timeout_milliseconds
            } catch {
                throw 'INVALID_SEARCH_READY_TIMEOUT'
            }
            if (
                $stdinSearchReadyTimeout -lt 10000 -or
                $stdinSearchReadyTimeout -gt 60000
            ) { throw 'INVALID_SEARCH_READY_TIMEOUT' }
            $SearchReadyTimeoutMilliseconds = $stdinSearchReadyTimeout
        }
    }
    if ($Command -notin @('probe','open','search','collect')) { throw 'INVALID_COMMAND' }
    if ($VerifyInputOnly -and (
        -not $Execute -or $Command -notin @('search','collect')
    )) { throw 'INVALID_INPUT_PROBE_MODE' }
    if ($Limit -lt 1 -or $Limit -gt 10) { throw 'INVALID_LIMIT' }
    if (
        $SearchReadyTimeoutMilliseconds -lt 10000 -or
        $SearchReadyTimeoutMilliseconds -gt 60000
    ) { throw 'INVALID_SEARCH_READY_TIMEOUT' }
    if ($Command -eq 'collect') {
        $query = New-SearchQuery $AssociationName $PersonName -ContactSuffix
    } elseif ($Command -eq 'search') {
        $query = New-SearchQuery $AssociationName $PersonName
    }
    if (-not $Execute) {
        Write-Result @{
            ok=$true; executed=$false; mode='dry_run'; command=$Command
            query_present=[bool]$query; limit=$Limit
        }
        exit 0
    }

    # Provider 的正式外层预算为 10 分钟。本进程最多使用 9 分钟执行业务，
    # 余下 1 分钟留给 finally 中既有的完整窗口清理和结果回传。
    $workDeadline = if ($Command -in @('search','collect')) {
        [DateTimeOffset]::UtcNow.AddMinutes(9)
    } else { $null }
    $assertWorkBudget = {
        param([int]$minimumRemainingMilliseconds = 0)
        if ($null -ne $workDeadline) {
            [void](Assert-WeixinWorkBudget `
                $workDeadline $minimumRemainingMilliseconds)
        }
    }

    $createdNew = $false
    $mutex = New-Object Threading.Mutex($false, 'Local\AidWorkAgent.WechatSouyisouRpa', [ref]$createdNew)
    if (-not $mutex.WaitOne(0)) { throw 'RPA_BUSY' }
    try {
        [void](Add-Type -AssemblyName System.Windows.Forms)
        [void](Add-Type -AssemblyName System.Drawing)
        [void](Add-Type -AssemblyName UIAutomationClient)
        [void](Add-Type -AssemblyName UIAutomationTypes)
        [void](Add-Type -AssemblyName WindowsBase)
        [void](Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class WechatSouyisouWin32 {
 public delegate bool EnumWindowsProc(IntPtr h, IntPtr p);
 [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr p);
 [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h,System.Text.StringBuilder b,int n);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h,System.Text.StringBuilder b,int n);
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int c);
 [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
 [DllImport("user32.dll")] public static extern IntPtr SetActiveWindow(IntPtr h);
 [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
 [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a,uint b,bool v);
 [DllImport("user32.dll")] public static extern void keybd_event(byte k,byte s,uint f,IntPtr e);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out RECT r);
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
 [DllImport("user32.dll")] public static extern void mouse_event(uint f,uint x,uint y,uint d,IntPtr e);
 [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr c);
 [DllImport("user32.dll")] public static extern IntPtr GetThreadDpiAwarenessContext();
 [DllImport("user32.dll")] public static extern bool AreDpiAwarenessContextsEqual(IntPtr a,IntPtr b);
 [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr h);
 [DllImport("user32.dll",SetLastError=true)] public static extern IntPtr SendMessageTimeout(IntPtr h,uint m,UIntPtr w,IntPtr l,uint f,uint t,out UIntPtr r);
 public struct RECT { public int Left,Top,Right,Bottom; }
}
"@)
        # UIA 的 BoundingRectangle/ClickablePoint 是物理屏幕坐标。在线程进入任何
        # GetWindowRect、截图或鼠标路径前固定 Per-Monitor V2，避免 150% DPI 下
        # Win32 坐标被虚拟化；双屏负坐标也原样传给 SetCursorPos。
        $perMonitorV2 = [IntPtr](-4)
        $previousDpiContext = [WechatSouyisouWin32]::SetThreadDpiAwarenessContext(
            $perMonitorV2)
        if (
            $previousDpiContext -eq [IntPtr]::Zero -or
            -not [WechatSouyisouWin32]::AreDpiAwarenessContextsEqual(
                [WechatSouyisouWin32]::GetThreadDpiAwarenessContext(),
                $perMonitorV2)
        ) { throw 'DPI_AWARENESS_FAILED' }
        $windows = @()
        [WechatSouyisouWin32]::EnumWindows({
            param($h,$unused)
            if ([WechatSouyisouWin32]::IsWindowVisible($h)) {
                $windowProcessId = [uint32]0
                [void][WechatSouyisouWin32]::GetWindowThreadProcessId($h,[ref]$windowProcessId)
                try {
                    $p = Get-Process -Id $windowProcessId -ErrorAction Stop
                    $script:windows += [pscustomobject]@{ Hwnd=$h.ToInt64(); MainWindowHwnd=$p.MainWindowHandle.ToInt64(); Visible=$true; ProcessPath=$p.Path }
                } catch {}
            }; return $true
        },[IntPtr]::Zero) | Out-Null
        $stage = 'enum'
        try {
            $main = Select-WeixinMainWindow $windows
        } catch {
            # 诊断：枚举到却没匹配上，把疑似微信的进程路径记进 diag，便于排查版本/路径/UAC
            $suspects = @($windows | Where-Object { [string]$_.ProcessPath -match '(?i)weixin|wechat|tencent' } | ForEach-Object { [string]$_.ProcessPath } | Sort-Object -Unique)
            Add-Content -Path "$env:TEMP\wechat_diag.log" -Value "[$([DateTimeOffset]::Now.ToString('HH:mm:ss'))] WX_WINDOW_NOT_FOUND diag: visible_windows=$($windows.Count) wechat_like_suspects=$($suspects -join '|')"
            throw $_
        }
        if ($Command -eq 'probe') {
            Write-Result @{ ok=$true; executed=$true; command='probe'; window_found=$true; hwnd=[int64]$main.Hwnd }
            exit 0
        }
        $getThread = {
            param($h)
            $windowProcessId = [uint32]0
            [WechatSouyisouWin32]::GetWindowThreadProcessId(
                [IntPtr]$h, [ref]$windowProcessId
            )
        }
        $activateWindow = {
            param($targetHwnd)
            Invoke-WeixinActivation ([int64]$targetHwnd) `
                { [WechatSouyisouWin32]::GetCurrentThreadId() } $getThread `
                { [WechatSouyisouWin32]::GetForegroundWindow().ToInt64() } `
                { param($a,$b,$v) [WechatSouyisouWin32]::AttachThreadInput($a,$b,$v) } `
                { param($h) [WechatSouyisouWin32]::ShowWindow([IntPtr]$h,9) } `
                { param($h) [WechatSouyisouWin32]::BringWindowToTop([IntPtr]$h) } `
                { param($h) [WechatSouyisouWin32]::SetActiveWindow([IntPtr]$h) } `
                { param($h) [WechatSouyisouWin32]::SetForegroundWindow([IntPtr]$h) } `
                { param($ms) Start-Sleep -Milliseconds $ms }
        }
        $stage = 'activate'
        $activated = & $activateWindow ([int64]$main.Hwnd)
        if (-not $activated) { throw 'WX_ACTIVATION_FAILED' }
        function Get-WindowIdentityByHwnd([int64]$Hwnd) {
            if (-not [WechatSouyisouWin32]::IsWindow([IntPtr]$Hwnd)) { return $null }
            $currentPid = [uint32]0
            [void][WechatSouyisouWin32]::GetWindowThreadProcessId([IntPtr]$Hwnd,[ref]$currentPid)
            try { $currentProcess = Get-Process -Id $currentPid -ErrorAction Stop } catch { return $null }
            $currentClass = New-Object Text.StringBuilder 256
            $currentTitle = New-Object Text.StringBuilder 256
            [void][WechatSouyisouWin32]::GetClassName([IntPtr]$Hwnd,$currentClass,$currentClass.Capacity)
            [void][WechatSouyisouWin32]::GetWindowText([IntPtr]$Hwnd,$currentTitle,$currentTitle.Capacity)
            [pscustomobject]@{
                Hwnd=$Hwnd; ProcessPath=$currentProcess.Path
                ProcessId=[uint32]$currentPid
                ClassName=$currentClass.ToString(); Title=$currentTitle.ToString()
            }
        }
        function Get-TrustedForegroundIdentity([switch]$RequirePlugin) {
            $currentHwnd = [WechatSouyisouWin32]::GetForegroundWindow()
            if ($currentHwnd -eq [IntPtr]::Zero) { return $null }
            $identity = Get-WindowIdentityByHwnd $currentHwnd.ToInt64()
            if (-not $identity) { return $null }
            if (-not (Test-WeixinForegroundIdentity $identity ([int64]$main.Hwnd))) { return $null }
            if ($RequirePlugin -and [int64]$identity.Hwnd -eq [int64]$main.Hwnd) { return $null }
            $identity
        }
        function Get-CurrentForegroundIdentity {
            $currentHwnd = [WechatSouyisouWin32]::GetForegroundWindow()
            if ($currentHwnd -eq [IntPtr]::Zero) { return $null }
            Get-WindowIdentityByHwnd $currentHwnd.ToInt64()
        }
        # 诊断用：安全获取前台窗口身份摘要，不抛异常，只记录关键字段。
        function Get-CurrentForegroundIdentitySafe {
            try {
                $id = Get-CurrentForegroundIdentity
                if ($null -eq $id) { return @{ hwnd=0; note='null_identity' } }
                return @{
                    hwnd=[int64]$id.Hwnd
                    process=[IO.Path]::GetFileName([string]$id.ProcessPath)
                    class_name=[string]$id.ClassName
                    title=[string]$id.Title
                }
            } catch {
                return @{ hwnd=0; note=$_.Exception.Message }
            }
        }
        function Get-WeixinUiaResultDescriptors(
            [int64]$Hwnd,
            [object]$WindowRect
        ) {
            if (-not (& $pluginGuard)) { throw 'FOREGROUND_LOST' }
            $root = [Windows.Automation.AutomationElement]::FromHandle([IntPtr]$Hwnd)
            if ($null -eq $root) { throw 'UIA_ROOT_UNAVAILABLE' }
            $nodes = $root.FindAll(
                [Windows.Automation.TreeScope]::Descendants,
                [Windows.Automation.Condition]::TrueCondition)
            $descriptors = New-Object Collections.Generic.List[object]
            foreach ($node in $nodes) {
                try {
                    $current = $node.Current
                    $controlType = $current.ControlType.ProgrammaticName.Replace(
                        'ControlType.','')
                    if ($controlType -notin @('Button','ListItem')) { continue }
                    $pattern = $null
                    $supportsInvoke = $node.TryGetCurrentPattern(
                        [Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)
                    if (-not $supportsInvoke) { continue }
                    $clickablePoint = New-Object Windows.Point
                    $hasClickablePoint = $node.TryGetClickablePoint([ref]$clickablePoint)
                    if (-not $hasClickablePoint) { continue }
                    $bounds = $current.BoundingRectangle
                    $descriptors.Add([pscustomobject]@{
                        Name=[string]$current.Name;ControlType=$controlType
                        IsOffscreen=[bool]$current.IsOffscreen
                        SupportsInvoke=$true;HasClickablePoint=$true
                        Left=[double]$bounds.Left;Top=[double]$bounds.Top
                        Width=[double]$bounds.Width;Height=[double]$bounds.Height
                        ClickableX=[double]$clickablePoint.X
                        ClickableY=[double]$clickablePoint.Y
                        Element=$node
                    })
                } catch {
                    # Chromium UIA 节点可能在 FindAll 后失效；忽略单节点，不输出 Name。
                    continue
                }
            }
            if (-not (& $pluginGuard)) { throw 'FOREGROUND_LOST' }
            @(Select-WeixinUiaResultTargets $descriptors `
                $AssociationName $PersonName $WindowRect)
        }
        # 按名字+控件类型重新定位结果卡片的「活」UIA 元素（Chromium 元素会失效，需现取），
        # 供 InvokePattern.Invoke() 触发点击——绕开鼠标 mouse_event 的前台抢占。
        # name 用去空白精确匹配（Select-WeixinUiaResultTargets 存的已是去空白名）。
        function Find-WeixinCardElement([IntPtr]$Hwnd, [string]$Name, [string]$ControlType) {
            if ([string]::IsNullOrWhiteSpace($Name)) { return $null }
            $root = [Windows.Automation.AutomationElement]::FromHandle($Hwnd)
            if ($null -eq $root) { return $null }
            $nodes = $root.FindAll(
                [Windows.Automation.TreeScope]::Descendants,
                [Windows.Automation.Condition]::TrueCondition)
            $want = [regex]::Replace($Name.Trim(), '\s+', '')
            foreach ($node in $nodes) {
                try {
                    $cur = $node.Current
                    if ([string]$cur.ControlType.ProgrammaticName.Replace('ControlType.','') -ne $ControlType) { continue }
                    $ip = $null
                    if (-not $node.TryGetCurrentPattern(
                        [Windows.Automation.InvokePattern]::Pattern, [ref]$ip)) { continue }
                    if ([regex]::Replace(([string]$cur.Name).Trim(), '\s+', '') -eq $want) { return $node }
                } catch { continue }
            }
            $null
        }
        $preexistingPluginHwnds = [Collections.Generic.HashSet[int64]]::new()
        foreach ($visibleWindow in $windows) {
            $visibleIdentity = Get-WindowIdentityByHwnd ([int64]$visibleWindow.Hwnd)
            if (
                $null -ne $visibleIdentity -and
                [int64]$visibleIdentity.Hwnd -ne [int64]$main.Hwnd -and
                (Test-WeixinForegroundIdentity $visibleIdentity ([int64]$main.Hwnd))
            ) {
                [void]$preexistingPluginHwnds.Add([int64]$visibleIdentity.Hwnd)
            }
        }
        if ($Command -eq 'open') {
            $stage = 'open'
            $virtualKeyMap = @{CTRL=0x11;F=0x46;DOWN=0x28;ENTER=0x0D;A=0x41;C=0x43;V=0x56;W=0x57}
            $mainGuard = { [WechatSouyisouWin32]::GetForegroundWindow().ToInt64() -eq [int64]$main.Hwnd }
            $send = { param($ks) Invoke-SafeKeyChord $ks {param($k,$up) [WechatSouyisouWin32]::keybd_event($virtualKeyMap[$k],0, $(if($up){2}else{0}),[IntPtr]::Zero)} {Start-Sleep -Milliseconds 40} $mainGuard }
            & $send @('CTRL','F'); Start-Sleep -Milliseconds 400
            & $send @('DOWN'); Start-Sleep -Milliseconds 250
            & $send @('ENTER'); Start-Sleep -Milliseconds $WaitMilliseconds
            if (-not (Get-TrustedForegroundIdentity -RequirePlugin)) { throw 'SOUYISOU_WINDOW_UNTRUSTED' }
            Write-Result @{ ok=$true; executed=$true; command='open' }
            exit 0
        }
        # search/collect 从主窗口重新打开搜一搜，避免依赖未知页面状态。
        $virtualKeyMap = @{CTRL=0x11;ALT=0x12;TAB=0x09;F=0x46;DOWN=0x28;LEFT=0x25;ENTER=0x0D;A=0x41;C=0x43;V=0x56;W=0x57}
        $mainGuard = { [WechatSouyisouWin32]::GetForegroundWindow().ToInt64() -eq [int64]$main.Hwnd }
        $send = { param($ks,$guard) Invoke-SafeKeyChord $ks {param($k,$up) [WechatSouyisouWin32]::keybd_event($virtualKeyMap[$k],0,$(if($up){2}else{0}),[IntPtr]::Zero)} {Start-Sleep -Milliseconds 40} $guard }
        $openSouyisou = {
            if (-not (& $mainGuard) -and -not (& $activateWindow ([int64]$main.Hwnd))) {
                return
            }
            & $send @('CTRL','F') $mainGuard; Start-Sleep -Milliseconds 400
            & $send @('DOWN') $mainGuard; Start-Sleep -Milliseconds 250
            & $send @('ENTER') $mainGuard; Start-Sleep -Milliseconds $WaitMilliseconds
        }
        $requestCloseWindow = {
            param($targetHwnd)
            $closeResult = [UIntPtr]::Zero
            $sent = [WechatSouyisouWin32]::SendMessageTimeout(
                [IntPtr]$targetHwnd,0x0010,[UIntPtr]::Zero,[IntPtr]::Zero,
                0x0002,2000,[ref]$closeResult)
            $sent -ne [IntPtr]::Zero
        }
        $verifySouyisou = { Get-TrustedForegroundIdentity -RequirePlugin }
                & $assertWorkBudget 90000
        $pluginIdentity = Invoke-LimitedTrustedOpen $openSouyisou $verifySouyisou 1
        $pluginHwnd = [IntPtr]$pluginIdentity.Hwnd
        $windowSession = New-WeixinWindowSession ([int64]$main.Hwnd) `
            @($preexistingPluginHwnds)
        [void](Add-WeixinWindowSessionForeground `
            $windowSession $pluginIdentity 'list' -AllowPreexisting)
        $requiresSessionCleanup = $true
        $sessionCleanupCompleted = $false
        $sessionCleanupAttempted = $false
        $pluginGuard = {
            $identity = Get-TrustedForegroundIdentity -RequirePlugin
            $null -ne $identity -and [int64]$identity.Hwnd -eq $pluginHwnd.ToInt64()
        }
        $cleanupSession = {
            $cleanupResult = Close-WeixinWindowSession $windowSession {
                param($cleanupHwnd)
                Close-WeixinPluginSession `
                    ([int64]$cleanupHwnd) ([int64]$main.Hwnd) `
                    { param($h) Get-WindowIdentityByHwnd $h } `
                    { param($h) [WechatSouyisouWin32]::IsWindow([IntPtr]$h) } `
                    { param($h) [WechatSouyisouWin32]::IsWindowVisible([IntPtr]$h) } `
                    { [WechatSouyisouWin32]::GetForegroundWindow().ToInt64() } `
                    $activateWindow `
                    $requestCloseWindow `
                    { param($ms) Start-Sleep -Milliseconds $ms }
            }
            if (-not $cleanupResult.session_closed) {
                throw ([string]$cleanupResult.error_codes[0])
            }
            $cleanupResult
        }
        $originalClipboard = $null
        $clipboardCaptured = $false
        try {
            $originalClipboard = [Windows.Forms.Clipboard]::GetDataObject()
            $clipboardCaptured = $true
        } catch { throw 'CLIPBOARD_CAPTURE_FAILED' }
        $stage = 'input_verify'
        $inputVerified = $false
        $inputDiagnostics = @{
            readback_matched=$false
        }
        # 新会话由可信主窗口 Ctrl+F/Down/Enter 打开后，焦点即位于搜索输入框。
        # 从此处到回读完成禁止截图、鼠标点击或窗口激活，避免主动抢走焦点。
        & $assertWorkBudget 60000
        # 输入提交封进 scriptblock，便于 readback 失败时重发组合键后再试一次。
        # 改用 UIA ValuePattern.SetValue 直接写搜索框（不依赖前台/焦点/剪贴板），取代旧的
        # 剪贴板 Ctrl+V 粘贴 + Ctrl+C 回读。WriteAndReadback 内部做 SetValue + Value 读回。
        $submitInput = {
            Invoke-VerifiedWeixinUaSearchSubmission $query `
                $pluginGuard `
                { Invoke-WeixinSouyisouSetValueAndReadback ([IntPtr]$pluginHwnd) $query } `
                { & $send @('ENTER') $pluginGuard } `
                (-not $VerifyInputOnly) $inputDiagnostics
        }
        $inputResult = $null
        $inputAttempt = 0
        while ($inputAttempt -lt 2 -and -not $inputResult) {
            $inputAttempt++
            try {
                $inputResult = & $submitInput
            } catch {
                if ($inputAttempt -ge 2 -or
                    $_.Exception.Message -notin @('SEARCH_INPUT_READBACK_MISMATCH','INPUT_FOCUS_LOST')) {
                    throw
                }
                # 焦点未落进搜索框（readback 不匹配）：重发 Ctrl+F/Down/Enter
                # 重新打开搜一搜，焦点会重新进入输入框，再试一次。
                & $assertWorkBudget 60000
                & $openSouyisou
                $reopenIdentity = & $verifySouyisou
                if (-not $reopenIdentity) { throw 'SOUYISOU_WINDOW_UNTRUSTED' }
                $pluginHwnd = [IntPtr]$reopenIdentity.Hwnd
                $inputDiagnostics.readback_matched = $false
            }
        }
        $inputVerified = [bool]$inputResult.input_verified
        if ($VerifyInputOnly) {
            try {
                if ($null -eq $originalClipboard) {
                    [Windows.Forms.Clipboard]::Clear()
                } else {
                    [Windows.Forms.Clipboard]::SetDataObject($originalClipboard, $true)
                }
                $clipboardCaptured = $false
            } catch { throw 'CLIPBOARD_RESTORE_FAILED' }
            $stage = 'cleanup'
            $sessionCleanupAttempted = $true
            $cleanupResult = Complete-WeixinPluginSession `
                ([ref]$sessionCleanupCompleted) $cleanupSession
            Write-Result @{
                ok=$true; executed=$true; input_verified=$true; submitted=$false
                session_closed=[bool]$cleanupResult.session_closed
            }
            exit 0
        }
        $stage = 'search_wait'
        $readyDeadline = [DateTimeOffset]::UtcNow.AddMilliseconds(
            $SearchReadyTimeoutMilliseconds
        )
        $readySamples = 0
        $text = ''
        $html = ''
        Add-Content -Path "$env:TEMP\wechat_diag.log" -Value "[$([DateTimeOffset]::Now.ToString('HH:mm:ss'))] collect START: command=$Command query='$query' person=$PersonName assoc=$AssociationName"
        do {
            & $assertWorkBudget 60000
            Start-Sleep -Milliseconds 500
            if (-not (& $pluginGuard)) { throw 'FOREGROUND_LOST' }
            [Windows.Forms.Clipboard]::Clear()
            & $send @('CTRL','A') $pluginGuard
            & $send @('CTRL','C') $pluginGuard
            Start-Sleep -Milliseconds 200
            $candidateText = [Windows.Forms.Clipboard]::GetText(
                [Windows.Forms.TextDataFormat]::UnicodeText
            )
            if (Test-SearchResultReady $candidateText $query `
                $AssociationName $PersonName $inputVerified) {
                $readySamples++
                $text = $candidateText
                $html = [Windows.Forms.Clipboard]::GetText(
                    [Windows.Forms.TextDataFormat]::Html
                )
            } else {
                $readySamples = 0
            }
        } while (
            $readySamples -lt 2 -and
            [DateTimeOffset]::UtcNow -lt $readyDeadline
        )
        if ($readySamples -lt 2) {
            # 区分两种"列表不就绪"：结果页已正常加载（栏目标记齐全）但人名不在
            # 列表里 → 确定搜过了没有，返回 not_found（上层不重试，避免同一个人
            # 白搜第二遍）；只有页面真没加载出来（无栏目标记）才返回 inconclusive
            # 交由上层重试兜住偶发焦点/加载问题。
            $candidateLines = @($candidateText -split '\r?\n' |
                ForEach-Object { $_.Trim() })
            $navigationMarkers = @('全部','文章','账号','相关搜索')
            $markerMatches = @($navigationMarkers |
                Where-Object { $candidateLines -contains $_ }).Count
            $pageLoaded = $markerMatches -ge 3
            $waitOutcome = if ($pageLoaded) { 'not_found' } else { 'inconclusive' }
            Add-Content -Path "$env:TEMP\wechat_diag.log" -Value ("[{0}] collect search_wait {1}: readySamples={2} last_candidate_len={3} person_in_candidate={4} page_loaded={5} query='{6}' person={7} assoc={8}" -f `
                [DateTimeOffset]::Now.ToString('HH:mm:ss'), $waitOutcome, $readySamples,
                $candidateText.Length, [bool]$candidateText.Contains($PersonName),
                $pageLoaded, $query, $PersonName, $AssociationName)
            $artifact=Protect-EvidenceArtifact $ArtifactDirectory @{
                kind='result_page_unbounded';association_name=$AssociationName
                person_name=$PersonName;text='';links=@();input_verified=[bool]$inputVerified
                captured_at=[DateTimeOffset]::Now.ToString('o')
            }
            $detailArtifact=Protect-EvidenceArtifact $ArtifactDirectory @{
                kind='collect_result';status=$waitOutcome;checked=0;failures=1
                list_artifact_id=$artifact.artifact_id;records=@([pscustomobject]@{
                    stage='list_judge';reason='list_text_unavailable';text_length=0
                    page_loaded=$pageLoaded
                });found_result=$null;captured_at=[DateTimeOffset]::Now.ToString('o')
            }
            $stage='cleanup';$sessionCleanupAttempted=$true
            $cleanupResult=Complete-WeixinPluginSession `
                ([ref]$sessionCleanupCompleted) $cleanupSession
            Write-Result @{
                ok=$true;executed=$true;status=$waitOutcome;checked=0;failures=1
                artifact_ref=$detailArtifact.artifact_ref
                session_closed=[bool]$cleanupResult.session_closed
            }
            exit 0
        }
        $stage = 'copy'
        $links = @(Get-CfHtmlLinks $html)
        $judge = $null
        if ($UseProjectLlm) {
            if ($CliExe) {
                # 打包：cli exe 内含 python + ProxyLLMGateway，不依赖客户机 python
                $judge = New-ExternalJudge $CliExe @('llm-judge')
            } else {
                # dev：python + llm_judge.py
                $judge = New-ExternalJudge (Get-Command python.exe -ErrorAction Stop).Source `
                    @((Join-Path $PSScriptRoot 'llm_judge.py'))
            }
        } elseif ($JudgeCommand) {
            $judge = New-ExternalJudge $JudgeCommand
        }
        $restorePluginForeground = {
            Test-OrRestoreTrustedForeground $pluginGuard `
                { & $activateWindow $pluginHwnd.ToInt64() } `
                { Start-Sleep -Milliseconds 250 }
        }
        $newPluginViewport = {
            param([int64]$Hwnd)
            $boundRect = New-Object WechatSouyisouWin32+RECT
            if (-not [WechatSouyisouWin32]::GetWindowRect(
                [IntPtr]$Hwnd,[ref]$boundRect)) { throw 'WINDOW_RECT_FAILED' }
            $boundWidth = $boundRect.Right - $boundRect.Left
            $boundHeight = $boundRect.Bottom - $boundRect.Top
            if (-not (Test-WindowRectDimensions $boundWidth $boundHeight)) {
                throw 'WINDOW_RECT_FAILED'
            }
            $boundCapture = {
                $bitmap = New-Object Drawing.Bitmap $boundWidth, $boundHeight
                $graphics = [Drawing.Graphics]::FromImage($bitmap)
                try {
                    $graphics.CopyFromScreen(
                        $boundRect.Left,$boundRect.Top,0,0,$bitmap.Size)
                } finally { $graphics.Dispose() }
                $bitmap
            }.GetNewClosure()
            [pscustomobject]@{
                rect=$boundRect;width=$boundWidth;height=$boundHeight;capture=$boundCapture
            }
        }
        $initialViewport = & $newPluginViewport $pluginHwnd.ToInt64()
        $rect=$initialViewport.rect;$width=$initialViewport.width
        $height=$initialViewport.height;$capture=$initialViewport.capture
        # 保留阶段名用于兼容诊断协议；本阶段仅封存无边界列表，不做命中判断。
        $stage = 'list_judge'
        $artifact = Protect-EvidenceArtifact $ArtifactDirectory @{
            kind='result_page_unbounded'; association_name=$AssociationName; person_name=$PersonName
            text=$text; links=$links; input_verified=[bool]$inputVerified
            input_method=if($inputResult){[string]$inputResult.input_method}else{$null}
            captured_at=[DateTimeOffset]::Now.ToString('o')
        }
        if ($Command -eq 'search') {
            $stage = 'cleanup'
            $sessionCleanupAttempted = $true
            $cleanupResult = Complete-WeixinPluginSession `
                ([ref]$sessionCleanupCompleted) $cleanupSession
            Write-Result @{
                ok=$true; executed=$true; status='captured'
                source='result_page_unbounded'; artifact_ref=$artifact.artifact_ref
                link_count=$links.Count; session_closed=[bool]$cleanupResult.session_closed
            }
            exit 0
        }
        # 业务主路径：始终先判断整页列表；有外部 judge 时使用外部严格证据，
        # 否则使用内置同一行姓名-手机号绑定。命中后直接结束；未命中或
        # 不确定才进入最多 10 条详情。列表 artifact 明确标记为 unbounded，
        # 供审计区分“整页列表证据”和“前 10 条详情证据”。
        if ($judge) { & $assertWorkBudget 120000 } else { & $assertWorkBudget }
        $mobileCandidates = @(Get-MobileCandidates $text)
        Add-Content -Path "$env:TEMP\wechat_diag.log" -Value "[$([DateTimeOffset]::Now.ToString('HH:mm:ss'))] list_judge IN: person=$PersonName text_len=$($text.Length) mobile_candidates=$($mobileCandidates.Count) has_judge=$([bool]$judge) assoc=$AssociationName"
        $listJudge = Invoke-EvidenceJudge $text $AssociationName $PersonName $judge
        Add-Content -Path "$env:TEMP\wechat_diag.log" -Value "[$([DateTimeOffset]::Now.ToString('HH:mm:ss'))] list_judge OUT: matched=$($listJudge.matched) inconclusive=$($listJudge.inconclusive) reason=$($listJudge.reason) person=$PersonName"
            if ($listJudge.token_usage) { $llmUsages += $listJudge.token_usage }
            $listJudgeStatus = if ($listJudge.matched) {
                'matched'
            } elseif ($listJudge.inconclusive) {
                'inconclusive'
            } else {
                'not_matched'
            }
            $listJudgeReasonCode = if (
                [string]$listJudge.reason -in @(
                    'no_mobile_candidate',
                    'judge_failed',
                    'judge_schema_rejected',
                    'judge_evidence_rejected',
                    'deterministic_same_clause'
                )
            ) {
                [string]$listJudge.reason
            } else {
                "llm_$listJudgeStatus"
            }
            if ($listJudge.matched) {
                $detailArtifact = Protect-EvidenceArtifact $ArtifactDirectory @{
                    kind='collect_result';status='found';checked=0;failures=0
                    source='result_page_unbounded';list_artifact_id=$artifact.artifact_id
                    list_judge_status=$listJudgeStatus
                    list_judge_reason_code=$listJudgeReasonCode
                    llm_usages=$llmUsages
                    records=@();found_result=$listJudge
                    captured_at=[DateTimeOffset]::Now.ToString('o')
                }
                $stage = 'cleanup'
                $sessionCleanupAttempted = $true
                $cleanupResult = Complete-WeixinPluginSession `
                    ([ref]$sessionCleanupCompleted) $cleanupSession
                Write-Result @{
                    ok=$true;executed=$true;status='found';checked=0;failures=0
                    source='result_page_unbounded'
                    artifact_ref=$detailArtifact.artifact_ref
                    session_closed=[bool]$cleanupResult.session_closed
                }
                exit 0
            }
            # list_judge inconclusive（列表有、LLM 没判出手机号）不在此 exit：
            # 落到下面 Test-ListHasActionableCandidates → 进详情页找手机号，与
            # L545 注释「未匹配或不确定才进入详情」一致。原逻辑在此 exit，导致
            # 「列表有结果却没进详情、还因 inconclusive 被上层当空结果重搜一遍」。
        Add-Content -Path "$env:TEMP\wechat_diag.log" -Value "[$([DateTimeOffset]::Now.ToString('HH:mm:ss'))] collect list: person=$PersonName text_len=$($text.Length) person_in_text=$([bool]$text.Contains($PersonName)) links=$($links.Count) list_judge=$listJudgeStatus query='$query' assoc=$AssociationName"
        if (-not (Test-ListHasActionableCandidates $text $PersonName $links)) {
            $detailArtifact = Protect-EvidenceArtifact $ArtifactDirectory @{
                kind='collect_result';status='inconclusive';checked=0;failures=0
                list_artifact_id=$artifact.artifact_id
                list_judge_status=if($listJudgeStatus){$listJudgeStatus}else{$null}
                list_judge_reason_code='no_actionable_list_candidate'
                llm_usages=$llmUsages
                records=@([pscustomobject]@{
                    stage='list_judge';reason='no_actionable_list_candidate';text_length=0
                })
                found_result=$null;captured_at=[DateTimeOffset]::Now.ToString('o')
            }
            $stage='cleanup';$sessionCleanupAttempted=$true
            $cleanupResult=Complete-WeixinPluginSession `
                ([ref]$sessionCleanupCompleted) $cleanupSession
            Write-Result @{
                ok=$true;executed=$true;status='inconclusive';checked=0;failures=0
                artifact_ref=$detailArtifact.artifact_ref
                session_closed=[bool]$cleanupResult.session_closed
            }
            exit 0
        }
        $ocr = $null
        if (-not $DisableOcr) {
            if ($OcrCommand) {
                $ocr = New-ExternalJudge $OcrCommand
            } else {
                $ocr = New-ExternalJudge (Get-Command python.exe -ErrorAction Stop).Source `
                    @((Join-Path $PSScriptRoot 'ocr_adapter.py'))
            }
        }
        $stage = 'locate'
        $stage = 'points'
        $checked=0; $failures=0; $consecutiveFailures=0; $scrolls=0
        $uiaEnumerationRecoveryAttempts=0
        $recoveryEvidenceUnavailable=$false
        $sessionNaturallyClosed=$false
        $seen=@{}; $seenDetailText=@{}; $records=@(); $foundJudge=$null
        $detailMayBeOpen=$false
        $detailCloseInProgress=$false
        $returnToResultPage = {
            $returnedHwnd = Invoke-WeixinWindowSessionReturnToList `
                $windowSession `
                { Get-CurrentForegroundIdentity } `
                { param($keys) & $send $keys { $true } } `
                { Start-Sleep -Milliseconds $WaitMilliseconds } `
                { param($h) Get-WindowIdentityByHwnd $h } `
                { param($h) [WechatSouyisouWin32]::IsWindow([IntPtr]$h) } `
                $activateWindow
            if ($windowSession.NaturallyClosed) {
                return [pscustomobject]@{
                    hwnd=[int64]0;session_closed=$true;evidence_verified=$false
                }
            }
            $pluginHwnd = [IntPtr]$returnedHwnd
            return [pscustomobject]@{
                hwnd=$returnedHwnd;session_closed=$false;evidence_verified=$true
            }
        }
        while ($checked -lt $Limit -and $scrolls -le 6 -and $consecutiveFailures -lt 2) {
            & $assertWorkBudget 60000
            if (-not (& $pluginGuard)) { throw 'FOREGROUND_LOST' }
            $viewport = & $capture
            try {
                $viewportHash = Get-BitmapSha256 $viewport
                $points = @(Get-WeixinUiaResultDescriptors `
                    $pluginHwnd.ToInt64() $rect)
                Add-Content -Path "$env:TEMP\wechat_diag.log" -Value "[$([DateTimeOffset]::Now.ToString('HH:mm:ss'))] collect detail: points=$($points.Count) checked=$checked scrolls=$scrolls uiaRecovery=$uiaEnumerationRecoveryAttempts person=$PersonName assoc=$AssociationName"
            } finally { $viewport.Dispose() }
            if (-not $points.Count) {
                $stage = 'points'
                if ($uiaEnumerationRecoveryAttempts -lt 1) {
                    $uiaEnumerationRecoveryAttempts++
                    if (-not (& $restorePluginForeground)) { throw 'FOREGROUND_LOST' }
                    Start-Sleep -Milliseconds 500
                    continue
                }
                # UIA 无候选只是有界枚举结束，不是窗口安全故障。
                # 正文和节点 Name 均不写记录。
                $records += New-WeixinUiaCandidateExhaustionRecord $checked
                $consecutiveFailures=2
                break
            }
            $newCount = 0
            foreach ($point in $points) {
                & $assertWorkBudget 60000
                if ($checked -ge $Limit -or $consecutiveFailures -ge 2) { break }
                $pointKey = [string]$point.fingerprint
                if ($seen.ContainsKey($pointKey)) { continue }
                $seen[$pointKey]=$true; $newCount++
                $stage = 'click'
                $clickForegroundRecoveryUsed=$false
                # 诊断埋点：记录点击全过程的窗口变化，用于排查详情打开后前台异常。
                $clickDiagnostics = [Collections.Generic.List[object]]::new()
                $clickDiagnostics.Add([pscustomobject]@{
                    checkpoint='candidate_selected'
                    point_control_type=[string]$point.control_type
                    point_fingerprint=[string]$point.fingerprint
                    point_name=[string]$point.name
                    point_x=[double]$point.x; point_y=[double]$point.y
                    plugin_hwnd=$pluginHwnd.ToInt64()
                    foreground=(Get-CurrentForegroundIdentitySafe)
                })
                $windowDpi = [int][WechatSouyisouWin32]::GetDpiForWindow(
                    $pluginHwnd)
                if ($windowDpi -lt 96 -or $windowDpi -gt 480) {
                    throw 'WINDOW_DPI_INVALID'
                }
                $physicalPoint = ConvertTo-WeixinPhysicalClickPoint `
                    ([double]$point.x) ([double]$point.y) `
                    $windowDpi 'PerMonitorV2'
                $x=[int]$physicalPoint.x; $y=[int]$physicalPoint.y
                # 详情打开前的截图（结果列表状态），点击后比对判断详情是否打开。
                $before = & $capture
                try { $beforeHash=Get-BitmapSha256 $before } finally { $before.Dispose() }
                & $assertWorkBudget 95000
                # 优先 UIA InvokePattern 触发点击——不依赖窗口前台/鼠标，绕开前台抢占
                # （mouse_event 在前台漂走时 FOREGROUND_LOST）。找不到卡片元素/不支持 Invoke/
                # 抛异常则回退鼠标点击。两种方式后续都走同一套"等待→新详情 HWND→截图比对"。
                $invokeClicked = $false
                try {
                    $invokeEl = Find-WeixinCardElement $pluginHwnd `
                        ([string]$point.name) ([string]$point.control_type)
                    if ($invokeEl) {
                        $invokePat = $null
                        if ($invokeEl.TryGetCurrentPattern(
                            [Windows.Automation.InvokePattern]::Pattern, [ref]$invokePat)) {
                            $invokePat.Invoke()
                            $invokeClicked = $true
                            $clickDiagnostics.Add([pscustomobject]@{
                                checkpoint='invoke_pattern'
                                point_name=[string]$point.name
                                foreground=(Get-CurrentForegroundIdentitySafe)
                            })
                        }
                    }
                } catch {
                    $clickDiagnostics.Add([pscustomobject]@{
                        checkpoint='invoke_failed'
                        error=$_.Exception.Message
                    })
                }
                if (-not $invokeClicked) {
                    # 回退：鼠标点击（前台 guard + SetCursorPos + mouse_event）
                    if (-not (& $pluginGuard)) {
                        $clickForegroundRecoveryUsed=$true
                        if (-not (& $restorePluginForeground)) { throw 'FOREGROUND_LOST' }
                    }
                    if (-not [WechatSouyisouWin32]::SetCursorPos($x,$y)) {
                        throw 'MOUSE_POSITION_FAILED'
                    }
                    Start-Sleep -Milliseconds 100
                    if (-not (& $pluginGuard)) {
                        if ($clickForegroundRecoveryUsed -or -not (& $restorePluginForeground)) {
                            throw 'FOREGROUND_LOST'
                        }
                        $clickForegroundRecoveryUsed=$true
                    }
                    $clickDiagnostics.Add([pscustomobject]@{
                        checkpoint='before_click'
                        cursor_x=$x; cursor_y=$y
                        foreground=(Get-CurrentForegroundIdentitySafe)
                    })
                    Invoke-SafeMouseClick {
                        param($up)
                        [WechatSouyisouWin32]::mouse_event($(if($up){4}else{2}),0,0,0,[IntPtr]::Zero)
                    } $pluginGuard
                    $clickDiagnostics.Add([pscustomobject]@{
                        checkpoint='after_mouse_click'
                        foreground=(Get-CurrentForegroundIdentitySafe)
                    })
                }
                Start-Sleep -Milliseconds $WaitMilliseconds
                $clickDiagnostics.Add([pscustomobject]@{
                    checkpoint='after_wait'
                    wait_ms=$WaitMilliseconds
                    foreground=(Get-CurrentForegroundIdentitySafe)
                })
                if (-not (& $pluginGuard)) {
                    # 点击是生产路径唯一允许引入新插件 HWND 的阶段。当前前台仍须
                    # 通过完整 WeChatAppEx/Chrome_WidgetWin_0 身份校验；后续返回
                    # 结果页只允许落到本会话已经见过的 HWND。
                    $detailIdentity = Add-WeixinWindowSessionForeground `
                        $windowSession `
                        (Get-TrustedForegroundIdentity -RequirePlugin) 'detail'
                    $pluginHwnd = [IntPtr][int64]$detailIdentity.Hwnd
                    $detailViewport = & $newPluginViewport $pluginHwnd.ToInt64()
                    $rect=$detailViewport.rect; $width=$detailViewport.width
                    $height=$detailViewport.height; $capture=$detailViewport.capture
                }
                $after = & $capture
                try { $afterHash=Get-BitmapSha256 $after } finally { $after.Dispose() }
                if (-not (Test-WeixinDetailOpened $beforeHash $afterHash)) {
                    $records += [pscustomobject]@{
                        stage='click';reason='screenshot_unchanged';text_length=0
                        before_hash=$beforeHash;after_hash=$afterHash
                    }
                    $checked++; $failures++
                    continue
                }
                $detailMayBeOpen=$true
                $stage = 'detail_settle'
                & $assertWorkBudget 65000
                Wait-WeixinDetailSettled $pluginGuard `
                    { param($milliseconds) Start-Sleep -Milliseconds $milliseconds } 5000
                $stage = 'detail_copy'
                [Windows.Forms.Clipboard]::Clear()
                & $send @('CTRL','A') $pluginGuard; & $send @('CTRL','C') $pluginGuard
                Start-Sleep -Milliseconds 200
                $detail=[Windows.Forms.Clipboard]::GetText([Windows.Forms.TextDataFormat]::UnicodeText)
                $checked++
                if (-not (Test-WeixinReadableDetailCopy $detail $text)) {
                    $records += [pscustomobject]@{
                        ordinal=$checked;stage='detail_copy';reason='detail_copy_unreadable'
                        text_length=if($detail){$detail.Length}else{0};detail_hash=$afterHash
                    }
                    $failures++
                    $stage='recover'; $detailCloseInProgress=$true
                    $returnResult = & $returnToResultPage
                    $detailCloseInProgress=$false; $detailMayBeOpen=$false
                    if ($returnResult.session_closed) {
                        $recoveryEvidenceUnavailable=$true
                        $sessionNaturallyClosed=$true
                        break
                    }
                    $returnedHwnd=[int64]$returnResult.hwnd
                    $pluginHwnd=[IntPtr]$returnedHwnd
                    $returnedViewport=& $newPluginViewport $returnedHwnd
                    $rect=$returnedViewport.rect; $width=$returnedViewport.width
                    $height=$returnedViewport.height; $capture=$returnedViewport.capture
                    continue
                }
                $ocrHashes=@()
                $ocrText=$null
                $contentUsable=$true
                # OCR 暂屏蔽（PaddleOCR 云 API 未配置；后续配好把 $ocrEnabled 改 $true 恢复）
                $ocrEnabled = $false
                $needsOcr = $ocrEnabled -and (Test-DetailNeedsOcr $detail $text $AssociationName $PersonName)
                if ($needsOcr) {
                    if (-not $ocr) {
                        $records += [pscustomobject]@{
                            ordinal=$checked;stage='detail_copy';reason='ocr_unavailable'
                            text=$detail;text_length=$detail.Length;detail_hash=$afterHash
                        }
                        $failures++; $contentUsable=$false
                    } else {
                        $temporaryImages=@()
                        try {
                            for ($ocrIndex=0; $ocrIndex -lt 2; $ocrIndex++) {
                                if (-not (& $pluginGuard)) { throw 'FOREGROUND_LOST' }
                                $ocrBitmap=& $capture
                                $ocrEvidenceBitmap=$null
                                try {
                                    # 当前微信详情为整页打开，只截取中央正文/PDF区域。
                                    # 结果列表已由 Test-ResultPageEvidence 在进入 OCR 前排除。
                                    $ocrRegion=New-Object Drawing.Rectangle(
                                        [int]($width*0.26),[int]($height*0.08),
                                        [int]($width*0.48),[int]($height*0.87))
                                    $ocrEvidenceBitmap=$ocrBitmap.Clone(
                                        $ocrRegion,$ocrBitmap.PixelFormat)
                                    $candidateOcrHash=Get-BitmapSha256 $ocrEvidenceBitmap
                                    if (-not (Test-NewOcrViewportHash $ocrHashes $candidateOcrHash)) { break }
                                    $ocrHashes += $candidateOcrHash
                                    $temporaryPath=Join-Path ([IO.Path]::GetTempPath()) (
                                        'wechat-ocr-' + [guid]::NewGuid().ToString('N') + '.png')
                                    $temporaryImages += $temporaryPath
                                    $ocrEvidenceBitmap.Save(
                                        $temporaryPath,[Drawing.Imaging.ImageFormat]::Png)
                                } finally {
                                    if ($ocrEvidenceBitmap) { $ocrEvidenceBitmap.Dispose() }
                                    $ocrBitmap.Dispose()
                                }
                                if ($ocrIndex -lt 1) {
                                    if (-not (& $pluginGuard)) { throw 'FOREGROUND_LOST' }
                                    [void][WechatSouyisouWin32]::SetCursorPos(
                                        $rect.Left+[int]($width*0.50),$rect.Top+[int]($height*0.72))
                                    if (-not (& $pluginGuard)) { throw 'FOREGROUND_LOST' }
                                    [WechatSouyisouWin32]::mouse_event(
                                        0x0800,0,0,(ConvertTo-MouseWheelData -480),[IntPtr]::Zero)
                                    Start-Sleep -Milliseconds 500
                                }
                            }
                            & $assertWorkBudget 120000
                            $ocrResult=& $ocr ([pscustomobject]@{image_paths=$temporaryImages})
                            $ocrText=Get-ValidatedOcrText $ocrResult $temporaryImages.Count
                            if (-not (Test-OcrEvidenceAttribution `
                                $ocrText $AssociationName $PersonName)) {
                                throw 'OCR_ATTRIBUTION_FAILED'
                            }
                            $detail=($detail + "`n`n" + $ocrText).Trim()
                        } catch {
                            if ($_.Exception.Message -in @(
                                'FOREGROUND_LOST','WECHAT_WORK_TIMEOUT'
                            )) { throw }
                            $records += [pscustomobject]@{
                                ordinal=$checked;stage='detail_copy';reason='ocr_failed'
                                text=$detail;ocr_text=$ocrText
                                text_length=$detail.Length;detail_hash=$afterHash
                                ocr_hashes=$ocrHashes
                                ocr_region='center_detail_0.26_0.08_0.74_0.95'
                            }
                            $failures++; $contentUsable=$false
                        } finally {
                            $temporaryCleanupFailed=$false
                            foreach($temporaryImage in $temporaryImages) {
                                try { Remove-Item -LiteralPath $temporaryImage -Force -ErrorAction Stop }
                                catch { $temporaryCleanupFailed=$true }
                            }
                            if ($temporaryCleanupFailed) { throw 'TEMP_CLEANUP_FAILED' }
                        }
                    }
                }
                if (-not $contentUsable) {
                    $stage='close'
                    $detailCloseInProgress=$true
                    $stage='recover'
                    $returnResult = & $returnToResultPage
                    if (-not $returnResult) { throw 'RECOVERY_FAILED' }
                    $returnedHwnd = [int64]$returnResult.hwnd
                    if ($returnResult.session_closed) {
                        $recoveryEvidenceUnavailable=$true
                        $sessionNaturallyClosed=$true
                        $detailCloseInProgress=$false
                        $detailMayBeOpen=$false
                        break
                    }
                    $pluginHwnd = [IntPtr]$returnedHwnd
                    $returnedViewport = & $newPluginViewport $returnedHwnd
                    $rect=$returnedViewport.rect; $width=$returnedViewport.width
                    $height=$returnedViewport.height; $capture=$returnedViewport.capture
                    $detailCloseInProgress=$false
                    $detailMayBeOpen=$false
                    continue
                }
                if ($seenDetailText.ContainsKey($detail)) {
                    $records += [pscustomobject]@{
                        ordinal=$checked;stage='detail_copy';reason='duplicate_detail'
                        text=$detail;ocr_text=$ocrText
                        text_length=$detail.Length;detail_hash=$afterHash;ocr_hashes=$ocrHashes
                    }
                    $stage = 'close'
                    $detailCloseInProgress=$true
                    $stage = 'recover'
                    $returnResult = & $returnToResultPage
                    if (-not $returnResult) { throw 'RECOVERY_FAILED' }
                    $returnedHwnd = [int64]$returnResult.hwnd
                    if ($returnResult.session_closed) {
                        $recoveryEvidenceUnavailable=$true
                        $sessionNaturallyClosed=$true
                        $detailCloseInProgress=$false
                        $detailMayBeOpen=$false
                        break
                    }
                    $pluginHwnd = [IntPtr]$returnedHwnd
                    $returnedViewport = & $newPluginViewport $returnedHwnd
                    $rect=$returnedViewport.rect; $width=$returnedViewport.width
                    $height=$returnedViewport.height; $capture=$returnedViewport.capture
                    $detailCloseInProgress=$false
                    $detailMayBeOpen=$false
                    continue
                }
                $seenDetailText[$detail]=$true
                $consecutiveFailures=0
                # 详情文本无手机号 pattern 就不走大模型（用户要求：只有含手机 pattern 才问 LLM）。
                # OCR 已屏蔽——无手机的详情（如新闻稿）直接记 no_mobile_pattern 返回，省一次 LLM。
                if ((Get-MobileCandidates $detail).Count -eq 0) {
                    $records += [pscustomobject]@{
                        ordinal=$checked; stage='detail_judge'; reason='no_mobile_pattern'
                        text_length=$detail.Length; detail_hash=$afterHash
                        text=$detail
                    }
                    $stage='close'; $detailCloseInProgress=$true; $stage='recover'
                    $returnResult = & $returnToResultPage
                    if (-not $returnResult) { throw 'RECOVERY_FAILED' }
                    $returnedHwnd = [int64]$returnResult.hwnd
                    if ($returnResult.session_closed) {
                        $recoveryEvidenceUnavailable=$true; $sessionNaturallyClosed=$true
                        $detailCloseInProgress=$false; $detailMayBeOpen=$false
                        break
                    }
                    $pluginHwnd = [IntPtr]$returnedHwnd
                    $returnedViewport = & $newPluginViewport $returnedHwnd
                    $rect=$returnedViewport.rect; $width=$returnedViewport.width
                    $height=$returnedViewport.height; $capture=$returnedViewport.capture
                    $detailCloseInProgress=$false; $detailMayBeOpen=$false
                    continue
                }
                $stage = 'detail_judge'
                if ($judge) { & $assertWorkBudget 120000 } else { & $assertWorkBudget }
                $judgeResult=Invoke-EvidenceJudge $detail $AssociationName $PersonName $judge
                if ($judgeResult.token_usage) { $llmUsages += $judgeResult.token_usage }
                $records += [pscustomobject]@{
                    ordinal=$checked;stage='detail_judge';reason='checked'
                    text_length=$detail.Length;detail_hash=$afterHash
                    text=$detail;matched=[bool]$judgeResult.matched
                    ocr_text=$ocrText;ocr_hashes=$ocrHashes
                    ocr_region=if($ocrHashes.Count){'center_detail_0.26_0.08_0.74_0.95'}else{$null}
                }
                $stage = 'close'
                $detailCloseInProgress=$true
                $stage = 'recover'
                $returnResult = & $returnToResultPage
                if (-not $returnResult) { throw 'RECOVERY_FAILED' }
                $returnedHwnd = [int64]$returnResult.hwnd
                if ($returnResult.session_closed) {
                    $recoveryEvidenceUnavailable=$true
                    $sessionNaturallyClosed=$true
                    $detailCloseInProgress=$false
                    $detailMayBeOpen=$false
                    break
                }
                $pluginHwnd = [IntPtr]$returnedHwnd
                $returnedViewport = & $newPluginViewport $returnedHwnd
                $rect=$returnedViewport.rect; $width=$returnedViewport.width
                $height=$returnedViewport.height; $capture=$returnedViewport.capture
                $detailCloseInProgress=$false
                $detailMayBeOpen=$false
                if ($judgeResult.inconclusive) { $failures++; continue }
                if ($judgeResult.matched) { $foundJudge=$judgeResult; break }
            }
            if (
                $foundJudge -or $sessionNaturallyClosed -or
                $checked -ge $Limit -or $consecutiveFailures -ge 2
            ) { break }
            if (-not $newCount) {
                $records += New-WeixinUiaCandidateExhaustionRecord $checked
                break
            }
            $stage = 'scroll'
            if (-not (& $pluginGuard)) { throw 'FOREGROUND_LOST' }
            [void][WechatSouyisouWin32]::SetCursorPos(
                $rect.Left+[int]($width*0.38),$rect.Top+[int]($height*0.72))
            if (-not (& $pluginGuard)) { throw 'FOREGROUND_LOST' }
            [WechatSouyisouWin32]::mouse_event(
                0x0800,0,0,(ConvertTo-MouseWheelData -360),[IntPtr]::Zero)
            Start-Sleep -Milliseconds 700
            $scrolls++
        }
        $status=Get-WeixinCollectStatus `
            $foundJudge $recoveryEvidenceUnavailable $checked $Limit $failures
        $detailArtifact=Protect-EvidenceArtifact $ArtifactDirectory @{
            kind='collect_result';status=$status;checked=$checked;failures=$failures
            list_artifact_id=$artifact.artifact_id
            records=$records
            list_judge_status=if ($listJudgeStatus){$listJudgeStatus}else{$null}
            list_judge_reason_code=if ($listJudgeReasonCode){$listJudgeReasonCode}else{$null}
            llm_usages=$llmUsages
            found_result=if($foundJudge){$foundJudge}else{$null}
            captured_at=[DateTimeOffset]::Now.ToString('o')
        }
        $stage = 'cleanup'
        $sessionCleanupAttempted = $true
        $cleanupResult = Complete-WeixinPluginSession `
            ([ref]$sessionCleanupCompleted) $cleanupSession
        Add-Content -Path "$env:TEMP\wechat_diag.log" -Value "[$([DateTimeOffset]::Now.ToString('HH:mm:ss'))] collect done: status=$status checked=$checked failures=$failures list_judge=$listJudgeStatus list_reason=$listJudgeReasonCode person=$PersonName assoc=$AssociationName"
        Write-Result @{
            ok=$true;executed=$true;status=$status;checked=$checked;failures=$failures
            artifact_ref=$detailArtifact.artifact_ref
            session_closed=[bool]$cleanupResult.session_closed
        }
    } finally {
        # search/collect 的严格终态只关闭完整插件 HWND。详情状态仅供诊断，
        # 不能用快捷键猜测插件内部层级，否则可能误关后台网页标签。
        $sessionCleanupFailure = $null
        try {
            if (
                $requiresSessionCleanup -and
                -not $sessionCleanupCompleted -and
                -not $sessionCleanupAttempted
            ) {
                $stageBeforeCleanup = $stage
                $stage = 'cleanup'
                $sessionCleanupAttempted = $true
                if (-not $cleanupSession) { throw 'SESSION_CLEANUP_FAILED' }
                [void](Complete-WeixinPluginSession `
                    ([ref]$sessionCleanupCompleted) $cleanupSession)
                # 清理成功时保留原业务失败阶段；只有清理自身失败才对外报告 cleanup。
                $stage = $stageBeforeCleanup
            }
        } catch { $sessionCleanupFailure = $_ }
        if ($clipboardCaptured) {
            try {
                if ($null -eq $originalClipboard) { [Windows.Forms.Clipboard]::Clear() }
                else { [Windows.Forms.Clipboard]::SetDataObject($originalClipboard, $true) }
            } catch {}
        }
        if ($mutex) { try { $mutex.ReleaseMutex() } catch {}; $mutex.Dispose() }
        if ($sessionCleanupFailure) {
            $stage = 'cleanup'
            if ($sessionCleanupFailure.Exception.Message -match '^[A-Z][A-Z0-9_]+$') {
                throw $sessionCleanupFailure
            }
            throw 'SESSION_CLEANUP_FAILED'
        }
    }
} catch {
    $code = if ($_.Exception.Message -match '^[A-Z][A-Z0-9_]+$') { $_.Exception.Message } else { 'RPA_FAILED' }
    $failureArtifactRef = $null
    if ($Execute) {
        try {
            $foregroundContext = $null
            if ('WechatSouyisouWin32' -as [type]) {
                $failureHwnd = [WechatSouyisouWin32]::GetForegroundWindow()
                if ($failureHwnd -ne [IntPtr]::Zero) {
                    $failurePid = [uint32]0
                    [void][WechatSouyisouWin32]::GetWindowThreadProcessId($failureHwnd,[ref]$failurePid)
                    $failureClass = New-Object Text.StringBuilder 256
                    [void][WechatSouyisouWin32]::GetClassName($failureHwnd,$failureClass,$failureClass.Capacity)
                    $failureProcessName = $null
                    try {
                        $failureProcessName = [IO.Path]::GetFileName(
                            (Get-Process -Id $failurePid -ErrorAction Stop).Path)
                    } catch {}
                    $foregroundContext = [pscustomobject]@{
                        hwnd=$failureHwnd.ToInt64(); process_basename=$failureProcessName
                        class_name=$failureClass.ToString()
                    }
                }
            }
            $failureArtifact = Protect-EvidenceArtifact $ArtifactDirectory @{
                kind='failure'; error_code=$code; stage=$stage
                cleanup_error_code=if ($stage -eq 'cleanup') {$code}else{$null}
                detail_may_be_open=[bool]$detailMayBeOpen
                input_verified=[bool]$inputVerified
                readback_matched=[bool]$inputDiagnostics.readback_matched
                foreground=$foregroundContext
                result_artifact_id=if ($detailArtifact) {
                    $detailArtifact.artifact_id
                } elseif ($artifact) {
                    $artifact.artifact_id
                } else { $null }
                list_judge_status=if ($listJudgeStatus){$listJudgeStatus}else{$null}
                list_judge_reason_code=if ($listJudgeReasonCode){$listJudgeReasonCode}else{$null}
                llm_usages=if ($llmUsages){$llmUsages}else{@()}
                viewport_hash=if ($viewportHash){[string]$viewportHash}else{$null}
                window_geometry=if ($width -and $height){
                    [pscustomobject]@{width=[int]$width;height=[int]$height}
                }else{$null}
                content_region=if ($contentRegion){$contentRegion}else{$null}
                click_diagnostics=if ($clickDiagnostics -and $clickDiagnostics.Count){
                    @($clickDiagnostics)
                }else{$null}
                captured_at=[DateTimeOffset]::Now.ToString('o')
            }
            $failureArtifactRef = $failureArtifact.artifact_ref
        } catch {}
    }
    $resultStatus = if ($detailArtifact -and $detailArtifact.status) {
        [string]$detailArtifact.status
    } else { $null }
    Fail $code $_.Exception.Message $failureArtifactRef $stage $resultStatus
}
