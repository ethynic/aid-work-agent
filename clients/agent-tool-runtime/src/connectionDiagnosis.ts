import { createHash } from 'node:crypto'
import { closeSync, existsSync, lstatSync, openSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { ApiClient, ApiError, DeviceRevokedError, NetworkError } from './apiClient.js'
import { dpapiProtect, dpapiUnprotect } from './dpapi.js'
import { type RuntimeConfig } from './config.js'

export interface ConnectionDiagnosis {
  version: 1; category: string; summary: string; steps: string[]
  service_verified: boolean; can_preserve: boolean; can_reconfigure: boolean; requires_reboot: boolean
}
export function diagnosis(category: string, summary: string, steps: string[], extras: Partial<ConnectionDiagnosis> = {}): ConnectionDiagnosis {
  return { version: 1, category, summary, steps, service_verified: false, can_preserve: false, can_reconfigure: false, requires_reboot: false, ...extras }
}
export function connectionFailure(error: unknown, pairing = false): ConnectionDiagnosis {
  if (error instanceof DeviceRevokedError || error instanceof ApiError && error.status === 401) return diagnosis('CREDENTIALS_REVOKED', '设备凭据已失效或授权已撤销', ['请管理员确认设备使用授权。', '授权恢复后选择重新配置，使用网页新生成的配对码。'])
  if (error instanceof NetworkError) {
    const category = error.failureKind === 'dns' ? 'DNS_FAILED' : error.failureKind === 'tls' ? 'TLS_FAILED' : 'NETWORK_FAILED'
    return diagnosis(category, category === 'DNS_FAILED' ? '无法解析企业服务地址' : category === 'TLS_FAILED' ? '企业服务的HTTPS证书验证失败' : '无法连接企业服务', [category === 'TLS_FAILED' ? '请管理员检查服务器证书和电脑日期，不要关闭证书验证。' : '检查网络、VPN或代理以及管理员提供的企业服务地址。', '服务恢复后点击检查连接；无需重新配对。'])
  }
  if (error instanceof ApiError && error.status === 400 && pairing) return diagnosis('PAIRING_CODE_INVALID', '配对码无效、过期或已被使用', ['在网页本地工具页面生成新的配对码。', '使用新码重新提交配对，设备名称可以沿用。'])
  if (error instanceof ApiError && error.status === 403) return diagnosis('SERVER_FORBIDDEN', '企业服务拒绝了当前请求', ['请管理员检查网关、设备授权及账号访问权限。', '权限恢复后点击检查连接。'])
  if (error instanceof ApiError && error.status === 404) return diagnosis('SERVER_API_MISSING', '该服务地址没有可用的Runtime接口', ['填写管理员提供的企业服务根地址，不要填写BOSS地址或网页子页面。', '请管理员确认Runtime接口已启用，再检查连接。'])
  if (error instanceof ApiError && error.status >= 500) return diagnosis('SERVER_UNAVAILABLE', '企业服务异常或返回了不兼容的数据', ['请管理员恢复服务器和Runtime接口。', '恢复后检查连接；配对响应未确认时使用新的配对码，不自动重复原请求。'])
  return diagnosis('CONNECTION_UNCONFIRMED', '连接结果未能确认', ['检查企业服务地址与网络。', pairing ? '请生成新的配对码再配对；程序不会自动重复可能已被服务器接受的请求。' : '点击检查连接重新验证；仍失败时按页面显示的类别处理。'])
}
export function validServer(value: string): boolean {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password && !url.search && !url.hash } catch { return false }
}
function readIdentity(home: string): { config: RuntimeConfig; cipher: string; digest: string } | null {
  const files = ['config.json', 'credentials.bin'].map(name => join(home, name))
  if (files.some(file => !existsSync(file) || lstatSync(file).isSymbolicLink() || !lstatSync(file).isFile() || lstatSync(file).size > 4 * 1024 * 1024)) return null
  const bytes = readFileSync(files[0]!); const cipher = readFileSync(files[1]!, 'utf8')
  const config = JSON.parse(bytes.toString('utf8').replace(/^\uFEFF/, '')) as RuntimeConfig
  if (!config || typeof config !== 'object' || typeof config.server !== 'string' || !validServer(config.server) || typeof config.device_id !== 'string' || !config.device_id) return null
  return { config, cipher, digest: createHash('sha256').update(bytes).update('\0').update(cipher).digest('hex') }
}
export function identityDigest(home: string): string | null { try { return readIdentity(home)?.digest ?? null } catch { return null } }
/** Fixed existing identity only. No caller-supplied token, URL or business claim. */
export async function diagnoseConnection(home: string): Promise<ConnectionDiagnosis> {
  try {
    const sentinel = randomUUID(); if (await dpapiUnprotect(await dpapiProtect(sentinel)) !== sentinel) throw new Error('protection')
  } catch { return diagnosis('WINDOWS_PROTECTION', 'Windows当前用户的凭据保护不可用', ['使用原配对的Windows用户正常登录后重新打开客户端。', '请管理员修复Windows系统PowerShell与用户凭据保护组件，再检查连接。']) }
  let probe: string | undefined
  try {
    if (!existsSync(home) || lstatSync(home).isSymbolicLink() || !lstatSync(home).isDirectory()) throw new Error('home')
    probe = join(home, `.connection-check-${randomUUID()}.tmp`)
    const fd = openSync(probe, 'wx'); try { writeFileSync(fd, 'connection-check') } finally { closeSync(fd) }
    unlinkSync(probe); probe = undefined
  } catch { return diagnosis('LOCAL_STORAGE', '客户端配置目录不可读写', ['确认使用原Windows用户，关闭占用配置目录的旧Runtime。', '请管理员恢复当前用户对本产品配置目录的读写权限；不要删除设备或任务记录。', '权限恢复后重新检查连接。']) }
  finally { if (probe) try { unlinkSync(probe) } catch { /* Fixed probe only. */ } }
  const can_reconfigure = true
  if (!existsSync(join(home, 'config.json'))) return diagnosis('CONFIG_MISSING', '原连接配置缺失', ['选择重新配置连接，输入企业服务地址、设备名称和新的配对码。', '原记录会归档保留；新配对后在网页选定新设备。'], { can_reconfigure })
  if (!existsSync(join(home, 'credentials.bin'))) return diagnosis('CREDENTIALS_MISSING', '原设备凭据缺失', ['选择重新配置连接并使用新配对码。', '不要复制其他Windows用户的凭据。'], { can_reconfigure })
  let identity: ReturnType<typeof readIdentity>
  try { identity = readIdentity(home) } catch { identity = null }
  if (!identity) return diagnosis('CONFIG_INVALID', '原连接配置格式损坏或服务地址无效', ['选择重新配置连接，使用管理员提供的服务根地址。', '原资料会保留，不会将旧任务转移到新设备。'], { can_reconfigure })
  let token: string
  try { token = await dpapiUnprotect(identity.cipher); if (!token) throw new Error('empty') }
  catch { return diagnosis('CREDENTIALS_UNREADABLE', '原设备凭据无法解密', ['先确认使用原配对的Windows用户。', '仍无法恢复时选择重新配置连接，使用新的配对码。'], { can_reconfigure }) }
  let selected: boolean
  try {
    selected = (await new ApiClient(identity.config.server, token).checkConnection()).selected
  } catch (error) { return { ...connectionFailure(error), can_reconfigure } }
  if (existsSync(join(home, 'pairing.pending.json'))) return diagnosis('PAIRING_TRANSACTION', '服务器可访问，但本机存在未完成的配对事务', ['重新打开客户端完成原事务。', '事务仍无法恢复时选择重新配置；原资料会归档保留。'], { service_verified: true, can_reconfigure })
  return diagnosis(selected ? 'VERIFIED' : 'DEVICE_NOT_SELECTED', selected ? '原配置中的凭据已通过服务器心跳验证' : '服务器心跳已通过，但网页尚未选定此设备', [...(!selected ? ['在网页本地工具中选定本次配对的设备；名称相同的新旧设备编号不同。'] : []), '可以保留原配置与配对凭据；心跳接口不返回设备编号，编号以原配对记录为准。', '若管理资料异常，选择修复管理资料；业务记录和插件留在原位置。', '验证连接不启动业务执行，需要执行任务时点击启动执行。'], { service_verified: true, can_preserve: true, can_reconfigure })
}
