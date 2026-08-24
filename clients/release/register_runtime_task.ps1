# 注册 runtime 后台自启任务（登录时运行，无窗口，带自愈循环）
# 用法：powershell -ExecutionPolicy Bypass -File register_runtime_task.ps1
$ErrorActionPreference = 'Stop'
$bat = Join-Path $env:APPDATA 'aidwork-tool-runtime\start_runtime.bat'
if (-not (Test-Path $bat)) { Write-Output "未找到 $bat，请先安装 aid-runtime（见 clients/README.md §三/§六）"; exit 1 }
$action = New-ScheduledTaskAction -Execute $bat
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName 'AidWorkToolRuntime' -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName 'AidWorkToolRuntime'
Write-Output ("已注册并启动后台任务 AidWorkToolRuntime；日志: %APPDATA%\aidwork-tool-runtime\logs\runtime.log")
