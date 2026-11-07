/**
 * CLI 子命令 select：搜索唯一名称并回车进入会话（动作，无出站消息）。
 *
 * 用法：
 *   aid-wecom select --target-ref <ref> [--json]
 *
 * 前置条件：search 返回账号绑定的唯一 target_ref，有效期 5 分钟。
 * 副作用：进入会话会清除该会话未读角标（企微固有行为）并切换当前会话视图；
 * 按动作处理，执行前打印 ⚠️ 提示（registry cli.write=true 元数据）。
 * 失败语义：ref 过期 / 名称不再唯一 → TARGET_REF_STALE；标题不符 → UI_CHANGED。
 * 与 MCP tool wecom_chat_select 调用同一 operation（业务能力只实现一次）。
 */
import { getOperationEntry } from '../../operations/registry.js'
import { runCliOperation } from '../render.js'
import type { WecomChatSelectArgs } from '../../operations/chatSelect.js'

export interface SelectCommandOptions {
  targetRef?: string
  /** true 时 stdout 最后一行输出 OperationResult JSON */
  json?: boolean
}

export async function selectCommand(opts: SelectCommandOptions): Promise<number> {
  const entry = getOperationEntry('wecom_chat_select')
  if (entry.cli.write) {
    console.error('⚠️ 动作操作：将搜索并进入目标会话（不发送消息）；进入会话会清除其未读角标并切换当前会话视图')
  }
  const args: WecomChatSelectArgs = {
    target_ref: opts.targetRef as string,
  }
  return runCliOperation(entry.operation, args, { exclusive: true, json: opts.json })
}
