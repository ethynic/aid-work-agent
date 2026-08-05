[CmdletBinding()]
param(
    [ValidateSet('probe','open','search','collect','flow_probe')][string]$Command = 'probe',
    [string]$AssociationName,
    [string]$PersonName,
    [string]$InputJson,
    [switch]$ReadStdin,
    [switch]$Execute,
    [ValidateRange(1,10)][int]$Limit = 10,
    [string]$LocatorPath,
    [string]$JudgeCommand,
    [switch]$UseProjectLlm,
    [string]$OcrCommand,
    [switch]$DisableOcr,
    [switch]$VerifyInputOnly,
    [string]$FlowProbeInputPath,
    [ValidateRange(0.28,0.68)][double]$FlowProbeXRatio = 0.30,
    [ValidateRange(0.18,0.70)][double]$FlowProbeYRatio = 0.30,
    [string]$ArtifactDirectory = (Join-Path $env:LOCALAPPDATA 'AidWorkAgent\wechat-souyisou-rpa\artifacts'),
    [ValidateRange(500,30000)][int]$WaitMilliseconds = 2500,
    [ValidateRange(10000,60000)][int]$SearchReadyTimeoutMilliseconds = 15000
)

$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = [Text.Encoding]::UTF8
. (Join-Path $PSScriptRoot 'wechat-souyisou-lib.ps1')

function Write-Result([hashtable]$Value) {
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
    if ($Command -notin @('probe','open','search','collect','flow_probe')) { throw 'INVALID_COMMAND' }
    if ($Command -eq 'flow_probe' -and (
        -not $Execute -or [string]::IsNullOrWhiteSpace($FlowProbeInputPath)
    )) { throw 'INVALID_FLOW_PROBE_INPUT' }
    if ($VerifyInputOnly -and (
        -not $Execute -or $Command -notin @('search','collect')
    )) { throw 'INVALID_INPUT_PROBE_MODE' }
    if ($Limit -lt 1 -or $Limit -gt 10) { throw 'INVALID_LIMIT' }
    if (
        $SearchReadyTimeoutMilliseconds -lt 10000 -or
        $SearchReadyTimeoutMilliseconds -gt 60000
    ) { throw 'INVALID_SEARCH_READY_TIMEOUT' }
    if ($Command -in @('search','collect')) { $query = New-SearchQuery $AssociationName $PersonName }
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
 [DllImport("user32.dll",SetLastError=true)] public static extern IntPtr SendMessageTimeout(IntPtr h,uint m,UIntPtr w,IntPtr l,uint f,uint t,out UIntPtr r);
 public struct RECT { public int Left,Top,Right,Bottom; }
}
"@)
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
        $main = Select-WeixinMainWindow $windows
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
        if ($Command -eq 'flow_probe') {
            $stage = 'flow_input'
            $flowItems = @(Read-WeixinFlowProbeItems $FlowProbeInputPath)
            $flowCloseSend = {
                param($keys)
                Invoke-SafeKeyChord $keys {
                    param($key,$up)
                    [WechatSouyisouWin32]::keybd_event(
                        $virtualKeyMap[$key],0,$(if($up){2}else{0}),
                        [IntPtr]::Zero)
                } { Start-Sleep -Milliseconds 40 } { $true }
            }

            $flowOriginalClipboard = $null
            $flowClipboardCaptured = $false
            $flowPluginHwnd = [int64]0
            $flowSteps = @()
            $flowFailure = $null
            $flowFailureStage = $null
            $flowFailureFocus = $null
            $flowForegroundIdentity = $null
            $flowHandleTransition = $false
            $flowSessionHwnds = New-Object Collections.Generic.List[int64]
            $flowInputHwndChanged = $false
            $flowListHwndChanged = $false
            $flowClickHwndChanged = $false
            $flowDetailHwndChanged = $false
            $flowCloseCount = 0
            $flowOpenAttempts = 0
            $flowBaseKind = ''
            try {
                $flowOriginalClipboard = [Windows.Forms.Clipboard]::GetDataObject()
                $flowClipboardCaptured = $true
                for ($flowIndex = 0; $flowIndex -lt $flowItems.Count; $flowIndex++) {
                    $flowItem = $flowItems[$flowIndex]
                    $flowHandleTransition = $false
                    $flowInputHwndChanged = $false
                    $flowListHwndChanged = $false
                    $flowClickHwndChanged = $false
                    $flowDetailHwndChanged = $false
                    $flowCloseCount = 0
                    $flowOpenAttempts = 0
                    $flowBaseKind = ''
                    $stage = 'flow_open'
                    $flowIdentity = $null
                    for ($flowOpenAttempt = 1; $flowOpenAttempt -le 2; $flowOpenAttempt++) {
                        $flowOpenBase = Get-CurrentForegroundIdentity
                        [void](Get-WeixinFlowBaseKind `
                            $flowOpenBase ([int64]$main.Hwnd))
                        $flowOpenBaseHwnd = [int64]$flowOpenBase.Hwnd
                        $flowOpenGuard = {
                            $current = Get-CurrentForegroundIdentity
                            if ($null -eq $current -or
                                [int64]$current.Hwnd -ne $flowOpenBaseHwnd) {
                                return $false
                            }
                            try {
                                [void](Get-WeixinFlowBaseKind `
                                    $current ([int64]$main.Hwnd))
                                return $true
                            } catch { return $false }
                        }.GetNewClosure()
                        & $send @('CTRL','F') $flowOpenGuard
                        Start-Sleep -Milliseconds 400
                        & $send @('DOWN') $flowOpenGuard
                        Start-Sleep -Milliseconds 250
                        & $send @('ENTER') $flowOpenGuard
                        $flowOpenAttempts++
                        Start-Sleep -Milliseconds $WaitMilliseconds
                        $flowOpenForeground = Get-CurrentForegroundIdentity
                        $flowOpenDecision = Get-WeixinFlowOpenDecision `
                            $flowOpenForeground ([int64]$main.Hwnd) $flowOpenAttempt
                        if ($flowOpenDecision -eq 'plugin') {
                            $flowIdentity = $flowOpenForeground
                            break
                        }
                        Start-Sleep -Milliseconds 250
                    }
                    $flowPluginHwnd = [int64]$flowIdentity.Hwnd
                    $flowSessionHwnds.Add($flowPluginHwnd)
                    $flowGuard = {
                        $current = Get-TrustedForegroundIdentity -RequirePlugin
                        $null -ne $current -and [int64]$current.Hwnd -eq $flowPluginHwnd
                    }.GetNewClosure()

                    $stage = 'flow_input_verify'
                    $flowInputIdentity = Resolve-WeixinFlowForegroundPlugin `
                        (Get-CurrentForegroundIdentity) ([int64]$main.Hwnd)
                    $flowInputHwndChanged =
                        [int64]$flowInputIdentity.Hwnd -ne $flowPluginHwnd
                    if ($flowInputHwndChanged) {
                        $flowPluginHwnd = [int64]$flowInputIdentity.Hwnd
                        $flowSessionHwnds.Add($flowPluginHwnd)
                        $flowHandleTransition = $true
                        $flowGuard = {
                            $current = Get-TrustedForegroundIdentity -RequirePlugin
                            $null -ne $current -and
                                [int64]$current.Hwnd -eq $flowPluginHwnd
                        }.GetNewClosure()
                    }
                    $flowPerson = ([string]$flowItem.person_name).Trim()
                    $flowQuery = if ($flowPerson) {
                        New-SearchQuery `
                            ([string]$flowItem.association_name) $flowPerson
                    } else {
                        ([string]$flowItem.association_name).Trim()
                    }
                    $flowInput = Invoke-VerifiedWeixinFocusedSearchSubmission `
                        $flowQuery $flowGuard {
                            param($value) [Windows.Forms.Clipboard]::SetText([string]$value)
                        } {
                            param($keys) & $send $keys $flowGuard
                        } {
                            Start-Sleep -Milliseconds 120
                            [Windows.Forms.Clipboard]::GetText(
                                [Windows.Forms.TextDataFormat]::UnicodeText)
                        } $true @{}

                    $stage = 'flow_list_copy'
                    $flowListIdentity = Resolve-WeixinFlowForegroundPlugin `
                        (Get-CurrentForegroundIdentity) ([int64]$main.Hwnd)
                    $flowListHwndChanged =
                        [int64]$flowListIdentity.Hwnd -ne $flowPluginHwnd
                    if ($flowListHwndChanged) {
                        $flowPluginHwnd = [int64]$flowListIdentity.Hwnd
                        $flowSessionHwnds.Add($flowPluginHwnd)
                        $flowHandleTransition = $true
                        $flowGuard = {
                            $current = Get-TrustedForegroundIdentity -RequirePlugin
                            $null -ne $current -and
                                [int64]$current.Hwnd -eq $flowPluginHwnd
                        }.GetNewClosure()
                    }
                    $flowListCopy = Wait-WeixinFlowListCopy $flowGuard {
                        [Windows.Forms.Clipboard]::Clear()
                    } {
                        param($keys) & $send $keys $flowGuard
                    } {
                        Start-Sleep -Milliseconds 120
                        [Windows.Forms.Clipboard]::GetText(
                            [Windows.Forms.TextDataFormat]::UnicodeText)
                    } {
                        param($ms) Start-Sleep -Milliseconds $ms
                    } 12 500 80
                    $flowListText = [string]$flowListCopy.text

                    $stage = 'flow_click'
                    $flowClickIdentity = Resolve-WeixinFlowForegroundPlugin `
                        (Get-CurrentForegroundIdentity) ([int64]$main.Hwnd)
                    $flowClickHwndChanged =
                        [int64]$flowClickIdentity.Hwnd -ne $flowPluginHwnd
                    if ($flowClickHwndChanged) {
                        $flowPluginHwnd = [int64]$flowClickIdentity.Hwnd
                        $flowSessionHwnds.Add($flowPluginHwnd)
                        $flowHandleTransition = $true
                        $flowGuard = {
                            $current = Get-TrustedForegroundIdentity -RequirePlugin
                            $null -ne $current -and
                                [int64]$current.Hwnd -eq $flowPluginHwnd
                        }.GetNewClosure()
                    }
                    $flowRect = New-Object WechatSouyisouWin32+RECT
                    if (-not [WechatSouyisouWin32]::GetWindowRect(
                        [IntPtr]$flowPluginHwnd,[ref]$flowRect)) { throw 'WINDOW_RECT_FAILED' }
                    $flowWidth = $flowRect.Right - $flowRect.Left
                    $flowHeight = $flowRect.Bottom - $flowRect.Top
                    if (-not (Test-WindowRectDimensions $flowWidth $flowHeight)) {
                        throw 'WINDOW_RECT_FAILED'
                    }
                    $flowCapture = {
                        $image = New-Object Drawing.Bitmap $flowWidth,$flowHeight
                        $graphics = [Drawing.Graphics]::FromImage($image)
                        try {
                            $graphics.CopyFromScreen(
                                $flowRect.Left,$flowRect.Top,0,0,$image.Size)
                        } finally { $graphics.Dispose() }
                        $image
                    }.GetNewClosure()
                    if (-not (& $flowGuard)) { throw 'INPUT_FOCUS_LOST' }
                    [void][WechatSouyisouWin32]::SetCursorPos(
                        $flowRect.Left+[int]($flowWidth*$FlowProbeXRatio),
                        $flowRect.Top+[int]($flowHeight*$FlowProbeYRatio))
                    Start-Sleep -Milliseconds 150
                    if (-not (& $flowGuard)) { throw 'INPUT_FOCUS_LOST' }
                    $flowBefore = & $flowCapture
                    try { $flowBeforeHash = Get-BitmapSha256 $flowBefore }
                    finally { $flowBefore.Dispose() }
                    Invoke-SafeMouseClick {
                        param($up)
                        [WechatSouyisouWin32]::mouse_event(
                            $(if($up){4}else{2}),0,0,0,[IntPtr]::Zero)
                    } $flowGuard
                    Start-Sleep -Milliseconds $WaitMilliseconds
                    $flowDetailIdentity = Resolve-WeixinFlowForegroundPlugin `
                        (Get-CurrentForegroundIdentity) ([int64]$main.Hwnd)
                    $flowDetailHwndChanged =
                        [int64]$flowDetailIdentity.Hwnd -ne $flowPluginHwnd
                    if ($flowDetailHwndChanged) {
                        $flowPluginHwnd = [int64]$flowDetailIdentity.Hwnd
                        $flowSessionHwnds.Add($flowPluginHwnd)
                        $flowHandleTransition = $true
                        $flowGuard = {
                            $current = Get-TrustedForegroundIdentity -RequirePlugin
                            $null -ne $current -and
                                [int64]$current.Hwnd -eq $flowPluginHwnd
                        }.GetNewClosure()
                        $flowRect = New-Object WechatSouyisouWin32+RECT
                        if (-not [WechatSouyisouWin32]::GetWindowRect(
                            [IntPtr]$flowPluginHwnd,[ref]$flowRect)) {
                            throw 'WINDOW_RECT_FAILED'
                        }
                        $flowWidth = $flowRect.Right - $flowRect.Left
                        $flowHeight = $flowRect.Bottom - $flowRect.Top
                        if (-not (Test-WindowRectDimensions $flowWidth $flowHeight)) {
                            throw 'WINDOW_RECT_FAILED'
                        }
                        $flowCapture = {
                            $image = New-Object Drawing.Bitmap $flowWidth,$flowHeight
                            $graphics = [Drawing.Graphics]::FromImage($image)
                            try {
                                $graphics.CopyFromScreen(
                                    $flowRect.Left,$flowRect.Top,0,0,$image.Size)
                            } finally { $graphics.Dispose() }
                            $image
                        }.GetNewClosure()
                    }
                    if (-not (& $flowGuard)) { throw 'INPUT_FOCUS_LOST' }
                    $flowAfter = & $flowCapture
                    try { $flowAfterHash = Get-BitmapSha256 $flowAfter }
                    finally { $flowAfter.Dispose() }
                    if ($flowAfterHash -eq $flowBeforeHash) { throw 'FLOW_DETAIL_NOT_OPENED' }

                    # 诊断关键序列：确认详情变化后只关闭详情和结果页，保留搜索主页
                    # 作为下一轮基准。两次 Ctrl+W 之间不执行任何其他操作。
                    $stage = 'flow_close_detail'
                    $flowCloseIdentity = Resolve-WeixinFlowForegroundPlugin `
                        (Get-CurrentForegroundIdentity) ([int64]$main.Hwnd)
                    if ([int64]$flowCloseIdentity.Hwnd -ne $flowPluginHwnd) {
                        $flowPluginHwnd = [int64]$flowCloseIdentity.Hwnd
                        $flowSessionHwnds.Add($flowPluginHwnd)
                        $flowHandleTransition = $true
                        $flowGuard = {
                            $current = Get-TrustedForegroundIdentity -RequirePlugin
                            $null -ne $current -and
                                [int64]$current.Hwnd -eq $flowPluginHwnd
                        }.GetNewClosure()
                    }
                    & $flowCloseSend @('CTRL','W')
                    $flowCloseCount++
                    Start-Sleep -Milliseconds $WaitMilliseconds
                    $stage = 'flow_close_list'
                    & $flowCloseSend @('CTRL','W')
                    $flowCloseCount++
                    Start-Sleep -Milliseconds $WaitMilliseconds
                    $flowAfterCloseIdentity = Get-CurrentForegroundIdentity
                    $flowBaseKind = Get-WeixinFlowBaseKind `
                        $flowAfterCloseIdentity ([int64]$main.Hwnd)
                    $flowSteps += [pscustomobject]@{
                        index=$flowIndex+1; plugin_hwnd=$flowPluginHwnd
                        opened=$true;input_verified=[bool]$flowInput.input_verified
                        list_copied=$true;detail_opened=$true;detail_closed=$true
                        list_closed=$true;base_ready=$true
                        base_kind=[string]$flowBaseKind
                        handle_transition=[bool]$flowHandleTransition
                        input_hwnd_changed=[bool]$flowInputHwndChanged
                        list_hwnd_changed=[bool]$flowListHwndChanged
                        click_hwnd_changed=[bool]$flowClickHwndChanged
                        detail_hwnd_changed=[bool]$flowDetailHwndChanged
                        close_count=[int]$flowCloseCount
                        open_attempts=[int]$flowOpenAttempts
                        stage='complete';error_code=$null
                    }
                    $flowPluginHwnd = [int64]0
                    $flowSessionHwnds.Clear()
                    $flowListText = $null
                    $flowQuery = $null
                }
                $stage = 'flow_complete'
                try {
                    if ($null -eq $flowOriginalClipboard) {
                        [Windows.Forms.Clipboard]::Clear()
                    } else {
                        [Windows.Forms.Clipboard]::SetDataObject(
                            $flowOriginalClipboard,$true)
                    }
                    $flowClipboardCaptured = $false
                } catch { throw 'CLIPBOARD_RESTORE_FAILED' }
                Write-Result @{
                    ok=$true;executed=$true;mode='flow_probe'
                    completed_count=$flowSteps.Count;steps=$flowSteps
                }
                exit 0
            } catch {
                $flowFailure = $_
                $flowFailureStage = $stage
                $flowOriginalCode = if (
                    $_.Exception.Message -match '^[A-Z][A-Z0-9_]+$'
                ) { $_.Exception.Message } else { 'FLOW_PROBE_FAILED' }
                try {
                    $flowForegroundHwnd = [WechatSouyisouWin32]::GetForegroundWindow()
                    $flowForegroundIdentity = if ($flowForegroundHwnd -eq [IntPtr]::Zero) {
                        $null
                    } else {
                        Get-WindowIdentityByHwnd $flowForegroundHwnd.ToInt64()
                    }
                    $flowFailureFocus = Get-WeixinFlowFocusFailureDiagnostic `
                        $flowForegroundIdentity $flowPluginHwnd ([int64]$main.Hwnd) `
                        $flowOriginalCode
                } catch {
                    # 诊断只能补充原失败，任何采样/身份格式异常均不得覆盖原错误。
                    $flowFailureFocus = [pscustomobject]@{
                        error_code=$flowOriginalCode;foreground_hwnd=[int64]0
                        process_basename='';class_name=''
                    }
                }
            } finally {
                $flowCleanupFailure = $null
                # 诊断失败只允许定向关闭当前前台、且已在本轮见过的可信插件。
                # 不激活主窗口，不操作后台句柄，也不触碰外部应用。
                try {
                    $flowCleanupIdentity = Get-CurrentForegroundIdentity
                    if (
                        $flowCleanupIdentity -and
                        [int64]$flowCleanupIdentity.Hwnd -ne [int64]$main.Hwnd -and
                        $flowSessionHwnds.Contains(
                            [int64]$flowCleanupIdentity.Hwnd) -and
                        (Test-WeixinForegroundIdentity `
                            $flowCleanupIdentity ([int64]$main.Hwnd))
                    ) {
                        if (-not (& $requestCloseWindow `
                            ([int64]$flowCleanupIdentity.Hwnd))) {
                            throw 'FLOW_PLUGIN_CLOSE_REJECTED'
                        }
                    }
                } catch {
                    if ($_.Exception.Message -eq 'FLOW_PLUGIN_CLOSE_REJECTED') {
                        $flowCleanupFailure = $_
                    }
                }
                if ($flowClipboardCaptured) {
                    try {
                        if ($null -eq $flowOriginalClipboard) {
                            [Windows.Forms.Clipboard]::Clear()
                        } else {
                            [Windows.Forms.Clipboard]::SetDataObject(
                                $flowOriginalClipboard,$true)
                        }
                    } catch {
                        if (-not $flowCleanupFailure) {
                            $flowCleanupFailure = New-Object Management.Automation.ErrorRecord(
                                (New-Object Exception 'CLIPBOARD_RESTORE_FAILED'),
                                'CLIPBOARD_RESTORE_FAILED',
                                [Management.Automation.ErrorCategory]::OperationStopped,$null)
                        }
                    }
                }
                if ($flowCleanupFailure) { throw $flowCleanupFailure }
            }
            if ($flowFailure) {
                Write-Result @{
                    ok=$false;executed=$true;mode='flow_probe'
                    completed_count=$flowSteps.Count;steps=$flowSteps
                    failed_index=$flowSteps.Count+1;stage=$flowFailureStage
                    error_code=[string]$flowFailureFocus.error_code
                    handle_transition=[bool]$flowHandleTransition
                    input_hwnd_changed=[bool]$flowInputHwndChanged
                    list_hwnd_changed=[bool]$flowListHwndChanged
                    click_hwnd_changed=[bool]$flowClickHwndChanged
                    detail_hwnd_changed=[bool]$flowDetailHwndChanged
                    close_count=[int]$flowCloseCount
                    open_attempts=[int]$flowOpenAttempts
                    base_ready=$false;base_kind=[string]$flowBaseKind
                    foreground_hwnd=[int64]$flowFailureFocus.foreground_hwnd
                    process_id=[uint32]$(if ($flowForegroundIdentity) {
                        $flowForegroundIdentity.ProcessId
                    } else { 0 })
                    process_basename=[string]$flowFailureFocus.process_basename
                    class_name=[string]$flowFailureFocus.class_name
                }
                exit 1
            }
        }
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
            locator_found=$false
            post_click_structure=$false
            readback_matched=$false
            final_structure=$false
        }
        # 新会话由可信主窗口 Ctrl+F/Down/Enter 打开后，焦点即位于搜索输入框。
        # 从此处到回读完成禁止截图、鼠标点击或窗口激活，避免主动抢走焦点。
        & $assertWorkBudget 60000
        $inputResult = Invoke-VerifiedWeixinFocusedSearchSubmission $query `
            $pluginGuard {
                param($value) [Windows.Forms.Clipboard]::SetText([string]$value)
            } {
                param($keys) & $send $keys $pluginGuard
            } {
                Start-Sleep -Milliseconds 120
                [Windows.Forms.Clipboard]::GetText(
                    [Windows.Forms.TextDataFormat]::UnicodeText)
            } (-not $VerifyInputOnly) $inputDiagnostics
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
            $artifact=Protect-EvidenceArtifact $ArtifactDirectory @{
                kind='result_page_unbounded';association_name=$AssociationName
                person_name=$PersonName;text='';links=@();input_verified=[bool]$inputVerified
                captured_at=[DateTimeOffset]::Now.ToString('o')
            }
            $detailArtifact=Protect-EvidenceArtifact $ArtifactDirectory @{
                kind='collect_result';status='inconclusive';checked=0;failures=1
                list_artifact_id=$artifact.artifact_id;records=@([pscustomobject]@{
                    stage='list_judge';reason='list_text_unavailable';text_length=0
                });found_result=$null;captured_at=[DateTimeOffset]::Now.ToString('o')
            }
            $stage='cleanup';$sessionCleanupAttempted=$true
            $cleanupResult=Complete-WeixinPluginSession `
                ([ref]$sessionCleanupCompleted) $cleanupSession
            Write-Result @{
                ok=$true;executed=$true;status='inconclusive';checked=0;failures=1
                artifact_ref=$detailArtifact.artifact_ref
                session_closed=[bool]$cleanupResult.session_closed
            }
            exit 0
        }
        $stage = 'article_switch'
        # 搜索结果首次稳定后，微信原生 Ctrl+Tab 会直接进入“文章”。每个 query
        # 只发送一次；不通过坐标、UIA、截图或重复快捷键猜测分类状态。
        & $assertWorkBudget 62000
        if (-not (& $pluginGuard)) { throw 'FOREGROUND_LOST' }
        & $send @('CTRL','TAB') $pluginGuard
        Start-Sleep -Milliseconds 2000
        if (-not (& $pluginGuard)) { throw 'FOREGROUND_LOST' }

        # 丢弃切换前的列表内容和 HTML。后续 artifact、Judge、viewport 与卡片
        # 定位只能从文章分类刷新后的页面重新建立。
        $text = ''
        $html = ''
        [Windows.Forms.Clipboard]::Clear()
        & $send @('CTRL','A') $pluginGuard
        & $send @('CTRL','C') $pluginGuard
        Start-Sleep -Milliseconds 200
        $postSwitchText = [Windows.Forms.Clipboard]::GetText(
            [Windows.Forms.TextDataFormat]::UnicodeText
        )
        if (-not (Test-SearchResultReady $postSwitchText $query `
            $AssociationName $PersonName $inputVerified)) {
            $artifact=Protect-EvidenceArtifact $ArtifactDirectory @{
                kind='result_page_unbounded';association_name=$AssociationName
                person_name=$PersonName;text='';links=@();input_verified=[bool]$inputVerified
                captured_at=[DateTimeOffset]::Now.ToString('o')
            }
            $detailArtifact=Protect-EvidenceArtifact $ArtifactDirectory @{
                kind='collect_result';status='inconclusive';checked=0;failures=1
                list_artifact_id=$artifact.artifact_id;records=@([pscustomobject]@{
                    stage='article_switch';reason='article_list_text_unavailable';text_length=0
                });found_result=$null;captured_at=[DateTimeOffset]::Now.ToString('o')
            }
            $stage='cleanup';$sessionCleanupAttempted=$true
            $cleanupResult=Complete-WeixinPluginSession `
                ([ref]$sessionCleanupCompleted) $cleanupSession
            Write-Result @{
                ok=$true;executed=$true;status='inconclusive';checked=0;failures=1
                artifact_ref=$detailArtifact.artifact_ref
                session_closed=[bool]$cleanupResult.session_closed
            }
            exit 0
        }
        $text = $postSwitchText
        $html = [Windows.Forms.Clipboard]::GetText(
            [Windows.Forms.TextDataFormat]::Html
        )
        $stage = 'copy'
        $links = @(Get-CfHtmlLinks $html)
        $judge = $null
        if ($UseProjectLlm) {
            $judge = New-ExternalJudge (Get-Command python.exe -ErrorAction Stop).Source `
                @((Join-Path $PSScriptRoot 'llm_judge.py'))
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
        $contentRegion = [pscustomobject]@{
            left_ratio=0.04;right_ratio=0.72;top_ratio=0.10;bottom_ratio=0.94
        }
        # 保留阶段名用于兼容诊断协议；本阶段仅封存无边界列表，不做命中判断。
        $stage = 'list_judge'
        $artifact = Protect-EvidenceArtifact $ArtifactDirectory @{
            kind='result_page_unbounded'; association_name=$AssociationName; person_name=$PersonName
            text=$text; links=$links; input_verified=[bool]$inputVerified
            input_locator=if($inputResult){[string]$inputResult.locator}else{$null}
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
        $listJudge = Invoke-EvidenceJudge $text $AssociationName $PersonName $judge
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
            if ($listJudge.inconclusive) {
                $detailArtifact = Protect-EvidenceArtifact $ArtifactDirectory @{
                    kind='collect_result';status='inconclusive';checked=0;failures=1
                    source='result_page_unbounded';list_artifact_id=$artifact.artifact_id
                    list_judge_status=$listJudgeStatus
                    list_judge_reason_code=$listJudgeReasonCode
                    llm_usages=$llmUsages;records=@();found_result=$null
                    captured_at=[DateTimeOffset]::Now.ToString('o')
                }
                $stage='cleanup';$sessionCleanupAttempted=$true
                $cleanupResult=Complete-WeixinPluginSession `
                    ([ref]$sessionCleanupCompleted) $cleanupSession
                Write-Result @{
                    ok=$true;executed=$true;status='inconclusive';checked=0;failures=1
                    artifact_ref=$detailArtifact.artifact_ref
                    session_closed=[bool]$cleanupResult.session_closed
                }
                exit 0
            }
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
        $explicitItems = $null
        if ($LocatorPath) {
            $stage = 'locator_read'
            $clientRoot = Split-Path -Parent $PSScriptRoot
            $resolvedLocatorPath = Resolve-LocatorFilePath $LocatorPath $clientRoot
            $locator = Read-LocatorJson $resolvedLocatorPath
            $stage = 'points'
            $explicitItems = @(Get-ValidatedLocatorPoints $locator)
        }
        $checked=0; $failures=0; $consecutiveFailures=0; $scrolls=0
        $locateRecoveryAttempts=0
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
                $points = @(if ($explicitItems) { $explicitItems } else {
                    @(Find-DarkThemeCardBands $viewport `
                        $contentRegion.left_ratio $contentRegion.right_ratio `
                        $contentRegion.top_ratio $contentRegion.bottom_ratio)
                })
            } finally { $viewport.Dispose() }
            if (-not $points.Count) {
                $stage = 'points'
                if ($locateRecoveryAttempts -lt 1) {
                    $locateRecoveryAttempts++
                    if (-not (& $restorePluginForeground)) { throw 'FOREGROUND_LOST' }
                    Start-Sleep -Milliseconds 500
                    continue
                }
                if (-not (Test-ListHasActionableCandidates $text $PersonName $links)) {
                    $records += [pscustomobject]@{
                        stage='points';reason='no_actionable_list_candidate';text_length=0
                        viewport_hash=$viewportHash
                    }
                    $consecutiveFailures=2
                    break
                }
                throw 'CARD_LOCATE_FAILED'
            }
            $newCount = 0
            foreach ($point in $points) {
                & $assertWorkBudget 60000
                if ($checked -ge $Limit -or $consecutiveFailures -ge 2) { break }
                $pointKey = if ($point.fingerprint) {
                    [string]$point.fingerprint
                } elseif ($point.title -or $point.source -or $point.date) {
                    "$($point.title)|$($point.source)|$($point.date)"
                } else {
                    "$viewportHash|$($point.x_ratio)|$($point.y_ratio)"
                }
                if ($seen.ContainsKey($pointKey)) { continue }
                $seen[$pointKey]=$true; $newCount++
                $stage = 'click'
                $clickForegroundRecoveryUsed=$false
                $x=$rect.Left+[int]($width*[double]$point.x_ratio)
                $y=$rect.Top+[int]($height*[double]$point.y_ratio)
                if (-not (& $pluginGuard)) {
                    $clickForegroundRecoveryUsed=$true
                    if (-not (& $restorePluginForeground)) { throw 'FOREGROUND_LOST' }
                }
                [void][WechatSouyisouWin32]::SetCursorPos($x,$y)
                Start-Sleep -Milliseconds 100
                if (-not (& $pluginGuard)) {
                    if ($clickForegroundRecoveryUsed -or -not (& $restorePluginForeground)) {
                        throw 'FOREGROUND_LOST'
                    }
                    $clickForegroundRecoveryUsed=$true
                }
                $before = & $capture
                try { $beforeHash=Get-BitmapSha256 $before } finally { $before.Dispose() }
                & $assertWorkBudget 95000
                Invoke-SafeMouseClick {
                    param($up)
                    [WechatSouyisouWin32]::mouse_event($(if($up){4}else{2}),0,0,0,[IntPtr]::Zero)
                } $pluginGuard
                Start-Sleep -Milliseconds $WaitMilliseconds
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
                $needsOcr = Test-DetailNeedsOcr $detail $text $AssociationName $PersonName
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
                $checked -ge $Limit -or $consecutiveFailures -ge 2 -or -not $newCount
            ) { break }
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
                locator_found=[bool]$inputDiagnostics.locator_found
                post_click_structure=[bool]$inputDiagnostics.post_click_structure
                readback_matched=[bool]$inputDiagnostics.readback_matched
                final_structure=[bool]$inputDiagnostics.final_structure
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
