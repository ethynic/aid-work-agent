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

# ---------- M3：滚轮 / 归一化 共享助手（read-session 用；M9 前身 history-read） ----------

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

# ---------- M4：搜索 V2（attachstate 聚焦 / 动态搜索框状态 / Jev 决策） ----------
# 2026-09-28 真机验证结论（experiments/probes/e1/e2/e3 系列）：
#   - attachstate 组合键（AttachThreadInput 共享键状态 + PostMessage）全后台 ~250ms 可用；
#     keybd_event/SendInput 被企微 5.0.9 丢弃（M1 结论，继续禁止）；ESC 禁用（最小化企微）。
#   - 旧固定像素带 searchbox OCR（x∈[140,510]）在窗口 1280 宽时把聊天区标题误判为残留
#     （已证实 bug）：改为裁切 x∈[120,420]、y∈[0,62] + 4x 放大 + 坐标判态（Get-WeComSearchBoxState）。
#   - Jev 决策 API（api.typesafe.ai/v1/systemone）实测通；无 key/超时/HTTP 错一律降级规则兜底。

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
    # 返回 $true=按键已发出；$false=AttachThreadInput 失败（调用方走降级链）。
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
        [void][WeComWin32]::PostMessageW([IntPtr]$Hwnd, 0x0100, [IntPtr]$Vk, $lpDown)
        Start-Sleep -Milliseconds 60
        [void][WeComWin32]::PostMessageW([IntPtr]$Hwnd, 0x0101, [IntPtr]$Vk, $lpUp)
        Start-Sleep -Milliseconds 60
    } finally {
        [void][WeComAttach32]::SetKeyboardState($saved)
        [void][WeComAttach32]::AttachThreadInput($me, $wxThread, $false)
    }
    return $true
}

function Get-WeComSearchBoxState {
    # 动态搜索框状态检测（替代旧 Get-WeComSearchBoxTexts 固定像素带——旧带 x0∈[140,510]
    # 在窗口 1280 宽时把聊天区标题误判为残留，已证实 bug）：
    # 截主窗口 → 裁 x∈[120,420]、y∈[0,62]（窗口像素）→ 4x 双三次放大 → RapidOCR boxes
    # 原始 token → 坐标 /4 加回偏移 = 窗口坐标 → 判态：
    #   - 占位符：token 匹配 搜[索素粟]（次字误读容忍：索 U+7D22/素 U+7D20/粟 U+7C9B）
    #   - 按钮：单字符 ^[xX×+十士]$（× 清空按钮 / + 按钮及其误读）
    #   - 内容 token 用 x1∈[212,400] 判定：OCR 常把图标+文本合并成一个 token「Q 陆伟」，
    #     x0 是图标的 ~186 不能用；x1 卡 212..400 排除左侧导航角标与右侧 + 按钮区
    # state：有 ×（x/X/× 单字符）→ has_content；有占位符无 × → empty；否则 unknown
    # （has_content/unknown 都触发清空）。返回 pscustomobject：
    #   State / Tokens（日志用格式串）/ TokensRaw（带窗口坐标 token 数组）/
    #   Content（内容 token 拼接）/ PlaceholderPos、ContentPos（@(x,y) 窗口坐标或 $null，
    #   聚焦降级链的点击锚点）
    param(
        [Parameter(Mandatory)][int64]$MainHwnd,
        [string]$Tag = 'state'
    )
    $shot = Join-Path $env:TEMP ('wecom-driver-sbstate-' + $Tag + '.png')
    Get-WeComWindowSnapshot -Hwnd $MainHwnd -Path $shot | Out-Null
    Add-Type -AssemblyName System.Drawing
    $img = [System.Drawing.Image]::FromFile($shot)
    $cx0 = 120; $cy0 = 0
    $cx1 = [Math]::Min(420, $img.Width); $cy1 = [Math]::Min(62, $img.Height)
    $crop = New-Object System.Drawing.Bitmap (($cx1 - $cx0) * 4), (($cy1 - $cy0) * 4)
    $g = [System.Drawing.Graphics]::FromImage($crop)
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.DrawImage($img, (New-Object System.Drawing.Rectangle 0, 0, $crop.Width, $crop.Height),
        (New-Object System.Drawing.Rectangle $cx0, $cy0, ($cx1 - $cx0), ($cy1 - $cy0)),
        [System.Drawing.GraphicsUnit]::Pixel)
    $g.Dispose()
    $cropPath = $shot -replace '\.png$', '-crop.png'
    $crop.Save($cropPath, [System.Drawing.Imaging.ImageFormat]::Png)
    $img.Dispose(); $crop.Dispose()

    # chat_ocr boxes 模式（内部 2x 放大后除回，坐标落在 4x 裁切图坐标系）→ /4 加回偏移
    $ocr = Invoke-WeComChatOcr -ImagePath $cropPath -Mode 'boxes'
    $mapped = @($ocr.boxes | ForEach-Object {
        [pscustomobject]@{
            text = [string]$_.text
            x0 = [int]($cx0 + $_.x0 / 4); x1 = [int]($cx0 + $_.x1 / 4)
            y0 = [int]($cy0 + $_.y0 / 4); y1 = [int]($cy0 + $_.y1 / 4)
        }
    })
    # × 清空按钮（x/X/× 单字符；+/十/士是右侧 + 按钮误读，不指示有内容）
    $xBox = @($mapped | Where-Object { $_.text -match '^[xX×]$' }) | Select-Object -First 1
    $placeholder = @($mapped | Where-Object { $_.text -match '^搜[索素粟]$' -or $_.text -eq '搜' }) | Select-Object -First 1
    # 占位符剔除只按占位符形态（^搜[索素粟]?$，同上方 $placeholder 判定）：整串含「搜」的
    # 真查询词（如「搜索测试」）不得被 substring 排除，否则回读 Content 恒空 → 误杀 UI_CHANGED
    $content = @($mapped | Where-Object {
        $_.text -notmatch '^搜[索素粟]?$' -and
        $_.text -notmatch '^[xX×+十士]$' -and
        $_.x1 -ge 212 -and $_.x1 -le 400 -and
        $_.text.Length -ge 1 -and $_.text.Length -le 12
    })
    $state = 'unknown'
    if ($placeholder -and -not $xBox) { $state = 'empty' }
    elseif ($xBox) { $state = 'has_content' }
    $phPos = $null
    if ($placeholder) { $phPos = @([int](($placeholder.x0 + $placeholder.x1) / 2), [int](($placeholder.y0 + $placeholder.y1) / 2)) }
    $ctPos = $null
    if ($content.Count -gt 0) {
        $c0 = $content[0]
        $ctPos = @([int](($c0.x0 + $c0.x1) / 2), [int](($c0.y0 + $c0.y1) / 2))
    }
    return [pscustomobject]@{
        State = $state
        Tokens = (($mapped | ForEach-Object { '{0}@{1}-{2},y{3}' -f $_.text, $_.x0, $_.x1, $_.y0 }) -join ' | ')
        TokensRaw = $mapped
        Content = (($content | ForEach-Object { $_.text }) -join '')
        PlaceholderPos = $phPos
        ContentPos = $ctPos
    }
}

function Clear-WeComSearchBoxV2 {
    # V2 清空（前提：搜索框已聚焦，即 Focus-WeComSearchBox 之后调用）：
    # attachstate Ctrl+A（VK 'A'=0x41 全选）→ 150ms → WM_KEYDOWN VK_DELETE(0x2E) →
    # 50ms → WM_KEYUP → 300ms。副作用：清空会关闭已打开的搜索 overlay（企微行为）。
    # 复核（必须 empty）由调用方做：清不空 UI_CHANGED fail-closed。
    # 返回 $true=按键序列已发出；$false=attach 失败（复核大概率不过，由调用方判）。
    param([Parameter(Mandatory)][int64]$MainHwnd)
    $ok = Send-WeComAttachChordKey -Hwnd $MainHwnd -Vk 0x41
    Start-Sleep -Milliseconds 150
    Initialize-WeComWin32
    $scan = [WeComWin32]::MapVirtualKeyW([uint32]0x2E, 0)
    $lpDown = [IntPtr](1 -bor ($scan -shl 16))
    $lpUp = [IntPtr](1 -bor ($scan -shl 16) -bor 0xC0000000L)
    [void][WeComWin32]::PostMessageW([IntPtr]$MainHwnd, 0x0100, [IntPtr]0x2E, $lpDown)
    Start-Sleep -Milliseconds 50
    [void][WeComWin32]::PostMessageW([IntPtr]$MainHwnd, 0x0101, [IntPtr]0x2E, $lpUp)
    Start-Sleep -Milliseconds 300
    return $ok
}

function Get-WeComChatAreaTitle {
    # 主窗口截图 → boxes OCR → 聊天区标题带（y0<0.07h 且 x0>0.20w）token 按视觉行拼接。
    # 2026-09-28 真机实测修订：搜索框内容/占位符（x0≈0.13-0.17w）与会话列表首行（≈0.17w）
    # 都在标题带同高位置，旧「整带拼接」会被污染成「搜索文件传输助手」/「XX XX」双拼；
    # 0.20w 阈值从几何上排除它们（聊天区标题实测 ≥0.22w 外部联系人 / 0.296w 普通会话），
    # 不再依赖搜索框是否已清空。行聚类：y 中心差 ≤14px 视为同一视觉行（标题高约 20-24px）。
    param([Parameter(Mandatory)][string]$ImagePath)
    $ocr = Invoke-WeComChatOcr -ImagePath $ImagePath -Mode 'boxes'
    Add-Type -AssemblyName System.Drawing
    $img = [System.Drawing.Image]::FromFile($ImagePath)
    $w = $img.Width; $h = $img.Height
    $img.Dispose()
    $tokens = @($ocr.boxes | Where-Object {
        [double]$_.y0 -lt ($h * 0.07) -and [double]$_.x0 -gt ($w * 0.20)
    } | Sort-Object { [double]$_.y0 }, { [double]$_.x0 })
    if ($tokens.Count -eq 0) { return '' }
    $anchorCy = ([double]$tokens[0].y0 + [double]$tokens[0].y1) / 2
    $line = @($tokens | Where-Object {
        $cy = ([double]$_.y0 + [double]$_.y1) / 2
        [Math]::Abs($cy - $anchorCy) -le 14
    })
    return (($line | ForEach-Object { [string]$_.text }) -join '')
}

function Invoke-WeComJev {
    # Jev System One 决策 API（2026-09-28 实测通）：
    # POST https://api.typesafe.ai/v1/systemone，body = {"state","model":"jev-latest","questions"}，
    # questions = {名: {type:"choice", instructions, criteria:{键:描述}}}；
    # 响应 answers.{名}.{choice, confidence, probabilities}。
    # key 只从环境变量读（进程 env，缺省回退用户级 env——MCP stdio Host 可能只传白名单
    # 环境变量），绝不入日志/命令行参数/artifact：Authorization 头走 -H @临时文件，
    # 请求体走 --data-binary @临时文件（UTF-8 无 BOM），用后即删；curl.exe 经
    # System.Diagnostics.Process 直启（同 Invoke-WeComChatOcr 的管道激活规避）。
    # 降级契约：无 key/超时(--max-time 15)/HTTP 错/响应异常 → used=false + reason，
    # 绝不抛错（调用方走规则兜底）。返回 @{ used; latency_ms; answers; reason }。
    param(
        [Parameter(Mandatory)][string]$StateText,
        [Parameter(Mandatory)][hashtable]$Questions
    )
    $key = $env:TYPESAFE_API_KEY
    if ([string]::IsNullOrEmpty($key)) { $key = [Environment]::GetEnvironmentVariable('TYPESAFE_API_KEY', 'User') }
    if ([string]::IsNullOrEmpty($key)) { return @{ used = $false; latency_ms = 0; reason = 'no_api_key'; answers = $null } }

    $bodyObj = @{ state = $StateText; model = 'jev-latest'; questions = $Questions }
    $reqPath = Join-Path $env:TEMP ('jev-req-' + [guid]::NewGuid().ToString('N') + '.json')
    $hdrPath = Join-Path $env:TEMP ('jev-hdr-' + [guid]::NewGuid().ToString('N') + '.txt')
    [System.IO.File]::WriteAllText($reqPath, (ConvertTo-Json -InputObject $bodyObj -Depth 10), (New-Object System.Text.UTF8Encoding $false))
    [System.IO.File]::WriteAllText($hdrPath, ('Content-Type: application/json' + "`n" + 'Authorization: Bearer ' + $key), (New-Object System.Text.UTF8Encoding $false))
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = 'curl.exe'
        $psi.Arguments = ('-s --fail --max-time 15 -X POST -H "@' + $hdrPath + '" --data-binary "@' + $reqPath + '" https://api.typesafe.ai/v1/systemone')
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true
        $proc = [System.Diagnostics.Process]::Start($psi)
        $out = $proc.StandardOutput.ReadToEnd()
        $proc.WaitForExit()
        $latency = [int]$sw.ElapsedMilliseconds
        if ($proc.ExitCode -ne 0) { return @{ used = $false; latency_ms = $latency; reason = ('curl_exit_' + $proc.ExitCode); answers = $null } }
        $parsed = $null
        try { $parsed = $out | ConvertFrom-Json } catch { $parsed = $null }
        if ($null -eq $parsed -or $null -eq $parsed.answers) {
            return @{ used = $false; latency_ms = $latency; reason = 'bad_response'; answers = $null }
        }
        return @{ used = $true; latency_ms = $latency; answers = $parsed.answers; reason = $null }
    } catch {
        return @{ used = $false; latency_ms = [int]$sw.ElapsedMilliseconds; reason = 'invoke_error'; answers = $null }
    } finally {
        Remove-Item -LiteralPath $reqPath, $hdrPath -ErrorAction SilentlyContinue
    }
}

function Focus-WeComSearchBox {
    # 聚焦主窗口搜索框（三级链，2026-09-28 真机验证）：
    # 1) 主路径：attachstate Ctrl+F（全后台 ~250ms）；
    # 2) attach 失败 → 裁切 OCR 顶部布局 → Jev 选 token → 点击其中心；
    # 3) Jev 不可用 → 规则：点击占位符 token 中心（token 匹配 搜[索素粟]）；
    #    占位符不可见（框内有残留内容）时点首个内容 token 中心（同在框内）。
    # 聚焦是否真正成功由调用方输入后的 OCR 回读兜底校验（此处不做二次确认）。
    # 返回聚焦方法 'ctrl_f' | 'jev_click' | 'placeholder_click'；三级全失败 → UI_CHANGED。
    param([Parameter(Mandatory)][int64]$MainHwnd)
    if (Send-WeComAttachChordKey -Hwnd $MainHwnd -Vk 0x46) {
        Start-Sleep -Milliseconds 150
        return 'ctrl_f'
    }
    $main = Get-WeComWindowInfo ([IntPtr]$MainHwnd)
    $sb = Get-WeComSearchBoxState -MainHwnd $MainHwnd -Tag 'focus'
    $tokens = @($sb.TokensRaw)
    if ($tokens.Count -gt 0) {
        $lines = @(); $crit = @{}
        for ($i = 0; $i -lt $tokens.Count; $i++) {
            $tk = $tokens[$i]
            $cx = [int](($tk.x0 + $tk.x1) / 2); $cy = [int](($tk.y0 + $tk.y1) / 2)
            $lines += ('T{0}: text={1} x0={2} x1={3} y0={4} y1={5} center=({6},{7})' -f $i, $tk.text, $tk.x0, $tk.x1, $tk.y0, $tk.y1, $cx, $cy)
            $crit[('T' + $i)] = ('OCR文本「' + $tk.text + '」中心坐标')
        }
        $stateText = '企业微信主窗口顶部区域 OCR 结果（窗口像素坐标）：' + "`n" + ($lines -join "`n") + "`n" + '任务：点击搜索输入框以聚焦它（准备输入搜索关键词）。'
        $r = Invoke-WeComJev -StateText $stateText -Questions @{
            click_which = @{ type = 'choice'; instructions = '点击哪个位置可以聚焦搜索输入框？'; criteria = $crit }
        }
        if ($r.used -and $r.answers.click_which.choice -match '^T(\d+)$') {
            $idx = [int]$Matches[1]
            if ($idx -lt $tokens.Count) {
                $tk = $tokens[$idx]
                $clickX = [int]($main.X + ($tk.x0 + $tk.x1) / 2)
                $clickY = [int]($main.Y + ($tk.y0 + $tk.y1) / 2)
                [void](Send-WeComClick -Hwnd $MainHwnd -ScreenX $clickX -ScreenY $clickY)
                Start-Sleep -Milliseconds 400
                return 'jev_click'
            }
        }
        $pos = $sb.PlaceholderPos
        if ($null -eq $pos) { $pos = $sb.ContentPos }
        if ($null -ne $pos) {
            [void](Send-WeComClick -Hwnd $MainHwnd -ScreenX ([int]($main.X + $pos[0])) -ScreenY ([int]($main.Y + $pos[1])))
            Start-Sleep -Milliseconds 400
            return 'placeholder_click'
        }
    }
    Throw-DriverError 'UI_CHANGED' '无法聚焦搜索框（attachstate Ctrl+F 失败且 OCR 降级路径均不可用），页面结构可能已变化'
}

# ---------- M6：发送 V2 共享助手（message-send 用；只增不改，M1-M5 函数不动） ----------

function Clear-WeComFocusedInput {
    # 清空当前聚焦的文本输入框内容（attachstate Ctrl+A 全选 + VK_DELETE 删除，与
    # Clear-WeComSearchBoxV2 同款原语）。M6 message-send 发送前会话复核失败时，用它清理
    # **自己刚输入**的会话草稿（防把文字留给错误会话）——只清自己输入的内容，
    # 绝不用于清除用户草稿（用户草稿场景一律 UI_CHANGED 中止，不触碰）。
    # 前提：目标输入框已聚焦（M6 驱动先点输入框并完成逐字输入，焦点确定在输入区）。
    # 复核是否真清空由调用方决定（尽力而为的防护动作，失败不阻断后续中止语义）。
    # 返回 $true=按键序列已发出；$false=attach 失败（调用方记录日志即可）。
    param([Parameter(Mandatory)][int64]$Hwnd)
    $ok = Send-WeComAttachChordKey -Hwnd $Hwnd -Vk 0x41
    Start-Sleep -Milliseconds 150
    Initialize-WeComWin32
    $scan = [WeComWin32]::MapVirtualKeyW([uint32]0x2E, 0)
    $lpDown = [IntPtr](1 -bor ($scan -shl 16))
    $lpUp = [IntPtr](1 -bor ($scan -shl 16) -bor 0xC0000000L)
    [void][WeComWin32]::PostMessageW([IntPtr]$Hwnd, 0x0100, [IntPtr]0x2E, $lpDown)
    Start-Sleep -Milliseconds 50
    [void][WeComWin32]::PostMessageW([IntPtr]$Hwnd, 0x0101, [IntPtr]0x2E, $lpUp)
    Start-Sleep -Milliseconds 300
    return $ok
}

# ---------- M7：图片发送共享助手（send-image 用；只增不改，M1-M6 函数不动） ----------

function Get-WeComRegionStddev {
    # 图像区域内灰度标准差（M7 send-image 图片预览检测；采样算法与阈值标定源自
    # experiments/probes/e5-image 真机验证 2026-09-28：输入区贴图后缩略图使方差
    # 9.8 → 36.5，判据 std1 > std0+8；发送后回落 std2 < std0+8）。
    # 区域坐标 = 截图图像像素坐标（窗口截图原点 = 窗口左上角，与 rect 同尺寸）；
    # 隔 4px 采样 ITU-R BT.601 灰度，返回总体标准差（double）。区域退化（n=0）返回 0。
    param(
        [Parameter(Mandatory)][string]$ImagePath,
        [Parameter(Mandatory)][int]$X0,
        [Parameter(Mandatory)][int]$Y0,
        [Parameter(Mandatory)][int]$X1,
        [Parameter(Mandatory)][int]$Y1
    )
    Add-Type -AssemblyName System.Drawing
    $img = [System.Drawing.Image]::FromFile($ImagePath)
    $bmp = New-Object System.Drawing.Bitmap $img
    $sum = 0.0; $sum2 = 0.0; $n = 0
    try {
        for ($x = $X0; $x -lt $X1; $x += 4) {
            for ($y = $Y0; $y -lt $Y1; $y += 4) {
                $p = $bmp.GetPixel($x, $y)
                $g = [int](0.299 * $p.R + 0.587 * $p.G + 0.114 * $p.B)
                $sum += $g; $sum2 += $g * $g; $n++
            }
        }
    } finally {
        $bmp.Dispose(); $img.Dispose()
    }
    if ($n -eq 0) { return 0.0 }
    $mean = $sum / $n
    return [Math]::Sqrt([Math]::Max(0, $sum2 / $n - $mean * $mean))
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
