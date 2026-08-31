/**
 * OPERATIONS 注册表：tool 名 → operation + CLI renderer 元数据（强制分层）。
 *
 * CLI command 与 MCP tool handler 都从这里取 operation，保证业务能力只实现一次。
 * M1 有 wecom_probe / wecom_add_customer；M2 增加 wecom_chat_search / wecom_message_send；
 * M3 增加 wecom_unread_list / wecom_history_read / wecom_watch_poll
 * （wecom_history_read 只走 CLI read 动词，不进 MCP toolDefs——MCP 只暴露
 * unread_list 与 watch_poll 两个读工具）。
 */
import { createWecomAddCustomerOperation } from './addCustomer.js'
import { createWecomChatSearchOperation } from './chatSearch.js'
import { createWecomHistoryReadOperation } from './historyRead.js'
import { createWecomMessageSendOperation } from './messageSend.js'
import { createWecomProbeOperation } from './probe.js'
import { createWecomUnreadListOperation } from './unreadList.js'
import { createWecomWatchPollOperation } from './watchPoll.js'
import type { WecomOperation } from './types.js'

export interface OperationEntry {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  operation: WecomOperation<any>
  /** CLI renderer 元数据 */
  cli: {
    /** 是否有外部写副作用（CLI 执行前打印 ⚠️ 提示） */
    write: boolean
  }
}

export const OPERATIONS: Record<string, OperationEntry> = {
  wecom_probe: { operation: createWecomProbeOperation(), cli: { write: false } },
  wecom_add_customer: { operation: createWecomAddCustomerOperation(), cli: { write: true } },
  wecom_chat_search: { operation: createWecomChatSearchOperation(), cli: { write: false } },
  wecom_message_send: { operation: createWecomMessageSendOperation(), cli: { write: true } },
  wecom_unread_list: { operation: createWecomUnreadListOperation(), cli: { write: false } },
  wecom_history_read: { operation: createWecomHistoryReadOperation(), cli: { write: false } },
  wecom_watch_poll: { operation: createWecomWatchPollOperation(), cli: { write: false } },
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
