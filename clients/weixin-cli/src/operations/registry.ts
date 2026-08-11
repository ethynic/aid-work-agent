/**
 * OPERATIONS 注册表：tool 名 → operation + CLI renderer 元数据（设计 §3.2 强制分层）。
 *
 * CLI command 与 MCP tool handler 都从这里取 operation，保证业务能力只实现一次。
 * M1 只有 weixin_probe；后续 search/collect/read/get-url/send/follow 按 probe 门禁逐个加入。
 */
import { createWeixinProbeOperation } from './probe.js'
import type { WeixinOperation } from './types.js'

export interface OperationEntry {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  operation: WeixinOperation<any>
  /** CLI renderer 元数据 */
  cli: {
    /** 是否有外部写副作用（CLI 执行前打印 ⚠️ 提示） */
    write: boolean
  }
}

export const OPERATIONS: Record<string, OperationEntry> = {
  weixin_probe: { operation: createWeixinProbeOperation(), cli: { write: false } },
}

export const OPERATION_NAMES = Object.keys(OPERATIONS)

/**
 * tool 名 → entry（toolDefs ↔ registry 一致性守卫）：未注册时 fail-loud 抛错，
 * 供 MCP server 启动期校验（漂移即启动失败，而非运行期 TypeError 裸错发给 Host）。
 */
export function getOperationEntry(name: string): OperationEntry {
  const entry = OPERATIONS[name]
  if (!entry) throw new Error(`toolDefs 与 registry 漂移：tool「${name}」未在 OPERATIONS 注册`)
  return entry
}
