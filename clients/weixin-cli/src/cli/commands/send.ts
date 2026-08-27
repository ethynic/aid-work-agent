/**
 * CLI 子命令 send：M2 落地 chat 域（向 target_ref 目标发送 1 条文本消息，写动作）。
 *
 * 用法：
 *   aid-weixin send --domain chat --target-ref <ref> --text <文本>
 *
 * 写动作执行前打印 ⚠️ 提示（registry cli.write=true 元数据）；发送后校验失败
 * 返回 EXECUTION_UNKNOWN 且绝不自动重试。
 * 与 MCP tool weixin_message_send 调用同一 operation（业务能力只实现一次）。
 */
import { getOperationEntry } from '../../operations/registry.js'
import { runCliOperation } from '../render.js'
import type { CliDomain } from '../args.js'
import type { WeixinMessageSendArgs } from '../../operations/messageSend.js'

export interface SendCommandOptions {
  domain: CliDomain | undefined | 'invalid'
  targetRef?: string
  text?: string
  /** true 时 stdout 最后一行输出 OperationResult JSON */
  json?: boolean
}

export async function sendCommand(opts: SendCommandOptions): Promise<number> {
  if (opts.domain === 'invalid') {
    console.error('非法 --domain 值（合法值：souyisou/article/chat/chat-history/unread）')
    return 2
  }
  if (opts.domain !== 'chat') {
    console.error('send 目前只支持 --domain chat（如 send --domain chat --target-ref <ref> --text <文本>）')
    return 2
  }
  const entry = getOperationEntry('weixin_message_send')
  if (entry.cli.write) {
    console.error('⚠️ 写操作：将向目标发送 1 条消息；发送后校验失败不会自动重试（effect=unknown 时请人工核对）')
  }
  const args: WeixinMessageSendArgs = {
    target_ref: opts.targetRef as string,
    text: opts.text as string,
  }
  return runCliOperation(entry.operation, args, { exclusive: true, json: opts.json })
}
