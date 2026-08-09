[CmdletBinding()]
# 实验：验证结果卡片能否用 UIA InvokePattern.Invoke() 打开详情页（绕开鼠标点击的前台抢占）。
#
# 动机：生产 wechat-souyisou.ps1 点结果卡片进详情用 mouse_event，前台被抢占就
# FOREGROUND_LOST（中国黄金协会 周洲 卡在这）。卡片是 UIA Button/ListItem 且支持
# InvokePattern（Get-WeixinUiaResultDescriptors 已校验 supportsInvoke）。本实验验证
# Invoke() 能否打开详情，且不需要前台/鼠标——和 SetValue 同思路。
#
# 判定：生产点击成功后前台会变成「新的详情 HWND」（:759-766 Add-WeixinWindowSessionForeground
# 'detail'）。所以 Invoke 后若出现新的 WeChatAppEx 窗口 = 详情打开了。
#
# 运行（微信登录、停聊天列表、关掉已开的搜一搜、鼠标别动）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File exp-souyisou-invoke-detail.ps1
param(
    [string]$AssociationName = '中国黄金协会',
    [string]$PersonName = '周洲',
    [ValidateRange(500,30000)][int]$WaitMilliseconds = 2500,
    [ValidateRange(5000,60000)][int]$CardTimeoutMilliseconds = 20000
)

$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = [Text.Encoding]::UTF8
. (Join-Path $PSScriptRoot 'wechat-souyisou-lib.ps1')

$query = "$AssociationName $PersonName 联系人"
$diagLog = Join-Path $env:TEMP 'wechat_diag.log'
function Write-Diag([string]$Msg) {
    Add-Content -Path $diagLog -Value "[$([DateTimeOffset]::Now.ToString('HH:mm:ss'))] $Msg"
}
function Write-Result([hashtable]$Value) {
    Write-Diag "Write-Result: ok=$($Value.ok) invoke_ok=$($Value.invoke_ok) new_detail_window=$($Value.new_detail_window)"
    [Console]::Out.WriteLine(($Value | ConvertTo-Json -Depth 10 -Compress))
}

try {
    Write-Host '===== 实验：InvokePattern 打开搜一搜结果详情 ====='
    Write-Host "query='$query'"
    Write-Diag "EXP-INVOKE START: query='$query'"

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
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
 [DllImport("user32.dll")] public static extern void mouse_event(uint f,uint x,uint y,uint d,IntPtr e);
 [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr c);
 [DllImport("user32.dll")] public static extern IntPtr GetThreadDpiAwarenessContext();
 [DllImport("user32.dll")] public static extern bool AreDpiAwarenessContextsEqual(IntPtr a,IntPtr b);
 [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr h);
 public struct RECT { public int Left,Top,Right,Bottom; }
}
"@)

    $perMonitorV2 = [IntPtr](-4)
    $prev = [WechatSouyisouWin32]::SetThreadDpiAwarenessContext($perMonitorV2)
    if ($prev -eq [IntPtr]::Zero -or
        -not [WechatSouyisouWin32]::AreDpiAwarenessContextsEqual(
            [WechatSouyisouWin32]::GetThreadDpiAwarenessContext(), $perMonitorV2)
    ) { throw 'DPI_AWARENESS_FAILED' }

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

    $getThread = { param($h); $wpid=[uint32]0; [void][WechatSouyisouWin32]::GetWindowThreadProcessId([IntPtr]$h,[ref]$wpid) }
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

    function Get-WindowIdentityByHwnd([int64]$Hwnd) {
        if (-not [WechatSouyisouWin32]::IsWindow([IntPtr]$Hwnd)) { return $null }
        $cpid = [uint32]0
        [void][WechatSouyisouWin32]::GetWindowThreadProcessId([IntPtr]$Hwnd,[ref]$cpid)
        try { $cproc = Get-Process -Id $cpid -ErrorAction Stop } catch { return $null }
        $cc = New-Object Text.StringBuilder 256; $ct = New-Object Text.StringBuilder 256
        [void][WechatSouyisouWin32]::GetClassName([IntPtr]$Hwnd,$cc,$cc.Capacity)
        [void][WechatSouyisouWin32]::GetWindowText([IntPtr]$Hwnd,$ct,$ct.Capacity)
        [pscustomobject]@{ Hwnd=$Hwnd; ProcessPath=$cproc.Path; ProcessId=[uint32]$cpid; ClassName=$cc.ToString(); Title=$ct.ToString() }
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

    # 重新枚举当前可信插件 HWND（修正版：用 script: 收集）
    function Get-TrustedPluginHwndsNow([int64]$MainHwnd) {
        $script:_pluginHwnds = New-Object Collections.Generic.List[int64]
        [WechatSouyisouWin32]::EnumWindows({
            param($h,$unused)
            if ([WechatSouyisouWin32]::IsWindowVisible($h)) {
                $id = Get-WindowIdentityByHwnd $h.ToInt64()
                if ($null -ne $id -and [int64]$id.Hwnd -ne $MainHwnd -and
                    (Test-WeixinForegroundIdentity $id $MainHwnd)) {
                    $script:_pluginHwnds.Add([int64]$id.Hwnd)
                }
            }; return $true
        }, [IntPtr]::Zero) | Out-Null
        $script:_pluginHwnds.ToArray()
    }

    $virtualKeyMap = @{CTRL=0x11;F=0x46;DOWN=0x28;ENTER=0x0D;A=0x41;C=0x43;V=0x56;W=0x57}
    $mainGuard = { [WechatSouyisouWin32]::GetForegroundWindow().ToInt64() -eq [int64]$main.Hwnd }
    $send = { param($ks,$guard) Invoke-SafeKeyChord $ks `
        {param($k,$up) [WechatSouyisouWin32]::keybd_event($virtualKeyMap[$k],0,$(if($up){2}else{0}),[IntPtr]::Zero)} `
        {Start-Sleep -Milliseconds 40} $guard }

    Write-Host '打开搜一搜: Ctrl+F -> Down -> Enter ...'
    & $send @('CTRL','F') $mainGuard; Start-Sleep -Milliseconds 400
    & $send @('DOWN')     $mainGuard; Start-Sleep -Milliseconds 250
    & $send @('ENTER')    $mainGuard; Start-Sleep -Milliseconds $WaitMilliseconds

    $pluginIdentity = Get-TrustedForegroundIdentity -RequirePlugin
    if (-not $pluginIdentity) { throw 'SOUYISOU_WINDOW_UNTRUSTED' }
    $pluginHwnd = [IntPtr][int64]$pluginIdentity.Hwnd
    Write-Host "搜一搜插件窗口: hwnd=$($pluginHwnd.ToInt64())"

    # SetValue 写入查询（生产 lib 函数，已验证）+ 回车提交
    Write-Host "SetValue 写入查询: '$query'"
    $readback = Invoke-WeixinSouyisouSetValueAndReadback $pluginHwnd $query
    Write-Host "  readback='$readback' matched=$([string]::Equals($readback,$query,[StringComparison]::Ordinal))"
    $pluginGuard = { $id = Get-TrustedForegroundIdentity -RequirePlugin; $null -ne $id -and [int64]$id.Hwnd -eq $pluginHwnd.ToInt64() }
    if (& $pluginGuard) { & $send @('ENTER') $pluginGuard }
    Start-Sleep -Milliseconds 2500

    # 轮询等结果卡片（Button/ListItem 支持 InvokePattern）出现
    $cardDeadline = [DateTimeOffset]::UtcNow.AddMilliseconds($CardTimeoutMilliseconds)
    $cards = @()
    $sample = 0
    Write-Host "轮询等结果卡片（最多 ${CardTimeoutMilliseconds}ms）..."
    while ([DateTimeOffset]::UtcNow -lt $cardDeadline) {
        $sample++
        $root = [Windows.Automation.AutomationElement]::FromHandle($pluginHwnd)
        if ($null -ne $root) {
            $nodes = $root.FindAll([Windows.Automation.TreeScope]::Descendants, [Windows.Automation.Condition]::TrueCondition)
            $tmp = New-Object Collections.Generic.List[object]
            foreach ($node in $nodes) {
                try {
                    $cur = $node.Current
                    $ct = [string]$cur.ControlType.ProgrammaticName.Replace('ControlType.','')
                    if ($ct -notin @('Button','ListItem')) { continue }
                    $ip = $null
                    if (-not $node.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern, [ref]$ip)) { continue }
                    $b = $cur.BoundingRectangle
                    if ([double]$b.Width -gt 0 -and [double]$b.Height -gt 0 -and
                        [double]$b.Left -lt [double]::PositiveInfinity) {
                        $tmp.Add([pscustomobject]@{
                            Name=[string]$cur.Name; ControlType=$ct; IsOffscreen=[bool]$cur.IsOffscreen
                            Left=[double]$b.Left; Top=[double]$b.Top; Width=[double]$b.Width; Height=[double]$b.Height
                            Element=$node; InvokePattern=$ip
                        })
                    }
                } catch { continue }
            }
            $cards = $tmp.ToArray()
        }
        Write-Host "  poll #$sample : invoke-cards=$($cards.Count)"
        if ($cards.Count -gt 0) { break }
        Start-Sleep -Milliseconds 500
    }
    Start-Sleep -Milliseconds 800

    Write-Host "----- 结果卡片 dump（共 $($cards.Count) 个 Button/ListItem+Invoke）-----"
    $i = 0
    foreach ($c in $cards) { $i++; Write-Host ("  card[{0}] type={1} offscreen={2} name='{3}' rect=({4:F0},{5:F0},{6:F0},{7:F0})" -f $i,$c.ControlType,$c.IsOffscreen,$c.Name,$c.Left,$c.Top,$c.Width,$c.Height) }

    if ($cards.Count -eq 0) {
        Write-Host '未找到任何支持 Invoke 的结果卡片'
        Write-Result @{ ok=$false; executed=$true; error_code='NO_INVOKE_CARDS'; card_count=0; invoke_ok=$false }
        exit 1
    }

    # 挑卡片：优先 Name 含人名/协会名的（结果卡，排除 tab），否则取第一个
    $assc = ($AssociationName -replace '\s',''); $pers = ($PersonName -replace '\s','')
    $target = $cards | Where-Object {
        $n = ($_.Name -replace '\s','')
        $n.IndexOf($pers,[StringComparison]::Ordinal) -ge 0 -or $n.IndexOf($assc,[StringComparison]::Ordinal) -ge 0
    } | Select-Object -First 1
    if (-not $target) { $target = $cards | Select-Object -First 1 }
    Write-Host ("选定卡片: type={0} name='{1}'" -f $target.ControlType, $target.Name)

    # Invoke 前后对比可信插件 HWND：出现新 HWND = 详情窗口打开了
    $before = Get-TrustedPluginHwndsNow ([int64]$main.Hwnd)
    Write-Host ("Invoke 前可信插件 HWND: " + ($before -join ','))

    $invokeOk = $false
    try {
        $target.InvokePattern.Invoke()
        $invokeOk = $true
        Write-Host 'InvokePattern.Invoke() 已调用（无异常）'
    } catch {
        Write-Host ("Invoke 抛异常: {0}: {1}" -f $_.Exception.GetType().Name, $_.Exception.Message)
    }

    Start-Sleep -Milliseconds 3500

    $after = Get-TrustedPluginHwndsNow ([int64]$main.Hwnd)
    Write-Host ("Invoke 后可信插件 HWND: " + ($after -join ','))
    $newOnes = @($after | Where-Object { $_ -notin $before })
    $newDetailWindow = $newOnes.Count -gt 0
    Write-Host ("新出现的详情 HWND: " + ($newOnes -join ',') + "  => 详情打开=$newDetailWindow")

    Write-Host '实验完成（搜一搜/详情窗保持打开，你看屏幕确认）'
    Write-Result @{
        ok=$newDetailWindow; executed=$true
        card_count=$cards.Count
        target_card=$target.Name
        invoke_ok=$invokeOk
        new_detail_window=$newDetailWindow
        new_hwnds=$newOnes
    }
} catch {
    $code = $_.Exception.Message
    Write-Host "实验失败: $code"
    Write-Diag "EXP-INVOKE FAIL: $code"
    Write-Result @{ ok=$false; executed=$true; error_code=$code; invoke_ok=$false; new_detail_window=$false }
    exit 1
}
