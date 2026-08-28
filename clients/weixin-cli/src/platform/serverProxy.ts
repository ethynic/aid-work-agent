/**
 * 服务端代理凭据管理（方案 A：Kimi 调用走服务端 LLM 网关集中计费，
 * 见 docs/design/weixin/weixin-cli-billing.md §4.2）。
 *
 * 配置（全部环境变量驱动，不新增 CLI 动词）：
 * - AID_WEIXIN_SERVER_URL        服务端地址，设置即启用代理模式
 * - AID_WEIXIN_ACTIVATION_CODE   一次性激活码；本地无有效绑定时首次调用自动激活
 * - AID_WEIXIN_KIMI_API_KEY      本机直调降级开关（仅开发调试，不计费；优先级最高）
 *
 * access_token 不落明文：DPAPI（CurrentUser）加密后存 <stateDir>/binding.json。
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { hostname } from 'node:os'
import { join } from 'node:path'
import { protectText, unprotectText, type DpapiRunnerFn } from '../security/dpapi.js'

export interface ProxyAuth {
  serverUrl: string
  accessToken: string
}

export type FetchFn = (url: string, init: { method: string; headers: Record<string, string>; body: string }) => Promise<{
  status: number
  json: () => Promise<Record<string, unknown>>
}>

export interface ServerProxyDeps {
  env?: NodeJS.ProcessEnv
  fetchFn?: FetchFn
  dpapiRunFn?: DpapiRunnerFn
  /** 测试用：覆盖状态目录（默认 %APPDATA%/aid-weixin 或 ~/.aid-weixin） */
  stateDir?: string
  hostnameFn?: () => string
}

interface BindingFile {
  server_url: string
  token_blob: string
  tenant_name?: string
  activated_at?: string
}

export function getStateDir(env: NodeJS.ProcessEnv = process.env): string {
  const base = env.APPDATA || env.HOME || '.'
  return join(base, 'aid-weixin')
}

function bindingPath(stateDir: string): string {
  return join(stateDir, 'binding.json')
}

function normalizeServerUrl(raw: string | undefined): string {
  return (raw ?? '').trim().replace(/\/+$/, '')
}

/** 读取本地绑定并解出 access_token；文件缺失/损坏/解密失败一律返回 null（触发重新激活） */
export async function loadBinding(deps: ServerProxyDeps = {}): Promise<(ProxyAuth & { tenantName?: string }) | null> {
  const env = deps.env ?? process.env
  const stateDir = deps.stateDir ?? getStateDir(env)
  const path = bindingPath(stateDir)
  if (!existsSync(path)) return null
  try {
    const parsed = JSON.parse(readFileSync(path, 'utf8')) as BindingFile
    if (!parsed.server_url || !parsed.token_blob) return null
    const accessToken = await unprotectText(parsed.token_blob, deps.dpapiRunFn)
    if (!accessToken) return null
    return { serverUrl: parsed.server_url, accessToken, tenantName: parsed.tenant_name }
  } catch {
    return null
  }
}

/** 激活码 → access_token，DPAPI 加密后落盘（明文 token 仅存在于内存） */
export async function activateAndStore(
  serverUrl: string,
  activationCode: string,
  deps: ServerProxyDeps = {},
): Promise<ProxyAuth> {
  const env = deps.env ?? process.env
  const stateDir = deps.stateDir ?? getStateDir(env)
  const fetchFn: FetchFn = deps.fetchFn ?? (fetch as unknown as FetchFn)
  const machineId = (deps.hostnameFn ?? hostname)()

  const resp = await fetchFn(`${serverUrl}/api/client/v1/activate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      activation_code: activationCode.trim().toUpperCase(),
      machine_id: machineId,
      client_name: `weixin-cli@${machineId}`,
    }),
  })
  const data: Record<string, unknown> = await resp.json().catch(() => ({} as Record<string, unknown>))
  if (resp.status !== 200) {
    const detail = typeof data.detail === 'string' ? data.detail : `HTTP ${resp.status}`
    throw new Error(`激活失败：${detail}`)
  }
  const accessToken = typeof data.access_token === 'string' ? data.access_token : ''
  if (!accessToken) throw new Error('激活响应缺少 access_token（服务端契约变更？）')

  const blob = await protectText(accessToken, deps.dpapiRunFn)
  mkdirSync(stateDir, { recursive: true })
  const file: BindingFile = {
    server_url: serverUrl,
    token_blob: blob,
    tenant_name: typeof data.tenant_name === 'string' ? data.tenant_name : undefined,
    activated_at: new Date().toISOString(),
  }
  writeFileSync(bindingPath(stateDir), JSON.stringify(file), { mode: 0o600 })
  return { serverUrl, accessToken }
}

export type ProxyResolution =
  | { status: 'disabled' } // 未配置 AID_WEIXIN_SERVER_URL（且走本机降级或报 CONFIG_MISSING）
  | { status: 'ok'; auth: ProxyAuth }
  | { status: 'error'; serverUrl: string; message: string }

/**
 * 解析代理凭据：本地绑定命中 → 复用；否则有激活码 → 惰性激活；否则报配置缺失。
 * 永不抛异常：激活/网络失败归并为 error 状态，由驱动侧映射 CONFIG_MISSING。
 */
export async function resolveProxyAuth(deps: ServerProxyDeps = {}): Promise<ProxyResolution> {
  const env = deps.env ?? process.env
  const serverUrl = normalizeServerUrl(env.AID_WEIXIN_SERVER_URL)
  if (!serverUrl) return { status: 'disabled' }

  const cached = await loadBinding(deps)
  if (cached && cached.serverUrl === serverUrl) return { status: 'ok', auth: cached }

  const code = (env.AID_WEIXIN_ACTIVATION_CODE ?? '').trim()
  if (!code) {
    return {
      status: 'error',
      serverUrl,
      message: '已配置 AID_WEIXIN_SERVER_URL 但本机无有效绑定；请设置 AID_WEIXIN_ACTIVATION_CODE（一次性激活码）后重试，首次调用会自动激活',
    }
  }
  try {
    const auth = await activateAndStore(serverUrl, code, deps)
    return { status: 'ok', auth }
  } catch (err) {
    return { status: 'error', serverUrl, message: err instanceof Error ? err.message : String(err) }
  }
}

// 进程内缓存：激活是网络 + 落盘操作，同一进程内多个驱动调用只解析一次
let cachedEnv: Record<string, string> | null = null

/**
 * 给 PowerShell 驱动子进程的环境变量（token 只走 env，不进命令行参数，避免进程列表泄漏）。
 * 失败态通过 AID_WEIXIN_PROXY_ERROR 传给驱动，由驱动 fail closed 为 CONFIG_MISSING。
 */
export async function resolveDriverProxyEnv(deps: ServerProxyDeps = {}): Promise<Record<string, string>> {
  if (cachedEnv !== null && !deps.env && !deps.stateDir) return cachedEnv
  const resolution = await resolveProxyAuth(deps)
  let env: Record<string, string>
  switch (resolution.status) {
    case 'disabled':
      env = {}
      break
    case 'ok':
      env = {
        AID_WEIXIN_SERVER_URL: resolution.auth.serverUrl,
        AID_WEIXIN_ACCESS_TOKEN: resolution.auth.accessToken,
      }
      break
    case 'error':
      env = {
        AID_WEIXIN_SERVER_URL: resolution.serverUrl,
        AID_WEIXIN_PROXY_ERROR: resolution.message,
      }
      break
  }
  if (!deps.env && !deps.stateDir) cachedEnv = env
  return env
}

/** 测试用：清空进程内缓存 */
export function resetDriverProxyEnvCache(): void {
  cachedEnv = null
}
