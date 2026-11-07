/** Shared image/file path: local DB intent, one UI dispatch, then read-only reconciliation. */
import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, statSync } from 'node:fs'
import { basename, isAbsolute, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { runWecomOperation, type OperationOutcome } from './context.js'
import { CancelledError, CodedOperationError, type OpContext, type OperationResult, type WecomOperation } from './types.js'
import { DEFAULT_DRIVER_TIMEOUT_MS, runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'
import { artifactDir } from '../platform/environment.js'
import { createTargetRef, verifyTargetRef, type CreateTargetRefFn, type VerifyTargetRefFn } from '../platform/targetRef.js'
import { resolveTargetByName, sanitizeTiming, validateTargetSelector, type ResolvedByNameTarget } from './navigate.js'
import { withSendSnapshot, verifySendReceipt, type MediaSendReceipt, type SendBaseline } from '../platform/localdb/sendVerification.js'
import { invalidateSendReceipt, saveSendReceipt, type ReceiptCrypto } from '../platform/sendReceipt.js'
import { probeCurrentAccount, checkCancelled } from '../platform/localdb/identity.js'
import { resolveFocusConversation } from '../platform/localdb/unread.js'
import { parseConversationRows } from '../platform/localdb/watch.js'
import { hash } from '../platform/localdb/access.js'
import type { LocaldbReadDeps } from '../platform/localdb/readSession.js'
import type { DirectoryArgs } from './sessionDirectory.js'
import { mapExecutorError } from './errorMapping.js'

export interface MediaArgs { target_ref?: string; target_name?: string; image_path?: string; file_path?: string }
export interface MediaDeps {
  runDriverFn?: RunPowerShellDriverFn
  verifyRefFn?: VerifyTargetRefFn
  createRefFn?: CreateTargetRefFn
  artifactDirFn?: () => string | null
  readCatalogFn?: (args: DirectoryArgs, ctx: OpContext) => Promise<OperationOutcome>
  localdb?: LocaldbReadDeps
  receiptCrypto?: ReceiptCrypto
  localdbVerifyPoll?: { attempts?: number; delayMs?: number }
}

const MAX_BYTES = { image: 20 * 1024 * 1024, file: 100 * 1024 * 1024 }
const DRIVER = {
  image: fileURLToPath(new URL('../../../drivers/ps1/send-image.ps1', import.meta.url)),
  file: fileURLToPath(new URL('../../../drivers/ps1/send-file.ps1', import.meta.url)),
}

async function delay(ms: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) throw new CancelledError()
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve() }, ms)
    const abort = () => { clearTimeout(timer); reject(new CancelledError()) }
    signal.addEventListener('abort', abort, { once: true })
  })
}

export function createMediaSendOperation(kind: 'image' | 'file', deps: MediaDeps = {}): WecomOperation<MediaArgs> {
  const label = kind === 'image' ? '图片' : '文件'
  const pathKey = kind === 'image' ? 'image_path' : 'file_path'
  const runDriver = deps.runDriverFn ?? runPowerShellDriver
  const verifyRef = deps.verifyRefFn ?? verifyTargetRef
  const createRef: CreateTargetRefFn = deps.createRefFn ??
    ((name, type, subtitle, coords, account) => createTargetRef(name, type, subtitle, { coords, account }))
  const local = deps.localdb ?? {}
  return {
    name: kind === 'image' ? 'wecom_send_image' : 'wecom_send_file',
    execute(args: MediaArgs, ctx: OpContext): Promise<OperationResult> {
      return runWecomOperation('write', ctx, () => {
        if (!args || typeof args !== 'object' || Array.isArray(args)) return '参数必须是对象'
        const selector = validateTargetSelector(args)
        if (selector) return selector
        const path = args[pathKey]
        if (typeof path !== 'string' || !path) return `${pathKey} 必填且必须是非空字符串`
        if (!isAbsolute(path)) return `${pathKey} 必须是本地绝对路径`
        if (kind === 'image' && !/\.(png|jpe?g|bmp|gif)$/i.test(path)) return '不支持的图片类型（仅支持 png/jpg/jpeg/bmp/gif）'
        return null
      }, async (opCtx, tracker) => {
        const path = args[pathKey]!
        const root = (deps.artifactDirFn ?? artifactDir)()
        if (!root) throw new CodedOperationError('INTERNAL_ERROR', '%LOCALAPPDATA% 未设置，无法定位 artifact 目录')
        let sizeBytes: number
        try {
          const st = statSync(path)
          if (!st.isFile()) throw new CodedOperationError('INVALID_ARGUMENT', `${pathKey} 不是常规文件`)
          sizeBytes = st.size
        } catch (err) {
          if (err instanceof CodedOperationError) throw err
          if ((err as NodeJS.ErrnoException).code === 'ENOENT') throw new CodedOperationError('INVALID_ARGUMENT', `${label}文件不存在`)
          throw new CodedOperationError('CONFIG_MISSING', `${label}文件无法访问`)
        }
        if (sizeBytes > MAX_BYTES[kind]) throw new CodedOperationError('INVALID_ARGUMENT', `${label}大小超过 ${kind === 'image' ? 20 : 100}MB 上限`)
        let sha256: string
        try { sha256 = createHash('sha256').update(readFileSync(path)).digest('hex') }
        catch { throw new CodedOperationError('CONFIG_MISSING', `${label}文件读取失败`) }

        // Name mode uses W25's read-only directory to obtain the canonical full name.
        let resolved: ResolvedByNameTarget | null = null
        if (args.target_name !== undefined) resolved = await resolveTargetByName(
          { runDriverFn: runDriver, createRefFn: createRef, opCtx, root, readCatalogFn: deps.readCatalogFn },
          args.target_name, '', { notSentNote: `未发送${label}` })
        const target = verifyRef(resolved?.target_ref ?? args.target_ref!)
        if (!target.account) throw new CodedOperationError('TARGET_REF_STALE', '旧目标句柄缺少账号绑定，请重新 search', 'none')
        const current = await (local.probeAccountFn ?? probeCurrentAccount)(opCtx)
        if (current.account !== target.account) throw new CodedOperationError('TARGET_REF_STALE', '目标句柄属于另一个账号，请重新 search', 'none')
        let selfName = ''
        let plan: SendBaseline | undefined
        await withSendSnapshot(current, opCtx, local, 'send-baseline', async (query, sourceId) => {
          const meta = await query(['--task', 'context', '--account', current.account])
          if (meta.self_user_id !== current.account || typeof meta.self_name !== 'string' || !meta.self_name.trim() ||
              !Number.isSafeInteger(meta.schema_version)) throw new CodedOperationError('IDENTITY_UNCONFIRMED', '本人身份映射未确认')
          selfName = meta.self_name
          // Full-name global resolution prevents a contact/group same-name default-result mismatch.
          const globalId = await resolveFocusConversation(query, target.name, current.account, undefined, target.subtitle)
          const typedId = await resolveFocusConversation(query, target.name, current.account,
            target.type === 'group' ? 'group' : target.type === 'contact' ? 'contact' : undefined, target.subtitle)
          if (globalId !== typedId) throw new CodedOperationError('TARGET_AMBIGUOUS', '本地库目标身份不唯一，未发送')
          const row = parseConversationRows((await query(['--task', 'conversations'])).conversations)
            .find(c => c.conversation_id === globalId)
          const baseline = row?.last_message_id ?? 0
          const anchor = row?.last_anchor ?? ''
          if (baseline > 0 && !/^[a-f0-9]{64}$/.test(anchor)) throw new CodedOperationError('LOCALDB_UNAVAILABLE', '发送基线无效')
          plan = { account: current.account, conversationId: globalId, baseline, anchor, sourceId, schema: meta.schema_version as number }
        })
        const receipt: MediaSendReceipt = { ...plan!, version: 1, kind,
          ...(kind === 'file' ? { fileName: basename(path) } : {}),
          since: Math.floor((local.now ?? Date.now)() / 1000) - 2,
          until: Math.ceil(((local.now ?? Date.now)() + DEFAULT_DRIVER_TIMEOUT_MS) / 1000) + 2 }
        const dir = join(root, `send-${new Date().toISOString().replace(/[:.]/g, '-')}`)
        mkdirSync(dir, { recursive: true })
        let receiptId: string
        try { receiptId = await saveSendReceipt(dir, receipt, deps.receiptCrypto) }
        catch (err) {
          if (err instanceof CodedOperationError && err.effectOverride === 'unknown') throw err
          throw new CodedOperationError('INTERNAL_ERROR', '发送前加密凭据未落盘，未调用发送驱动', 'none')
        }
        let driverStarted = false
        let data: Record<string, unknown>
        try {
          opCtx.progress({ stage: 'checkpoint', message: `发送恢复凭据：${receiptId}（结果未知时仅用 send-status 对账，勿重发）` })
          checkCancelled(opCtx.signal)
          const live = await (local.probeAccountFn ?? probeCurrentAccount)(opCtx)
          if (live.account !== target.account) throw new CodedOperationError('TARGET_REF_STALE', '发送前账号已切换，未发送', 'none')
          driverStarted = true
          data = await runDriver({ script: DRIVER[kind], args: [
            '-TargetName', target.name, '-Subtitle', target.subtitle, '-Section', target.type,
            kind === 'image' ? '-ImagePath' : '-FilePath', path,
            '-SelfName', selfName, '-ArtifactDir', dir,
          ], signal: opCtx.signal })
          if (data.sent !== true) throw new CodedOperationError('EXECUTION_UNKNOWN', '驱动未确认 Enter 投递，结果未知', 'unknown')
        } catch (err) {
          const knownNone = !driverStarted || (err instanceof CodedOperationError &&
            (err.effectOverride === 'none' || !['RESULT_TIMEOUT', 'EXECUTION_UNKNOWN', 'CANCELLED', 'INTERNAL_ERROR'].includes(err.code)))
          if (knownNone) {
            try { invalidateSendReceipt(dir) }
            catch { throw new CodedOperationError('INTERNAL_ERROR', '已知无发送动作但凭据无法失效，勿自动确认或重发', 'unknown',
              { receipt_id: receiptId, receipt_invalidation_failed: true }) }
          }
          const mapped = mapExecutorError(err)
          throw new CodedOperationError(mapped.code === 'RESULT_TIMEOUT' ? 'EXECUTION_UNKNOWN' : mapped.code,
            mapped.code === 'RESULT_TIMEOUT' ? '发送驱动超时，结果未知；仅用 send-status 对账，勿重发' : mapped.message,
            knownNone ? 'none' : 'unknown', knownNone ? {} : { receipt_id: receiptId, send_status: 'unknown' })
        }
        tracker.completed = 1
        let check
        let failure = ''
        for (let attempt = 0; attempt < Math.max(1, deps.localdbVerifyPoll?.attempts ?? 3); attempt++) {
          try {
            if (attempt) await delay(deps.localdbVerifyPoll?.delayMs ?? 1000, opCtx.signal)
            check = await verifySendReceipt(receipt, opCtx, local)
            if (check.status === 'confirmed') break
          } catch (err) { failure = mapExecutorError(err).code; break }
        }
        const baseData = {
          receipt_id: receiptId, target: target.name, title: data.title, navigated: true,
          ...(resolved ? { resolved_target: { name: resolved.name, subtitle: resolved.subtitle, section: resolved.section } } : {}),
          [kind]: { name: basename(path), size_bytes: sizeBytes, sha256 },
          input_point: data.input_point, timing_ms: sanitizeTiming(data.timing_ms),
          screenshot_paths: Array.isArray(data.screenshot_paths) ? data.screenshot_paths : [],
          localdb_verification: check ?? null,
        }
        if (check?.status === 'confirmed') return {
          message: kind === 'image'
            ? `客户端已记录发往「${target.name}」的唯一图片消息；不证明与原图片字节一致，也不代表对方已收到`
            : `客户端已记录发往「${target.name}」的唯一同名文件消息；不代表对方已收到`,
          data: { ...baseData, verification: 'local-record', verification_detail: {
            method: 'local-record', message_id: check.message_id, source_generation: hash([receipt.sourceId, receipt.schema]) } },
        }
        throw new CodedOperationError('EXECUTION_UNKNOWN', failure
          ? `本地库发送核验失败（${failure}），结果未知；请用 send-status 只读对账，勿重发`
          : '本地库未找到唯一匹配的出站记录，结果未知；请用 send-status 只读对账，勿重发',
        'unknown', { ...baseData, send_status: 'unknown' })
      })
    },
  }
}
