<#
.SYNOPSIS
  企业微信个人账号 RPA 编码前准入验证探测工具的便捷运行脚本。

.DESCRIPTION
  在已安装企业微信 PC 客户端的 Windows 上构建并运行 Client.Probe 诊断控制台。
  探测只读：只截图 / 读控件树 / 验证焦点夺取，绝不向企微发送任何键鼠或剪贴板输入。
  产出 probe-report.yaml 与 probe-screenshots/ 供运维回填 assets/wecom_nodes.yaml。

  本脚本兼容 Windows PowerShell 5.1 与 PowerShell 7+，不依赖任何外部模块。
  Client.Probe 是诊断工程，不加入 WeComPersonalRpaClient.sln；本脚本独立构建它。

.PARAMETER ProbeArgs
  透传给 Client.Probe 的命令行参数（以数组方式传入，保持引号正确）。
  常用：
    --window-class <name>          企微主窗口类名候选（默认 WeWorkWindow/WeComMainWnd/TXGuiFoundation）
    --qr-region <x,y,w,h>          二维码区域，相对主窗口左上角
    --template <path>              发送按钮模板 PNG（准入阶段先裁出来再带这个参数重跑）
    --template-threshold <0..1>    模板匹配置信度阈值（默认 0.85）
    --out <path>                   报告输出路径（默认 clients/wecom-personal-rpa/probe-report.yaml）
    --screenshots-dir <dir>        截图输出目录

.EXAMPLE
  .\scripts\run-probe.ps1
  # 最简：用默认参数探测，产物在 clients/wecom-personal-rpa/probe-report.yaml

.EXAMPLE
  .\scripts\run-probe.ps1 -ProbeArgs @("--template", "assets/templates/send_btn.png", "--qr-region", "520,240,200,200")
  # 带模板 + 二维码区域：验证发送按钮模板匹配置信度并精确截取二维码

.NOTES
  需要在已安装企业微信 PC 客户端的 Windows 上运行。企微需已启动并显示主窗口。
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $ProbeArgs
)

# 1) 切到本脚本所在目录的上一级（clients/wecom-personal-rpa/），与 sln 同级。
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$clientRoot = Resolve-Path (Join-Path $scriptDir "..")
Set-Location $clientRoot

# 2) 控制台 UTF-8（PowerShell 5.1 默认 GBK，企微窗口名/控件名含中文需要 UTF-8 输出）。
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {
    # 某些精简环境不支持，忽略。
}

Write-Host "================================================================"
Write-Host " 企业微信个人账号 RPA 准入验证探测工具（run-probe.ps1）"
Write-Host "================================================================"
Write-Host " 需在已安装企业微信 PC 客户端的 Windows 上运行。"
Write-Host " 探测只读：不发送任何键鼠或剪贴板输入到企微，不会触发误发。"
Write-Host " 工作目录：$clientRoot"
Write-Host "================================================================"

# 3) dotnet 必须可用。
$dotnet = Get-Command dotnet -ErrorAction SilentlyContinue
if (-not $dotnet) {
    Write-Host "[FAIL] 未找到 dotnet。请先安装 .NET 8 SDK（含 Windows 桌面工作负载）。" -ForegroundColor Red
    exit 3
}

# 4) 构建并运行 Client.Probe（参数透传）。
$proj = "src/Client.Probe/Client.Probe.csproj"
Write-Host "[*] 构建 + 运行：dotnet run --project $proj -c Debug -- <ProbeArgs>"
if ($ProbeArgs -and $ProbeArgs.Count -gt 0) {
    & dotnet run --project $proj -c Debug -- @ProbeArgs
} else {
    & dotnet run --project $proj -c Debug
}
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    Write-Host ""
    Write-Host "[FAIL] Client.Probe 退出码 = $exitCode" -ForegroundColor Red
} else {
    Write-Host ""
    Write-Host "[OK] 探测完成。请查看 probe-report.yaml 与 probe-screenshots/ 目录。" -ForegroundColor Green
    Write-Host "    回填指引见 docs/准入验证手册.md「翻译字段回填」章节。"
}

exit $exitCode