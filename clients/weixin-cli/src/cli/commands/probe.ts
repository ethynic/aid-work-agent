/**
 * CLI 子命令 probe：只读环境探测（设计 §4.1 标准命令面）。
 *
 * 用法：
 *   aid-weixin probe [--verbose]
 *
 * 与 MCP tool weixin_probe 调用同一 operation（业务能力只实现一次）。
 */
import { getOperationEntry } from '../../operations/registry.js'
import { runCliOperation } from '../render.js'
import type { WeixinProbeArgs } from '../../operations/probe.js'

export interface ProbeCommandOptions {
  verbose?: boolean
}

export async function probeCommand(opts: ProbeCommandOptions): Promise<number> {
  const args: WeixinProbeArgs = { verbose: opts.verbose }
  return runCliOperation(getOperationEntry('weixin_probe').operation, args)
}
