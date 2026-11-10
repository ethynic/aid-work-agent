/** Open a uniquely resolved conversation by name and confirm its copied title. */
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { runWecomOperation } from './context.js'
import { CancelledError, CodedOperationError, type OpContext, type WecomOperation } from './types.js'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'
import { artifactDir } from '../platform/environment.js'
import { verifyTargetRef, type VerifyTargetRefFn } from '../platform/targetRef.js'
import { readSessionDirectory, type DirectoryArgs } from './sessionDirectory.js'
import type { OperationOutcome } from './context.js'

const DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/chat-select.ps1', import.meta.url))
export interface WecomChatSelectArgs { target_ref: string }

export function selectedTitleMatches(title: unknown, name: string, type: string): boolean {
  if (typeof title !== 'string') return false
  if (title === name) return true
  return type === 'contact' && !name.endsWith('@微信') &&
    (title === `${name}@微信` || title === `${name} @微信`)
}

export function createWecomChatSelectOperation(deps: {
  runDriverFn?: RunPowerShellDriverFn
  verifyRefFn?: VerifyTargetRefFn
  artifactDirFn?: () => string | null
  readCatalogFn?: (args: DirectoryArgs, ctx: OpContext) => Promise<OperationOutcome>
} = {}): WecomOperation<WecomChatSelectArgs> {
  const runDriver = deps.runDriverFn ?? runPowerShellDriver
  const verifyRef = deps.verifyRefFn ?? verifyTargetRef
  const readCatalog = deps.readCatalogFn ?? readSessionDirectory
  return {
    name: 'wecom_chat_select',
    execute: (args, ctx) => runWecomOperation('readonly', ctx,
      () => !args || typeof args.target_ref !== 'string' || !args.target_ref
        ? 'target_ref 必填且必须来自 wecom_chat_search 的唯一匹配结果' : null,
      async opCtx => {
        const target = verifyRef(args.target_ref)
        if (!target.account) throw new CodedOperationError('TARGET_REF_STALE', '旧目标句柄缺少账号绑定，请重新搜索')
        if (!['contact', 'group'].includes(target.type))
          throw new CodedOperationError('INVALID_ARGUMENT', '目标类型必须是联系人或群聊')
        const catalog = await readCatalog({ mode: 'search', query: target.name,
          type: 'any', limit: 20 }, opCtx)
        const result = catalog.data ?? {}
        const candidates = Array.isArray(result.candidates) ? result.candidates as { name: string; section: string }[] : []
        if (result.self_user_id !== target.account)
          throw new CodedOperationError('TARGET_REF_STALE', '目标句柄属于另一个账号，请重新搜索')
        if (typeof result.match_count === 'number' && result.match_count > 1)
          throw new CodedOperationError('TARGET_AMBIGUOUS', result.same_name === true
            ? '目标名称跨联系人与群聊同名；请先备注或重命名'
            : '目标名称跨联系人与群聊有多个匹配名称；请用户指定完整名称',
            'none', { candidates: result.candidates, match_count: result.match_count })
        if (result.match_count !== 1 || candidates[0]?.name !== target.name ||
            candidates[0]?.section !== (target.type === 'group' ? '群聊' : '联系人'))
          throw new CodedOperationError('TARGET_REF_STALE', '目标名称已变化，请重新搜索确认')
        if (typeof result.self_name !== 'string' || !result.self_name)
          throw new CodedOperationError('IDENTITY_UNCONFIRMED', '本人名称不可用，无法按需标定标题坐标')
        const root = (deps.artifactDirFn ?? artifactDir)()
        if (!root) throw new CodedOperationError('INTERNAL_ERROR', '无法定位 artifact 目录')
        const dir = join(root, `select-${new Date().toISOString().replace(/[:.]/g, '-')}`)
        mkdirSync(dir, { recursive: true })
        opCtx.progress({ stage: 'execute', message: `进入唯一匹配会话「${target.name}」` })
        let data: Record<string, unknown>
        try {
          data = await runDriver({ script: DRIVER_PATH,
            args: ['-TargetName', target.name, '-Section', target.type,
              '-SelfName', result.self_name, '-ArtifactDir', dir],
            timeoutMs: 120_000, signal: opCtx.signal })
        } catch (error) {
          if (error instanceof CodedOperationError && error.code === 'RESULT_TIMEOUT')
            throw new CodedOperationError('EXECUTION_UNKNOWN', '进入会话超时，可能已切换；请核对当前会话')
          if (error instanceof CancelledError)
            throw new CodedOperationError('CANCELLED', '进入会话被取消，可能已切换；请核对当前会话')
          throw error
        }
        if (!selectedTitleMatches(data.title, target.name, target.type))
          throw new CodedOperationError('UI_CHANGED', '复制的会话标题与目标不一致')
        return { message: `已进入会话「${target.name}」（未读角标可能被清除）`,
          data: { target: { name: target.name, section: target.type === 'group' ? '群聊' : '联系人' },
            title: data.title, timing_ms: data.timing_ms ?? {}, screenshot_paths: data.screenshot_paths ?? [] } }
      }),
  }
}
