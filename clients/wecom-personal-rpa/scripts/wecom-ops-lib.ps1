# ====================================================================================
# wecom-ops-lib.ps1
#
# 企微操作 PowerShell 公共函数库。被 wecom-ops.ps1 dot-source 引用。
# 仅包含稳定函数（不含 debug-only 的视觉/状态文件/API key 解析逻辑）。
#
# 编码：UTF-8 with BOM。PS 5.1 看到 BOM 会按 UTF-8 解析源码，中文字面量不乱码。
# 调用方式：由 wecom-ops.ps1 通过 `. (Join-Path $ScriptDir 'wecom-ops-lib.ps1')` 加载。
# ====================================================================================

# ---------- Win32 P/Invoke 类型定义（user32：鼠标/键盘/窗口枚举） ----------
# 必要性：脚本启动时默认非 DPI-aware，SetCursorPos 在虚拟坐标系工作（基于 96 DPI），
# 但企微窗口在物理坐标系（150% DPI），会导致点击位置错位。
# 必须在 Add-Type 之后立即设为 PER_MONITOR_AWARE_V2，让 SetCursorPos/GetCursorPos 用物理坐标。
try {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class WeOpsWin32 {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, IntPtr dwExtraInfo);
    [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, IntPtr dwExtraInfo);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool attach);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern IntPtr SetActiveWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr value);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    public const uint MOUSEEVENTF_LEFTDOWN = 0x02;
    public const uint MOUSEEVENTF_LEFTUP = 0x04;
    public const uint KEYEVENTF_KEYDOWN = 0x00;
    public const uint KEYEVENTF_KEYUP = 0x02;
    // EnumWindows 系列：用于查找 WeWorkWindow 主窗口
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll", CharSet = CharSet.Auto)] public static extern int GetClassName(IntPtr hWnd, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rc);
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
} catch {
    # 类型已存在（被多次 dot-source 时），忽略
}

# 设置 DPI awareness：PER_MONITOR_AWARE_V2 优先，fallback 到 SYSTEM_AWARE
try {
    [WeOpsWin32]::SetProcessDpiAwarenessContext([IntPtr](-4)) | Out-Null
} catch {
    try { [WeOpsWin32]::SetProcessDPIAware() | Out-Null } catch { }
}

# ---------- 鼠标点击（DPI-aware 后的物理坐标） ----------
function Click-At([int]$x, [int]$y) {
    [WeOpsWin32]::SetCursorPos($x, $y) | Out-Null
    Start-Sleep -Milliseconds 80
    [WeOpsWin32]::mouse_event([WeOpsWin32]::MOUSEEVENTF_LEFTDOWN, 0, 0, 0, [IntPtr]::Zero)
    Start-Sleep -Milliseconds 60
    [WeOpsWin32]::mouse_event([WeOpsWin32]::MOUSEEVENTF_LEFTUP, 0, 0, 0, [IntPtr]::Zero)
}

# ---------- 清空输入框（Ctrl+A → Delete） ----------
function Press-CtrlA-Delete {
    [WeOpsWin32]::keybd_event(0x11, 0, [WeOpsWin32]::KEYEVENTF_KEYDOWN, [IntPtr]::Zero)  # Ctrl down
    [WeOpsWin32]::keybd_event(0x41, 0, [WeOpsWin32]::KEYEVENTF_KEYDOWN, [IntPtr]::Zero)  # A down
    Start-Sleep -Milliseconds 40
    [WeOpsWin32]::keybd_event(0x41, 0, [WeOpsWin32]::KEYEVENTF_KEYUP, [IntPtr]::Zero)
    [WeOpsWin32]::keybd_event(0x11, 0, [WeOpsWin32]::KEYEVENTF_KEYUP, [IntPtr]::Zero)
    Start-Sleep -Milliseconds 40
    [WeOpsWin32]::keybd_event(0x2E, 0, [WeOpsWin32]::KEYEVENTF_KEYDOWN, [IntPtr]::Zero)  # Delete down
    Start-Sleep -Milliseconds 40
    [WeOpsWin32]::keybd_event(0x2E, 0, [WeOpsWin32]::KEYEVENTF_KEYUP, [IntPtr]::Zero)
}

# ---------- 输入文本（剪贴板 + Ctrl+V，兼容中文） ----------
function Type-Text {
    param([string]$text, [IntPtr]$ExpectedForegroundHwnd = [IntPtr]::Zero)
    Add-Type -AssemblyName System.Windows.Forms
    if ($ExpectedForegroundHwnd -ne [IntPtr]::Zero -and
        -not (Test-WeComForegroundWindow -Hwnd $ExpectedForegroundHwnd)) { return $false }
    [System.Windows.Forms.Clipboard]::SetText($text)
    Start-Sleep -Milliseconds 80
    if ($ExpectedForegroundHwnd -ne [IntPtr]::Zero -and
        -not (Test-WeComForegroundWindow -Hwnd $ExpectedForegroundHwnd)) { return $false }
    [WeOpsWin32]::keybd_event(0x11, 0, [WeOpsWin32]::KEYEVENTF_KEYDOWN, [IntPtr]::Zero)  # Ctrl down
    [WeOpsWin32]::keybd_event(0x56, 0, [WeOpsWin32]::KEYEVENTF_KEYDOWN, [IntPtr]::Zero)  # V down
    Start-Sleep -Milliseconds 40
    [WeOpsWin32]::keybd_event(0x56, 0, [WeOpsWin32]::KEYEVENTF_KEYUP, [IntPtr]::Zero)
    [WeOpsWin32]::keybd_event(0x11, 0, [WeOpsWin32]::KEYEVENTF_KEYUP, [IntPtr]::Zero)
    if ($ExpectedForegroundHwnd -ne [IntPtr]::Zero) { return $true }
}

# ---------- 按 Enter 键 ----------
function Press-Enter {
    param([IntPtr]$ExpectedForegroundHwnd = [IntPtr]::Zero)
    if ($ExpectedForegroundHwnd -ne [IntPtr]::Zero -and
        -not (Test-WeComForegroundWindow -Hwnd $ExpectedForegroundHwnd)) { return $false }
    [WeOpsWin32]::keybd_event(0x0D, 0, 0, [IntPtr]::Zero)   # Enter down
    Start-Sleep -Milliseconds 50
    [WeOpsWin32]::keybd_event(0x0D, 0, 2, [IntPtr]::Zero)   # Enter up
    if ($ExpectedForegroundHwnd -ne [IntPtr]::Zero) { return $true }
}

# ---------- Ctrl+V 粘贴 ----------
function Press-CtrlV {
    param([IntPtr]$ExpectedForegroundHwnd = [IntPtr]::Zero)
    if ($ExpectedForegroundHwnd -ne [IntPtr]::Zero -and
        -not (Test-WeComForegroundWindow -Hwnd $ExpectedForegroundHwnd)) { return $false }
    [WeOpsWin32]::keybd_event(0x11, 0, 0, [IntPtr]::Zero)   # Ctrl down
    [WeOpsWin32]::keybd_event(0x56, 0, 0, [IntPtr]::Zero)   # V down
    Start-Sleep -Milliseconds 50
    [WeOpsWin32]::keybd_event(0x56, 0, 2, [IntPtr]::Zero)   # V up
    [WeOpsWin32]::keybd_event(0x11, 0, 2, [IntPtr]::Zero)   # Ctrl up
    if ($ExpectedForegroundHwnd -ne [IntPtr]::Zero) { return $true }
}

# ---------- 激活企微主窗口并用双 Alt 聚焦搜索框 ----------
function Focus-WeComSearchBox {
    param([Parameter(Mandatory = $true)][IntPtr]$Hwnd)

    if ($Hwnd -eq [IntPtr]::Zero) { return $false }
    [WeOpsWin32]::ShowWindow($Hwnd, 9) | Out-Null  # SW_RESTORE
    $activated = $false
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $attachedThreadIds = New-Object System.Collections.ArrayList
        $currentThreadId = [WeOpsWin32]::GetCurrentThreadId()
        $targetProcessId = [uint32]0
        $targetThreadId = [WeOpsWin32]::GetWindowThreadProcessId($Hwnd, [ref]$targetProcessId)
        $foregroundHwnd = [WeOpsWin32]::GetForegroundWindow()
        $foregroundProcessId = [uint32]0
        $foregroundThreadId = if ($foregroundHwnd -ne [IntPtr]::Zero) {
            [WeOpsWin32]::GetWindowThreadProcessId($foregroundHwnd, [ref]$foregroundProcessId)
        } else { [uint32]0 }

        try {
            # 后台 Client.App 启动的 PowerShell 通常没有前台权限。把当前调用线程临时
            # 关联到前台线程和企微目标线程后，再执行完整激活序列。
            foreach ($threadId in @($foregroundThreadId, $targetThreadId)) {
                if ($threadId -ne 0 -and $threadId -ne $currentThreadId -and
                    -not $attachedThreadIds.Contains($threadId)) {
                    if ([WeOpsWin32]::AttachThreadInput($currentThreadId, $threadId, $true)) {
                        [void]$attachedThreadIds.Add($threadId)
                    }
                }
            }
            [WeOpsWin32]::BringWindowToTop($Hwnd) | Out-Null
            [WeOpsWin32]::SetActiveWindow($Hwnd) | Out-Null
            [WeOpsWin32]::SetForegroundWindow($Hwnd) | Out-Null
        } finally {
            # 只解绑本轮成功建立的关联，并按建立顺序反向释放。
            for ($index = $attachedThreadIds.Count - 1; $index -ge 0; $index--) {
                [WeOpsWin32]::AttachThreadInput(
                    $currentThreadId, [uint32]$attachedThreadIds[$index], $false
                ) | Out-Null
            }
        }
        Start-Sleep -Milliseconds 150
        if ([WeOpsWin32]::GetForegroundWindow() -eq $Hwnd) { $activated = $true; break }
    }
    if (-not $activated) { return $false }

    for ($i = 0; $i -lt 2; $i++) {
        # 双 Alt 期间也可能被其他应用抢走前台；一旦发生就停止注入按键。
        if ([WeOpsWin32]::GetForegroundWindow() -ne $Hwnd) { return $false }
        [WeOpsWin32]::keybd_event(0x12, 0, [WeOpsWin32]::KEYEVENTF_KEYDOWN, [IntPtr]::Zero)
        Start-Sleep -Milliseconds 50
        [WeOpsWin32]::keybd_event(0x12, 0, [WeOpsWin32]::KEYEVENTF_KEYUP, [IntPtr]::Zero)
        Start-Sleep -Milliseconds 120
    }
    # 只有企微在双 Alt 完成后仍为前台，调用方才可继续 Ctrl+A/Delete 和输入。
    return ([WeOpsWin32]::GetForegroundWindow() -eq $Hwnd)
}

# ---------- 校验当前前台仍是本次自动化选中的企微主窗口 ----------
# 会话复用时只做失败安全校验，不激活窗口、不发送 Alt，避免离开当前会话。
function Test-WeComForegroundWindow {
    param(
        [Parameter(Mandatory = $true)][IntPtr]$Hwnd,
        [IntPtr]$ForegroundHwnd = [WeOpsWin32]::GetForegroundWindow()
    )
    return $Hwnd -ne [IntPtr]::Zero -and $ForegroundHwnd -eq $Hwnd
}

# ---------- 取企微主窗口左上角物理屏幕坐标（EnumWindows 找 WeWorkWindow） ----------
# 必要性：企微窗口可能被用户移动，每次操作前必须实时取窗口 origin 用于坐标换算。
# 选择策略：在所有可见的 WeWorkWindow 类名窗口中，选面积最大且 >=600x400 的（主窗口）。
function Get-WeWorkWindowOrigin {
    $script:wopsTarget = [IntPtr]::Zero
    $script:wopsTargetArea = 0
    $script:wopsAllWeWork = New-Object System.Collections.ArrayList
    [WeOpsWin32]::EnumWindows({
        param($h, $l)
        $sb = New-Object System.Text.StringBuilder 256
        [WeOpsWin32]::GetClassName($h, $sb, 256) | Out-Null
        if ($sb.ToString() -eq 'WeWorkWindow' -and [WeOpsWin32]::IsWindowVisible($h)) {
            $r = New-Object WeOpsWin32+RECT
            [void][WeOpsWin32]::GetWindowRect($h, [ref]$r)
            $w = $r.Right - $r.Left; $hgt = $r.Bottom - $r.Top
            $area = $w * $hgt
            $script:wopsAllWeWork.Add("hwnd=0x$($h.ToInt64().ToString('X')) ${w}x${hgt} L=$($r.Left) T=$($r.Top)") | Out-Null
            if ($w -ge 600 -and $hgt -ge 400 -and $area -gt $script:wopsTargetArea) {
                $script:wopsTarget = $h; $script:wopsTargetArea = $area
            }
        }
        return $true
    }, [IntPtr]::Zero) | Out-Null

    if ($script:wopsTarget -eq [IntPtr]::Zero) { return $null }
    $r = New-Object WeOpsWin32+RECT
    [void][WeOpsWin32]::GetWindowRect($script:wopsTarget, [ref]$r)
    return @{
        Hwnd = $script:wopsTarget
        Left = $r.Left
        Top = $r.Top
        Width = $r.Right - $r.Left
        Height = $r.Bottom - $r.Top
        CandidatesCount = $script:wopsAllWeWork.Count
    }
}

# ---------- 封装 capture-wecom-for-csharp.ps1（截图企微主窗口） ----------
# 必要性：debug-navigate.ps1 已验证的截图能力，独立脚本，需 cmd /c 包一层避免 NativeCommandError。
# 保留：Phase 3 F7 Get-WeComLoginState 详细版会用
function Capture-WeCom {
    param([string]$OutDir = '')
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $captureScript = Join-Path $ScriptDir 'capture-wecom-for-csharp.ps1'
    if (-not (Test-Path $captureScript)) { throw "找不到 $captureScript" }
    if (-not $OutDir) { $OutDir = Join-Path $ScriptDir '..\debug-out' }
    $OutDir = (New-Object -TypeName System.IO.DirectoryInfo -ArgumentList $OutDir).FullName
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
    $tmpOut = Join-Path $OutDir ('cap_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
    New-Item -ItemType Directory -Force -Path $tmpOut | Out-Null
    $stdoutFile = Join-Path $tmpOut 'stdout.log'
    # $ErrorActionPreference=Stop 会让子进程任何 stderr 输出（即使中文日志）变成 NativeCommandError。
    # 这里临时降为 SilentlyContinue，调用完恢复。
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    try {
        $cmdLine = 'powershell -ExecutionPolicy Bypass -NoProfile -File "' + $captureScript + '" -OutDir "' + $tmpOut + '"'
        cmd /c "$cmdLine > `"$stdoutFile`" 2>&1" | Out-Null
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    if (-not (Test-Path $stdoutFile)) { throw "capture 脚本未生成 stdout 文件" }
    $capOut = Get-Content $stdoutFile -Encoding UTF8
    $lastJson = ($capOut | Where-Object { $_ -match '^\{' }) | Select-Object -Last 1
    if (-not $lastJson) { throw "capture 脚本无 JSON 输出，stdout 见：$stdoutFile" }
    $cap = $lastJson | ConvertFrom-Json
    if (-not $cap.ok) { throw "截图失败：$($cap.error)" }
    return @{
        png_path = $cap.png_path
        left = [int]$cap.left
        top = [int]$cap.top
        width = [int]$cap.width
        height = [int]$cap.height
        dpi_scale = [double]$cap.dpi_scale
        wecom_version = [string]$cap.wecom_version
        hwnd = [string]$cap.hwnd
    }
}

# ---------- 加载关键词字典（UTF-8 文件，避免 PS 5.1 GBK 解析中文字面量错位） ----------
# 已废弃：中文走 stdin JSON 传参，不需要文件字典；保留函数签名作为历史参考，Phase 3 视情况删除
<#
function Load-Keywords {
    param([string]$KeywordsFile = '')
    if (-not $KeywordsFile) {
        $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
        $KeywordsFile = Join-Path $ScriptDir 'prompts\keywords.txt'
    }
    $kw = @{}
    if (-not (Test-Path $KeywordsFile)) { return $kw }
    foreach ($line in Get-Content $KeywordsFile -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed) { continue }
        if ($trimmed.StartsWith('#')) { continue }
        $idx = $trimmed.IndexOf('=')
        if ($idx -le 0) { continue }
        $key = $trimmed.Substring(0, $idx).Trim()
        $val = $trimmed.Substring($idx + 1).Trim()
        $kw[$key] = $val
    }
    return $kw
}
#>

# ---------- 统一 JSON 输出到 stdout（C# 端读最后一行 { 开头的 JSON） ----------
function Write-Result {
    param([Parameter(Mandatory = $true)][hashtable]$Result)
    [Console]::Out.WriteLine(($Result | ConvertTo-Json -Depth 10 -Compress))
}
