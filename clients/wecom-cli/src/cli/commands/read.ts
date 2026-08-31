/**
 * CLI 子命令 read：读取与 target_ref 目标的会话消息（只读消息内容）。
 *
 * 用法：
 *   aid-wecom read --target-ref <ref> [--max-pages N] [--since-days N] [--json]
 *
 * 副作用提示：进入会话会清除该会话未读角标（企微客户端固有行为，stderr 提示）；
 * 抓取完成后驱动滚回底部恢复原位。wecom_history_read 不进 MCP，只能走本命令。
 */
import { getOperationEntry } from '../../operations/registry.js'
import { runCliOperation } from '../render.js'
import type { WecomHistoryReadArgs } from '../../operations/historyRead.js'

export interface ReadCommandOptions {
  targetRef?: string
  maxPages?: string
  sinceDays?: string
  /** true 时 stdout 最后一行输出 OperationResult JSON */
  json?: boolean
}

export async function readCommand(opts: ReadCommandOptions): Promise<number> {
  // 数字参数原样转换；非法值（NaN）留给 operation 统一返回 INVALID_ARGUMENT
  const args: WecomHistoryReadArgs = {
    target_ref: opts.targetRef as string,
    max_pages: opts.maxPages === undefined ? undefined : Number(opts.maxPages),
    since_days: opts.sinceDays === undefined ? undefined : Number(opts.sinceDays),
  }
  console.error('ℹ️ 注意：进入会话会清除该会话未读角标（只读消息内容，无其它副作用）')
  return runCliOperation(getOperationEntry('wecom_history_read').operation, args, { exclusive: true, json: opts.json })
}
