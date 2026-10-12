/**
 * DPAPI 加解密：PowerShell .NET ProtectedData（CurrentUser scope）。
 *
 * - 仅使用系统 WindowsPowerShell，子进程独立的系统模块/可执行路径。
 * - 不落 .ps1 文件；秘密只经 base64 stdin/stdout，不进入 argv 或错误信息。
 * - 输入输出走 base64 stdin/stdout。
 * - 非 Windows / PowerShell 不可用 → fail-loud 抛错（doctor 必检）。
 */
import { execFile, type ChildProcess } from 'node:child_process'
import { realpathSync } from 'node:fs'
import { win32 } from 'node:path'

const MAX_BASE64_LENGTH = 4 * 1024 * 1024
const COMMAND_PREFIX = "$ErrorActionPreference='Stop'; try { "
const COMMAND_SUFFIX = "; exit 0 } catch { [Console]::Error.Write('DPAPI_CALL_FAILED'); exit 1 }"
const PROTECT_SCRIPT = COMMAND_PREFIX + [
  'Add-Type -AssemblyName System.Security',
  '$in = [Console]::In.ReadToEnd()',
  '$bytes = [Convert]::FromBase64String($in)',
  '$enc = [System.Security.Cryptography.ProtectedData]::Protect($bytes, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)',
  '[Console]::Out.Write([Convert]::ToBase64String($enc))',
].join('; ') + COMMAND_SUFFIX

const UNPROTECT_SCRIPT = COMMAND_PREFIX + [
  'Add-Type -AssemblyName System.Security',
  '$in = [Console]::In.ReadToEnd()',
  '$bytes = [Convert]::FromBase64String($in)',
  '$dec = [System.Security.Cryptography.ProtectedData]::Unprotect($bytes, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)',
  '[Console]::Out.Write([Convert]::ToBase64String($dec))',
].join('; ') + COMMAND_SUFFIX

function canonicalBase64(value: string, allowEmpty: boolean): boolean {
  return typeof value === 'string' && (allowEmpty || value.length > 0)
    && value.length <= MAX_BASE64_LENGTH && value.length % 4 === 0
    && /^[A-Za-z0-9+/]*={0,2}$/.test(value)
    && Buffer.from(value, 'base64').toString('base64') === value
}

/** Shared Windows-only launcher configuration; does not execute a command. */
export function trustedSystemPowerShell(): { executable: string; cwd: string; env: NodeJS.ProcessEnv } {
  if (process.platform !== 'win32') throw new Error('DPAPI_UNSUPPORTED_PLATFORM')
  const root = process.env.SystemRoot ?? process.env.SYSTEMROOT
  if (!root || !/^[a-z]:\\[^\x00-\x1f"<>|?*:]+$/i.test(root) || win32.resolve(root) !== root) {
    throw new Error('DPAPI_POWERSHELL_UNAVAILABLE')
  }
  const directory = win32.join(root, 'System32', 'WindowsPowerShell', 'v1.0')
  const executable = win32.join(directory, 'powershell.exe')
  try {
    // Do not follow a replacement junction/symlink to a user executable.
    if (realpathSync.native(root).toLowerCase() !== root.toLowerCase()
      || realpathSync.native(executable).toLowerCase() !== executable.toLowerCase()) {
      throw new Error('invalid system path')
    }
  } catch { throw new Error('DPAPI_POWERSHELL_UNAVAILABLE') }
  const env: NodeJS.ProcessEnv = { ...process.env }
  // Windows environment names are case-insensitive. Keep exactly one of each.
  for (const name of Object.keys(env)) {
    if (/^(?:path|psmodulepath|systemroot|windir)$/i.test(name)) delete env[name]
  }
  env.SystemRoot = root
  env.windir = root
  env.Path = [win32.join(root, 'System32'), root, directory].join(';')
  env.PSModulePath = win32.join(directory, 'Modules')
  return { executable, cwd: directory, env }
}

function runPowerShell(script: string, stdinBase64: string, allowEmptyOutput = false): Promise<string> {
  if (process.platform !== 'win32') {
    return Promise.reject(new Error('DPAPI_UNSUPPORTED_PLATFORM'))
  }
  const { executable, cwd, env } = trustedSystemPowerShell()
  return new Promise((resolve, reject) => {
    let child: ChildProcess | undefined
    const failed = () => {
      try { child?.kill() } catch { /* The owned helper may already have exited. */ }
      reject(new Error('DPAPI_CALL_FAILED'))
    }
    try { child = execFile(
      executable,
      ['-NoProfile', '-NonInteractive', '-Command', script],
      { timeout: 30_000, maxBuffer: MAX_BASE64_LENGTH, windowsHide: true, cwd, env },
      (err, stdout, stderr) => {
        if (err || stderr.length > 0) { failed(); return }
        if (!canonicalBase64(stdout, allowEmptyOutput)) { reject(new Error('DPAPI_OUTPUT_INVALID')); return }
        resolve(stdout)
      },
    ) } catch { failed(); return }
    child.on('error', failed)
    if (!child.stdin) { failed(); return }
    child.stdin.on('error', failed)
    try { child.stdin.end(stdinBase64) } catch { failed() }
  })
}

/** 加密：utf8 明文 → base64 密文 */
export async function dpapiProtect(plaintext: string): Promise<string> {
  if (typeof plaintext !== 'string' || Buffer.byteLength(plaintext, 'utf8') > MAX_BASE64_LENGTH / 4 * 3) throw new Error('DPAPI_INPUT_INVALID')
  const inputB64 = Buffer.from(plaintext, 'utf8').toString('base64')
  return runPowerShell(PROTECT_SCRIPT, inputB64)
}

/** 解密：base64 密文 → utf8 明文 */
export async function dpapiUnprotect(cipherBase64: string): Promise<string> {
  if (typeof cipherBase64 !== 'string' || cipherBase64.length > MAX_BASE64_LENGTH * 2) throw new Error('DPAPI_INPUT_INVALID')
  // Legacy .NET FromBase64String accepts ASCII spaces and folded file lines.
  // Preserve those bytes while keeping unexpected characters/output invalid.
  const normalized = cipherBase64.replace(/[ \t\r\n]/g, '')
  if (!canonicalBase64(normalized, false)) throw new Error('DPAPI_INPUT_INVALID')
  const plainB64 = await runPowerShell(UNPROTECT_SCRIPT, normalized, true)
  try { return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(Buffer.from(plainB64, 'base64')) }
  catch { throw new Error('DPAPI_OUTPUT_INVALID') }
}
