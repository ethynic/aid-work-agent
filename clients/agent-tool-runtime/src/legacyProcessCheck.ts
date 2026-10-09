import { execFile } from 'node:child_process'
import { ManagementError } from '@aid/local-tool-host-core'

/** Old CLI releases do not own a Host lease. Do not run beside an unverified claimant. */
export async function assertNoLegacyRuntimeProcess(): Promise<void> {
  if (process.platform !== 'win32') return
  const script = String.raw`$ErrorActionPreference='Stop'; $found=0
  $currentSid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  if ([string]::IsNullOrWhiteSpace($currentSid)) { throw 'Runtime identity unavailable' }
  foreach ($p in (Get-CimInstance Win32_Process -Filter "Name='node.exe'")) {
    if ($p.ProcessId -eq ${process.pid}) { continue }
    if ($p.CommandLine -notmatch 'agent-tool-runtime[\\/]dist[\\/]src[\\/]cli\.js' -or $p.CommandLine -notmatch '(?:\s|")start(?:\s|"|$)') { continue }
    $owner=Invoke-CimMethod -InputObject $p -MethodName GetOwnerSid
    if ($owner.ReturnValue -ne 0) { throw 'Runtime owner unavailable' }
    if ($owner.Sid -eq $currentSid) { $found++ }
  }
  Write-Output $found`
  const count = await new Promise<number>((resolve, reject) => {
    execFile('powershell.exe', ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')],
      { windowsHide: true, timeout: 15_000, maxBuffer: 4096 }, (error, stdout) => {
        if (error || !/^\d+$/.test(stdout.trim())) reject(new ManagementError(11, '旧Runtime进程停止事实无法核对'))
        else resolve(Number(stdout.trim()))
      })
  })
  // The old command line does not prove its home/version. Conservatively require an
  // explicit stop, including differently configured old CLI instances, without killing them.
  if (count > 0) throw new ManagementError(4, '检测到未核验的CLI实例，请先停止旧Runtime')
}
