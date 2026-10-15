# drivers/ps1/localdb-login.ps1 — 本地库轻量登录探测驱动（localdb 任务 1.10，严格只读）
# 判定逻辑源自 probe.ps1 Invoke-DriverMain body + _common.ps1 的 Get-WeComTopLevelWindows /
# Find-WeComLoginWindow / Resolve-WeComMainWindow（窗口类名/标题/尺寸判据逐字复刻）：
#   online     = 可见 WeWorkWindow + 标题「企业微信」+ >=600x400 的主窗口候选存在；
# 操作主窗口 PID：同 Resolve-WeComMainWindow，面积最大者；最大并列不签发 PID。
#   need_login = 主窗口不可见但登录二维码窗在（class=WeChatLogin（2026-09-28 真机实测修订），
#                或小尺寸 WeWorkWindow（W<500 且 H<700，RPA 时代形态向后兼容））；
#   offline    = WXWork.exe 未运行，或运行中但上述窗口皆无（含托盘隐藏）。
# 与 probe.ps1 的差异（有意为之）：
#   - 【无交互桌面会话门禁】（设计 §2：本地库就绪不继承 UI 门禁，会话/进程预检归调用方）；
#   - 无命名 mutex 单飞：纯只读枚举无并发副作用，BUSY 反而误伤轻量探测（与互斥写驱动解耦）；
#   - 无截图/无 OCR/无二维码返回：只判三态（二维码是登录凭证，轻量探测不取不留）；
#   - DPI 感知设置失败 best-effort 继续：仅高缩放下尺寸阈值可能偏小，不因此中断探测
#     （probe.ps1 经 Invoke-DriverMain 对 DPI 失败报 INTERNAL_ERROR，此处放宽）。
# 输出协议：最后一行 DRIVER_JSON: {"ok":true,"data":{"login_state":"online|need_login|offline"}}
#   （对齐 _common.ps1 Write-DriverJson 的嵌套 data 惯例，TS runPowerShellDriver 只认 data）；
#   'unknown' 不由本驱动产出——驱动失败按惯例 ok=false，TS 侧归并为 unknown。
# 绝不激活窗口、不发送输入、不改剪贴板（EnumWindows/IsWindowVisible/GetWindowTextW/
#   GetClassNameW/GetWindowRect 均纯读）。
# 注意：本文件含中文，必须以 UTF-8 with BOM 保存（Windows PowerShell 5.1 否则按 GBK 解析报错）。
param()
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Win32 原语（自包含最小集；Add-Type 幂等。类名刻意区别于 _common.ps1 的 WeComWin32，
# 防未来同进程内联复用时与完整底座冲突）
if ([type]::GetType('LocaldbLoginWin32') -eq $null) {
    Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public class LocaldbLoginWin32 {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassNameW(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
    [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr v);
    public struct RECT { public int Left, Top, Right, Bottom; }
}
'@
}

function Write-DriverJson([object]$Payload) {
    # 与 _common.ps1 Write-DriverJson 同款（单行 DRIVER_JSON，ConvertTo-Json -Compress）
    $json = ConvertTo-Json -InputObject $Payload -Compress -Depth 10
    Write-Output ("DRIVER_JSON: " + $json)
}

try {
    # DPI（Per-Monitor V2）：GetWindowRect 返回物理像素，尺寸阈值（600x400 / 500x700）才可信；
    # 已是感知进程或设置失败 → best-effort 继续（见头注释差异说明）
    try {
        [void][LocaldbLoginWin32]::SetProcessDpiAwarenessContext([IntPtr](-4))
    } catch { }

    # 进程事实源：WXWork.exe 不在 → offline（判据同 probe.ps1 的 offline 兜底；会话门禁不在此做）
    $wxPids = @(Get-Process -Name 'WXWork' -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
    if ($wxPids.Count -eq 0) {
        Write-DriverJson @{ ok = $true; data = @{ login_state = 'offline' } }
        exit 0
    }

    # 枚举 WXWork 进程拥有的全部顶层窗口（含不可见；复刻 _common.ps1 Get-WeComTopLevelWindows）
    $wins = New-Object System.Collections.ArrayList
    $cb = [LocaldbLoginWin32+EnumWindowsProc]{
        param($hWnd, $lParam)
        $pidOut = 0
        [void][LocaldbLoginWin32]::GetWindowThreadProcessId($hWnd, [ref]$pidOut)
        if ($wxPids -contains $pidOut) {
            $sbT = New-Object System.Text.StringBuilder 512
            $sbC = New-Object System.Text.StringBuilder 256
            [void][LocaldbLoginWin32]::GetWindowTextW($hWnd, $sbT, 512)
            [void][LocaldbLoginWin32]::GetClassNameW($hWnd, $sbC, 256)
            $r = New-Object LocaldbLoginWin32+RECT
            [void][LocaldbLoginWin32]::GetWindowRect($hWnd, [ref]$r)
            [void]$wins.Add([pscustomobject]@{
                Pid = $pidOut
                Visible = [LocaldbLoginWin32]::IsWindowVisible($hWnd)
                Class = $sbC.ToString()
                Title = $sbT.ToString()
                W = ($r.Right - $r.Left); H = ($r.Bottom - $r.Top)
            })
        }
        return $true
    }
    [void][LocaldbLoginWin32]::EnumWindows($cb, [IntPtr]::Zero)

    # ① 主窗口在线判定（复刻 probe.ps1 主窗口候选谓词；状态探测不关心面积并列歧义——
    #    候选 >=1 即 online；PID 按操作定位的面积最大规则选取，并列歧义不签发 PID）
    $mainCandidates = @($wins | Where-Object {
        $_.Class -eq 'WeWorkWindow' -and $_.Visible -and $_.Title -eq '企业微信' -and $_.W -ge 600 -and $_.H -ge 400
    })
    if ($mainCandidates.Count -gt 0) {
        $sorted = @($mainCandidates | Sort-Object { $_.W * $_.H } -Descending)
        if ($sorted.Count -gt 1 -and ($sorted[0].W * $sorted[0].H) -eq ($sorted[1].W * $sorted[1].H)) {
            Write-DriverJson @{ ok = $true; data = @{ login_state = 'online'; window_pid = $null; window_error = 'WINDOW_AMBIGUOUS' } }
        } else {
            Write-DriverJson @{ ok = $true; data = @{ login_state = 'online'; window_pid = $sorted[0].Pid } }
        }
        exit 0
    }

    # ② 登录二维码窗判定（复刻 _common.ps1 Find-WeComLoginWindow 双类名匹配）
    $login = @($wins | Where-Object {
        $_.Visible -and (
            ($_.Class -eq 'WeChatLogin' -and $_.W -gt 0) -or
            ($_.Class -eq 'WeWorkWindow' -and $_.W -gt 0 -and $_.W -lt 500 -and $_.H -lt 700)
        )
    })
    if ($login.Count -gt 0) {
        Write-DriverJson @{ ok = $true; data = @{ login_state = 'need_login' } }
        exit 0
    }

    # ③ 兜底：进程在但无任何可识别窗口（托盘隐藏等）→ offline
    Write-DriverJson @{ ok = $true; data = @{ login_state = 'offline' } }
    exit 0
} catch {
    # 未预期崩溃：按驱动惯例 ok=false + 稳定 code + 截断消息，退出码 0（TS 归并 unknown）
    $raw = [string]$_.Exception.Message
    if ($raw.Length -gt 300) { $raw = $raw.Substring(0, 300) }
    Write-DriverJson @{ ok = $false; code = 'INTERNAL_ERROR'; message = ('本地库登录探测内部错误：' + $raw) }
    exit 0
}
