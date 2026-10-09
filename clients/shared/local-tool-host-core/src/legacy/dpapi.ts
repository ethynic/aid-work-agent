/**
 * DPAPI 加解密：PowerShell .NET ProtectedData（CurrentUser scope）。
 *
 * - 通过 execFile('powershell.exe', ['-NoProfile','-NonInteractive','-Command', ...]) 内联执行，
 *   不落 .ps1 文件（规避编码问题）。
 * - 输入输出走 base64 stdin/stdout。
 * - 非 Windows / PowerShell 不可用 → fail-loud 抛错（doctor 必检）。
 */
import { execFile } from 'node:child_process'

const PROTECT_SCRIPT = [
  'Add-Type -AssemblyName System.Security',
  '$in = [Console]::In.ReadToEnd()',
  '$bytes = [Convert]::FromBase64String($in.Trim())',
  '$enc = [System.Security.Cryptography.ProtectedData]::Protect($bytes, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)',
  '[Console]::Out.Write([Convert]::ToBase64String($enc))',
].join('; ')

const UNPROTECT_SCRIPT = [
  'Add-Type -AssemblyName System.Security',
  '$in = [Console]::In.ReadToEnd()',
  '$bytes = [Convert]::FromBase64String($in.Trim())',
  '$dec = [System.Security.Cryptography.ProtectedData]::Unprotect($bytes, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)',
  '[Console]::Out.Write([Convert]::ToBase64String($dec))',
].join('; ')

function runPowerShell(script: string, stdinBase64: string): Promise<string> {
  if (process.platform !== 'win32') {
    return Promise.reject(new Error(`DPAPI 仅支持 Windows（当前平台 ${process.platform}）`))
  }
  return new Promise((resolve, reject) => {
    const child = execFile(
      'powershell.exe',
      ['-NoProfile', '-NonInteractive', '-Command', script],
      { timeout: 30_000, maxBuffer: 4 * 1024 * 1024 },
      (err, stdout) => {
        if (err) {
          reject(new Error(`DPAPI PowerShell 调用失败: ${err.message}`))
          return
        }
        const trimmed = stdout.trim()
        if (!trimmed) {
          reject(new Error('DPAPI PowerShell 无输出'))
          return
        }
        resolve(trimmed)
      },
    )
    child.on('error', (err) => reject(new Error(`DPAPI PowerShell 启动失败: ${err.message}`)))
    child.stdin!.write(stdinBase64)
    child.stdin!.end()
  })
}

/** 加密：utf8 明文 → base64 密文 */
export async function dpapiProtect(plaintext: string): Promise<string> {
  const inputB64 = Buffer.from(plaintext, 'utf8').toString('base64')
  return runPowerShell(PROTECT_SCRIPT, inputB64)
}

/** 解密：base64 密文 → utf8 明文 */
export async function dpapiUnprotect(cipherBase64: string): Promise<string> {
  const plainB64 = await runPowerShell(UNPROTECT_SCRIPT, cipherBase64)
  return Buffer.from(plainB64, 'base64').toString('utf8')
}
