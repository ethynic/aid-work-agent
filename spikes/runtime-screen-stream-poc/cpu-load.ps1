# 给验证程序模拟「电脑很忙、编码器抢不到 CPU」。只影响这个脚本启动的进程，不影响电脑上的其他程序。两种方式可以单用，也可以一起用：
#   -CpuRate  整机 CPU 的硬上限，取值 1～10000（10000 = 整机 100%）。例如 28 个逻辑处理器的电脑上限定为约 0.4 个核心：0.4 / 28 * 10000 ≈ 143。
#             实测这种硬限只是让各线程排队等配额，帧率会掉，但编码器不报「因 CPU 受限」，见 README。
#   -Affinity 亲和性掩码，把验证程序和占位进程绑到指定的逻辑处理器上；-Busy N 同时起 N 个满载的占位进程和它抢这几个处理器。
#             这种方式下各线程轮流被调度，更接近真实情况，但负载很难调到恰到好处：实测两个能效核加 5 个占位进程时三个画质档都只有 1～2 帧，
#             而且 Electron 退出时会卡住，一定要配 -MaxSeconds。
#             例如 i7-14700KF 有 8 个性能核（逻辑处理器 0～15）和 12 个能效核（16～27），用两个能效核：2^16 + 2^17 = 196608。
#   -MaxSeconds N  验证程序运行 N 秒后还没退出，就先停占位进程、再等 20 秒，仍不退出则结束它的整棵进程树。0 表示一直等。
# 用法：先设好 POC_* 环境变量，再运行（参数都按名字传）
#   powershell -NoProfile -ExecutionPolicy Bypass -File cpu-load.ps1 -CpuRate 143 -Exe "<electron.exe 的路径>" .
#   powershell -NoProfile -ExecutionPolicy Bypass -File cpu-load.ps1 -Affinity 196608 -Busy 5 -MaxSeconds 240 -Exe "<electron.exe 的路径>" .
# 验证程序主进程退出后脚本结束占位进程；脚本意外退出时，作业对象把留在里面的进程一并结束。
# 参数都按名字传（-Exe 之后是验证程序自己的参数），不按位置传。
[CmdletBinding(PositionalBinding = $false)]
param(
  [int]$CpuRate = 0,
  [long]$Affinity = 0,
  [int]$Busy = 0,
  [int]$MaxSeconds = 0,
  [Parameter(Mandatory = $true)][string]$Exe,
  [Parameter(ValueFromRemainingArguments = $true)][string[]]$ChildArgs = @()
)
$ErrorActionPreference = 'Stop'
trap { [Console]::Error.WriteLine('POC_CPU_LOAD_ERROR line=' + $_.InvocationInfo.ScriptLineNumber + ' offset=' + $_.InvocationInfo.OffsetInLine + ' statement=' + $_.InvocationInfo.Line.Trim() + ' id=' + $_.FullyQualifiedErrorId); exit 1 }
if ($CpuRate -lt 0 -or $CpuRate -gt 10000) { throw 'CpuRate 必须在 0～10000 之间' }
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class PocJob {
  [StructLayout(LayoutKind.Sequential)]
  public struct Basic { public long PerProcessUserTimeLimit; public long PerJobUserTimeLimit; public uint LimitFlags; public UIntPtr MinimumWorkingSetSize; public UIntPtr MaximumWorkingSetSize; public uint ActiveProcessLimit; public UIntPtr Affinity; public uint PriorityClass; public uint SchedulingClass; }
  [StructLayout(LayoutKind.Sequential)]
  public struct Io { public ulong ReadOperationCount; public ulong WriteOperationCount; public ulong OtherOperationCount; public ulong ReadTransferCount; public ulong WriteTransferCount; public ulong OtherTransferCount; }
  [StructLayout(LayoutKind.Sequential)]
  public struct Extended { public Basic BasicLimitInformation; public Io IoInfo; public UIntPtr ProcessMemoryLimit; public UIntPtr JobMemoryLimit; public UIntPtr PeakProcessMemoryUsed; public UIntPtr PeakJobMemoryUsed; }
  [StructLayout(LayoutKind.Sequential)]
  public struct CpuRateInfo { public uint ControlFlags; public uint CpuRate; }
  [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
  public static extern IntPtr CreateJobObject(IntPtr attributes, string name);
  [DllImport("kernel32.dll", SetLastError = true)]
  public static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref Extended info, int length);
  [DllImport("kernel32.dll", SetLastError = true)]
  public static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref CpuRateInfo info, int length);
  [DllImport("kernel32.dll", SetLastError = true)]
  public static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
  [DllImport("kernel32.dll")]
  public static extern IntPtr GetCurrentProcess();
}
"@
$job = [PocJob]::CreateJobObject([IntPtr]::Zero, $null)
if ($job -eq [IntPtr]::Zero) { throw ('CreateJobObject 失败：' + [Runtime.InteropServices.Marshal]::GetLastWin32Error()) }
# 作业句柄关闭时结束作业里的进程：脚本意外退出也不会把验证程序和占位进程留在后台。
$extended = New-Object PocJob+Extended
$extended.BasicLimitInformation.LimitFlags = 0x2000
if (-not [PocJob]::SetInformationJobObject($job, 9, [ref]$extended, [Runtime.InteropServices.Marshal]::SizeOf($extended))) { throw ('设置作业信息失败：' + [Runtime.InteropServices.Marshal]::GetLastWin32Error()) }
if ($CpuRate -gt 0) {
  $rate = New-Object PocJob+CpuRateInfo
  $rate.ControlFlags = 0x1 -bor 0x4   # 启用 CPU 速率控制，并设为硬上限
  $rate.CpuRate = $CpuRate
  if (-not [PocJob]::SetInformationJobObject($job, 15, [ref]$rate, [Runtime.InteropServices.Marshal]::SizeOf($rate))) { throw ('设置 CPU 上限失败：' + [Runtime.InteropServices.Marshal]::GetLastWin32Error()) }
}
# 先把本脚本自己放进作业、设好亲和性：之后由它启动的进程（占位进程、验证程序及其子进程）都在作业里、用同一组逻辑处理器。
if (-not [PocJob]::AssignProcessToJobObject($job, [PocJob]::GetCurrentProcess())) { throw ('加入作业失败：' + [Runtime.InteropServices.Marshal]::GetLastWin32Error()) }
if ($Affinity -gt 0) { (Get-Process -Id $PID).ProcessorAffinity = [IntPtr]$Affinity }
$busyProcs = @()
if ($Busy -gt 0) {
  $node = (Get-Command node -ErrorAction Stop).Source
  $loop = Join-Path $PSScriptRoot 'busy-loop.js'
  for ($i = 0; $i -lt $Busy; $i++) { $busyProcs += Start-Process -FilePath $node -ArgumentList @($loop) -NoNewWindow -PassThru }
}
try {
  if ($MaxSeconds -gt 0) {
    $child = Start-Process -FilePath $Exe -ArgumentList $ChildArgs -NoNewWindow -PassThru
    if (-not $child.WaitForExit($MaxSeconds * 1000)) {
      # 到点还没退出（重负载下 Electron 的退出流程会卡住，实测占位进程结束后才退出）：先停占位进程给它让路，
      # 再等 20 秒，仍不退出就结束整棵进程树。
      foreach ($p in $busyProcs) { if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } }
      if (-not $child.WaitForExit(20000)) { & taskkill.exe /PID $child.Id /T /F | Out-Null }
    }
  } else {
    $child = Start-Process -FilePath $Exe -ArgumentList $ChildArgs -NoNewWindow -Wait -PassThru
  }
} finally {
  foreach ($p in $busyProcs) { if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } }
}
Write-Output ('POC_CPU_LOAD child exit=' + $child.ExitCode + ' rate=' + $CpuRate + ' affinity=' + $Affinity + ' busy=' + $Busy)
