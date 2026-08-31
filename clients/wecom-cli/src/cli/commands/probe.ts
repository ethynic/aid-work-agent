/**
 * CLI 子命令 probe：只读环境探测。
 *
 * 用法：
 *   aid-wecom probe [--verbose] [--json]
 *
 * 与 MCP tool wecom_probe 调用同一 operation（业务能力只实现一次）。
 */
import { getOperationEntry } from '../../operations/registry.js'
import { runCliOperation } from '../render.js'
import type { WecomProbeArgs } from '../../operations/probe.js'

export interface ProbeCommandOptions {
  verbose?: boolean
  /** true 时 stdout 最后一行输出 OperationResult JSON */
  json?: boolean
}

export async function probeCommand(opts: ProbeCommandOptions): Promise<number> {
  const args: WecomProbeArgs = { verbose: opts.verbose }
  return runCliOperation(getOperationEntry('wecom_probe').operation, args, { json: opts.json })
}
