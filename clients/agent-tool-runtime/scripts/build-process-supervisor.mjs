import { spawnSync } from 'node:child_process'
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { fileURLToPath } from 'node:url'
const root = fileURLToPath(new URL('../', import.meta.url))
if (process.platform === 'win32') {
  const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', `${root}native/build.ps1`], { stdio: 'inherit', windowsHide: true })
  if (result.status !== 0) throw new Error('Process supervisor build failed')
  mkdirSync(`${root}dist/src/native`, { recursive: true })
  copyFileSync(`${root}native/build/RuntimeProcessSupervisor.exe`, `${root}dist/src/native/RuntimeProcessSupervisor.exe`)
  const digest = file => createHash('sha256').update(readFileSync(file)).digest('hex')
  const compiler = `${process.env.WINDIR}/Microsoft.NET/Framework64/v4.0.30319/csc.exe`
  writeFileSync(`${root}dist/src/native/build-info.json`, JSON.stringify({ schemaVersion: 1,
    sourceSha256: digest(`${root}native/RuntimeProcessSupervisor.cs`), buildScriptSha256: digest(`${root}native/build.ps1`),
    compilerSha256: digest(compiler), executableSha256: digest(`${root}dist/src/native/RuntimeProcessSupervisor.exe`),
    target: 'NET-Framework-4-AnyCPU', policy: 'KILL_ON_JOB_CLOSE-no-breakaway-no-preserve-chrome' }, null, 2) + '\n')
}
