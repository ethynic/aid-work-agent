# drivers/ps1/probe.ps1 — wecom_probe 窗口级探测驱动（严格只读；E5 登录态增强）
# 前置：TS 侧已确认 win32 交互会话 + WXWork.exe 进程在运行，本脚本只做窗口枚举解析：
#   主窗口（可见 WeWorkWindow / 标题「企业微信」/ >=600x400，取面积最大者）hwnd/rect、
#   登录态（online=主窗口在；need_login=检测到登录二维码小窗；offline=两者皆无）、
#   当前内容页（内容子窗口类名「WXworkWindow - 企业微信-<页名>」编码）。
# E5 登录态增强（RPA get_login_state 完整替代，参照 wecom-personal-rpa wecom-ops.ps1 思路）：
#   need_login 时附 login_window rect + 登录窗整窗 PNG base64（qr_image_base64，供扫码上线）
#   + 二维码状态提示（qr_status_hint：normal/expired/limited/unknown，RapidOCR best-effort，
#   OCR 不可用不算失败——探测职责是报告能力矩阵，不因可选依赖缺席而失败）。
# 安全：二维码是登录凭证——qr base64 只经 DRIVER_JSON stdout 返回给调用方，绝不写日志、
#   绝不存 artifact（probe 无 artifact，保持）；OCR 临时文件固定名、用完即删（probe 命名
#   mutex 单飞无并发写冲突），文件名不含任何敏感信息。
# 绝不激活窗口、不发送输入、不改剪贴板（EnumWindows/EnumChildWindows/GetWindowRect/
#   PrintWindow/CopyFromScreen 均纯读，截图不改变系统状态）。
# 注意：本文件含中文，必须以 UTF-8 with BOM 保存。
param()
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$driverDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $driverDir '_common.ps1')

function Get-WeComLoginWindowBmp {
    # 登录窗整窗截图（内存 Bitmap，不落盘）。策略（M6 真机实测：PrintWindow 对 CEF 区域
    # 出纯白 mean=247 stddev=0，登录页同源大概率纯白/纯黑，回退 CopyFromScreen 是主路径
    # 而非兜底）：PrintWindow(hwnd, PW_RENDERFULLCONTENT=2) 优先 → 9 点采样全黑或全白 →
    # 回退 Graphics.CopyFromScreen 按窗口 rect（需窗口在屏幕可见，登录窗通常前台弹出，
    # 成立）。与 _common.ps1 Get-WeComWindowSnapshot 同模式但补全白判定、且不落盘
    # （二维码不入 artifact）——不改 M1-M8 已验证的共享函数，在 probe 内单独实现
    # （最小侵入方案）。返回 Bitmap（调用方负责 Dispose）；窗口消失/尺寸异常/两路截图
    # 均失败 → $null（探测仍上报 need_login，仅缺二维码，同 RPA get_login_state 先例）。
    param([Parameter(Mandatory)][int64]$Hwnd)
    Initialize-WeComWin32
    if (-not [WeComWin32]::IsWindow([IntPtr]$Hwnd)) { return $null }
    $r = New-Object WeComWin32+RECT
    [void][WeComWin32]::GetWindowRect([IntPtr]$Hwnd, [ref]$r)
    $w = $r.Right - $r.Left; $h = $r.Bottom - $r.Top
    if ($w -le 0 -or $h -le 0) { return $null }

    $bmp = New-Object System.Drawing.Bitmap $w, $h
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $hdc = $g.GetHdc()
    [void][WeComWin32]::PrintWindow([IntPtr]$Hwnd, $hdc, 2)
    $g.ReleaseHdc($hdc)
    $g.Dispose()

    # 9 点采样：全黑（个别渲染路径 PrintWindow 出黑图，阈值同 _common.ps1 <8）或
    # 全白（CEF 实测 247，阈值 ≥240 兼容）→ CopyFromScreen 回退；回退失败 → 丢弃
    # 无信息量的纯色图返回 $null，不误导调用方
    $black = 0; $white = 0
    foreach ($fx in @(0.1, 0.5, 0.9)) { foreach ($fy in @(0.1, 0.5, 0.9)) {
        $px = $bmp.GetPixel([int]($w * $fx), [int]($h * $fy))
        if ($px.R -lt 8 -and $px.G -lt 8 -and $px.B -lt 8) { $black++ }
        elseif ($px.R -ge 240 -and $px.G -ge 240 -and $px.B -ge 240) { $white++ }
    }}
    if (($black -eq 9) -or ($white -eq 9)) {
        # 窗口消失竞态：入口 IsWindow 之后、CopyFromScreen 之前登录窗可能关闭（如恰好扫码
        # 完成），按失效 rect 截到的是其它屏幕内容——复查已消失则按契约返回 $null
        if (-not [WeComWin32]::IsWindow([IntPtr]$Hwnd)) { $bmp.Dispose(); return $null }
        $bmp.Dispose()
        $bmp = New-Object System.Drawing.Bitmap $w, $h
        try {
            $g2 = [System.Drawing.Graphics]::FromImage($bmp)
            try { $g2.CopyFromScreen($r.Left, $r.Top, 0, 0, (New-Object System.Drawing.Size $w, $h)) }
            finally { $g2.Dispose() }
        } catch {
            $bmp.Dispose()
            return $null
        }
    }
    return $bmp
}

function Get-WeComQrStatusHint {
    # 对登录窗截图跑 RapidOCR（boxes 模式，与 qr_image_base64 同一张截图）best-effort
    # 判二维码状态：「已失效/过期/刷新」→ expired；「受限/冻结/异常」→ limited；
    # OCR 不可用（venv 缺失等 CONFIG_MISSING）/无标记/识别异常 → normal。
    # 任何异常吞掉返回 normal，绝不因 OCR 让 probe 失败。
    param([Parameter(Mandatory)][string]$ImagePath)
    try {
        $ocr = Invoke-WeComChatOcr -ImagePath $ImagePath -Mode 'boxes'
        $text = (@($ocr.boxes | ForEach-Object { [string]$_.text }) -join '')
        if ($text -match '已失效|过期|刷新') { return 'expired' }
        if ($text -match '受限|冻结|异常') { return 'limited' }
        return 'normal'
    } catch {
        return 'normal'
    }
}

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WecomCli.Probe' -Body {
    $all = @(Get-WeComTopLevelWindows)
    $candidates = @($all | Where-Object {
        $_.Class -eq 'WeWorkWindow' -and $_.Visible -and $_.Title -eq '企业微信' -and $_.W -ge 600 -and $_.H -ge 400
    })

    if ($candidates.Count -eq 0) {
        # 主窗口不可见：区分「登录二维码小窗」（need_login，附二维码供扫码上线）与
        # 「进程在但无窗口/托盘隐藏」（offline 兜底）
        $login = Find-WeComLoginWindow
        if ($null -ne $login) {
            # 登录窗整窗截图 → TEMP PNG → base64 + OCR 状态提示（同一张截图，用完即删）。
            # 截图与 OCR 均 best-effort：任一失败只缺 qr 字段（hint=unknown），不影响
            # need_login 上报（登录窗 rect 来自枚举结果，不依赖截图成功）。
            $qrBase64 = $null
            $hint = 'unknown'
            $shotPath = Join-Path $env:TEMP 'wecom-driver-login-qr.png'
            $bmp = Get-WeComLoginWindowBmp -Hwnd ([int64]$login.Hwnd)
            if ($null -ne $bmp) {
                try {
                    $bmp.Save($shotPath, [System.Drawing.Imaging.ImageFormat]::Png)
                    $qrBase64 = [System.Convert]::ToBase64String([System.IO.File]::ReadAllBytes($shotPath))
                    $hint = Get-WeComQrStatusHint -ImagePath $shotPath
                } catch {
                    # 截图存盘/读回/OCR 任一失败：保持 qr=null/hint=unknown，不上抛
                } finally {
                    $bmp.Dispose()
                    Remove-Item -LiteralPath $shotPath -ErrorAction SilentlyContinue
                }
            }
            return @{
                login_state = 'need_login'
                main_window = $null
                current_page = $null
                login_window = @{
                    x = [int]$login.X
                    y = [int]$login.Y
                    w = [int]$login.W
                    h = [int]$login.H
                }
                qr_image_base64 = $qrBase64
                qr_status_hint = $hint
            }
        }
        return @{
            login_state = 'offline'
            main_window = $null
            current_page = $null
        }
    }

    $sorted = @($candidates | Sort-Object { $_.W * $_.H } -Descending)
    if ($sorted.Count -gt 1 -and ($sorted[0].W * $sorted[0].H) -eq ($sorted[1].W * $sorted[1].H)) {
        Throw-DriverError 'WINDOW_AMBIGUOUS' ('找到 ' + $sorted.Count + ' 个面积相同的企业微信主窗口候选，无法唯一确定')
    }
    $main = $sorted[0]

    # 当前内容页：可见内容子窗口类名编码页名（无可见内容页 → null，不算失败）
    $currentPage = $null
    foreach ($child in @(Get-WeComContentChildWindow -MainHwnd ([int64]$main.Hwnd))) {
        if ($child.Visible) {
            $prefix = 'WXworkWindow - 企业微信-'
            $currentPage = $child.Class.Substring($prefix.Length)
            break
        }
    }

    return @{
        login_state = 'online'
        main_window = @{
            hwnd = [int64]$main.Hwnd
            x = [int]$main.X
            y = [int]$main.Y
            w = [int]$main.W
            h = [int]$main.H
        }
        current_page = $currentPage
    }
}
