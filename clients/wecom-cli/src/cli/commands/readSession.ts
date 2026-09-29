/**
 * CLI 子命令 read-session：读取与目标的会话消息（只读消息内容）。
 *
 * 用法（target-ref 与 target-name 二选一，M11a）：
 *   aid-wecom read-session --target-ref <ref> [--max-pages N] [--since-days N] [--json]
 *   aid-wecom read-session --target-name <会话名> [--max-pages N] [--since-days N] [--json]
 *
 * --target-name 直达模式：CLI 内部自动 search 定位（身份校验 + 唯一匹配，歧义拒绝），
 * data 附 resolved_target；其余契约与 --target-ref 模式一致。
 *
 * M10b 双通道解析（M10c 起直连 token）：配置 AID_WECOM_SERVER_URL +
 * AID_WECOM_SERVER_TOKEN 时走服务端模型通道（按次计积分，data.channel="model"，
 * 附 model_usage/billing/model_latency_ms）；未配置或模型不可用（网络/超时/5xx）
 * 时本地 OCR 兜底（data.channel="ocr"）；余额不足（402）与 token 配置错误直接
 * 报错不降级。详见 README M10b 章节。
 *
 * 副作用提示：进入会话会清除该会话未读角标（企微客户端固有行为，stderr 提示）；
 * 抓取完成后驱动滚回底部恢复原位。wecom_read_session 不进 MCP，只能走本命令。
 * （M9 由 read 改名而来，旧 read 动词直接废弃，无别名。）
 */
import { getOperationEntry } from '../../operations/registry.js'
import { runCliOperation } from '../render.js'
import type { WecomReadSessionArgs } from '../../operations/readSession.js'

export interface ReadSessionCommandOptions {
  targetRef?: string
  targetName?: string
  maxPages?: string
  sinceDays?: string
  /** true 时 stdout 最后一行输出 OperationResult JSON */
  json?: boolean
}

export async function readSessionCommand(opts: ReadSessionCommandOptions): Promise<number> {
  // 数字参数原样转换；非法值（NaN）留给 operation 统一返回 INVALID_ARGUMENT
  const args: WecomReadSessionArgs = {
    ...(opts.targetRef !== undefined ? { target_ref: opts.targetRef } : {}),
    ...(opts.targetName !== undefined ? { target_name: opts.targetName } : {}),
    max_pages: opts.maxPages === undefined ? undefined : Number(opts.maxPages),
    since_days: opts.sinceDays === undefined ? undefined : Number(opts.sinceDays),
  }
  console.error('ℹ️ 注意：进入会话会清除该会话未读角标（只读消息内容，无其它副作用）')
  return runCliOperation(getOperationEntry('wecom_read_session').operation, args, { exclusive: true, json: opts.json })
}
