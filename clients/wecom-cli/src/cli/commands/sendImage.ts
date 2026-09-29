/**
 * CLI 子命令 send-image：向目标发送 1 张本地图片（写动作）。
 *
 * 用法（target-ref 与 target-name 二选一，M11a）：
 *   aid-wecom send-image --target-ref <ref> --image <本地绝对路径> [--json]
 *   aid-wecom send-image --target-name <会话名> --image <本地绝对路径> [--json]
 *
 * --target-name 直达模式：CLI 内部自动 search 定位（身份校验 + 唯一匹配，歧义拒绝），
 * data 附 resolved_target；其余契约与 --target-ref 模式一致。
 * 文件契约：image 只收本机绝对路径（png/jpg/jpeg/bmp/gif，≤20MB），调用方负责把文件
 * 落到装有 runtime 与 wecom-cli 的机器上。写动作执行前打印 ⚠️ 提示（registry
 * cli.write=true 元数据）；发送后校验失败返回 EXECUTION_UNKNOWN 且绝不自动重试。
 * 与 MCP tool wecom_send_image 调用同一 operation（业务能力只实现一次）。
 */
import { getOperationEntry } from '../../operations/registry.js'
import { runCliOperation } from '../render.js'
import type { WecomSendImageArgs } from '../../operations/sendImage.js'

export interface SendImageCommandOptions {
  targetRef?: string
  targetName?: string
  image?: string
  /** true 时 stdout 最后一行输出 OperationResult JSON */
  json?: boolean
}

export async function sendImageCommand(opts: SendImageCommandOptions): Promise<number> {
  const entry = getOperationEntry('wecom_send_image')
  if (entry.cli.write) {
    console.error('⚠️ 写操作：将向目标发送 1 张图片；发送后校验失败不会自动重试（effect=unknown 时请人工核对）')
  }
  const args: WecomSendImageArgs = {
    ...(opts.targetRef !== undefined ? { target_ref: opts.targetRef } : {}),
    ...(opts.targetName !== undefined ? { target_name: opts.targetName } : {}),
    image_path: opts.image as string,
  }
  return runCliOperation(entry.operation, args, { exclusive: true, json: opts.json })
}
