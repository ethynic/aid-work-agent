/**
 * CLI 子命令 add-customer：按手机号检索并发送添加客户邀请（写动作）。
 *
 * 用法：
 *   aid-wecom add-customer --phone <11位手机号> --yes [--json]
 *
 * --yes 映射 operation 的 confirm: true（对外发邀请的写动作必须显式确认）；
 * 缺 --yes 时 operation 参数校验即拒绝（INVALID_ARGUMENT，退出码 2），不会触达企微。
 * 写动作执行前打印 ⚠️ 提示（registry cli.write=true 元数据）。
 * 与 MCP tool wecom_add_customer 调用同一 operation（业务能力只实现一次）。
 */
import { getOperationEntry } from '../../operations/registry.js'
import { runCliOperation } from '../render.js'
import type { WecomAddCustomerArgs } from '../../operations/addCustomer.js'

export interface AddCustomerCommandOptions {
  phone?: string
  /** --yes：显式确认写动作 */
  yes?: boolean
  /** true 时 stdout 最后一行输出 OperationResult JSON */
  json?: boolean
}

export async function addCustomerCommand(opts: AddCustomerCommandOptions): Promise<number> {
  const entry = getOperationEntry('wecom_add_customer')
  if (entry.cli.write) {
    console.error('⚠️ 写操作：将按手机号检索并向对方发出添加客户邀请；失败不会自动重试（effect=unknown 时请人工核对）')
  }
  const args: WecomAddCustomerArgs = {
    phone: opts.phone as string,
    confirm: opts.yes === true,
  }
  return runCliOperation(entry.operation, args, { exclusive: true, json: opts.json })
}
