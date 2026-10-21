import { execFile } from 'node:child_process'
import { lstatSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

// 输入回退 ps1 通道（点击/滚轮，2026-10-21）已删除：native 输入（@aid/win32-native-input）
// 全量替代；读取链路 cv-segdiff/cv-stitch 与恢复核验 visual-region-compare（2026-10-21）
// 已迁 Node（boss/imageOps.ts）。生产链路仅剩实验工具 window-experiment.ps1。
// 详见 docs/plans/plan-recruiting-error-containment-and-input-performance.md
const dependencies: Readonly<Record<string, readonly string[]>> = {
  'window-experiment.ps1': ['BossPhysicalInput.cs', 'BossWindowExperiment.cs'],
}

export class PowerShellScriptError extends Error {
  constructor(message: string, readonly exitCode?: number) { super(message); this.name = 'PowerShellScriptError' }
}

export interface PowerShellScriptOptions {
  timeout: number
  /** Trusted composition/test input only; never supplied by an operation or MCP argument. */
  executable?: string
  temporaryRoot?: string
}

function systemRoot(): string {
  const root = process.env.SystemRoot ?? process.env.SYSTEMROOT
  if (process.platform !== 'win32' || !root || !/^[a-z]:\\[^\x00-\x1f"<>|?*:]+$/i.test(root) || path.win32.resolve(root) !== root) throw new Error('POWERSHELL_UNAVAILABLE')
  ordinaryPath(root, true)
  return root
}

function trustedPowerShell(): string {
  const root = systemRoot()
  return path.join(root, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
}

function localAbsolute(file: string): boolean {
  return /^[a-z]:\\[^\x00-\x1f"<>|?*:]+$/i.test(file) && path.win32.resolve(file) === file
}

function ordinaryPath(file: string, directory = false): void {
  const stat = lstatSync(file)
  if (stat.isSymbolicLink() || (directory ? !stat.isDirectory() : !stat.isFile())
    || path.resolve(realpathSync.native(file)).toLowerCase() !== path.resolve(file).toLowerCase()) {
    throw new Error('POWERSHELL_UNSAFE_PATH')
  }
}

function decode(bytes: Buffer): string {
  try { return new TextDecoder('utf-8', { fatal: true }).decode(bytes) }
  catch { return new TextDecoder('gb18030').decode(bytes) }
}

async function run(executable: string, args: string[], timeout: number, extraEnv: Record<string, string> = {}): Promise<{ stdout: string; stderr: string }> {
  const root = systemRoot()
  const directory = path.join(root, 'System32', 'WindowsPowerShell', 'v1.0')
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^(?:path|psmodulepath|systemroot|windir)$/i.test(key)))
  Object.assign(env, { SystemRoot: root, windir: root, Path: [path.join(root, 'System32'), root, directory].join(';'), PSModulePath: path.join(directory, 'Modules') })
  return new Promise((accept, reject) => {
    try { execFile(executable, args, { timeout, windowsHide: true, cwd: path.dirname(executable), encoding: 'buffer', maxBuffer: 1024 * 1024, env: { ...env, ...extraEnv } }, (error, stdout, stderr) => {
      const output = { stdout: decode(stdout), stderr: decode(stderr) }
      if (error) {
        const code = typeof error.code === 'number' ? error.code : undefined
        // Never include error.message: Node embeds the entire command and -Text argument.
        const launch = code === 4294770688 || typeof error.code === 'string'
        reject(new PowerShellScriptError(`${launch ? '[BOSS_POWERSHELL_LAUNCH] ' : ''}${output.stderr || (error.killed ? 'PowerShell 执行超时' : 'PowerShell 执行失败')}`, code))
      } else accept(output)
    }) } catch { reject(new PowerShellScriptError('[BOSS_POWERSHELL_LAUNCH] 无法启动本机 PowerShell')) }
  })
}

// No path/argument interpolation into executable PowerShell text. The nonce path
// travels in the child-only environment; ACLs are set before any code is copied.
const privateDirectory = `
$ErrorActionPreference='Stop'
$security=New-Object System.Security.AccessControl.DirectorySecurity
$security.SetAccessRuleProtection($true,$false)
$inherit=[System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
foreach($sid in @([System.Security.Principal.WindowsIdentity]::GetCurrent().User,[System.Security.Principal.SecurityIdentifier]'S-1-5-18')) {
  $rule=New-Object System.Security.AccessControl.FileSystemAccessRule($sid,'FullControl',$inherit,'None','Allow')
  $security.AddAccessRule($rule)
}
[System.IO.Directory]::SetAccessControl($env:AID_BOSS_PS_STAGE,$security)
`

/** Execute only the current plugin's fixed scripts. A fresh private nonce prevents
 * stale versions/cache state from blocking or changing a subsequent invocation. */
export async function execPowerShellScript(script: string, args: string[], options: PowerShellScriptOptions): Promise<{ stdout: string; stderr: string }> {
  let stage: string | undefined
  let executable: string
  try {
    executable = options.executable ?? trustedPowerShell()
    if (!localAbsolute(executable)) throw new Error('POWERSHELL_UNSAFE_PATH')
    ordinaryPath(executable)
    const name = path.basename(script)
    if (!Object.hasOwn(dependencies, name) || !localAbsolute(script)) throw new Error('POWERSHELL_SCRIPT_NOT_ALLOWED')
    ordinaryPath(path.dirname(script), true)
    const files = [name, ...dependencies[name]!]
    // Read verified source bytes before creating the stage. No source link or
    // missing sibling can fall back to an older installed copy.
    const sources = files.map(file => {
      const source = path.join(path.dirname(script), file)
      ordinaryPath(source)
      return { file, bytes: readFileSync(source) }
    })
    const base = path.resolve(options.temporaryRoot ?? tmpdir())
    if (!localAbsolute(base)) throw new Error('POWERSHELL_UNSAFE_PATH')
    ordinaryPath(base, true)
    if (files.some(file => path.join(base, 'aid-bps-XXXXXX', file).length >= 240)) throw new Error('POWERSHELL_TEMP_PATH_TOO_LONG')
    stage = mkdtempSync(path.join(base, 'aid-bps-'))
    ordinaryPath(stage, true)
    if (files.some(file => path.join(stage!, file).length >= 240)) throw new Error('POWERSHELL_TEMP_PATH_TOO_LONG')
    await run(executable, ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', Buffer.from(privateDirectory, 'utf16le').toString('base64')], 15_000, { AID_BOSS_PS_STAGE: stage })
    for (const { file, bytes } of sources) {
      const destination = path.join(stage, file)
      writeFileSync(destination, bytes, { flag: 'wx' })
      ordinaryPath(destination)
    }
  } catch (error) {
    if (stage) cleanup(stage)
    // Source paths and command arguments never enter the preparation error.
    const reason = error instanceof Error && /^POWERSHELL_[A-Z_]+$/.test(error.message) ? error.message : 'POWERSHELL_PREPARATION_FAILED'
    throw new PowerShellScriptError(`[BOSS_POWERSHELL_PREPARE] 本机脚本准备失败（${reason}）`)
  }
  try {
    return await run(executable!, ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', path.join(stage!, path.basename(script)), ...args], options.timeout)
  } finally { cleanup(stage!) }
}

function cleanup(stage: string): void {
  try {
    ordinaryPath(stage, true)
    if (!path.basename(stage).startsWith('aid-bps-')) return
    // Only the fresh, caller-owned nonce; rm never follows contained symlinks.
    rmSync(stage, { recursive: true, force: true })
  } catch {
    try { process.stderr.write('[boss-powershell] temporary cleanup deferred\n') } catch { /* diagnostics cannot change the business result */ }
  }
}
