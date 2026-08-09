[CmdletBinding()]
# 实验：用 UI Automation 找到搜一搜搜索框并点进去（确定性解决失焦）。
#
# 动机：生产 wechat-souyisou.ps1 打开搜一搜后直接「假设焦点已在搜索框」(:375-376)，
# 焦点经常没落进去，靠一堆重开/alt+tab 重试兜底。本实验验证：改用 UIA 在搜一搜
# Chromium 窗口里找到搜索框 Edit 控件、点进去，把焦点确定性拉回输入框。
#
# 实验控制（用户指定）：打开搜一搜后搜索框默认就有焦点，直接点+验证会假阳性。
# 所以先按 TAB 把焦点移走，再用 UIA 点回去，用 HasKeyboardFocus 做铁证验证
# （TAB 后=false、点击后=true，证明是点击生效而非默认焦点）。
#
# 复用：dot-source wechat-souyisou-lib.ps1，照搬生产脚本的 Win32 P/Invoke、DPI 固定、
# EnumWindows、激活、打开序列、UIA 遍历、点击路径。仅新增「找搜索框+点击+验证」。
#
# 运行（微信已登录、停在聊天列表，鼠标别乱动，运行前先关掉已打开的搜一搜窗）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File exp-souyisou-click-searchbox.ps1
param(
    [string]$ProbeText = '测试焦点',
    [ValidateRange(500,30000)][int]$WaitMilliseconds = 2500,
    [ValidateRange(5000,60000)][int]$RenderTimeoutMilliseconds = 15000,
    [ValidateRange(0,5)][int]$DefocusTabCount = 1,
    [switch]$SubmitEnter,
    [switch]$ClickMode,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = [Text.Encoding]::UTF8
. (Join-Path $PSScriptRoot 'wechat-souyisou-lib.ps1')

$diagLog = Join-Path $env:TEMP 'wechat_diag.log'
function Write-Diag([string]$Msg) {
    Add-Content -Path $diagLog -Value "[$([DateTimeOffset]::Now.ToString('HH:mm:ss'))] $Msg"
}
function Write-Result([hashtable]$Value) {
    Write-Diag "Write-Result: ok=$($Value.ok) search_box_found=$($Value.search_box_found) kbfocus_after_click=$($Value.kbfocus_after_click) readback_matched=$($Value.readback_matched)"
    [Console]::Out.WriteLine(($Value | ConvertTo-Json -Depth 12 -Compress))
}

# NEW：扫描插件 UIA 树，收集所有 Edit 控件 + 全树 ControlType 直方图。
# 遍历范式照搬生产 Get-WeixinUiaResultDescriptors (wechat-souyisou.ps1:244-287)。
function Get-SouyisouEditDescriptors([IntPtr]$Hwnd) {
    $root = [Windows.Automation.AutomationElement]::FromHandle($Hwnd)
    if ($null -eq $root) { return [pscustomobject]@{ Edits=@(); Histogram=@{}; TotalNodes=0 } }
    $nodes = $root.FindAll(
        [Windows.Automation.TreeScope]::Descendants,
        [Windows.Automation.Condition]::TrueCondition)
    $edits = New-Object Collections.Generic.List[object]
    $histogram = @{}
    foreach ($node in $nodes) {
        try {
            $current = $node.Current
            $ct = [string]$current.ControlType.ProgrammaticName.Replace('ControlType.','')
            if ($histogram.ContainsKey($ct)) { $histogram[$ct]++ } else { $histogram[$ct] = 1 }
            if ($ct -ne 'Edit') { continue }
            $vp = $null
            $supportsValue = $node.TryGetCurrentPattern(
                [Windows.Automation.ValuePattern]::Pattern, [ref]$vp)
            $b = $current.BoundingRectangle
            # 先把 if 表达式算成变量再放进 hashtable：直接写 Value=if(){...}else{...}
            # 会让 PS 解析器在花括号上错乱（hashtable 属性值不要直接放 if 表达式）。
            $valueStr = if ($supportsValue) { [string]$vp.Current.Value } else { $null }
            $edits.Add([pscustomobject]@{
                Name=[string]$current.Name
                ControlType=$ct
                IsOffscreen=[bool]$current.IsOffscreen
                HasKeyboardFocus=[bool]$current.HasKeyboardFocus
                SupportsValue=[bool]$supportsValue
                Value=$valueStr
                Left=[double]$b.Left; Top=[double]$b.Top
                Width=[double]$b.Width; Height=[double]$b.Height
            })
        } catch {
            # Chromium UIA 节点可能在枚举后失效（见生产 :279-282），忽略单节点。
            continue
        }
    }
    [pscustomobject]@{ Edits=$edits.ToArray(); Histogram=$histogram; TotalNodes=$nodes.Count }
}

# NEW：挑搜索框。DryRun 实测：搜一搜落地页恰好 2 个 Edit——1 个矩形无效
#（UIA 对无位置节点返回 {+∞,+∞,-∞,-∞}，点不了），另 1 个是搜索框（合法矩形 + 落地默认有焦点）。
# 所以判据定为：只留矩形合法（有限、正面积）的 Edit，优先有键盘焦点、其次最宽。
# （早先用「窗顶30%内/宽≥窗宽35%」的位置启发式，但因窗口尺寸假设错误把真搜索框排除了，弃用。）
function Find-SouyisouSearchBox([object]$Snapshot, [object]$Rect) {
    if (-not $Snapshot -or $Snapshot.Edits.Count -eq 0) { return $null }
    # 矩形合法性：宽高>0（排除 -∞/NaN/0），且左上顶 < +∞（排除 UIA 空位置节点）。
    # 注意：Windows PowerShell 5.1 的 .NET Framework 没有 [double]::IsFinite（.NET Core 才有），
    # 用下面的比较代替（PositiveInfinity / 比较运算在 .NET Framework 4.x 都有；NaN 与任何值比较均为 false，自动排除）。
    $valid = @($Snapshot.Edits | Where-Object {
        [double]$_.Width -gt 0 -and [double]$_.Height -gt 0 -and
        [double]$_.Left -lt [double]::PositiveInfinity -and
        [double]$_.Top -lt [double]::PositiveInfinity
    })
    if ($valid.Count -eq 0) { return $null }
    $valid | Sort-Object `
        @{Expression={ if([bool]$_.HasKeyboardFocus){0}else{1} }}, `
        @{Expression={ [double]$_.Width };Descending=$true} |
        Select-Object -First 1
}

# NEW：UIA 原生写入搜索框——重新枚举取合法矩形 Edit 的「活」元素，SetFocus 聚焦后
# ValuePattern.SetValue 直接写值。不依赖窗口前台/鼠标，避开 Win32 前台抢占（鼠标点击不稳的根因）。
function Invoke-SouyisouSearchBoxInput([IntPtr]$Hwnd, [string]$Text) {
    $root = [Windows.Automation.AutomationElement]::FromHandle($Hwnd)
    if ($null -eq $root) { return @{ found=$false } }
    $nodes = $root.FindAll([Windows.Automation.TreeScope]::Descendants, [Windows.Automation.Condition]::TrueCondition)
    $target = $null
    foreach ($node in $nodes) {
        try {
            $current = $node.Current
            if ([string]$current.ControlType.ProgrammaticName.Replace('ControlType.','') -ne 'Edit') { continue }
            $b = $current.BoundingRectangle
            if ([double]$b.Width -gt 0 -and [double]$b.Height -gt 0 -and
                [double]$b.Left -lt [double]::PositiveInfinity -and [double]$b.Top -lt [double]::PositiveInfinity) {
                $target = $node; break
            }
        } catch { continue }
    }
    if ($null -eq $target) { return @{ found=$false } }
    $r = @{
        found=$true
        kbfocus_before=[bool]$target.Current.HasKeyboardFocus
        set_value_ok=$false
        kbfocus_after_setvalue=$false
        readback=$null
        matched=$false
    }
    # 不调 SetFocus：要证明 SetValue 本身不依赖焦点（调用前已用 TAB 把焦点移走）。
    try {
        $vp = $null
        if ($target.TryGetCurrentPattern([Windows.Automation.ValuePattern]::Pattern, [ref]$vp)) {
            $vp.SetValue($Text)
            $r.set_value_ok = $true
            Start-Sleep -Milliseconds 200
            $r.readback = [string]$vp.Current.Value
            $r.matched = [string]::Equals($r.readback, $Text, [StringComparison]::Ordinal)
            try { $r.kbfocus_after_setvalue = [bool]$target.Current.HasKeyboardFocus } catch {}
        }
    } catch { $r.set_value_error = $_.Exception.Message }
    $r
}

try {
    Write-Host '===== 实验：用 UIA 找到搜一搜搜索框并点进去 ====='
    Write-Host ("ProbeText='{0}' Wait={1}ms RenderTimeout={2}ms DefocusTab={3} SubmitEnter={4} DryRun={5}" -f `
        $ProbeText,$WaitMilliseconds,$RenderTimeoutMilliseconds,$DefocusTabCount,[bool]$SubmitEnter,[bool]$DryRun)
    Write-Diag "EXP START: probe='$ProbeText' wait=$WaitMilliseconds render_timeout=$RenderTimeoutMilliseconds tab=$DefocusTabCount submit=$([bool]$SubmitEnter) dry=$([bool]$DryRun)"

    # ---- 加载 UIA 程序集 + Win32 P/Invoke（照搬生产 wechat-souyisou.ps1:113-147）----
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
 public struct RECT { public int Left,Top,Right,Bottom; }
}
"@)

    # ---- 固定 Per-Monitor V2：UIA 坐标与 SetCursorPos 同为物理屏坐标（照搬 :148-159）----
    $perMonitorV2 = [IntPtr](-4)
    $previousDpiContext = [WechatSouyisouWin32]::SetThreadDpiAwarenessContext($perMonitorV2)
    if ($previousDpiContext -eq [IntPtr]::Zero -or
        -not [WechatSouyisouWin32]::AreDpiAwarenessContextsEqual(
            [WechatSouyisouWin32]::GetThreadDpiAwarenessContext(), $perMonitorV2)
    ) { throw 'DPI_AWARENESS_FAILED' }

    # ---- EnumWindows 收集可见窗口（照搬 :160-171）----
    $windows = @()
    [WechatSouyisouWin32]::EnumWindows({
        param($h,$unused)
        if ([WechatSouyisouWin32]::IsWindowVisible($h)) {
            $wpid = [uint32]0
            [void][WechatSouyisouWin32]::GetWindowThreadProcessId($h,[ref]$wpid)
            try {
                $p = Get-Process -Id $wpid -ErrorAction Stop
                $script:windows += [pscustomobject]@{
                    Hwnd=$h.ToInt64(); MainWindowHwnd=$p.MainWindowHandle.ToInt64()
                    Visible=$true; ProcessPath=$p.Path
                }
            } catch {}
        }; return $true
    },[IntPtr]::Zero) | Out-Null

    $main = Select-WeixinMainWindow $windows
    Write-Host "主窗口: hwnd=$($main.Hwnd)"

    # ---- 激活主窗口（照搬 :178-198 接线）----
    $getThread = {
        param($h)
        $wpid = [uint32]0
        [void][WechatSouyisouWin32]::GetWindowThreadProcessId([IntPtr]$h,[ref]$wpid)
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
    if (-not (& $activateWindow ([int64]$main.Hwnd))) { throw 'WX_ACTIVATION_FAILED' }

    # ---- 前台身份辅助（照搬 :200-223，RequirePlugin 形式）----
    function Get-WindowIdentityByHwnd([int64]$Hwnd) {
        if (-not [WechatSouyisouWin32]::IsWindow([IntPtr]$Hwnd)) { return $null }
        $cpid = [uint32]0
        [void][WechatSouyisouWin32]::GetWindowThreadProcessId([IntPtr]$Hwnd,[ref]$cpid)
        try { $cproc = Get-Process -Id $cpid -ErrorAction Stop } catch { return $null }
        $cclass = New-Object Text.StringBuilder 256
        $ctitle = New-Object Text.StringBuilder 256
        [void][WechatSouyisouWin32]::GetClassName([IntPtr]$Hwnd,$cclass,$cclass.Capacity)
        [void][WechatSouyisouWin32]::GetWindowText([IntPtr]$Hwnd,$ctitle,$ctitle.Capacity)
        [pscustomobject]@{
            Hwnd=$Hwnd; ProcessPath=$cproc.Path; ProcessId=[uint32]$cpid
            ClassName=$cclass.ToString(); Title=$ctitle.ToString()
        }
    }
    function Get-TrustedForegroundIdentity([switch]$RequirePlugin) {
        $cur = [WechatSouyisouWin32]::GetForegroundWindow()
        if ($cur -eq [IntPtr]::Zero) { return $null }
        $id = Get-WindowIdentityByHwnd $cur.ToInt64()
        if (-not $id) { return $null }
        if (-not (Test-WeixinForegroundIdentity $id ([int64]$main.Hwnd))) { return $null }
        if ($RequirePlugin -and [int64]$id.Hwnd -eq [int64]$main.Hwnd) { return $null }
        $id
    }

    # ---- 打开搜一搜：Ctrl+F/↓/Enter（照搬 :312-322）----
    $virtualKeyMap = @{CTRL=0x11;TAB=0x09;F=0x46;DOWN=0x28;ENTER=0x0D;A=0x41;C=0x43;V=0x56;W=0x57}
    $mainGuard = { [WechatSouyisouWin32]::GetForegroundWindow().ToInt64() -eq [int64]$main.Hwnd }
    $send = { param($ks,$guard) Invoke-SafeKeyChord $ks `
        {param($k,$up) [WechatSouyisouWin32]::keybd_event($virtualKeyMap[$k],0,$(if($up){2}else{0}),[IntPtr]::Zero)} `
        {Start-Sleep -Milliseconds 40} $guard }

    Write-Host '打开搜一搜: Ctrl+F -> Down -> Enter ...'
    if (-not (& $mainGuard) -and -not (& $activateWindow ([int64]$main.Hwnd))) { throw 'WX_ACTIVATION_FAILED' }
    & $send @('CTRL','F') $mainGuard; Start-Sleep -Milliseconds 400
    & $send @('DOWN')     $mainGuard; Start-Sleep -Milliseconds 250
    & $send @('ENTER')    $mainGuard; Start-Sleep -Milliseconds $WaitMilliseconds

    $pluginIdentity = Get-TrustedForegroundIdentity -RequirePlugin
    if (-not $pluginIdentity) { throw 'SOUYISOU_WINDOW_UNTRUSTED' }
    $pluginHwnd = [IntPtr][int64]$pluginIdentity.Hwnd
    Write-Host "搜一搜插件窗口: hwnd=$($pluginHwnd.ToInt64())"

    $pluginGuard = {
        $id = Get-TrustedForegroundIdentity -RequirePlugin
        $null -ne $id -and [int64]$id.Hwnd -eq $pluginHwnd.ToInt64()
    }

    # ---- 插件窗口 rect（照搬 :508-516）----
    $pluginRect = New-Object WechatSouyisouWin32+RECT
    if (-not [WechatSouyisouWin32]::GetWindowRect($pluginHwnd,[ref]$pluginRect)) {
        throw 'WINDOW_RECT_FAILED'
    }

    # ---- 轮询等渲染：出现 ≥1 个非离屏 Value-Edit 即视为渲染完成（你要的「等完全渲染好再找」）----
    $renderDeadline = [DateTimeOffset]::UtcNow.AddMilliseconds($RenderTimeoutMilliseconds)
    $snapshot = $null
    $sample = 0
    Write-Host "轮询等渲染（最多 ${RenderTimeoutMilliseconds}ms）..."
    while ([DateTimeOffset]::UtcNow -lt $renderDeadline) {
        $sample++
        $snapshot = Get-SouyisouEditDescriptors $pluginHwnd
        $onValue = @($snapshot.Edits | Where-Object { -not $_.IsOffscreen -and $_.SupportsValue })
        $histLine = ($snapshot.Histogram.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ' '
        Write-Host ("  poll #{0}: total={1} edits={2} onScreenValueEdits={3}" -f $sample,$snapshot.TotalNodes,$snapshot.Edits.Count,$onValue.Count)
        Write-Diag "render poll #$sample total=$($snapshot.TotalNodes) edits=$($snapshot.Edits.Count) onValue=$($onValue.Count) hist[$histLine]"
        if ($onValue.Count -gt 0) { break }
        Start-Sleep -Milliseconds 400
    }
    Start-Sleep -Milliseconds 400  # 动画落定
    if ($null -eq $snapshot) { $snapshot = Get-SouyisouEditDescriptors $pluginHwnd }

    # ---- UIA 诊断 dump（本实验核心交付：看 Chromium 到底暴露了什么）----
    Write-Host '----- UIA 控件类型直方图 -----'
    if ($snapshot.Histogram.Count -eq 0) {
        Write-Host '  (空树：UIA 取不到任何节点，该 Chromium 构建可能没开辅助功能)'
    } else {
        $snapshot.Histogram.GetEnumerator() | Sort-Object Value -Descending | ForEach-Object {
            Write-Host ("  {0,-18} {1}" -f $_.Key, $_.Value)
        }
    }
    Write-Host "----- Edit 控件 dump（共 $($snapshot.Edits.Count) 个）-----"
    $ei=0
    foreach ($e in $snapshot.Edits) {
        $ei++
        Write-Host ("  Edit[{0}] name='{1}' offscreen={2} kbfocus={3} valueSupported={4} value='{5}' rect=({6:F0},{7:F0},{8:F0},{9:F0})" -f `
            $ei,$e.Name,$e.IsOffscreen,$e.HasKeyboardFocus,$e.SupportsValue,$e.Value,$e.Left,$e.Top,$e.Width,$e.Height)
    }

    if ($DryRun) {
        Write-Host '-DryRun：只做 UIA 诊断，不 TAB/点击/打字'
        Write-Result @{
            ok=$true; executed=$true; dry_run=$true
            total_nodes=$snapshot.TotalNodes; edit_count=$snapshot.Edits.Count
            histogram=$snapshot.Histogram
            search_box_found=$false
        }
        exit 0
    }

    # ---- 默认：UIA 原生写入（SetFocus + ValuePattern.SetValue），不依赖窗口前台/鼠标 ----
    # 搜索框是 <input>，SetValue 直接写值、SetFocus 直接聚焦，绕开 mouse 点击的 Win32 前台抢占。
    # -ClickMode 才走下面的 TAB+鼠标点击+剪贴板打字路径（保留对比；前台抢占下不稳）。
    if (-not $ClickMode) {
        # 用户要求：先 TAB 把焦点从搜索框移走，再 SetValue，证明 SetValue 不依赖焦点。
        $boxBeforeTab = Find-SouyisouSearchBox $snapshot $pluginRect
        Write-Host ("TAB 前: 搜索框候选={0} HasKeyboardFocus={1}" -f `
            ($null -ne $boxBeforeTab), $(if($boxBeforeTab){$boxBeforeTab.HasKeyboardFocus}else{'-'}))
        for ($t=0; $t -lt $DefocusTabCount; $t++) {
            if (-not (& $pluginGuard)) { Write-Host '  (插件已失焦，跳过 TAB)'; break }
            & $send @('TAB') $pluginGuard
            Start-Sleep -Milliseconds 200
        }
        Start-Sleep -Milliseconds 250
        Write-Host '--- UIA 原生写入搜索框（ValuePattern.SetValue；此时焦点已被 TAB 移走）---'
        $inputResult = Invoke-SouyisouSearchBoxInput $pluginHwnd $ProbeText
        Write-Host ("  found={0} kbfocus_before(TAB后)={1} set_value_ok={2} kbfocus_after_setvalue={3} readback='{4}' matched={5} err={6}" -f `
            $inputResult.found, $inputResult.kbfocus_before, $inputResult.set_value_ok, $inputResult.kbfocus_after_setvalue, `
            $inputResult.readback, $inputResult.matched, $(if($inputResult.set_value_error){$inputResult.set_value_error}else{'-'}))
        if (-not $inputResult.found) {
            Write-Result @{
                ok=$false; executed=$true; error_code='SEARCHBOX_NOT_FOUND'; mode='set_value'
                total_nodes=$snapshot.TotalNodes; edit_count=$snapshot.Edits.Count
                histogram=$snapshot.Histogram; search_box_found=$false
            }
            exit 1
        }
        $submitted = $false
        if ($SubmitEnter -and $inputResult.matched) {
            if (-not (& $pluginGuard)) {
                Write-Host '  (SubmitEnter：插件非前台，回车可能无效；跳过)'
            } else {
                & $send @('ENTER') $pluginGuard
                $submitted = $true
                Write-Host '  已按回车（提交真实搜索）'
            }
        }
        Write-Host '实验完成（SetValue 模式，TAB 已先移走焦点）。看屏幕确认搜索框已写入探针词。'
        Write-Result @{
            ok=$true; executed=$true; mode='set_value'
            total_nodes=$snapshot.TotalNodes; edit_count=$snapshot.Edits.Count
            search_box_found=$true
            defocus_tab_count=$DefocusTabCount
            kbfocus_before_tab=if($boxBeforeTab){[bool]$boxBeforeTab.HasKeyboardFocus}else{$null}
            kbfocus_before_setvalue=$inputResult.kbfocus_before
            kbfocus_after_setvalue=$inputResult.kbfocus_after_setvalue
            set_value_ok=$inputResult.set_value_ok
            readback_matched=$inputResult.matched
            readback=$inputResult.readback
            probe_text=$ProbeText
            submitted=$submitted
        }
        exit 0
    }

    # ---- TAB 移走焦点（用户指定的实验控制）----
    $boxBeforeTab = Find-SouyisouSearchBox $snapshot $pluginRect
    Write-Host ("TAB 前: 搜索框候选={0} HasKeyboardFocus={1}" -f `
        ($null -ne $boxBeforeTab), $(if($boxBeforeTab){$boxBeforeTab.HasKeyboardFocus}else{'-'}))
    for ($t=0; $t -lt $DefocusTabCount; $t++) {
        if (-not (& $pluginGuard)) { Write-Host '  (插件已失焦，跳过 TAB)'; break }
        & $send @('TAB') $pluginGuard
        Start-Sleep -Milliseconds 200
    }
    Start-Sleep -Milliseconds 250
    $afterTabSnap = Get-SouyisouEditDescriptors $pluginHwnd
    $boxAfterTab = Find-SouyisouSearchBox $afterTabSnap $pluginRect
    if (-not $boxAfterTab) {
        Write-Host 'TAB 后未找到搜索框候选（见上方 dump 判断 Chromium 是否暴露了 Edit）'
        Write-Result @{
            ok=$false; executed=$true; error_code='SEARCHBOX_NOT_FOUND'
            total_nodes=$snapshot.TotalNodes; edit_count=$snapshot.Edits.Count
            histogram=$snapshot.Histogram; search_box_found=$false
        }
        exit 1
    }
    Write-Host ("TAB 后: 搜索框 HasKeyboardFocus={0}（期望 false=焦点已移走）" -f $boxAfterTab.HasKeyboardFocus)

    # ---- 点击搜索框（照搬 :714-748 点击路径 + :503-507 的失焦恢复）----
    # 实测：搜一搜窗在渲染轮询/dump 期间前台会漂走（多半漂回微信主窗），所以点击前
    # 要像生产那样先恢复插件前台（Test-OrRestoreTrustedForeground），否则 mouse_event
    # 会点错窗口。restore 失败则打印当前前台身份做诊断。
    $restorePluginForeground = {
        Test-OrRestoreTrustedForeground $pluginGuard `
            { & $activateWindow ([int64]$pluginHwnd) } `
            { Start-Sleep -Milliseconds 250 }
    }
    $windowDpi = [int][WechatSouyisouWin32]::GetDpiForWindow($pluginHwnd)
    if ($windowDpi -lt 96 -or $windowDpi -gt 480) { throw 'WINDOW_DPI_INVALID' }
    $cx=[double]$boxAfterTab.Left + [double]$boxAfterTab.Width/2
    $cy=[double]$boxAfterTab.Top + [double]$boxAfterTab.Height/2
    $physicalPoint = ConvertTo-WeixinPhysicalClickPoint $cx $cy $windowDpi 'PerMonitorV2'
    $x=[int]$physicalPoint.x; $y=[int]$physicalPoint.y
    Write-Host ("点击搜索框中心: ({0},{1}) dpi={2}" -f $x,$y,$windowDpi)
    if (-not (& $pluginGuard) -and -not (& $restorePluginForeground)) {
        $fg = [WechatSouyisouWin32]::GetForegroundWindow()
        $fgId = Get-WindowIdentityByHwnd $fg.ToInt64()
        Write-Host ("点击前前台丢失且恢复失败: fg_hwnd={0} proc={1} class={2} title={3}" -f `
            $(if($fgId){[int64]$fgId.Hwnd}else{0}), `
            $(if($fgId){[IO.Path]::GetFileName([string]$fgId.ProcessPath)}else{'?'}), `
            $(if($fgId){[string]$fgId.ClassName}else{'?'}), `
            $(if($fgId){[string]$fgId.Title}else{'?'}))
        throw 'FOREGROUND_LOST'
    }
    if (-not [WechatSouyisouWin32]::SetCursorPos($x,$y)) { throw 'MOUSE_POSITION_FAILED' }
    Start-Sleep -Milliseconds 120
    if (-not (& $pluginGuard) -and -not (& $restorePluginForeground)) { throw 'FOREGROUND_LOST' }
    Invoke-SafeMouseClick {
        param($up)
        [WechatSouyisouWin32]::mouse_event($(if($up){4}else{2}),0,0,0,[IntPtr]::Zero)
    } $pluginGuard

    # ---- 验证焦点回来：重新扫描读 HasKeyboardFocus（不复用旧节点引用，Chromium UIA 会失效）----
    Start-Sleep -Milliseconds 400
    $afterClickSnap = Get-SouyisouEditDescriptors $pluginHwnd
    $boxAfterClick = Find-SouyisouSearchBox $afterClickSnap $pluginRect
    $focusRestored = if ($boxAfterClick) { [bool]$boxAfterClick.HasKeyboardFocus } else { $false }
    Write-Host ("点击后: 搜索框 HasKeyboardFocus={0}（true=点击成功把焦点拉回）" -f $focusRestored)

    # ---- 打字探针（你要的「打字，我来看」）----
    $readbackMatched = $false
    if ($focusRestored) {
        if (-not (& $pluginGuard) -and -not (& $restorePluginForeground)) { throw 'FOREGROUND_LOST' }
        [Windows.Forms.Clipboard]::SetText($ProbeText)
        & $send @('CTRL','A') $pluginGuard
        & $send @('CTRL','V') $pluginGuard
        Start-Sleep -Milliseconds 250
        $afterTypeSnap = Get-SouyisouEditDescriptors $pluginHwnd
        $boxAfterType = Find-SouyisouSearchBox $afterTypeSnap $pluginRect
        if ($boxAfterType) {
            $actual = [string]$boxAfterType.Value
            $readbackMatched = [string]::Equals($actual,$ProbeText,[StringComparison]::Ordinal)
            Write-Host ("打字读回: actual='{0}' matched={1}（屏幕上搜索框应出现探针词）" -f $actual,$readbackMatched)
        } else {
            Write-Host '打字后未能重新定位搜索框（Value 读回失败，但屏幕上可肉眼确认）'
        }
    } else {
        Write-Host '焦点未回到搜索框，跳过打字'
    }

    # ---- 可选：回车触发真实搜索（默认关，无副作用）----
    $submitted = $false
    if ($SubmitEnter -and $readbackMatched) {
        & $send @('ENTER') $pluginGuard
        $submitted = $true
        Write-Host '已按回车（提交真实搜索）'
    }

    Write-Host '实验完成（搜一搜窗保持打开，你看完手动关即可）'
    Write-Result @{
        ok=$true; executed=$true
        total_nodes=$snapshot.TotalNodes; edit_count=$snapshot.Edits.Count
        search_box_found=$true
        search_box=@{left=$boxAfterTab.Left;top=$boxAfterTab.Top;width=$boxAfterTab.Width;height=$boxAfterTab.Height}
        kbfocus_before_tab=if($boxBeforeTab){[bool]$boxBeforeTab.HasKeyboardFocus}else{$null}
        kbfocus_after_tab=[bool]$boxAfterTab.HasKeyboardFocus
        kbfocus_after_click=$focusRestored
        click_point=@{x=$x;y=$y}
        readback_matched=$readbackMatched
        probe_text=$ProbeText
        submitted=$submitted
    }
} catch {
    $code = $_.Exception.Message
    Write-Host "实验失败: $code"
    Write-Diag "EXP FAIL: $code"
    Write-Result @{
        ok=$false; executed=$true; error_code=$code
        search_box_found=$false; kbfocus_after_click=$null; readback_matched=$false
    }
    exit 1
}
