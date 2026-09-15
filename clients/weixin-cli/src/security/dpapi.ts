/**
 * DPAPI 本机密文存取（CurrentUser 作用域）。
 *
 * 复用仓库既有 idiom（association-client-cli/scripts/wechat-souyisou-lib.ps1 的
 * Protect-EvidenceArtifact / Unprotect-EvidenceArtifact）：Node 侧不新造加密，
 * 通过 powershell.exe -Command 调 .NET ProtectedData。仅用于 access_token 等
 * 小体积凭据，不做通用加密层。
 */
import { spawn } from 'node:child_process'

export type DpapiRunnerFn = (command: string, stdinText: string) => Promise<string>

/** 默认执行器：powershell.exe -NoProfile -Command，payload 走 stdin 避免进程列表泄漏 */
export async function runDpapiCommand(command: string, stdinText: string, signal?: AbortSignal): Promise<string> {
  signal?.throwIfAborted()
  return new Promise<string>((resolve, reject) => {
    const utf8Command = '[Console]::InputEncoding = [Text.UTF8Encoding]::new($false); [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); ' + command
    const child = spawn('powershell.exe', ['-NoProfile', '-Command', utf8Command], {
      windowsHide: true,
      signal,
      stdio: ['pipe', 'pipe', 'pipe'],
    })
    let stdout = ''
    let stderr = ''
    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')
    child.stdout.on('data', (d: string) => (stdout += d))
    child.stderr.on('data', (d: string) => (stderr += d))
    child.on('error', reject)
    child.on('close', (code) => {
      if (code === 0) resolve(stdout.trim())
      else reject(new Error(`DPAPI 调用失败（exit=${code}）：${stderr.trim().slice(0, 200)}`))
    })
    child.stdin.on('error', reject)
    child.stdin.write(stdinText)
    child.stdin.end()
  })
}

// stdin 读明文 → 输出 base64 密文
const PROTECT_COMMAND = [
  'Add-Type -AssemblyName System.Security;',
  '$bytes = [Text.Encoding]::UTF8.GetBytes([Console]::In.ReadToEnd());',
  '[Convert]::ToBase64String([Security.Cryptography.ProtectedData]::Protect(',
  '$bytes, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser))',
].join(' ')

// stdin 读 base64 密文 → 输出明文
const UNPROTECT_COMMAND = [
  'Add-Type -AssemblyName System.Security;',
  '$blob = [Convert]::FromBase64String([Console]::In.ReadToEnd().Trim());',
  '[Text.Encoding]::UTF8.GetString([Security.Cryptography.ProtectedData]::Unprotect(',
  '$blob, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser))',
].join(' ')

export async function protectText(plaintext: string, runFn: DpapiRunnerFn = runDpapiCommand): Promise<string> {
  return runFn(PROTECT_COMMAND, plaintext)
}

export async function unprotectText(blobBase64: string, runFn: DpapiRunnerFn = runDpapiCommand): Promise<string> {
  return runFn(UNPROTECT_COMMAND, blobBase64)
}
