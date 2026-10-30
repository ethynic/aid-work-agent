/** Send-only projection and bounded read-only reconciliation; never invokes a UI driver. */
import { CodedOperationError, type OpContext } from '../../operations/types.js'
import { admitLocaldbVersion, prepareDecryptedDbs, unavailable } from './access.js'
import { readLocalState, writeLocalState } from './matrix.js'
import { runLocaldbPy } from './driver.js'
import { logLocaldbEvent } from './log.js'
import { checkCancelled, probeCurrentAccount, type CurrentAccount } from './identity.js'
import type { LocaldbReadDeps } from './readSession.js'

export interface SendBaseline {
  account: string
  conversationId: string
  baseline: number
  anchor: string
  sourceId: string
  schema: number
}

export interface SendReceipt extends SendBaseline {
  version: 1
  text: string
  since: number
  until: number
}

type Query = (params: string[]) => Promise<Record<string, unknown>>

export async function withSendSnapshot<T>(
  current: CurrentAccount, ctx: OpContext, local: LocaldbReadDeps,
  workPrefix: 'send-baseline' | 'send-verify', fn: (query: Query, sourceId: string) => Promise<T>,
): Promise<T> {
  checkCancelled(ctx.signal)
  const getState = local.readStateFn ?? (() => readLocalState(local.stateDir))
  const putState = local.writeStateFn ?? (state => writeLocalState(state, local.stateDir))
  const state = getState()
  const admission = await admitLocaldbVersion(ctx, current, state, {}, local)
  const paths = current.dataDir.db_paths!
  const prepared = await prepareDecryptedDbs(
    { message: paths.message, user: paths.user, session: paths.session },
    { ctx, current, version: admission.version, expected: admission.expected, algorithm: admission.algorithm,
      state, getState, putState, workPrefix, tolerateSessionFailure: true, deps: local },
  )
  try {
    const query: Query = async params => {
      checkCancelled(ctx.signal)
      const result = await (local.runPyFn ?? runLocaldbPy)(['query', '--workdir', prepared.work.dir, ...params], { signal: ctx.signal })
      checkCancelled(ctx.signal)
      if (!result.ok || !result.data) throw unavailable('本地库子进程未完成')
      const value = result.data
      if (value.ok === true) return value
      if (value.error === 'IDENTITY_UNCONFIRMED') throw new CodedOperationError('IDENTITY_UNCONFIRMED', '本人身份映射未确认')
      if (value.error === 'SCHEMA_UNSUPPORTED') throw new CodedOperationError('VERSION_UNSUPPORTED', '本地库 schema 不支持当前读取')
      // Do not expose Python payloads: they can contain message text and account identifiers.
      if (value.error === 'CURSOR_STALE') throw unavailable('发送基线失效，不能确认原发送结果')
      throw unavailable('本地库查询未完成')
    }
    return await fn(query, prepared.sourceId)
  } finally {
    if (!prepared.work.cleanup()) (local.logFn ?? logLocaldbEvent)({ type: 'db_fallback', stage: 'send-verify', reason: 'remove_failed' })
  }
}

export interface SendCheck {
  status: 'confirmed' | 'unknown'
  reason: 'matched' | 'not_observed' | 'ambiguous' | 'scan_limit'
  pages: number
  rows: number
  untrusted: number
  message_id?: number
}

/** Bound work per snapshot; a cap is unknown, never evidence that nothing was sent. */
export const SEND_VERIFY_PAGE_SIZE = 50
export const SEND_VERIFY_MAX_PAGES = 20

export async function verifySendReceipt(receipt: SendReceipt, ctx: OpContext, local: LocaldbReadDeps): Promise<SendCheck> {
  const current = await (local.probeAccountFn ?? probeCurrentAccount)(ctx)
  checkCancelled(ctx.signal)
  if (current.account !== receipt.account) throw new CodedOperationError('IDENTITY_UNCONFIRMED', '发送凭据与当前账号不匹配')
  return withSendSnapshot(current, ctx, local, 'send-verify', async (query, sourceId) => {
    const meta = await query(['--task', 'context', '--account', current.account])
    if (meta.self_user_id !== receipt.account) throw new CodedOperationError('IDENTITY_UNCONFIRMED', '本人身份映射未确认')
    if (sourceId !== receipt.sourceId || meta.schema_version !== receipt.schema) throw unavailable('源库代际已变化，不能确认原发送结果')
    let after = receipt.baseline
    let anchor = receipt.anchor
    const matches: number[] = []
    const diagnostic = { pages: 0, rows: 0, untrusted: 0 }
    for (let page = 0; page < SEND_VERIFY_MAX_PAGES; page++) {
      const res = await query(['--task', 'send-messages', '--conv', receipt.conversationId,
        '--after-id', String(after), ...(after > 0 ? ['--anchor', anchor] : []),
        '--since', String(receipt.since), '--until', String(receipt.until), '--limit', String(SEND_VERIFY_PAGE_SIZE)])
      if (res.conversation_id !== receipt.conversationId || !Array.isArray(res.messages) || res.messages.length > SEND_VERIFY_PAGE_SIZE) throw unavailable('发送核验结果结构无效')
      diagnostic.pages++
      for (const raw of res.messages) {
        if (!raw || typeof raw !== 'object') throw unavailable('发送核验消息结构无效')
        const row = raw as Record<string, unknown>
        if (!Number.isSafeInteger(row.message_id) || (row.message_id as number) <= after || row.conversation_id !== receipt.conversationId) throw unavailable('发送核验水位无效')
        after = row.message_id as number
        diagnostic.rows++
        if (row.trusted !== true) diagnostic.untrusted++
        if (row.trusted === true && row.sender_id === receipt.account && row.text === receipt.text &&
          typeof row.send_time === 'number' && row.send_time >= receipt.since && row.send_time <= receipt.until) matches.push(after)
      }
      if (res.messages.length < SEND_VERIFY_PAGE_SIZE) {
        return matches.length === 1
          ? { status: 'confirmed', reason: 'matched', ...diagnostic, message_id: matches[0]! }
          : { status: 'unknown', reason: matches.length > 1 ? 'ambiguous' : 'not_observed', ...diagnostic }
      }
      if (res.next_cursor_id !== after || typeof res.next_anchor !== 'string' || !/^[a-f0-9]{64}$/.test(res.next_anchor)) throw unavailable('发送核验分页锚点无效')
      anchor = res.next_anchor
    }
    return { status: 'unknown', reason: 'scan_limit', ...diagnostic }
  })
}
