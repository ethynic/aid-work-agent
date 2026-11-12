/**
 * 内置受信 Provider manifest 注册表（静态常量，不接受云端下发 executable/cwd/env/argv）。
 *
 * - 每个 Provider 一份 manifest：provider_id / tools / 写集合 / execution_target /
 *   protocol_version / shared_lock_capable；claim 到的 tool_name 必须 ∈ 对应 manifest
 *   的 tools，否则直接回 TOOL_NOT_ALLOWED。
 * - manifest digest 按 Provider 独立计算（输入只含 provider_id/tools/execution_target，
 *   与历史算法逐字节一致；新增工具随发布变更 digest，旧工具集摘要保留兼容锚点）。
 * - protocol_version=1 且 shared_lock_capable=false 表示该 Provider 尚未交付 v2 受控
 *   操作协议：v2 invocation 在能力门禁处拒绝（PROTOCOL_NOT_SUPPORTED），不降级旧发送。
 */
import { createHash } from 'node:crypto'
import type { RuntimeManifest, ToolDeclaration } from '../plugins/package.js'

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
  'boss_recovery_probe',
  'boss_filter',
  'boss_clear_filter',
  'boss_filter_options',
  'boss_goto',
  'boss_greet',
  'boss_lookup_candidates',
  'boss_greet_selected',
  'boss_accept_resume',
  'boss_reject_current',
  'boss_interview_demo',
  'boss_send_to',
  'boss_send_current',
  'boss_list_jobs',
  'boss_select_job',
  'boss_resume_detail',
  'boss_resume_batch',
  'boss_resume_next',
  'boss_read_chat',
  'boss_open_chat',
  'boss_overlay_inspect',
  'boss_overlay_dismiss',
  'boss_open_detail',
  'boss_greet_detail',
  'boss_close_detail',
] as const

// 与 boss CLI OPERATIONS 的 cli.write 同步 + interview_demo 沿用历史归类
const BOSS_WRITE_TOOLS: ReadonlySet<string> = new Set([
  'boss_filter',
  'boss_clear_filter',
  'boss_greet',
  'boss_greet_selected',
  'boss_greet_detail',
  'boss_accept_resume',
  'boss_reject_current',
  'boss_interview_demo',
  'boss_send_to',
  'boss_send_current',
  'boss_select_job',
  'boss_resume_detail',
  'boss_resume_batch',
  'boss_resume_next',
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

// 与 wecom CLI 的 TOOL_DEFS（src/mcp/toolDefs.ts，M11b 起 read_session 进 MCP）保持同步
// ——兼容旧工具及内部恢复，新增批量读取；Agent业务面由云端投影为读/发/批量读三项。
// search/select 不单独接入（send 类内部已含 search 定位链）；
// watch_poll/add_customer 不接入（轮询与获客动作不进 runtime 受信面）。
// 代理任务 P3 增加两个配置工具（契约 §3：受信配置流程专用，不进模型工具集，
// 可见性控制同 session_directory 先例——由云端投影决定，host core 不维护模型清单）。
// 写集合按现 CLI OPERATIONS 的 cli.write：三个发送工具为写；probe/unread/
// read_session 只读（read_session 进会话清角标归 CLI 只读语义，锁屏前置不拦）；
// config_calibrate 为写（CLI cli.write=true：切会话/写标定缓存），沿既有写工具
// 语义走锁屏前置与崩溃 unknown，不另造门禁；config_doctor 只读不加。
const WECOM_TOOLS = [
  'wecom_probe',
  'wecom_message_send',
  'wecom_send_status',
  'wecom_group_poll',
  'wecom_send_image',
  'wecom_send_file',
  'wecom_read_session',
  'wecom_read_sessions',
  'wecom_session_directory',
  'wecom_config_doctor',
  'wecom_config_calibrate',
  'wecom_unread_list',
] as const

const WECOM_WRITE_TOOLS: ReadonlySet<string> = new Set([
  'wecom_message_send',
  'wecom_send_image',
  'wecom_send_file',
  'wecom_config_calibrate',
])

export const TRUSTED_MANIFESTS: Readonly<Record<string, ProviderManifest>> = {
  'runtime-recovery': { provider_key: 'runtime-recovery', provider_id: 'ai.aidwork.runtime-recovery',
    tools: ['runtime_recovery_observe'], execution_target: 'local_required', protocol_version: 1,
    shared_lock_capable: false, write_tools: new Set() },
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
  wecom: {
    provider_key: 'wecom',
    provider_id: 'ai.aidwork.wecom',
    tools: WECOM_TOOLS,
    execution_target: 'local_required',
    // v1 受控形态：v2 invocation 在能力门禁处拒绝（PROTOCOL_NOT_SUPPORTED）。
    // shared_lock_capable=false = 独占桌面锁（与 boss 同机时经 invocationRunner 的
    // 桌面资源锁互斥排队，不并发控制桌面）
    protocol_version: 1,
    shared_lock_capable: false,
    write_tools: WECOM_WRITE_TOOLS,
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

/** 单 Provider manifest digest：输入只含 provider_id/tools(排序)/execution_target（历史算法，工具集合变化会改变摘要） */
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

// ----- 商店插件声明派生（契约 2.20：签名清单声明 → Runtime 通用接入） -----

/** 商店插件 plugin_id → provider key：去除 `ai.aidwork.` 前缀（已交付三插件与 legacy key 一致） */
export function providerKeyOfPlugin(pluginId: string): string {
  return pluginId.startsWith('ai.aidwork.') ? pluginId.slice('ai.aidwork.'.length) : pluginId
}

/**
 * 验签清单 → 通用 ProviderManifest 派生（Runtime 消费端唯一入口）。
 *
 * - 契约 2.20 修订：声明即调度授权——受信面 = tool_declaration.tools 全集（内部命令
 *   不声明的工具不进受信面）；不与历史写死名单取交集，未知插件与已交付插件同一逻辑。
 *   store 门禁已保证到达此处的清单带有效声明（声明名 ⊆ 清单 tools、无重复）。
 * - write_tools = 声明工具的 annotations.readOnlyHint !== true（缺省按写，保守）
 * - protocol_version=1 / shared_lock_capable=false / execution_target=local_required（验签已固定）
 */
export function deriveProviderManifest(manifest: RuntimeManifest): ProviderManifest {
  const provider_id = String(manifest.provider_id ?? '')
  const key = providerKeyOfPlugin(provider_id)
  const declaration = (manifest as { tool_declaration?: { tools?: unknown } }).tool_declaration
  const names = [...new Set((Array.isArray(declaration?.tools) ? declaration.tools : [])
    .map(tool => (tool as { name?: unknown } | null)?.name)
    .filter((name): name is string => typeof name === 'string' && name !== ''))]
  const readOnlyHints = new Map((Array.isArray(manifest.tools) ? manifest.tools : [])
    .map(tool => tool as { name?: unknown; annotations?: { readOnlyHint?: unknown } } | null)
    .filter((def): def is { name: string; annotations?: { readOnlyHint?: unknown } } => typeof def?.name === 'string' && def.name !== '')
    .map(def => [def.name, def.annotations?.readOnlyHint] as const))
  const write_tools = new Set(names.filter(name => readOnlyHints.get(name) !== true))
  return {
    provider_key: key,
    provider_id,
    tools: names,
    execution_target: 'local_required',
    protocol_version: 1,
    shared_lock_capable: false,
    write_tools,
  }
}

/** PluginStore 启动装配输入：启用且带有效调度声明的已验签插件 */
export interface PluginDeclaration {
  readonly plugin_id: string
  readonly provider_key: string
  readonly manifest: RuntimeManifest
  readonly declaration: ToolDeclaration
}

/**
 * 商店声明插件 → 派生 manifest 与并行/声明全集上报记录（Runtime 启动装配一次，runner
 * 门禁与能力上报同源）。parallel_tools 仅列声明 parallel:true 的工具；declared_tools 为
 * 声明全集映射（服务端 claim 放行与设备闸门的声明依据）；全串行插件不出现在 parallel_tools。
 */
export function assembleStoreManifests(declared: readonly PluginDeclaration[]): {
  manifests: Record<string, ProviderManifest>
  parallelTools: Record<string, string[]>
  declaredTools: Record<string, string[]>
} {
  const manifests: Record<string, ProviderManifest> = {}
  const parallelTools: Record<string, string[]> = {}
  const declaredTools: Record<string, string[]> = {}
  for (const item of declared) {
    const key = providerKeyOfPlugin(item.plugin_id)
    manifests[key] = deriveProviderManifest(item.manifest)
    declaredTools[key] = item.declaration.tools.map(tool => tool.name)
    const parallel = item.declaration.tools.filter(tool => tool.parallel === true).map(tool => tool.name)
    if (parallel.length > 0) parallelTools[key] = parallel
  }
  return { manifests, parallelTools, declaredTools }
}
