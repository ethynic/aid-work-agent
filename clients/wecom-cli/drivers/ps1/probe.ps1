# drivers/ps1/probe.ps1 — wecom_probe 窗口级探测驱动（严格只读）
# 前置：TS 侧已确认 win32 交互会话 + WXWork.exe 进程在运行，本脚本只做窗口枚举解析：
#   主窗口（可见 WeWorkWindow / 标题「企业微信」/ >=600x400，取面积最大者）hwnd/rect、
#   登录态（online=主窗口在；need_login=检测到登录二维码小窗；offline=两者皆无）、
#   当前内容页（内容子窗口类名「WXworkWindow - 企业微信-<页名>」编码）。
# 绝不激活窗口、不发送输入、不改剪贴板（EnumWindows/EnumChildWindows/GetWindowRect 纯读）。
# 注意：本文件含中文，必须以 UTF-8 with BOM 保存。
param()
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$driverDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $driverDir '_common.ps1')

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WecomCli.Probe' -Body {
    $all = @(Get-WeComTopLevelWindows)
    $candidates = @($all | Where-Object {
        $_.Class -eq 'WeWorkWindow' -and $_.Visible -and $_.Title -eq '企业微信' -and $_.W -ge 600 -and $_.H -ge 400
    })

    if ($candidates.Count -eq 0) {
        # 主窗口不可见：区分「登录二维码小窗」（need_login）与「进程在但无窗口/托盘隐藏」（offline 兜底）
        $login = Find-WeComLoginWindow
        if ($null -ne $login) {
            return @{
                login_state = 'need_login'
                main_window = $null
                current_page = $null
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
