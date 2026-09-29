/**
 * CLI 子命令 send：向目标发送 1 条文本消息（写动作）。
 *
 * 用法（target-ref 与 target-name 二选一，M11a）：
 *   aid-wecom send --target-ref <ref> --text <文本> [--json]
 *   aid-wecom send --target-name <会话名> [--subtitle <副标题>] --text <文本> [--json]
 *
 * --target-name 直达模式：CLI 内部自动 search 定位（身份校验 + 唯一匹配，歧义拒绝），
 * data 附 resolved_target；其余契约与 --target-ref 模式一致。
 * 写动作执行前打印 ⚠️ 提示（registry cli.write=true 元数据）；发送后校验失败
 * 返回 EXECUTION_UNKNOWN 且绝不自动重试。
 * 与 MCP tool wecom_message_send 调用同一 operation（业务能力只实现一次）。
 */
import { getOperationEntry } from '../../operations/registry.js'
import { runCliOperation } from '../render.js'
import type { WecomMessageSendArgs } from '../../operations/messageSend.js'

export interface SendCommandOptions {
  targetRef?: string
  targetName?: string
  subtitle?: string
  text?: string
  /** true 时 stdout 最后一行输出 OperationResult JSON */
  json?: boolean
}

export async function sendCommand(opts: SendCommandOptions): Promise<number> {
  const entry = getOperationEntry('wecom_message_send')
  if (entry.cli.write) {
    console.error('⚠️ 写操作：将向目标发送 1 条消息；发送后校验失败不会自动重试（effect=unknown 时请人工核对）')
  }
  const args: WecomMessageSendArgs = {
    ...(opts.targetRef !== undefined ? { target_ref: opts.targetRef } : {}),
    ...(opts.targetName !== undefined ? { target_name: opts.targetName } : {}),
    ...(opts.subtitle !== undefined ? { subtitle: opts.subtitle } : {}),
    text: opts.text as string,
  }
  return runCliOperation(entry.operation, args, { exclusive: true, json: opts.json })
}
