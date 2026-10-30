import { runWecomOperation } from './context.js'
import { CancelledError, CodedOperationError, type WecomOperation } from './types.js'
import { artifactDir } from '../platform/environment.js'
import { assertSendReceiptPresent, loadSendReceipt, validReceiptId, type ReceiptCrypto } from '../platform/sendReceipt.js'
import { verifySendReceipt } from '../platform/localdb/sendVerification.js'
import type { LocaldbReadDeps } from '../platform/localdb/readSession.js'
import { checkCancelled } from '../platform/localdb/identity.js'

export interface SendStatusArgs { receipt_id: string }

/** Read-only recovery: no navigation, input, OCR, retries or dispatch to the sending driver. */
export function createWecomSendStatusOperation(deps: {
  artifactDirFn?: () => string | null; localdb?: LocaldbReadDeps; receiptCrypto?: ReceiptCrypto
} = {}): WecomOperation<SendStatusArgs> {
  return { name: 'wecom_send_status', execute: (args, ctx) => runWecomOperation('readonly', ctx,
    () => !args || !validReceiptId(args.receipt_id) ? 'receipt_id 必须是 send 返回的有效凭据ID' : null,
    async opCtx => {
      let receipt
      const root = (deps.artifactDirFn ?? artifactDir)()
      try {
        if (!root) throw new Error('missing artifact root')
        receipt = await loadSendReceipt(root, args.receipt_id, deps.receiptCrypto)
      } catch {
        checkCancelled(opCtx.signal)
        throw new CodedOperationError('CONTENT_UNAVAILABLE', '发送凭据缺失、损坏或无法解密；结果仍未知，请人工核对，勿自动重发', undefined,
          { receipt_id: args.receipt_id, send_status: 'unknown' })
      }
      try {
        const check = await verifySendReceipt(receipt, opCtx, deps.localdb ?? {})
        if (check.status === 'confirmed') assertSendReceiptPresent(root!, args.receipt_id)
        return { message: check.status === 'confirmed'
          ? '已匹配原发送窗口内的唯一可信客户端记录；不代表对方已收到或已读'
          : '发送结果仍未知，请稍后只读对账或人工核对，勿自动重发',
        data: { receipt_id: args.receipt_id, send_status: check.status, reconciliation: check } }
      } catch (err) {
        if (err instanceof CancelledError) throw err
        checkCancelled(opCtx.signal)
        throw new CodedOperationError(err instanceof CodedOperationError ? err.code : 'LOCALDB_UNAVAILABLE',
          '本地库对账未完成，发送结果仍未知；勿自动重发', undefined,
          { receipt_id: args.receipt_id, send_status: 'unknown' })
      }
    }) }
}
