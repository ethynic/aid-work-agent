/**
 * 设备 token 存取：DPAPI 加密后落盘 credentials.bin。
 *
 * - config.json 不含 token；token 只存 credentials.bin（DPAPI 密文）。
 * - 日志永不输出明文或密文内容。
 */
import { existsSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { credentialsPath, runtimeHomeDir } from './config.js'
import { dpapiProtect, dpapiUnprotect } from './dpapi.js'
import { mkdirSync } from 'node:fs'

export async function saveDeviceToken(token: string): Promise<void> {
  const cipher = await dpapiProtect(token)
  mkdirSync(runtimeHomeDir(), { recursive: true })
  writeFileSync(credentialsPath(), cipher, 'utf8')
}

export function hasDeviceToken(): boolean {
  return existsSync(credentialsPath())
}

/** 读取并解密设备 token；文件缺失或解密失败均 fail-loud 抛错 */
export async function loadDeviceToken(): Promise<string> {
  const file = credentialsPath()
  if (!existsSync(file)) {
    throw new Error('未找到设备凭证（credentials.bin），请先执行 pair')
  }
  const cipher = readFileSync(file, 'utf8').trim()
  try {
    const token = await dpapiUnprotect(cipher)
    if (!token) throw new Error('解密结果为空')
    return token
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err)
    throw new Error(`设备凭证解密失败（可能凭证损坏或当前 Windows 用户不是配对用户）: ${detail}`)
  }
}

export function clearCredentials(): boolean {
  const file = credentialsPath()
  if (!existsSync(file)) return false
  rmSync(file)
  return true
}
