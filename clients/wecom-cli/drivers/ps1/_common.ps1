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
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr hWndParent, EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassNameW(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hwnd, IntPtr hdcBlt, uint nFlags);
    [DllImport("user32.dll")] public static extern bool PostMessageW(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool ScreenToClient(IntPtr hWnd, ref POINT p);
    [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT p);
    [DllImport("user32.dll")] public static extern short VkKeyScanW(char ch);
    [DllImport("user32.dll")] public static extern uint MapVirtualKeyW(uint code, uint mapType);
    [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr v);
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
    # 登录二维码页：小尺寸 WeWorkWindow（真机典型 380x540；启发式沿用 wecom-personal-rpa F7）
    foreach ($w in @(Get-WeComTopLevelWindows)) {
        if ($w.Class -eq 'WeWorkWindow' -and $w.Visible -and $w.W -gt 0 -and $w.W -lt 500 -and $w.H -lt 700) {
            return $w
        }
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

# ---------- M2：搜索 overlay / OCR 共享助手（chat-search 与 message-send 共用） ----------

# 主窗口搜索框像素坐标（窗口左上角偏移；2026-09-04 真机实测：搜索框固定在左栏内
# x∈[154,505]、y∈[44,100]，不随窗口宽度按比例伸缩——外部联系人会话会把主窗口撑宽到
# 2916，比例坐标（旧 0.243w=709）会点出框外，必须像素锚定）。取框内中心 (330,72)。
$script:SearchBoxPx = 330
$script:SearchBoxPy = 72
# 搜索框内右侧 × 清空按钮像素坐标（框内有内容时出现，PostMessage 点击即清空，
# 清空后占位符「搜索」恢复；2026-09-04 真机实测 × 中心 ≈(473,77)，同框体固定像素）。
# 空框时该坐标仍落搜索框内，点击无害仅聚焦——但本实现走「先 OCR 判残留、有残留才
# 点 ×」，避免对空框多点
$script:SearchClearPx = 473
$script:SearchClearPy = 77
# 聊天输入框比例坐标（底部工具栏之下、窗口底边之上；2026-09-04 实测工具栏图标行
# 在 ≈0.83h，文本输入区在其下，0.90h 落文本区内）
$script:ChatInputRx = 0.500
$script:ChatInputRy = 0.900

function Get-WeComSearchBoxTexts {
    # 截图主窗口 → OCR searchbox 模式读顶部搜索框内容 tokens（平铺，不过滤）
    param([Parameter(Mandatory)][int64]$MainHwnd)
    $shot = Join-Path $env:TEMP 'wecom-driver-searchbox.png'
    Get-WeComWindowSnapshot -Hwnd $MainHwnd -Path $shot | Out-Null
    $ocr = Invoke-WeComChatOcr -ImagePath $shot -Mode 'searchbox'
    return @($ocr.texts | ForEach-Object { [string]$_ })
}

function Get-WeComSearchBoxResidual([string[]]$Texts) {
    # 剔除占位符与 × 清空按钮误识（x/X/× 单字符），剩余即框内真实内容。
    # 占位符「搜索」是灰字，OCR 次字误识率高（真机实测读成「搜扮」）：
    # 凡「搜」开头且 ≤2 字的 token 一律按占位符剔除——真实残留是黑字、
    # 误识率低且通常是完整查询词，与此模式冲突的概率可忽略。
    return @($Texts | Where-Object { $_ -notmatch '^搜.{0,1}$' -and $_ -notmatch '^[xX×]$' })
}

function Clear-WeComSearchBox {
    # 清空主窗口搜索框残留查询词（企微搜索框保留上次查询：不清空则新输入拼接在
    # 旧词后，真机实测「WayneLWayneLu」）。先 OCR 判残留：有残留才点 ×（避免对空框
    # 多点一次），点后复核仍不空 → UI_CHANGED。-BestEffort：失败仅返回 $false
    # 不抛错（收尾恢复原状用）。
    param(
        [Parameter(Mandatory)][int64]$MainHwnd,
        [switch]$BestEffort
    )
    try {
        $residual = Get-WeComSearchBoxResidual (Get-WeComSearchBoxTexts $MainHwnd)
        if ($residual.Count -eq 0) { return $true }
        $main = Get-WeComWindowInfo ([IntPtr]$MainHwnd)
        [void](Send-WeComClick -Hwnd $MainHwnd `
            -ScreenX ([int]($main.X + $script:SearchClearPx)) `
            -ScreenY ([int]($main.Y + $script:SearchClearPy)))
        Start-Sleep -Milliseconds 400
        $residual2 = Get-WeComSearchBoxResidual (Get-WeComSearchBoxTexts $MainHwnd)
        if ($residual2.Count -eq 0) { return $true }
        if ($BestEffort) { return $false }
        Throw-DriverError 'UI_CHANGED' ('搜索框残留内容点击 × 后仍未清空（残留：' + ($residual2 -join '') + '），为避免拼接旧查询词已中止')
    } catch {
        if ($BestEffort) { return $false }
        throw
    }
}

function Open-WeComSearchOverlay {
    # 解析主窗口 → PostMessage 点搜索框 → 清空上次查询残留 → 输入 query →
    # OCR 回读验证框内文本==query（不等则清框补输一次，仍不等 → UI_CHANGED；
    # 真机出现过末字符被竞态吃掉：「WayneLu」落成「WayneL」）→ 等 SearchResultWindow2 →
    # 轮询等 overlay 渲染稳定（每 400ms 截图，高度连续两次相同即稳定，≤TimeoutMs；
    # 以稳定帧 OCR 为终态：有结果条目返回条目，只有「进入全局搜索」行也算终态，
    # 返回空 items——无结果是合法结果而非错误；超时未稳定则用最后一帧兜底）。
    # 返回 @{ MainHwnd; OverlayHwnd; Ocr; ShotPath }（Ocr = 稳定帧 search 模式结果，
    # ShotPath = 稳定帧截图 TEMP 路径，调用方可拷入 artifact）。
    param(
        [Parameter(Mandatory)][string]$Query,
        [int]$TimeoutMs = 8000
    )
    $mainHwnd = Resolve-WeComMainWindow
    $main = Get-WeComWindowInfo ([IntPtr]$mainHwnd)
    [void](Send-WeComClick -Hwnd $mainHwnd `
        -ScreenX ([int]($main.X + $script:SearchBoxPx)) `
        -ScreenY ([int]($main.Y + $script:SearchBoxPy)))
    Start-Sleep -Milliseconds 400

    # 清空上次查询残留（无残留时为空操作；清不掉 → UI_CHANGED）
    [void](Clear-WeComSearchBox -MainHwnd $mainHwnd)

    # 输入 query + OCR 回读验证（不等 → 清框补输一次 → 仍不等 UI_CHANGED）
    $normQuery = ($Query -replace '\s+', '')
    $echo = ''
    $typed = $false
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        Send-WeComText -Hwnd $mainHwnd -Text $Query
        Start-Sleep -Milliseconds 400
        $echo = ((Get-WeComSearchBoxResidual (Get-WeComSearchBoxTexts $mainHwnd)) -join '') -replace '\s+', ''
        # 光标伪影容忍（真机实测：OCR 把框内文本末尾的输入光标 | 误识为 l，echo
        # 落成「WayneLul」≠ query「WayneLu」，补输仍带光标 → 误杀 UI_CHANGED）：
        # echo 以 normQuery 开头且仅末尾多 1 个字符、该字符属于光标误识集合
        # （l | I 1 i !）即视为一致。方向不可逆：只能容忍「echo 比 query 多末尾 1 个
        # 伪影字符」；query 比 echo 多说明真丢了字符（如「WayneLu」落成「WayneL」），
        # 必须走补输，不得放行。中英文 query 均适用（误识集合全为 ASCII 伪影）。
        $echoMatch = ($echo -eq $normQuery) -or (
            $echo.Length -eq $normQuery.Length + 1 -and
            $echo.StartsWith($normQuery) -and
            'l|I1i!'.Contains($echo.Substring($echo.Length - 1))
        )
        if ($echoMatch) { $typed = $true; break }
        if ($attempt -lt 2) { [void](Clear-WeComSearchBox -MainHwnd $mainHwnd) }
    }
    if (-not $typed) {
        Throw-DriverError 'UI_CHANGED' ('搜索框回读文本与 query 不一致（补输 1 次后仍不等：框内「' + $echo + '」≠「' + $normQuery + '」），已中止')
    }

    $overlayHwnd = Wait-WeComWindow -ClassName 'SearchResultWindow2' -TimeoutMs $TimeoutMs
    if ($overlayHwnd -eq 0) {
        Throw-DriverError 'UI_CHANGED' '未等到搜索结果面板（SearchResultWindow2）：搜索框点击未生效或页面结构已变化'
    }

    # overlay 渲染异步：刚出现可能只有 400x84（仅「进入全局搜索」一行），结果加载后
    # 才长高。轮询截图：窗口高度连续两次相同即渲染稳定，以该帧 OCR 为终态。
    $shot = Join-Path $env:TEMP 'wecom-driver-search-overlay.png'
    $deadline = [DateTime]::UtcNow.AddMilliseconds($TimeoutMs)
    $lastH = -1
    $ocr = $null
    while ([DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 400
        $info = Get-WeComWindowInfo ([IntPtr]$overlayHwnd)
        Get-WeComWindowSnapshot -Hwnd $overlayHwnd -Path $shot | Out-Null
        $ocr = Invoke-WeComChatOcr -ImagePath $shot -Mode 'search'
        if ($info.H -eq $lastH) { break }
        $lastH = $info.H
    }
    if ($null -eq $ocr) {
        Throw-DriverError 'UI_CHANGED' '等待搜索结果面板渲染超时（未取到任何一帧），页面结构可能已变化'
    }
    return @{ MainHwnd = $mainHwnd; OverlayHwnd = $overlayHwnd; Ocr = $ocr; ShotPath = $shot }
}

function Close-WeComSearchOverlay {
    # 关闭搜索 overlay 恢复原状（best-effort）：先 PostMessage ESC 到 overlay，
    # 仍在则点主窗口空白区（点面板外关面板）；最后清空搜索框残留查询词
    # （企微搜索框保留上次查询，不清会给下次搜索留残留）并复核。不抛错。
    param(
        [int64]$OverlayHwnd,
        [int64]$MainHwnd
    )
    try {
        if ($OverlayHwnd -ne 0 -and [WeComWin32]::IsWindow([IntPtr]$OverlayHwnd)) {
            Send-WeComEscape -Hwnd $OverlayHwnd
            Start-Sleep -Milliseconds 400
        }
        if ($OverlayHwnd -ne 0 -and [WeComWin32]::IsWindow([IntPtr]$OverlayHwnd)) {
            $main = Get-WeComWindowInfo ([IntPtr]$MainHwnd)
            [void](Send-WeComClick -Hwnd $MainHwnd `
                -ScreenX ([int]($main.X + $main.W * 0.6)) `
                -ScreenY ([int]($main.Y + $main.H * 0.5)))
            Start-Sleep -Milliseconds 300
        }
        # 清空搜索框残留查询词（best-effort）：不给下次搜索留旧词
        [void](Clear-WeComSearchBox -MainHwnd $MainHwnd -BestEffort)
    } catch {}
}

function Invoke-WeComChatOcr {
    # 仓库 venv python 跑 drivers/py/chat_ocr.py（M2 单一 RapidOCR 入口）；
    # drivers/ps1 上四级为仓库根。CHATOCR_JSON 协议；error 归并 CONFIG_MISSING/INTERNAL_ERROR。
    param(
        [Parameter(Mandatory)][string]$ImagePath,
        [Parameter(Mandatory)][string]$Mode
    )
    $repoRoot = (Resolve-Path (Join-Path $script:DriverPs1Dir '..\..\..\..')).Path
    $pythonExe = Join-Path $repoRoot 'venv\Scripts\python.exe'
    $ocrScript = Join-Path $script:DriverPs1Dir '..\py\chat_ocr.py'
    if (-not (Test-Path $ocrScript)) { Throw-DriverError 'INTERNAL_ERROR' ('未找到 OCR 脚本：' + $ocrScript) }
    if (-not (Test-Path $pythonExe)) { Throw-DriverError 'CONFIG_MISSING' ('未找到仓库 venv python（RapidOCR 所在解释器）：' + $pythonExe) }
    # 用 System.Diagnostics.Process 直接启动（不走 PS 原生命令管道）：MCP stdio 场景下
    # Host 可能只继承白名单环境变量（@modelcontextprotocol/sdk getDefaultEnvironment），
    # PS 5.1 对管道化原生命令的「文档激活」在该环境下抛 CantActivateDocumentInPipeline，
    # python 根本未执行且 $LASTEXITCODE 为空（2026-09-04 真机实测）。直启进程绕开该机制，
    # 且 stderr 可留存诊断。
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $pythonExe
    $psi.Arguments = ('"' + $ocrScript + '" "' + $ImagePath + '" ' + $Mode)
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $proc = [System.Diagnostics.Process]::Start($psi)
    $out = $proc.StandardOutput.ReadToEnd()
    $errText = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()
    if ($proc.ExitCode -ne 0) {
        Throw-DriverError 'INTERNAL_ERROR' ('OCR 脚本执行失败（exit=' + $proc.ExitCode + '；stderr: ' + ($errText.Substring(0, [Math]::Min(300, $errText.Length))) + '）')
    }
    $jsonLine = @($out | Where-Object { $_ -match '^CHATOCR_JSON:' }) | Select-Object -Last 1
    if (-not $jsonLine) { Throw-DriverError 'INTERNAL_ERROR' 'OCR 脚本未输出 CHATOCR_JSON' }
    $parsed = ($jsonLine -replace '^CHATOCR_JSON:\s*', '') | ConvertFrom-Json
    if ($parsed.error) {
        if ($parsed.error -eq 'OCR_UNAVAILABLE') { Throw-DriverError 'CONFIG_MISSING' ([string]$parsed.message) }
        Throw-DriverError 'INTERNAL_ERROR' ('OCR 失败：' + [string]$parsed.message)
    }
    return $parsed
}

# ---------- M3：滚轮 / 归一化 共享助手（history-read 用） ----------

function Send-WeComWheel {
    # PostMessage WM_MOUSEWHEEL（0x020A）：wParam 高字=delta（120 上滚看历史 / -120 下滚，
    # 负值必须按 uint32 掩码），lParam 是屏幕坐标（不是客户区坐标，.tmp/wecom-probe/
    # probe-wheel.ps1 真机验证翻页有效）。落点默认消息区中心（比例 0.63/0.55）。
    param(
        [Parameter(Mandatory)][int64]$Hwnd,
        [Parameter(Mandatory)][int]$Delta,
        [Parameter(Mandatory)][int]$Count,
        [int]$GapMs = 60,
        [double]$Rx = 0.63,
        [double]$Ry = 0.55
    )
    Initialize-WeComWin32
    $r = New-Object WeComWin32+RECT
    [void][WeComWin32]::GetWindowRect([IntPtr]$Hwnd, [ref]$r)
    $sx = $r.Left + [int](($r.Right - $r.Left) * $Rx)
    $sy = $r.Top + [int](($r.Bottom - $r.Top) * $Ry)
    $lp = [IntPtr](($sy -shl 16) -bor ($sx -band 0xFFFF))
    $wp = [IntPtr](($Delta -shl 16) -band 0xFFFFFFFF)
    foreach ($i in 1..$Count) {
        [void][WeComWin32]::PostMessageW([IntPtr]$Hwnd, 0x020A, $wp, $lp)
        Start-Sleep -Milliseconds $GapMs
    }
}

function ConvertTo-WeComNormalized([string]$s) {
    # 归一化（与 chat_ocr.py normalize_text 同规则，跨侧比对两侧必须一致）：
    # 去全部空白（OCR 可能插入/丢失空格）、全角 ASCII（U+FF01–FF5E，含：，！？；（）
    # 字母数字）转半角、常见全角标点（。、～）转半角、小写折叠
    $sb = New-Object System.Text.StringBuilder
    foreach ($ch in $s.ToCharArray()) {
        $o = [int][char]$ch
        if ([char]::IsWhiteSpace($ch)) { continue }
        if ($o -ge 0xFF01 -and $o -le 0xFF5E) { [void]$sb.Append([char]($o - 0xFEE0)); continue }
        if ($ch -eq '。') { [void]$sb.Append('.'); continue }
        if ($ch -eq '、') { [void]$sb.Append(','); continue }
        if ($ch -eq '～') { [void]$sb.Append('~'); continue }
        [void]$sb.Append($ch)
    }
    return $sb.ToString().ToLower()
}

# ---------- 驱动统一入口 ----------

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
