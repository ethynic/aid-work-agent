# drivers/ps1/_common.ps1 — wecom-cli 驱动公共底座（M1）
# 产品化自 .tmp/wecom-probe/ 真机验证探测脚本（2026-08-29，企业微信 5.0.9 实测）：
#   - 主窗口：进程 WXWork.exe、class WeWorkWindow、标题「企业微信」，可见且面积最大者
#   - 内容子窗口：EnumChildWindows，class 前缀「WXworkWindow - 企业微信-」，类名编码页名
#   - 点击：PostMessage WM_MOUSEMOVE+LBUTTONDOWN/UP 投递到坐标归属 HWND（WindowFromPoint 路由）
#   - 截图：PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT=2)，9 点采样全黑回退 CopyFromScreen
#   - 文本输入：无修饰 ASCII 走纯 WM_KEYDOWN+WM_KEYUP（VkKeyScanW+MapVirtualKeyW）；
#     需 Shift/Ctrl/Alt 修饰的字符与中文等无 VK 字符一律只发 WM_CHAR（见 Send-WeComText 注释）
#   - 注入输入（SendInput/keybd_event，键盘与鼠标）均被企微 5.0.9 完全丢弃（2026-08-30 真机复核，
#     覆盖早期"SendInput 鼠标可用"的误判），本底座只提供 PostMessage 原语，刻意不提供任何 SendInput 原语
# 约定：业务失败一律 Throw-DriverError（DRIVER_JSON ok=false + 退出码 0）；
#   只有未预期崩溃才非零退出（TS 侧映射 INTERNAL_ERROR）。
# 坐标一律从窗口 rect 实时计算（比例坐标），禁止硬编码绝对屏幕坐标。
# 注意：本文件含中文，必须以 UTF-8 with BOM 保存（Windows PowerShell 5.1 否则按 GBK 解析报错）。

$script:DriverPs1Dir = Split-Path -Parent $MyInvocation.MyCommand.Path

# ---------- Win32 原语（单类承载全部 P/Invoke；Add-Type 幂等） ----------

function Initialize-WeComWin32 {
    if ([type]::GetType('WeComWin32') -ne $null) { return }
    Add-Type -AssemblyName System.Drawing
    Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public class WeComWin32 {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode)] public static extern uint GetShortPathNameW(string path, StringBuilder buffer, uint length);
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr hWndParent, EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassNameW(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hwnd, IntPtr hdcBlt, uint nFlags);
    [DllImport("user32.dll")] public static extern bool PostMessageW(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool ScreenToClient(IntPtr hWnd, ref POINT p);
    [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT p);
    [DllImport("user32.dll")] public static extern short VkKeyScanW(char ch);
    [DllImport("user32.dll")] public static extern uint MapVirtualKeyW(uint code, uint mapType);
    [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr v);
    [DllImport("user32.dll")] public static extern uint GetClipboardSequenceNumber();
    [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern IntPtr GetClipboardOwner();
    [DllImport("user32.dll")] static extern bool OpenClipboard(IntPtr owner);
    [DllImport("user32.dll")] static extern bool CloseClipboard();
    [DllImport("user32.dll")] static extern uint EnumClipboardFormats(uint format);
    [DllImport("user32.dll")] static extern IntPtr GetClipboardData(uint format);
    [DllImport("user32.dll")] static extern bool EmptyClipboard();
    [DllImport("user32.dll")] static extern IntPtr SetClipboardData(uint format, IntPtr data);
    [DllImport("kernel32.dll")] static extern IntPtr GlobalAlloc(uint flags, UIntPtr bytes);
    [DllImport("kernel32.dll")] static extern IntPtr GlobalLock(IntPtr memory);
    [DllImport("kernel32.dll")] static extern bool GlobalUnlock(IntPtr memory);
    [DllImport("kernel32.dll")] static extern UIntPtr GlobalSize(IntPtr memory);
    [DllImport("kernel32.dll")] static extern IntPtr GlobalFree(IntPtr memory);
    [DllImport("edputil.dll")] static extern int EdpGetEnterpriseIdForClipboard(out IntPtr enterpriseId);
    [DllImport("kernel32.dll")] static extern IntPtr GetProcessHeap();
    [DllImport("kernel32.dll")] static extern bool HeapFree(IntPtr heap, uint flags, IntPtr memory);
    public static bool IsPersonalClipboard() {
        IntPtr enterpriseId = IntPtr.Zero;
        try {
            return EdpGetEnterpriseIdForClipboard(out enterpriseId) == 0 && enterpriseId == IntPtr.Zero;
        } catch (DllNotFoundException) { return false; }
        catch (EntryPointNotFoundException) { return false; }
        finally {
            if (enterpriseId != IntPtr.Zero && !HeapFree(GetProcessHeap(), 0, enterpriseId))
                throw new InvalidOperationException("CLIPBOARD_PROTECTION_CHECK_FAILED");
        }
    }
    static IntPtr AllocateClipboardText(string text) {
        byte[] bytes = Encoding.Unicode.GetBytes(text + "\0");
        IntPtr memory = GlobalAlloc(2, (UIntPtr)bytes.Length);
        if (memory == IntPtr.Zero) throw new InvalidOperationException("CLIPBOARD_ALLOC_FAILED");
        try {
            IntPtr address = GlobalLock(memory);
            if (address == IntPtr.Zero) throw new InvalidOperationException("CLIPBOARD_LOCK_FAILED");
            try { Marshal.Copy(bytes, 0, address, bytes.Length); }
            finally { GlobalUnlock(memory); }
            return memory;
        } catch { GlobalFree(memory); throw; }
    }
    public static bool ExchangeClipboardText(IntPtr owner, uint expectedSequence, string expectedText,
            bool expectedEmpty, string nextText, bool nextEmpty, out uint writtenSequence) {
        writtenSequence = expectedSequence;
        IntPtr memory = IntPtr.Zero;
        IntPtr rollback = IntPtr.Zero;
        bool opened = false;
        try {
            // Allocate before taking the short clipboard lock. No delayed rendering or OLE call under it.
            if (!nextEmpty) {
                memory = AllocateClipboardText(nextText);
                if (!expectedEmpty) rollback = AllocateClipboardText(expectedText);
            }
            if (!OpenClipboard(owner)) return false;
            opened = true;
            if (GetClipboardSequenceNumber() != expectedSequence) return false;
            if (expectedEmpty) {
                if (EnumClipboardFormats(0) != 0) return false;
            } else {
                IntPtr previous = GetClipboardData(13); // CF_UNICODETEXT
                if (previous == IntPtr.Zero || GlobalSize(previous).ToUInt64() < (ulong)expectedText.Length * 2 + 2)
                    return false;
                IntPtr address = GlobalLock(previous);
                if (address == IntPtr.Zero) return false;
                try {
                    if (!String.Equals(Marshal.PtrToStringUni(address, expectedText.Length), expectedText,
                            StringComparison.Ordinal) || Marshal.ReadInt16(address, checked(expectedText.Length * 2)) != 0)
                        return false;
                } finally { GlobalUnlock(previous); }
            }
            // Compare and replace share one OpenClipboard lifetime: external writes cannot be overwritten.
            if (!EmptyClipboard()) throw new InvalidOperationException("CLIPBOARD_WRITE_FAILED");
            if (!nextEmpty) {
                if (SetClipboardData(13, memory) == IntPtr.Zero) {
                    // One bounded recovery under the same lock; no external write can be overwritten.
                    // Failure still escapes: even this recovery may fail during an OS clipboard fault.
                    if (rollback != IntPtr.Zero && SetClipboardData(13, rollback) != IntPtr.Zero)
                        rollback = IntPtr.Zero;
                    throw new InvalidOperationException("CLIPBOARD_WRITE_FAILED");
                }
                memory = IntPtr.Zero; // OS now owns this HGLOBAL.
            }
            writtenSequence = GetClipboardSequenceNumber();
            return true;
        } finally {
            bool closed = !opened || CloseClipboard();
            if (memory != IntPtr.Zero) GlobalFree(memory);
            if (rollback != IntPtr.Zero) GlobalFree(rollback);
            if (!closed) throw new InvalidOperationException("CLIPBOARD_CLOSE_FAILED");
        }
    }
    public struct RECT { public int Left, Top, Right, Bottom; }
    public struct POINT { public int X, Y; }
}
'@
}

function Set-WeComDpiContext {
    # PER_MONITOR_AWARE_V2(-4)：保证 GetWindowRect/WindowFromPoint/ScreenToClient/PrintWindow
    # 坐标与截图统一为物理像素，避免多屏/缩放场景坐标系混杂
    if (-not [WeComWin32]::SetProcessDpiAwarenessContext([IntPtr](-4))) {
        throw 'DPI_AWARENESS_FAILED'
    }
}

# ---------- DRIVER_JSON 协议 ----------

function Write-DriverJson([object]$Payload) {
    $json = ConvertTo-Json -InputObject $Payload -Compress -Depth 10
    Write-Output ("DRIVER_JSON: " + $json)
}

function Throw-DriverError([string]$Code, [string]$Message) {
    # 驱动内统一的可识别错误标记，由 Invoke-DriverMain 解析为 DRIVER_JSON ok=false
    throw ("WECOMDRIVE|" + $Code + "|" + $Message)
}

# ---------- 窗口枚举 ----------

function Get-WeComWindowInfo([IntPtr]$Hwnd) {
    $sbT = New-Object System.Text.StringBuilder 512
    $sbC = New-Object System.Text.StringBuilder 256
    [void][WeComWin32]::GetWindowTextW($Hwnd, $sbT, 512)
    [void][WeComWin32]::GetClassNameW($Hwnd, $sbC, 256)
    $pidOut = 0
    [void][WeComWin32]::GetWindowThreadProcessId($Hwnd, [ref]$pidOut)
    $r = New-Object WeComWin32+RECT
    [void][WeComWin32]::GetWindowRect($Hwnd, [ref]$r)
    return [pscustomobject]@{
        Hwnd = $Hwnd.ToInt64()
        Pid = [int64]$pidOut
        Visible = [WeComWin32]::IsWindowVisible($Hwnd)
        Class = $sbC.ToString()
        Title = $sbT.ToString()
        X = $r.Left; Y = $r.Top
        W = ($r.Right - $r.Left); H = ($r.Bottom - $r.Top)
    }
}

function Get-WeComTopLevelWindows {
    # 枚举 WXWork.exe 进程拥有的全部顶层窗口（含不可见；WeWorkWindow 托盘隐藏态 vis=false）
    Initialize-WeComWin32
    $pids = @(Get-Process -Name 'WXWork' -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
    $wins = New-Object System.Collections.ArrayList
    $cb = [WeComWin32+EnumWindowsProc]{
        param($hWnd, $lParam)
        $pidOut = 0
        [void][WeComWin32]::GetWindowThreadProcessId($hWnd, [ref]$pidOut)
        if ($pids -contains $pidOut) {
            [void]$wins.Add((Get-WeComWindowInfo $hWnd))
        }
        return $true
    }
    [void][WeComWin32]::EnumWindows($cb, [IntPtr]::Zero)
    return @($wins)
}

function Find-WeComLoginWindow {
    # 登录二维码页（2026-09-28 真机实测修订：企微 5.0.9 登录窗 class=WeChatLogin，
    # 实测 300x420 @ 标题「企业微信」——旧启发式只认小尺寸 WeWorkWindow 会漏检报 offline）。
    # 双类名匹配：WeChatLogin（新版实测）或小尺寸 WeWorkWindow（RPA 时代形态，向后兼容）。
    foreach ($w in @(Get-WeComTopLevelWindows)) {
        if (-not $w.Visible) { continue }
        if ($w.Class -eq 'WeChatLogin' -and $w.W -gt 0) { return $w }
        if ($w.Class -eq 'WeWorkWindow' -and $w.W -gt 0 -and $w.W -lt 500 -and $w.H -lt 700) { return $w }
    }
    return $null
}

function Resolve-WeComMainWindow {
    # 可见 + class WeWorkWindow + 标题「企业微信」+ 主界面尺寸（>=600x400），取面积最大者；
    # 面积并列最大 → WINDOW_AMBIGUOUS；只有登录小窗 → NOT_LOGGED_IN；无任何 WeWorkWindow → WECOM_NOT_FOUND。
    # 返回 int64 hwnd（hwnd 动态，调用方每次操作前重新解析）。
    $all = @(Get-WeComTopLevelWindows)
    $candidates = @($all | Where-Object {
        $_.Class -eq 'WeWorkWindow' -and $_.Visible -and $_.Title -eq '企业微信' -and $_.W -ge 600 -and $_.H -ge 400
    })
    if ($candidates.Count -eq 0) {
        if ($null -ne (Find-WeComLoginWindow)) {
            Throw-DriverError 'NOT_LOGGED_IN' '检测到企业微信登录二维码窗口（未登录），请先扫码登录后重试'
        }
        $hidden = @($all | Where-Object {
            $_.Class -eq 'WeWorkWindow' -and -not $_.Visible -and $_.Title -eq '企业微信' -and $_.W -ge 600 -and $_.H -ge 400
        } | Sort-Object { $_.W * $_.H } -Descending)
        if ($hidden.Count -gt 1 -and ($hidden[0].W * $hidden[0].H) -eq ($hidden[1].W * $hidden[1].H)) {
            Throw-DriverError 'WINDOW_AMBIGUOUS' '多个隐藏企业微信主窗口，无法确认当前窗口'
        }
        if ($hidden.Count -gt 0 -and ($hidden.Count -eq 1 -or ($hidden[0].W * $hidden[0].H) -ne ($hidden[1].W * $hidden[1].H))) {
            [void][WeComWin32]::ShowWindowAsync([IntPtr]$hidden[0].Hwnd, 9)
            Start-Sleep -Milliseconds 200
            $candidates = @(Get-WeComTopLevelWindows | Where-Object { $_.Hwnd -eq $hidden[0].Hwnd -and $_.Visible })
        }
    }
    if ($candidates.Count -eq 0) {
        Throw-DriverError 'WECOM_NOT_FOUND' '未找到企业微信主窗口（WXWork.exe 未运行、主窗口不可见或已退到托盘；请先打开并登录企业微信）'
    }
    $sorted = @($candidates | Sort-Object { $_.W * $_.H } -Descending)
    if ($sorted.Count -gt 1 -and ($sorted[0].W * $sorted[0].H) -eq ($sorted[1].W * $sorted[1].H)) {
        Throw-DriverError 'WINDOW_AMBIGUOUS' ('找到 ' + $sorted.Count + ' 个面积相同的企业微信主窗口候选，无法唯一确定')
    }
    return [int64]$sorted[0].Hwnd
}

function Get-WeComContentChildWindow {
    # 内容子窗口：主窗口下 class 前缀「WXworkWindow - 企业微信-」的子 HWND（每个功能页一个，切换时 hide/show）。
    # -PageName 过滤精确页名（如 '通讯录'）；返回可见匹配项数组（类名编码页名，可做状态校验）。
    param(
        [Parameter(Mandatory)][int64]$MainHwnd,
        [string]$PageName
    )
    Initialize-WeComWin32
    $prefix = 'WXworkWindow - 企业微信-'
    $found = New-Object System.Collections.ArrayList
    $cb = [WeComWin32+EnumWindowsProc]{
        param($hWnd, $lParam)
        $info = Get-WeComWindowInfo $hWnd
        if ($info.Class.StartsWith($prefix)) {
            $page = $info.Class.Substring($prefix.Length)
            if ([string]::IsNullOrEmpty($PageName) -or $page -eq $PageName) {
                [void]$found.Add($info)
            }
        }
        return $true
    }
    [void][WeComWin32]::EnumChildWindows([IntPtr]$MainHwnd, $cb, [IntPtr]::Zero)
    return @($found)
}

function Wait-WeComWindow {
    # 按顶层窗口 class（精确）轮询等待出现（弹窗发现：SearchExternalsWnd / InputReasonWnd）。
    # 返回 int64 hwnd；超时返回 0（调用方决定错误码）。
    param(
        [Parameter(Mandatory)][string]$ClassName,
        [int]$TimeoutMs = 10000,
        [int]$IntervalMs = 250
    )
    Initialize-WeComWin32
    $deadline = [DateTime]::UtcNow.AddMilliseconds($TimeoutMs)
    while ([DateTime]::UtcNow -lt $deadline) {
        $hit = New-Object System.Collections.ArrayList
        $cb = [WeComWin32+EnumWindowsProc]{
            param($hWnd, $lParam)
            $sb = New-Object System.Text.StringBuilder 256
            [void][WeComWin32]::GetClassNameW($hWnd, $sb, 256)
            if ($sb.ToString() -eq $ClassName -and [WeComWin32]::IsWindowVisible($hWnd)) {
                [void]$hit.Add($hWnd.ToInt64())
            }
            return $true
        }
        [void][WeComWin32]::EnumWindows($cb, [IntPtr]::Zero)
        if ($hit.Count -gt 0) { return [int64]$hit[0] }
        Start-Sleep -Milliseconds $IntervalMs
    }
    return [int64]0
}

# ---------- 截图 ----------

function Get-WeComWindowSnapshot {
    # PrintWindow(PW_RENDERFULLCONTENT=2)：前台/后台/被遮挡/最小化均出完整画面。
    # 9 点采样全黑（个别渲染路径 PrintWindow 出黑图）回退 CopyFromScreen。
    # 返回 @(left, top, w, h)（窗口 rect，用于图像坐标 → 屏幕坐标换算）。
    param(
        [Parameter(Mandatory)][int64]$Hwnd,
        [Parameter(Mandatory)][string]$Path
    )
    Initialize-WeComWin32
    $hwnd = [IntPtr]$Hwnd
    if (-not [WeComWin32]::IsWindow($hwnd)) { Throw-DriverError 'UI_CHANGED' ('截图目标窗口已不存在（hwnd=' + $Hwnd + '）') }
    $r = New-Object WeComWin32+RECT
    [void][WeComWin32]::GetWindowRect($hwnd, [ref]$r)
    $w = $r.Right - $r.Left; $h = $r.Bottom - $r.Top
    if ($w -le 0 -or $h -le 0) { Throw-DriverError 'UI_CHANGED' ('截图目标窗口尺寸异常（' + $w + 'x' + $h + '）') }

    $bmp = New-Object System.Drawing.Bitmap $w, $h
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $hdc = $g.GetHdc()
    [void][WeComWin32]::PrintWindow($hwnd, $hdc, 2)
    $g.ReleaseHdc($hdc)
    $g.Dispose()

    $black = 0
    foreach ($fx in @(0.1, 0.5, 0.9)) { foreach ($fy in @(0.1, 0.5, 0.9)) {
        $px = $bmp.GetPixel([int]($w * $fx), [int]($h * $fy))
        if ($px.R -lt 8 -and $px.G -lt 8 -and $px.B -lt 8) { $black++ }
    }}
    if ($black -eq 9) {
        $bmp.Dispose()
        $bmp = New-Object System.Drawing.Bitmap $w, $h
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($r.Left, $r.Top, 0, 0, (New-Object System.Drawing.Size $w, $h))
        $g.Dispose()
    }
    $bmp.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    return @([int]$r.Left, [int]$r.Top, [int]$w, [int]$h)
}

# ---------- 输入原语 ----------

function Send-WeComClick {
    # PostMessage 点击（WM_MOUSEMOVE+LBUTTONDOWN/UP），不移动真实鼠标。
    # 未显式给 -Hwnd 时按 WindowFromPoint 路由到坐标归属 HWND（导航栏→主窗口，内容区→子窗口，弹窗→弹窗）。
    param(
        [Parameter(Mandatory)][int]$ScreenX,
        [Parameter(Mandatory)][int]$ScreenY,
        [int64]$Hwnd = 0
    )
    Initialize-WeComWin32
    $target = [IntPtr]$Hwnd
    if ($Hwnd -eq 0) {
        $pt = New-Object WeComWin32+POINT
        $pt.X = $ScreenX; $pt.Y = $ScreenY
        $target = [WeComWin32]::WindowFromPoint($pt)
        if ($target -eq [IntPtr]::Zero) { Throw-DriverError 'UI_CHANGED' ("点击坐标（$ScreenX,$ScreenY）下没有任何窗口（WindowFromPoint 落空）") }
    }
    if (-not [WeComWin32]::IsWindow($target)) { Throw-DriverError 'UI_CHANGED' '点击目标窗口已不存在' }
    $p = New-Object WeComWin32+POINT
    $p.X = $ScreenX; $p.Y = $ScreenY
    [void][WeComWin32]::ScreenToClient($target, [ref]$p)
    $lparam = [IntPtr](($p.Y -shl 16) -bor ($p.X -band 0xFFFF))
    [void][WeComWin32]::PostMessageW($target, 0x0200, [IntPtr]::Zero, $lparam)
    Start-Sleep -Milliseconds 60
    [void][WeComWin32]::PostMessageW($target, 0x0201, [IntPtr]1, $lparam)
    Start-Sleep -Milliseconds 60
    [void][WeComWin32]::PostMessageW($target, 0x0202, [IntPtr]::Zero, $lparam)
    return [int64]$target
}

function Send-WeComText {
    # 逐字输入（2026-08-31 真机实测修订）：
    # - 无修饰键的 ASCII（小写字母/数字/无修饰符号）：纯 WM_KEYDOWN+WM_KEYUP
    #   （VkKeyScanW+MapVirtualKeyW 带 scan code），禁止同发 WM_CHAR（双倍字符）。
    # - VkKeyScanW 高位含 Shift/Ctrl/Alt 修饰的字符（大写字母、冒号等），以及
    #   无虚拟键映射的字符（VkKeyScanW 返回 -1，中文等）：一律只发 WM_CHAR，
    #   不发 keydown/keyup。原因：Qt 从真实键盘状态读修饰键（与 Ctrl+V 变 v 同源），
    #   PostMessage 投递的 Shift 按下不被识别，大写 M 会落成小写 m（真机实测）；
    #   WM_CHAR 直接携带 Unicode 字符，绕开修饰键判定。修饰字符也不能
    #   keydown+WM_CHAR 同发：Qt 会把 keydown 也翻成字符导致双倍字符。
    param(
        [Parameter(Mandatory)][int64]$Hwnd,
        [Parameter(Mandatory)][string]$Text
    )
    Initialize-WeComWin32
    $target = [IntPtr]$Hwnd
    foreach ($ch in $Text.ToCharArray()) {
        $vk = [WeComWin32]::VkKeyScanW($ch)
        if ($vk -lt 0 -or ((($vk -shr 8) -band 0x07) -ne 0)) {
            # 中文等无虚拟键映射，或需 Shift/Ctrl/Alt 修饰：纯 WM_CHAR（不得再发 KEYDOWN，否则双倍字符）
            [void][WeComWin32]::PostMessageW($target, 0x0102, [IntPtr][int][char]$ch, [IntPtr]::Zero)
            Start-Sleep -Milliseconds 30
            continue
        }
        $v = [uint32]($vk -band 0xFF)
        $scan = [WeComWin32]::MapVirtualKeyW($v, 0)
        $lpDown = [IntPtr](1 -bor ($scan -shl 16))
        $lpUp = [IntPtr](1 -bor ($scan -shl 16) -bor 0xC0000000L)
        [void][WeComWin32]::PostMessageW($target, 0x0100, [IntPtr]$v, $lpDown)   # WM_KEYDOWN
        Start-Sleep -Milliseconds 20
        [void][WeComWin32]::PostMessageW($target, 0x0101, [IntPtr]$v, $lpUp)     # WM_KEYUP
        Start-Sleep -Milliseconds 30
    }
}

function Send-WeComEnter {
    # PostMessage Enter（VK_RETURN 0x0D，scan 0x1C）。企微内检索触发只能走这条路：
    # 注入键盘（SendInput/keybd_event）被企微 5.0.9 丢弃（真机实测）。
    param([Parameter(Mandatory)][int64]$Hwnd)
    Initialize-WeComWin32
    $target = [IntPtr]$Hwnd
    $scan = [WeComWin32]::MapVirtualKeyW(0x0D, 0)
    $lpDown = [IntPtr](1 -bor ($scan -shl 16))
    $lpUp = [IntPtr](1 -bor ($scan -shl 16) -bor 0xC0000000L)
    [void][WeComWin32]::PostMessageW($target, 0x0100, [IntPtr]0x0D, $lpDown)
    Start-Sleep -Milliseconds 30
    [void][WeComWin32]::PostMessageW($target, 0x0101, [IntPtr]0x0D, $lpUp)
}

function Send-WeComEscape {
    # PostMessage ESC（VK_ESCAPE 0x1B，scan 0x01）：关闭搜索 overlay 用。
    param([Parameter(Mandatory)][int64]$Hwnd)
    Initialize-WeComWin32
    $target = [IntPtr]$Hwnd
    $scan = [WeComWin32]::MapVirtualKeyW(0x1B, 0)
    $lpDown = [IntPtr](1 -bor ($scan -shl 16))
    $lpUp = [IntPtr](1 -bor ($scan -shl 16) -bor 0xC0000000L)
    [void][WeComWin32]::PostMessageW($target, 0x0100, [IntPtr]0x1B, $lpDown)
    Start-Sleep -Milliseconds 30
    [void][WeComWin32]::PostMessageW($target, 0x0101, [IntPtr]0x1B, $lpUp)
}

# ---------- 非 OCR 搜索键盘、剪贴板及窗口助手 ----------

function Initialize-WeComAttachInput {
    # attachstate 所需 P/Invoke（AttachThreadInput/Get/SetKeyboardState/GetCurrentThreadId）；幂等
    if ([type]::GetType('WeComAttach32') -ne $null) { return }
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class WeComAttach32 {
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool attach);
    [DllImport("user32.dll")] public static extern bool GetKeyboardState(byte[] ks);
    [DllImport("user32.dll")] public static extern bool SetKeyboardState(byte[] ks);
}
'@
}

function Send-WeComAttachChordKey {
    # attachstate 组合键（Ctrl+Vk，全后台）：AttachThreadInput(本线程, 目标窗口线程) →
    # GetKeyboardState 存副本 → ks[0x11]=0x80（Ctrl 按下）→ SetKeyboardState → PostMessage
    # WM_KEYDOWN（lParam=1|(scan<<16)）→ 60ms → WM_KEYUP（lParam 再 |0xC0000000）→ finally
    # 恢复键状态 + detach。scan=MapVirtualKeyW(Vk,0)。
    # 返回 $true=按下/抬起消息均已投递；$false=attach 或任一投递失败。
    param(
        [Parameter(Mandatory)][int64]$Hwnd,
        [Parameter(Mandatory)][int]$Vk
    )
    Initialize-WeComWin32
    Initialize-WeComAttachInput
    $wxPid = 0
    $wxThread = [WeComAttach32]::GetWindowThreadProcessId([IntPtr]$Hwnd, [ref]$wxPid)
    $me = [WeComAttach32]::GetCurrentThreadId()
    if (-not [WeComAttach32]::AttachThreadInput($me, $wxThread, $true)) { return $false }
    $ks = New-Object 'byte[]' 256
    [void][WeComAttach32]::GetKeyboardState($ks)
    $saved = $ks.Clone()
    $ks[0x11] = 0x80
    [void][WeComAttach32]::SetKeyboardState($ks)
    try {
        $scan = [WeComWin32]::MapVirtualKeyW([uint32]$Vk, 0)
        $lpDown = [IntPtr](1 -bor ($scan -shl 16))
        $lpUp = [IntPtr](1 -bor ($scan -shl 16) -bor 0xC0000000L)
        $down = [WeComWin32]::PostMessageW([IntPtr]$Hwnd, 0x0100, [IntPtr]$Vk, $lpDown)
        Start-Sleep -Milliseconds 60
        $up = [WeComWin32]::PostMessageW([IntPtr]$Hwnd, 0x0101, [IntPtr]$Vk, $lpUp)
        Start-Sleep -Milliseconds 60
    } finally {
        [void][WeComAttach32]::SetKeyboardState($saved)
        [void][WeComAttach32]::AttachThreadInput($me, $wxThread, $false)
    }
    return ($down -and $up)
}

function Test-WeComSearchWindowUnchanged($Before, $After) {
    return ($Before.Pid -gt 0 -and $Before.Pid -eq $After.Pid -and $Before.Hwnd -eq $After.Hwnd -and
        $After.Class -eq 'WeWorkWindow' -and $After.Visible -and
        $Before.X -eq $After.X -and $Before.Y -eq $After.Y -and $Before.W -eq $After.W -and $Before.H -eq $After.H)
}

function Get-WeComPlainClipboard {
    Add-Type -AssemblyName System.Windows.Forms
    $data = [Windows.Forms.Clipboard]::GetDataObject()
    if ($null -eq $data -or @($data.GetFormats($false)).Count -eq 0) {
        # OLE can briefly report no data just after a native write, even at the same
        # clipboard sequence. Refresh once; callers still require stable provenance.
        Start-Sleep -Milliseconds 25
        $data = [Windows.Forms.Clipboard]::GetDataObject()
    }
    if ($null -eq $data) { return @{empty=$true;text=''} }
    $formats=@($data.GetFormats($false))
    if ($formats.Count -eq 0) { return @{empty=$true;text=''} }
    $extra=@($formats | Where-Object {
        $_ -notin @('UnicodeText','Text','OEMText','System.String','Locale')
    })
    if (-not [Windows.Forms.Clipboard]::ContainsText()) { throw 'CLIPBOARD_NOT_PLAIN_TEXT' }
    if ($extra.Count -gt 0) {
        # A system EDP marker alone does not prove enterprise ownership. Ask Windows;
        # never discard a real identity, unknown policy or other non-text formats.
        if ($extra.Count -ne 1 -or $extra[0] -ne 'EnterpriseDataProtectionId') { throw 'CLIPBOARD_NOT_PLAIN_TEXT' }
        Initialize-WeComWin32
        if (-not [WeComWin32]::IsPersonalClipboard()) { throw 'CLIPBOARD_NOT_PLAIN_TEXT' }
    }
    return @{empty=$false;text=[Windows.Forms.Clipboard]::GetText()}
}

function New-WeComClipboardOwner {
    Add-Type -AssemblyName System.Windows.Forms
    # An undisplayed owner HWND is required by EmptyClipboard/SetClipboardData; never Show/Focus it.
    return New-Object Windows.Forms.Control
}

function Test-WeComSearchClipboardEcho {
    param([int64]$MainHwnd, [string]$Query, [int]$TimeoutMs = 1500)
    $result = @{matched=$false;copied_length=$null;reason='clipboard_unavailable';restored=$false}
    $original=$null;$touched=$false;$copied=$null;$copySeq=$null;$sentinelSeq=$null;$window=$null;$copyRequested=$false
    $sentinel='AidWecomSearchEcho:' + [guid]::NewGuid().ToString('N')
    try {
        if ([Threading.Thread]::CurrentThread.ApartmentState -ne 'STA') { return $result }
        $before=Get-WeComWindowInfo ([IntPtr]$MainHwnd)
        $originalSeq=[WeComWin32]::GetClipboardSequenceNumber()
        $original=Get-WeComPlainClipboard
        $ownerPid=0
        [void][WeComWin32]::GetWindowThreadProcessId([IntPtr]$MainHwnd,[ref]$ownerPid)
        if ($ownerPid -le 0) { $result.reason='search_window_changed';return $result }
        $window=New-WeComClipboardOwner
        $lockedWriteStamp=[uint32]0
        $touched=$true
        if (-not [WeComWin32]::ExchangeClipboardText($window.Handle,$originalSeq,$original.text,
                $original.empty,$sentinel,$false,[ref]$lockedWriteStamp)) { $result.reason='clipboard_changed';return $result }
        # lockedWriteStamp is not a receipt: CloseClipboard may publish synthesized formats.
        # Certify the published unique nonce with a stable read and our exact hidden owner.
        $publishedSeq=[WeComWin32]::GetClipboardSequenceNumber()
        $check=Get-WeComPlainClipboard
        $publishedOwner=[WeComWin32]::GetClipboardOwner()
        $publishedAfter=[WeComWin32]::GetClipboardSequenceNumber()
        if ($check.empty -or $check.text -cne $sentinel -or
                $publishedOwner -ne $window.Handle -or $publishedSeq -ne $publishedAfter) { return $result }
        $sentinelSeq=$publishedSeq
        if (-not (Test-WeComSearchWindowUnchanged $before (Get-WeComWindowInfo ([IntPtr]$MainHwnd)))) { return $result }
        if (-not (Send-WeComAttachChordKey -Hwnd $MainHwnd -Vk 0x46)) {
            $result.reason='search_focus_failed';return $result
        }
        Start-Sleep -Milliseconds 150
        if (-not (Test-WeComSearchWindowUnchanged $before (Get-WeComWindowInfo ([IntPtr]$MainHwnd)))) {
            $result.reason='search_window_changed';return $result
        }
        if (-not (Send-WeComAttachChordKey -Hwnd $MainHwnd -Vk 0x41)) { return $result }
        Start-Sleep -Milliseconds 100
        $copyRequested=$true
        if (-not (Send-WeComAttachChordKey -Hwnd $MainHwnd -Vk 0x43)) { return $result }
        $deadline=[DateTime]::UtcNow.AddMilliseconds($TimeoutMs)
        while ([DateTime]::UtcNow -lt $deadline) {
            $seq=[WeComWin32]::GetClipboardSequenceNumber()
            if ($seq -ne $sentinelSeq) {
                $observed=Get-WeComPlainClipboard
                $copyPid=0
                [void][WeComWin32]::GetWindowThreadProcessId([WeComWin32]::GetClipboardOwner(),[ref]$copyPid)
                $copyAfter=[WeComWin32]::GetClipboardSequenceNumber()
                if ($copyPid -eq $ownerPid -and $observed.text -cne $sentinel -and $seq -eq $copyAfter) {
                    if ($observed.empty -or $observed.text.Length -eq 0) {
                        # An empty OLE snapshot cannot certify this nonempty search query.
                        $result.reason='clipboard_copy_read_failed'
                    } else {
                        $copied=$observed.text;$copySeq=$seq
                        $result.copied_length=$copied.Length
                        $result.matched=([string]::Equals($copied,$Query,[StringComparison]::Ordinal) -and
                            (Test-WeComSearchWindowUnchanged $before (Get-WeComWindowInfo ([IntPtr]$MainHwnd))))
                        $result.reason='copied'
                        break
                    }
                }
            }
            Start-Sleep -Milliseconds 75
        }
    } catch {
        $result.matched=$false
        $result.reason=if ($_.Exception.Message -match 'CLIPBOARD_WRITE_FAILED') { 'clipboard_write_failed' }
            elseif ($_.Exception.Message -match 'CLIPBOARD_CLOSE_FAILED') { 'clipboard_close_failed' }
            elseif ($copyRequested) { 'clipboard_copy_read_failed' } else { 'clipboard_unavailable' }
    }
    finally {
        if ($touched -and $null -ne $original) {
            try {
                $seq=[WeComWin32]::GetClipboardSequenceNumber();$current=Get-WeComPlainClipboard
                $currentOwner=[WeComWin32]::GetClipboardOwner()
                $stable=($seq -eq [WeComWin32]::GetClipboardSequenceNumber())
                # Even a failed receipt/close may leave our nonce. Retire it only with our
                # exact owner and a stable current snapshot; native CAS rechecks before writing.
                $nonceOwned=($null -ne $window -and -not $current.empty -and
                    $currentOwner -eq $window.Handle -and $current.text -ceq $sentinel)
                $ours=$stable -and ($nonceOwned -or
                    ($null -ne $copySeq -and $seq -eq $copySeq -and $current.text -ceq $copied))
                if ($ours) {
                    $lockedWriteStamp=[uint32]0
                    $result.restored=[WeComWin32]::ExchangeClipboardText($window.Handle,$seq,$current.text,
                        $current.empty,$original.text,$original.empty,[ref]$lockedWriteStamp)
                }
            } catch {
                $result.matched=$false;$result.restored=$false
                $result.reason=if ($_.Exception.Message -match 'CLIPBOARD_WRITE_FAILED') { 'clipboard_write_failed' }
                    elseif ($_.Exception.Message -match 'CLIPBOARD_CLOSE_FAILED') { 'clipboard_close_failed' } else { 'clipboard_restore_failed' }
            }
            if (-not $result.restored) {
                $result.matched=$false
                if ($result.reason -eq 'clipboard_copy_read_failed') {
                    # Copy may already have replaced the saved value, but no copy receipt was proved.
                    $result.reason='clipboard_restore_failed'
                } elseif ($result.reason -notin @('clipboard_write_failed','clipboard_close_failed','clipboard_restore_failed')) {
                    if ($stable -and $current.empty -and $null -ne $window -and $currentOwner -eq $window.Handle) {
                        $result.reason='clipboard_restore_failed'
                    } else { $result.reason='clipboard_changed' }
                }
            }
        }
        if ($null -ne $window) {
            try { $window.Dispose() } catch { $result.matched=$false;$result.reason='clipboard_close_failed' }
        }
    }
    return $result
}

function Focus-WeComSearchBox {
    # Ctrl+F 是搜索框聚焦原语；输入后的完整剪贴板回读验证实际目标。
    param([Parameter(Mandatory)][int64]$MainHwnd)
    if (-not (Send-WeComAttachChordKey -Hwnd $MainHwnd -Vk 0x46)) {
        Throw-DriverError 'UI_CHANGED' 'attachstate Ctrl+F 聚焦搜索框失败，已中止且未清除当前输入'
    }
    Start-Sleep -Milliseconds 150
    return 'shortcut'
}

# ---------- M6：发送 V2 共享助手（message-send 用；只增不改，M1-M5 函数不动） ----------

function Clear-WeComFocusedInput {
    # 清空当前聚焦的文本输入框内容（attachstate Ctrl+A 全选 + VK_DELETE 删除）。
    # message-send 确认目标并点击输入区后，用它替换既有内容；发送前标题变化时也清理
    # 本次已输入内容。调用方负责确认目标与焦点，不在此处做 OCR 草稿判定。
    # 返回 $true=全选与删除按键已投递；$false=任一按键投递失败。
    param([Parameter(Mandatory)][int64]$Hwnd)
    $ok = Send-WeComAttachChordKey -Hwnd $Hwnd -Vk 0x41
    if (-not $ok) { return $false }
    Start-Sleep -Milliseconds 150
    Initialize-WeComWin32
    $scan = [WeComWin32]::MapVirtualKeyW([uint32]0x2E, 0)
    $lpDown = [IntPtr](1 -bor ($scan -shl 16))
    $lpUp = [IntPtr](1 -bor ($scan -shl 16) -bor 0xC0000000L)
    $down = [WeComWin32]::PostMessageW([IntPtr]$Hwnd, 0x0100, [IntPtr]0x2E, $lpDown)
    Start-Sleep -Milliseconds 50
    $up = [WeComWin32]::PostMessageW([IntPtr]$Hwnd, 0x0101, [IntPtr]0x2E, $lpUp)
    Start-Sleep -Milliseconds 300
    return ($down -and $up)
}

# ---------- M7：图片发送共享助手（send-image 用；只增不改，M1-M6 函数不动） ----------

function Invoke-DriverMain {
    # 命名 mutex 单飞 → Win32/DPI 初始化 → body → DRIVER_JSON 输出。
    # body 返回的 hashtable 作为 data；Throw-DriverError 映射为 ok=false + 稳定 code。
    param(
        [Parameter(Mandatory)][string]$MutexName,
        [Parameter(Mandatory)][scriptblock]$Body
    )
    $createdNew = $false
    $mutex = New-Object Threading.Mutex($false, $MutexName, [ref]$createdNew)
    # 上一个驱动进程被超时 kill / 强杀会遗留 abandoned mutex：WaitOne 抛 AbandonedMutexException
    # 但异常抛出时已实际获得所有权——必须捕获按已获得处理，否则每次驱动被杀后首次运行必崩。
    $acquired = $false
    try {
        $acquired = $mutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        $acquired = $true
    }
    if (-not $acquired) {
        Write-DriverJson @{ ok = $false; code = 'BUSY'; message = '另一个企业微信自动化驱动正在执行（命名 mutex 单飞），请稍后重试' }
        $mutex.Dispose()
        return
    }
    try {
        Initialize-WeComWin32
        Set-WeComDpiContext
        $data = & $Body
        # body 最后一个语句的返回值即 data；脚本块输出可能混入数组，取最后一个 hashtable
        if ($data -is [array]) { $data = ($data | Where-Object { $_ -is [hashtable] } | Select-Object -Last 1) }
        if ($null -eq $data) { $data = @{} }
        Write-DriverJson @{ ok = $true; data = $data }
    } catch {
        $raw = [string]$_.Exception.Message
        if ($raw -match '^WECOMDRIVE\|([A-Z_]+)\|([\s\S]*)$') {
            Write-DriverJson @{ ok = $false; code = $Matches[1]; message = $Matches[2] }
        } elseif ($raw -match 'DPI_AWARENESS_FAILED') {
            Write-DriverJson @{ ok = $false; code = 'INTERNAL_ERROR'; message = 'DPI 感知上下文设置失败（Per-Monitor V2）' }
        } else {
            if ($raw.Length -gt 300) { $raw = $raw.Substring(0, 300) }
            Write-DriverJson @{ ok = $false; code = 'INTERNAL_ERROR'; message = ('驱动内部错误：' + $raw) }
        }
    } finally {
        [void]$mutex.ReleaseMutex()
        $mutex.Dispose()
    }
}
