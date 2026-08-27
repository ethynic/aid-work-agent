# drivers/ps1/resolve-open.ps1 — 打开与目标的会话（weixin_history_read 前置步骤）
# 与 message-send 共用 _common.ps1 的 Open-WeixinChat（搜索+定位+点击+校验标题），
# 只是不校验输入框草稿（读取场景草稿无害）。
# 注意：本文件含中文，必须以 UTF-8 with BOM 保存。
param(
    [Parameter(Mandatory)][string]$TargetName
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$driverDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $driverDir '_common.ps1')

Invoke-DriverMain -MutexName 'Local\AidWorkAgent.WeixinCli.ResolveOpen' -Body {
    $chat = Open-WeixinChat -TargetName $TargetName
    return @{ target = $TargetName; title = [string]$chat.Title }
}
