# p1-chat-search-group probe — Win32/UIA 底座
# 代码一次性复制自 clients/association-client-cli/scripts/wechat-souyisou*.ps1 中已验证实现，
# 在 weixin-cli 内独立维护（设计文档 §2.1）。禁止反向 import 协会客户端文件。

$script:WeixinExpectedPathSuffix = 'Weixin.exe'
$script:WeixinTitle = [string]([char]0x5FAE) + [char]0x4FE1  # "微信"

function Initialize-WeixinProbeWin32 {
    [void](Add-Type -AssemblyName System.Windows.Forms)
    [void](Add-Type -AssemblyName System.Drawing)
    [void](Add-Type -AssemblyName UIAutomationClient)
    [void](Add-Type -AssemblyName UIAutomationTypes)
    [void](Add-Type -AssemblyName WindowsBase)
    if ('WeixinProbeWin32' -as [type]) { return }
    [void](Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class WeixinProbeWin32 {
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
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h,uint m,IntPtr w,IntPtr l);
 [DllImport("user32.dll")] public static extern bool ScreenToClient(IntPtr h,ref POINT p);
 [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr c);
 [DllImport("user32.dll")] public static extern IntPtr GetThreadDpiAwarenessContext();
 [DllImport("user32.dll")] public static extern bool AreDpiAwarenessContextsEqual(IntPtr a,IntPtr b);
 [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr h);
 [DllImport("user32.dll",SetLastError=true)] public static extern IntPtr SendMessageTimeout(IntPtr h,uint m,UIntPtr w,IntPtr l,uint f,uint t,out UIntPtr r);
 // SendInput：逐字输入 Unicode 字符（KEYEVENTF_UNICODE 不依赖 IME/键盘布局）
 [DllImport("user32.dll",SetLastError=true)] public static extern uint SendInput(uint n, INPUT[] p, int cb);
 public struct RECT { public int Left,Top,Right,Bottom; }
 public struct POINT { public int X, Y; }
 [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public INPUTUNION u; }
 [StructLayout(LayoutKind.Explicit)] public struct INPUTUNION { [FieldOffset(0)] public KEYBDINPUT ki; [FieldOffset(0)] public MOUSEINPUT mi; }
 [StructLayout(LayoutKind.Sequential)] public struct KEYBDINPUT { public ushort wVk; public ushort wScan; public uint dwFlags; public uint time; public IntPtr dwExtraInfo; }
 [StructLayout(LayoutKind.Sequential)] public struct MOUSEINPUT { public int dx; public int dy; public uint mouseData; public uint dwFlags; public uint time; public IntPtr dwExtraInfo; }
}
"@)
}

function Set-WeixinProbeDpiContext {
    # UIA BoundingRectangle 与物理坐标一致性：Per-Monitor V2（-4）
    $perMonitorV2 = [IntPtr](-4)
    $previous = [WeixinProbeWin32]::SetThreadDpiAwarenessContext($perMonitorV2)
    if ($previous -eq [IntPtr]::Zero -or
        -not [WeixinProbeWin32]::AreDpiAwarenessContextsEqual(
            [WeixinProbeWin32]::GetThreadDpiAwarenessContext(), $perMonitorV2)) {
        throw 'DPI_AWARENESS_FAILED'
    }
}

function Test-WeixinExecutablePath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    return $Path.Replace('/', '\').Trim().EndsWith(
        "\$script:WeixinExpectedPathSuffix", [StringComparison]::OrdinalIgnoreCase)
}

function Get-VisibleWindowList {
    $list = New-Object Collections.Generic.List[object]
    [WeixinProbeWin32]::EnumWindows({
        param($h, $unused)
        if ([WeixinProbeWin32]::IsWindowVisible($h)) {
            $windowProcessId = [uint32]0
            [void][WeixinProbeWin32]::GetWindowThreadProcessId($h, [ref]$windowProcessId)
            try {
                $p = Get-Process -Id $windowProcessId -ErrorAction Stop
                $list.Add([pscustomobject]@{
                    Hwnd = $h.ToInt64(); MainWindowHwnd = $p.MainWindowHandle.ToInt64()
                    Visible = $true; ProcessPath = $p.Path
                })
            } catch {}
        }
        return $true
    }, [IntPtr]::Zero) | Out-Null
    $list.ToArray()
}

function Select-WeixinMainWindow {
    param([Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Candidates)
    $matches = @($Candidates | Where-Object {
        $_.Visible -eq $true -and [int64]$_.Hwnd -ne 0 -and
        [int64]$_.MainWindowHwnd -eq [int64]$_.Hwnd -and
        (Test-WeixinExecutablePath ([string]$_.ProcessPath))
    })
    if ($matches.Count -eq 0) { throw 'WX_WINDOW_NOT_FOUND' }
    if ($matches.Count -ne 1) { throw 'WX_WINDOW_AMBIGUOUS' }
    $matches[0]
}

function Get-WindowIdentityByHwnd([int64]$Hwnd) {
    if (-not [WeixinProbeWin32]::IsWindow([IntPtr]$Hwnd)) { return $null }
    $currentPid = [uint32]0
    [void][WeixinProbeWin32]::GetWindowThreadProcessId([IntPtr]$Hwnd, [ref]$currentPid)
    try { $currentProcess = Get-Process -Id $currentPid -ErrorAction Stop } catch { return $null }
    $currentClass = New-Object Text.StringBuilder 256
    $currentTitle = New-Object Text.StringBuilder 256
    [void][WeixinProbeWin32]::GetClassName([IntPtr]$Hwnd, $currentClass, $currentClass.Capacity)
    [void][WeixinProbeWin32]::GetWindowText([IntPtr]$Hwnd, $currentTitle, $currentTitle.Capacity)
    [pscustomobject]@{
        Hwnd = $Hwnd; ProcessPath = $currentProcess.Path
        ProcessId = [uint32]$currentPid
        ClassName = $currentClass.ToString(); Title = $currentTitle.ToString()
    }
}

function Test-WeixinMainIdentity {
    param([AllowNull()][object]$Identity, [int64]$MainHwnd)
    return $null -ne $Identity -and
        [int64]$Identity.Hwnd -eq $MainHwnd -and
        (Test-WeixinExecutablePath ([string]$Identity.ProcessPath)) -and
        [string]$Identity.ClassName -eq 'Qt51514QWindowIcon' -and
        [string]$Identity.Title -eq $script:WeixinTitle
}

function Invoke-WeixinActivation {
    param([int64]$Hwnd)
    [void][WeixinProbeWin32]::ShowWindow([IntPtr]$Hwnd, 9)
    for ($attempt = 0; $attempt -lt 3; $attempt++) {
        $attached = @()
        $current = [uint32][WeixinProbeWin32]::GetCurrentThreadId()
        $targetPid = [uint32]0
        $target = [WeixinProbeWin32]::GetWindowThreadProcessId([IntPtr]$Hwnd, [ref]$targetPid)
        $foregroundHwnd = [WeixinProbeWin32]::GetForegroundWindow()
        $foregroundPid = [uint32]0
        $foreground = if ($foregroundHwnd -ne [IntPtr]::Zero) {
            [WeixinProbeWin32]::GetWindowThreadProcessId($foregroundHwnd, [ref]$foregroundPid)
        } else { 0 }
        try {
            foreach ($thread in @($foreground, $target)) {
                if ($thread -and $thread -ne $current -and $attached -notcontains $thread -and
                    [WeixinProbeWin32]::AttachThreadInput($current, $thread, $true)) { $attached += $thread }
            }
            [void][WeixinProbeWin32]::BringWindowToTop([IntPtr]$Hwnd)
            [void][WeixinProbeWin32]::SetActiveWindow([IntPtr]$Hwnd)
            [void][WeixinProbeWin32]::SetForegroundWindow([IntPtr]$Hwnd)
        } finally {
            [array]::Reverse($attached)
            foreach ($thread in $attached) { [void][WeixinProbeWin32]::AttachThreadInput($current, $thread, $false) }
        }
        Start-Sleep -Milliseconds 150
        if ([WeixinProbeWin32]::GetForegroundWindow().ToInt64() -eq $Hwnd) { return $true }
    }
    return $false
}

function Send-WeixinKeyChord {
    # 安全组合键：按下前查前台守卫，异常路径逐键 KeyUp
    param([Parameter(Mandatory)][string[]]$Keys, [Parameter(Mandatory)][scriptblock]$ForegroundGuard)
    $virtualKeyMap = @{ CTRL = 0x11; ALT = 0x12; F = 0x46; V = 0x56; A = 0x41; DEL = 0x2E; ESC = 0x1B; ENTER = 0x0D; DOWN = 0x28 }
    $failure = $null
    $pressedKeys = @()
    try {
        if (-not (& $ForegroundGuard)) { throw 'FOREGROUND_LOST' }
        foreach ($key in $Keys) {
            $pressedKeys += $key
            [WeixinProbeWin32]::keybd_event($virtualKeyMap[$key], 0, 0, [IntPtr]::Zero)
        }
        Start-Sleep -Milliseconds 40
    } catch { $failure = $_ } finally {
        for ($i = $pressedKeys.Count - 1; $i -ge 0; $i--) {
            [WeixinProbeWin32]::keybd_event($virtualKeyMap[$pressedKeys[$i]], 0, 2, [IntPtr]::Zero)
        }
    }
    if ($failure) { throw $failure }
}

function Send-WeixinUnicodeChar {
    # 逐字输入单个 Unicode 字符（KEYEVENTF_UNICODE，不经过 IME）
    # 实测：微信 4.x Qt 搜索框不接收该方式注入的字符（输入被丢弃），
    # 中文逐字输入改用 Send-WeixinPasteText（剪贴板逐字粘贴）。
    param([Parameter(Mandatory)][char]$Char, [Parameter(Mandatory)][scriptblock]$ForegroundGuard)
    if (-not (& $ForegroundGuard)) { throw 'FOREGROUND_LOST' }
    $scan = [uint16][int]$Char
    $down = New-Object WeixinProbeWin32+INPUT
    $down.type = 1  # INPUT_KEYBOARD
    $down.u.ki.wVk = 0
    $down.u.ki.wScan = $scan
    $down.u.ki.dwFlags = 0x0004  # KEYEVENTF_UNICODE
    $down.u.ki.time = 0
    $down.u.ki.dwExtraInfo = [IntPtr]::Zero
    $up = New-Object WeixinProbeWin32+INPUT
    $up.type = 1
    $up.u.ki.wVk = 0
    $up.u.ki.wScan = $scan
    $up.u.ki.dwFlags = 0x0004 -bor 0x0002  # KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
    $up.u.ki.time = 0
    $up.u.ki.dwExtraInfo = [IntPtr]::Zero
    $sent = [WeixinProbeWin32]::SendInput(2, @($down, $up), [Runtime.InteropServices.Marshal]::SizeOf([type][WeixinProbeWin32+INPUT]))
    if ($sent -ne 2) { throw "UNICODE_INPUT_FAILED:$sent" }
}

function Get-ClipboardTextSafe {
    # 仅保存/恢复文本类剪贴板内容；非文本（图片/文件）不碰
    try {
        if ([Windows.Forms.Clipboard]::ContainsText()) {
            return [Windows.Forms.Clipboard]::GetText()
        }
    } catch {}
    return $null
}

function Set-ClipboardTextRetry {
    param([Parameter(Mandatory)][string]$Text)
    for ($attempt = 0; $attempt -lt 5; $attempt++) {
        try {
            [Windows.Forms.Clipboard]::SetText($Text)
            return
        } catch {
            Start-Sleep -Milliseconds 80
        }
    }
    throw 'CLIPBOARD_SET_FAILED'
}

function Send-WeixinPasteText {
    # 通过剪贴板粘贴输入一段文本（用于 Qt 搜索框逐字输入：每次只放一个字符再 Ctrl+V）
    param([Parameter(Mandatory)][string]$Text, [Parameter(Mandatory)][scriptblock]$ForegroundGuard)
    Set-ClipboardTextRetry $Text
    Send-WeixinKeyChord @('CTRL', 'V') $ForegroundGuard
}

function Send-WeixinPostMessageClick {
    # 光标无关的点击：ScreenToClient 换算后直接 PostMessage 投递 WM_MOUSEMOVE/LBUTTONDOWN/UP。
    # 2026-08-26 真机实测结论：RDP 会话中客户端指针位置是权威值，脚本 SetCursorPos 移动
    # 会话光标后 ~50ms 内会被 RDP 拉回客户端停放位置，SetCursorPos+mouse_event 的 down/up
    # 会被拆到不同窗口（点击落空、误点其他窗口、前台被抢）。PostMessage 不经过系统光标，
    # 免疫该问题，且不要求目标窗口在前台。已在微信 4.x 会话列表点击进会话、输入框聚焦
    # 上验证有效。RDP/远程会话下优先用本函数，不要用 SetCursorPos+mouse_event。
    param(
        [Parameter(Mandatory)][int64]$Hwnd,
        [Parameter(Mandatory)][int]$ScreenX,   # 物理屏幕坐标（Per-Monitor V2 下与截图像素一致）
        [Parameter(Mandatory)][int]$ScreenY
    )
    $pt = New-Object WeixinProbeWin32+POINT
    $pt.X = $ScreenX; $pt.Y = $ScreenY
    if (-not [WeixinProbeWin32]::ScreenToClient([IntPtr]$Hwnd, [ref]$pt)) { throw 'SCREEN_TO_CLIENT_FAILED' }
    $lp = [IntPtr](($pt.Y -shl 16) -bor ($pt.X -band 0xFFFF))
    [void][WeixinProbeWin32]::PostMessage([IntPtr]$Hwnd, 0x0200, [IntPtr]::Zero, $lp)  # WM_MOUSEMOVE
    Start-Sleep -Milliseconds 60
    [void][WeixinProbeWin32]::PostMessage([IntPtr]$Hwnd, 0x0201, [IntPtr]1, $lp)       # WM_LBUTTONDOWN (MK_LBUTTON)
    Start-Sleep -Milliseconds 60
    [void][WeixinProbeWin32]::PostMessage([IntPtr]$Hwnd, 0x0202, [IntPtr]::Zero, $lp)  # WM_LBUTTONUP
}

function Get-WeixinSearchOverlayHwnd {
    # Ctrl+F 搜索结果面板是独立顶层窗口（Qt51514QWindowToolSaveBits），结果项在它上面，
    # PostMessage 点击必须投递给该窗口；投给主窗口会被当作"点击面板外"而关闭面板。
    # 返回 [int64] hwnd，未找到返回 0。
    foreach ($w in @(Get-VisibleWindowList)) {
        $id = Get-WindowIdentityByHwnd ([int64]$w.Hwnd)
        if ($null -ne $id -and [string]$id.ClassName -eq 'Qt51514QWindowToolSaveBits' -and
            (Test-WeixinExecutablePath ([string]$id.ProcessPath))) {
            return [int64]$id.Hwnd
        }
    }
    return [int64]0
}

function Get-WeixinUiaDump {
    # 导出指定窗口 UIA 子树摘要（P0 观察用）。full 输出写本地 %TEMP%（不进仓库）。
    param(
        [Parameter(Mandatory)][int64]$Hwnd,
        [ValidateRange(1, 12000)][int]$MaxNodes = 8000
    )
    $root = [Windows.Automation.AutomationElement]::FromHandle([IntPtr]$Hwnd)
    if ($null -eq $root) { throw 'UIA_ROOT_UNAVAILABLE' }
    $nodes = $root.FindAll(
        [Windows.Automation.TreeScope]::Descendants,
        [Windows.Automation.Condition]::TrueCondition)
    $items = New-Object Collections.Generic.List[object]
    $count = 0
    foreach ($node in $nodes) {
        if ($count -ge $MaxNodes) { break }
        $count++
        try {
            $cur = $node.Current
            $b = $cur.BoundingRectangle
            $hasInvoke = $node.TryGetCurrentPattern(
                [Windows.Automation.InvokePattern]::Pattern, [ref]$null)
            $hasValue = $node.TryGetCurrentPattern(
                [Windows.Automation.ValuePattern]::Pattern, [ref]$null)
            $items.Add([pscustomobject]@{
                control_type = [string]$cur.ControlType.ProgrammaticName.Replace('ControlType.', '')
                name = [string]$cur.Name
                automation_id = [string]$cur.AutomationId
                class_name = [string]$cur.ClassName
                left = [double]$b.Left; top = [double]$b.Top
                width = [double]$b.Width; height = [double]$b.Height
                is_offscreen = [bool]$cur.IsOffscreen
                has_invoke = $hasInvoke; has_value = $hasValue
            })
        } catch { continue }
    }
    $items.ToArray()
}
