import { getOperationEntry } from '../../operations/registry.js'
import { runCliOperation } from '../render.js'

export async function sendStatusCommand(opts: { receiptId?: string; json?: boolean }): Promise<number> {
  return runCliOperation(getOperationEntry('wecom_send_status').operation,
    { receipt_id: opts.receiptId }, { exclusive: true, json: opts.json })
}
