/**
 * DPAPI 本机密文存取（CurrentUser 作用域）。
 *
 * 移植来源：weixin-cli src/security/dpapi.ts（P0.3 已验证打包可行，wecom-cli 本地库
 * 密钥存储采用同一机制，设计 §3.4）。复用仓库既有 idiom（association-client-cli/
 * scripts/wechat-souyisou-lib.ps1 的 Protect-EvidenceArtifact / Unprotect-EvidenceArtifact）：
 * Node 侧不新造加密，通过 powershell.exe -Command 调 .NET ProtectedData。仅用于
 * 本地库主密钥等小体积凭据，不做通用加密层。
 */
import { spawn } from 'node:child_process'

export type DpapiRunnerFn = (command: string, stdinText: string) => Promise<string>

/**
 * DPAPI 调用超时（毫秒）：与全仓 spawn 纪律对齐（version/running 5s、py 120s/600s、
 * ps1 驱动带超时），本调用是唯一此前无超时的 spawn——挂死的 PowerShell（Add-Type 被
 * 安全软件拦截等）会让 keystore saveKey/loadKey 与 doctor 深检无限阻塞。小体积凭据
 * （密钥 hex / blob）Protect/Unprotect 实测毫秒级，30s 已含极慢机余量。
 */
export const DPAPI_TIMEOUT_MS = 30_000

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
    let settled = false
    const finish = (fn: typeof resolve | typeof reject, value: string | Error) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      ;(fn as (v: string | Error) => void)(value)
    }
    // 超时兜底：kill 子进程并按失败落定；错误消息不含 stdinText（密钥/payload 不进异常）
    const timer = setTimeout(() => {
      child.kill()
      finish(reject, new Error(`DPAPI 调用超时（>${DPAPI_TIMEOUT_MS / 1000}s），已终止 PowerShell 进程`))
    }, DPAPI_TIMEOUT_MS)
    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')
    child.stdout.on('data', (d: string) => (stdout += d))
    child.stderr.on('data', (d: string) => (stderr += d))
    child.on('error', (err) => finish(reject, err))
    child.on('close', (code) => {
      if (code === 0) finish(resolve, stdout.trim())
      else finish(reject, new Error(`DPAPI 调用失败（exit=${code}）：${stderr.trim().slice(0, 200)}`))
    })
    child.stdin.on('error', () => finish(reject, new Error('DPAPI stdin 写入失败')))
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
