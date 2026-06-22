#requires -Version 5.1
<#
.SYNOPSIS
  安装/卸载/启停 Client.Supervisor 为 Windows 服务（需管理员）。
.EXAMPLE
  powershell scripts\install-service.ps1 -Action install -SupervisorPath "C:\Program Files\WeComRpa\supervisor\Client.Supervisor.exe"
  powershell scripts\install-service.ps1 -Action uninstall
  powershell scripts\install-service.ps1 -Action status
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('install', 'uninstall', 'start', 'stop', 'status')][string]$Action,
    [string]$ServiceName = 'WeComRpaSupervisor',
    [string]$SupervisorPath,
    [string]$Description = '企业微信个人账号 RPA 客户端监督进程（拉起并守护 Client.App）'
)
$ErrorActionPreference = 'Stop'

# 必须管理员
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "需要管理员权限。请用「以管理员身份运行」的 PowerShell。"
    exit 1
}

function Test-ServiceExists($name) { [bool](Get-Service -Name $name -ErrorAction SilentlyContinue) }

switch ($Action) {
    'install' {
        if (-not $SupervisorPath) { Write-Error "install 需要 -SupervisorPath 指向 Client.Supervisor.exe"; exit 1 }
        if (-not (Test-Path $SupervisorPath)) { Write-Error "找不到文件：$SupervisorPath"; exit 1 }
        $exe = (Resolve-Path $SupervisorPath).Path
        if (Test-ServiceExists $ServiceName) { Write-Warning "服务 $ServiceName 已存在，跳过安装。"; return }
        $binPath = '"' + $exe + '"'
        Write-Host "==> 创建服务 $ServiceName -> $exe"
        # 注意：sc.exe 的 binPath= / start= 等号后必须有一个空格，值作为下一个 token
        sc.exe create $ServiceName binPath= $binPath start= auto | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Error "sc.exe create 失败 (exit $LASTEXITCODE)"; exit $LASTEXITCODE }
        sc.exe description $ServiceName $Description | Out-Null
        sc.exe start $ServiceName | Out-Null
        Write-Host "[OK] 服务 $ServiceName 已创建并启动（启动类型 auto）"
    }
    'uninstall' {
        if (Test-ServiceExists $ServiceName) {
            Write-Host "==> 停止并删除服务 $ServiceName"
            sc.exe stop $ServiceName | Out-Null
            Start-Sleep -Seconds 2
            sc.exe delete $ServiceName | Out-Null
            Write-Host "[OK] 服务 $ServiceName 已删除"
        } else { Write-Warning "服务 $ServiceName 不存在" }
    }
    'start' {
        if (Test-ServiceExists $ServiceName) { sc.exe start $ServiceName | Out-Null; Write-Host "[OK] 已启动 $ServiceName" }
        else { Write-Warning "服务不存在" }
    }
    'stop' {
        if (Test-ServiceExists $ServiceName) { sc.exe stop $ServiceName | Out-Null; Write-Host "[OK] 已停止 $ServiceName" }
        else { Write-Warning "服务不存在" }
    }
    'status' {
        if (Test-ServiceExists $ServiceName) { Get-Service $ServiceName | Format-Table Name, Status, StartType -AutoSize }
        else { Write-Warning "服务 $ServiceName 未安装" }
    }
}
