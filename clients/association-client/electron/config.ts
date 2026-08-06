/**
 * 激活配置管理 —— 使用 Electron safeStorage 加密存储 access_token。
 *
 * 设计文档 §4.3。配置文件：%APPDATA%\association-client\client-config.json
 */

import { app, safeStorage } from 'electron'
import { existsSync, readFileSync, writeFileSync, unlinkSync } from 'node:fs'
import path from 'node:path'

export interface ClientConfig {
  bindingId: string
  accessToken: string
  tenantId: string
  tenantName: string
  serverUrl: string
  activatedAt: string
}

const CONFIG_FILE = 'client-config.json'

function getConfigPath(): string {
  return path.join(app.getPath('userData'), CONFIG_FILE)
}

/** 读取配置（解密 accessToken）。未激活返回 null。 */
export function loadConfig(): ClientConfig | null {
  const file = getConfigPath()
  if (!existsSync(file)) return null
  try {
    const raw = JSON.parse(readFileSync(file, 'utf-8'))
    if (!raw.accessTokenEnc) return null
    if (!safeStorage.isEncryptionAvailable()) {
      throw new Error('系统不支持加密存储（safeStorage 不可用）')
    }
    return {
      bindingId: raw.bindingId,
      accessToken: safeStorage.decryptString(Buffer.from(raw.accessTokenEnc, 'base64')),
      tenantId: raw.tenantId,
      tenantName: raw.tenantName || '',
      serverUrl: raw.serverUrl,
      activatedAt: raw.activatedAt,
    }
  } catch (err) {
    console.error('读取配置失败:', err)
    return null
  }
}

/** 保存配置（加密 accessToken）。 */
export function saveConfig(config: ClientConfig): void {
  if (!safeStorage.isEncryptionAvailable()) {
    throw new Error('系统不支持加密存储（safeStorage 不可用）')
  }
  const file = getConfigPath()
  const encrypted = {
    bindingId: config.bindingId,
    accessTokenEnc: safeStorage.encryptString(config.accessToken).toString('base64'),
    tenantId: config.tenantId,
    tenantName: config.tenantName,
    serverUrl: config.serverUrl,
    activatedAt: config.activatedAt,
  }
  writeFileSync(file, JSON.stringify(encrypted, null, 2), { encoding: 'utf-8' })
}

/** 删除配置（注销）。 */
export function clearConfig(): void {
  const file = getConfigPath()
  if (existsSync(file)) {
    unlinkSync(file)
  }
}
