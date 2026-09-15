/**
 * 内置受信 Provider manifest 注册表（静态常量，不接受云端下发 executable/cwd/env/argv）。
 *
 * - 每个 Provider 一份 manifest：provider_id / tools / 写集合 / execution_target /
 *   protocol_version / shared_lock_capable；claim 到的 tool_name 必须 ∈ 对应 manifest
 *   的 tools，否则直接回 TOOL_NOT_ALLOWED。
 * - manifest digest 按 Provider 独立计算（输入只含 provider_id/tools/execution_target，
 *   与历史算法逐字节一致——boss 的 digest 值不变，云端核对不受多 Provider 改造影响）。
 * - protocol_version=1 且 shared_lock_capable=false 表示该 Provider 尚未交付 v2 受控
 *   操作协议：v2 invocation 在能力门禁处拒绝（PROTOCOL_NOT_SUPPORTED），不降级旧发送。
 */
import { createHash } from 'node:crypto'

export interface ProviderManifest {
  readonly provider_key: string
  readonly provider_id: string
  readonly tools: readonly string[]
  readonly execution_target: 'local_required'
  /** Provider 侧操作协议版本（v1=既有 MCP 契约；v2=target/payload/permit 受控协议） */
  readonly protocol_version: number
  /** 是否已具备共享桌面锁混用能力（真 v2 交付并通过验收后才置 true） */
  readonly shared_lock_capable: boolean
  /** 写动作集合：外部副作用不可逆，崩溃/锁屏策略与只读不同 */
  readonly write_tools: ReadonlySet<string>
}

// 与 boss CLI 的 TOOL_DEFS（src/mcp/toolDefs.ts）保持同步——2026-08-17 真机踩坑：
// 新增工具没进此清单会被 TOOL_NOT_ALLOWED 拒绝，云端智能体误判「功能不可用」
const BOSS_TOOLS = [
  'boss_filter',
  'boss_clear_filter',
  'boss_filter_options',
  'boss_goto',
  'boss_greet',
  'boss_accept_resume',
  'boss_reject_current',
  'boss_interview_demo',
  'boss_send_to',
  'boss_send_current',
  'boss_list_jobs',
  'boss_select_job',
  'boss_resume_detail',
  'boss_resume_batch',
  'boss_read_chat',
  'boss_open_chat',
  'boss_overlay_inspect',
  'boss_overlay_dismiss',
] as const

// 与 boss CLI OPERATIONS 的 cli.write 同步 + interview_demo 沿用历史归类
const BOSS_WRITE_TOOLS: ReadonlySet<string> = new Set([
  'boss_filter',
  'boss_clear_filter',
  'boss_greet',
  'boss_accept_resume',
  'boss_reject_current',
  'boss_interview_demo',
  'boss_send_to',
  'boss_send_current',
  'boss_select_job',
  'boss_resume_detail',
  'boss_resume_batch',
])

// 与 weixin CLI 的 TOOL_DEFS（src/mcp/toolDefs.ts）保持同步；写集合按现 CLI：
// 仅 message_send 为写。真实 Provider 尚无 weixin_message_send_v2——受信 manifest
// 保持真实 v1 形态；v2 会话发送能力经 config.providers.weixin.v2Send=true 显式
// 协商（隔离测试/未来真实 v2 Provider），由 cli 在启动时调用 setManifestOverride
// 升级为 V2 变体。能力上报与 v2 门禁同源，缺能力不得通过发布/分配门禁（#6）
const WEIXIN_TOOLS = [
  'weixin_probe',
  'weixin_name_resolve',
  'weixin_chat_search',
  'weixin_message_send',
  'weixin_history_read',
  'weixin_unread_list',
] as const

const WEIXIN_WRITE_TOOLS: ReadonlySet<string> = new Set(['weixin_message_send'])

/** v2 会话变体（显式协商时启用）：追加 observe + v2 发送，协议 2 + 共享锁 */
const WEIXIN_V2_TOOLS = [...WEIXIN_TOOLS, 'weixin_session_observe', 'weixin_message_send_v2'] as const
const WEIXIN_V2_WRITE_TOOLS: ReadonlySet<string> = new Set(['weixin_message_send', 'weixin_message_send_v2'])

export const TRUSTED_MANIFESTS: Readonly<Record<string, ProviderManifest>> = {
  'boss-recruiting': {
    provider_key: 'boss-recruiting',
    provider_id: 'ai.aidwork.boss-recruiting',
    tools: BOSS_TOOLS,
    execution_target: 'local_required',
    protocol_version: 1,
    shared_lock_capable: false,
    write_tools: BOSS_WRITE_TOOLS,
  },
  weixin: {
    provider_key: 'weixin',
    provider_id: 'ai.aidwork.weixin',
    tools: WEIXIN_TOOLS,
    execution_target: 'local_required',
    protocol_version: 1,
    shared_lock_capable: false,
    write_tools: WEIXIN_WRITE_TOOLS,
  },
}

const _manifestOverrides: Partial<Record<string, ProviderManifest>> = {}

/** 显式协商的 manifest 变体（#6）：cli 启动时按 config.providers.<key>.v2Send
 * 注入——运行期全部能力判断（v2 门禁/写集合/白名单）与上报同源生效 */
export function setManifestOverride(key: string, manifest: ProviderManifest | null): void {
  if (manifest === null) delete _manifestOverrides[key]
  else _manifestOverrides[key] = manifest
}

export function getProviderManifest(key: string): ProviderManifest | undefined {
  return _manifestOverrides[key] ?? TRUSTED_MANIFESTS[key]
}

/** 实际生效 manifest（含显式协商变体）；无则 undefined（调用方回退 TRUSTED） */
export function effectiveManifestFor(key: string): ProviderManifest | undefined {
  return getProviderManifest(key)
}

/** 任意 manifest 实例的摘要（与 manifestDigestFor 同算法——历史算法：
 * provider_id + 排序 tools + execution_target） */
export function manifestDigestOf(manifest: ProviderManifest): string {
  const stable = JSON.stringify({
    provider_id: manifest.provider_id,
    tools: [...manifest.tools].sort(),
    execution_target: manifest.execution_target,
  })
  return createHash('sha256').update(stable).digest('hex')
}

/** weixin v2 会话变体工厂（测试/未来真实 v2 Provider 显式协商用） */
export function weixinV2Manifest(): ProviderManifest {
  return {
    provider_key: 'weixin',
    provider_id: 'ai.aidwork.weixin',
    tools: WEIXIN_V2_TOOLS,
    execution_target: 'local_required',
    protocol_version: 2,
    shared_lock_capable: true,
    write_tools: WEIXIN_V2_WRITE_TOOLS,
  }
}

export function isToolAllowedFor(manifest: ProviderManifest, toolName: string): boolean {
  return manifest.tools.includes(toolName)
}

export function isWriteToolFor(manifest: ProviderManifest, toolName: string): boolean {
  return manifest.write_tools.has(toolName)
}

/** 单 Provider manifest digest：输入只含 provider_id/tools(排序)/execution_target（历史算法，boss 值不变） */
export function manifestDigestFor(key: string): string {
  const manifest = TRUSTED_MANIFESTS[key]
  if (!manifest) throw new Error(`未知 provider key: ${key}`)
  const stable = JSON.stringify({
    provider_id: manifest.provider_id,
    tools: [...manifest.tools].sort(),
    execution_target: manifest.execution_target,
  })
  return createHash('sha256').update(stable).digest('hex')
}
