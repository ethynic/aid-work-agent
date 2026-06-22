#requires -Version 5.1
<#
.SYNOPSIS
  导出脱敏诊断包（日志 + 配置摘要 + 服务/进程状态），供排错或上报。
  不会导出明文 secret / token / 绝对路径（已正则脱敏）。
.EXAMPLE
  powershell scripts\diagnostics.ps1 -OutPath .\diag.zip
#>
[CmdletBinding()]
param(
    [string]$OutPath = (Join-Path $PWD 'wecom-rpa-diag.zip')
)
$ErrorActionPreference = 'Continue'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$tmp = Join-Path $env:TEMP "wecom-rpa-diag-$stamp"
New-Item $tmp -ItemType Directory -Force | Out-Null

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("WeCom Personal RPA 诊断包")
$lines.Add("Generated: $(Get-Date -Format o)")
$lines.Add("OS: $([System.Environment]::OSVersion.VersionString)")
$lines.Add("Machine: $env:COMPUTERNAME")
$lines.Add("User: $env:USERNAME")

$lines.Add("")
$lines.Add("== Service ==")
$svc = Get-Service WeComRpaSupervisor -ErrorAction SilentlyContinue
if ($svc) { $lines.Add(($svc | Select-Object Name, Status, StartType | Out-String)) }
else { $lines.Add("WeComRpaSupervisor: 未安装") }

$lines.Add("")
$lines.Add("== Processes ==")
$procs = Get-Process -Name 'Client.App', 'Client.Supervisor' -ErrorAction SilentlyContinue
if ($procs) { $procs | Select-Object Name, Id, StartTime | ForEach-Object { $lines.Add(($_ | Out-String)) } }
else { $lines.Add("Client.App / Client.Supervisor: 未运行") }

$lines.Add("")
$lines.Add("== Config (redacted) ==")
$cfgCandidates = @()
$cfgCandidates += (Join-Path $env:LOCALAPPDATA 'WeComPersonalRpa\Client.App\data\client.yaml')
$cfgCandidates += (Join-Path (Split-Path $PSScriptRoot -Parent) 'configs\client.yaml')
foreach ($p in $cfgCandidates) {
    if (Test-Path $p) {
        $lines.Add("--- $p ---")
        $raw = Get-Content $p -Raw -ErrorAction SilentlyContinue
        if ($raw) {
            $redacted = [regex]::Replace($raw, '(?i)(secret|api[_-]?key|token|password)["\s:=]+[^\s,"''\r\n]+', '$1=***')
            $redacted = [regex]::Replace($redacted, '(?i)[A-Za-z]:\\[^\s"''\r\n]+', '<path>')
            $lines.Add($redacted)
        }
    }
}
$lines | Set-Content (Join-Path $tmp 'summary.txt') -Encoding UTF8

# 日志
$logDir = Join-Path $env:LOCALAPPDATA 'WeComPersonalRpa\Client.App\logs'
if (Test-Path $logDir) {
    Copy-Item $logDir (Join-Path $tmp 'logs') -Recurse -ErrorAction SilentlyContinue
    Write-Host "已收集日志：$logDir"
} else {
    Write-Warning "未找到日志目录 $logDir（App 可能从未启动过）"
}

# 打包
if (Test-Path $OutPath) { Remove-Item $OutPath -Force }
Compress-Archive -Path (Join-Path $tmp '*') -DestinationPath $OutPath -Force
Remove-Item $tmp -Recurse -Force
Write-Host "[OK] 诊断包已生成：$OutPath"
