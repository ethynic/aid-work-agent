/**
 * wecom_message_send operation（M6：智能分发）：向 target_ref 目标发送 1 条文本消息。
 *
 * 编排（TS 层）：verifyTargetRef 解出目标（过期 TARGET_REF_STALE / 篡改
 * INVALID_ARGUMENT；坐标可有无皆可，本命令不要求）→ spawn message-send.ps1（发送阶段
 * 驱动：OCR 两带 + Jev 三问判定当前会话/输入点/草稿）→ 驱动返回 navigate_required=true
 * （当前会话不是目标/无法判定）→ 内部编排：直接调用 chatSearch operation 工厂（注入
 * 同一 runDriverFn，Jev 选 best）→ 取 best（须与目标 name+section+subtitle 消歧键一致，
 * 否则按 items 里 name/subtitle/section 规则取唯一匹配项）→ 调用 chatSelect operation（点击进会话并校验标题）→ 再次 spawn
 * message-send.ps1 完成发送。两轮都不在目标会话 → TARGET_NOT_FOUND；编排失败透传
 * 对应错误码（此时未发送任何消息），不重试。
 *
 * 发送阶段驱动契约：当前会话对 → 点输入框（Jev 选 token / 降级比例坐标）→ 输入
 * （单行 Send-WeComText 逐字；多行剪贴板粘贴通道 + OCR 回读校验，副作用：覆盖用户
 * 剪贴板且不恢复）→ 发送前规则复核标题（不一致 → 清空自己刚输入的草稿 + UI_CHANGED；
 * 用户草稿一律 UI_CHANGED 中止不清除）→ Enter → Jev 终态判定（降级 M2 三选二）。
 *
 * 写语义：发送后校验失败 → EXECUTION_UNKNOWN，绝不自动重试；驱动超时（写可能已落地）
 * 同样归并 EXECUTION_UNKNOWN，而不是 RESULT_TIMEOUT；驱动执行中被取消 →
 * CANCELLED/effect=unknown。定位阶段（search/select）失败或取消时尚未输入任何文字，
 * effect=none 且 message 注明未发送消息。
 */
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { runWecomOperation } from './context.js'
import {
  CancelledError,
  CodedOperationError,
  type ErrorCode,
  type OperationResult,
  type OpContext,
  type WecomOperation,
} from './types.js'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'
import { artifactDir } from '../platform/environment.js'
import {
  createTargetRef,
  verifyTargetRef,
  type CreateTargetRefFn,
  type TargetRefIdentity,
  type VerifyTargetRefFn,
} from '../platform/targetRef.js'
import { createWecomChatSearchOperation } from './chatSearch.js'
import { createWecomChatSelectOperation } from './chatSelect.js'

/** dist/src/operations → 包根 drivers/ps1 */
const DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/message-send.ps1', import.meta.url))

export interface WecomMessageSendArgs {
  target_ref: string
  text: string
}

const MAX_TEXT_LENGTH = 2000

/** 定位阶段选中条目（来自内部 chatSearch 结果的 best 或规则匹配唯一项） */
interface LocatedTarget {
  name: string
  target_ref: string
}

/** 轻量名称归一化：去空白 + 剥 @微信 后缀 + 小写（TS 侧比对；驱动侧仍按全量归一化严格校验标题） */
function normNameForMatch(s: string): string {
  return s.replace(/\s+/g, '').replace(/@微信$/, '').toLowerCase()
}

/** 目标分区 → 搜索结果分区（contact=联系人 / group=群聊 / other 等不限定） */
function sectionMatchesTarget(section: string, type: string): boolean {
  if (type === 'contact') return section === '联系人'
  if (type === 'group') return section === '群聊'
  return true
}

function identityMatches(item: { name: string; section: string }, target: TargetRefIdentity): boolean {
  return normNameForMatch(item.name) === normNameForMatch(target.name) && sectionMatchesTarget(item.section, target.type)
}

/** chatSearch 返回的候选（TS 防御性解析形态） */
interface SearchCandidate {
  name: string
  subtitle: string
  section: string
  target_ref: string
}

function parseCandidates(raw: unknown): SearchCandidate[] {
  const arr = Array.isArray(raw) ? raw : raw != null ? [raw] : []
  const out: SearchCandidate[] = []
  for (const r of arr) {
    const it = (r ?? {}) as Record<string, unknown>
    if (typeof it.target_ref !== 'string' || it.target_ref.length === 0) continue
    out.push({
      name: String(it.name ?? ''),
      subtitle: String(it.subtitle ?? ''),
      section: String(it.section ?? ''),
      target_ref: it.target_ref,
    })
  }
  return out
}

/**
 * 从 chatSearch 结果中选定位目标：先按 name+section（subtitle 优先收紧，落空放宽）算
 * 候选集；best（Jev/驱动规则选出）仅在身份相符**且落在候选集内**（即 subtitle 消歧键
 * 也一致）时才可信——Jev 看不到 target_ref 的 subtitle，同名同分区多条时它的 best
 * 无从消歧，直接信任会把消息发给错误的同名联系人（M2 的 subtitle 精确匹配语义）。
 * 无匹配 → null（TARGET_NOT_FOUND），多项 → 'ambiguous'（TARGET_AMBIGUOUS）。
 */
function pickLocatedTarget(data: Record<string, unknown>, target: TargetRefIdentity): LocatedTarget | null | 'ambiguous' {
  const items = parseCandidates(data.items)
  const byNameSection = items.filter((it) => identityMatches(it, target))
  const withSubtitle =
    target.subtitle.length > 0 ? byNameSection.filter((it) => it.subtitle === target.subtitle) : byNameSection
  const candidates = withSubtitle.length > 0 ? withSubtitle : byNameSection
  const b = (data.best ?? null) as Record<string, unknown> | null
  if (
    b !== null &&
    typeof b === 'object' &&
    typeof b.target_ref === 'string' &&
    b.target_ref.length > 0 &&
    identityMatches({ name: String(b.name ?? ''), section: String(b.section ?? '') }, target) &&
    candidates.some((it) => it.target_ref === b.target_ref)
  ) {
    return { name: String(b.name ?? target.name), target_ref: b.target_ref }
  }
  if (candidates.length === 0) return null
  if (candidates.length > 1) return 'ambiguous'
  return { name: candidates[0]!.name, target_ref: candidates[0]!.target_ref }
}

function sanitizeTiming(raw: unknown): Record<string, number> {
  const out: Record<string, number> = {}
  if (raw === null || typeof raw !== 'object') return out
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof v === 'number' && Number.isFinite(v)) out[k] = Math.round(v)
  }
  return out
}

export function createWecomMessageSendOperation(
  deps: {
    runDriverFn?: RunPowerShellDriverFn
    verifyRefFn?: VerifyTargetRefFn
    /** 内部 chatSearch 签发 target_ref 用（与 verifyRefFn 配套注入，测试可替换） */
    createRefFn?: CreateTargetRefFn
    artifactDirFn?: () => string | null
  } = {},
): WecomOperation<WecomMessageSendArgs> {
  const runDriverFn = deps.runDriverFn ?? runPowerShellDriver
  const verifyRefFn = deps.verifyRefFn ?? verifyTargetRef
  // 与 chatSearch 的默认签发适配保持一致（坐标随条目写入 payload，select 消费）
  const createRefFn: CreateTargetRefFn =
    deps.createRefFn ?? ((name, type, subtitle, coords) => createTargetRef(name, type, subtitle, { coords }))
  const artifactDirFn = deps.artifactDirFn ?? (() => artifactDir())
  return {
    name: 'wecom_message_send',
    execute(args: WecomMessageSendArgs, ctx: OpContext): Promise<OperationResult> {
      return runWecomOperation(
        'write',
        ctx,
        () => {
          if (args === null || typeof args !== 'object' || Array.isArray(args)) {
            return '参数必须是对象（target_ref/text 必填）'
          }
          if (typeof args.target_ref !== 'string' || args.target_ref.length === 0) {
            return 'target_ref 必填且必须是非空字符串（先经 wecom_chat_search 获取）'
          }
          if (typeof args.text !== 'string' || args.text.length === 0) {
            return 'text 必填且必须是非空字符串'
          }
          // 纯空白拒绝：驱动侧归一化会把全部空白剥掉，前缀变 '' 使终态校验的
          // Contains('') 恒真（三选二失真，可能假报成功）。\u0085 补齐 .NET IsWhiteSpace
          // 与 JS \s 的差集（驱动侧会剥它，TS 侧必须同步拒绝）
          if (/^[\s\u0085]*$/.test(args.text)) {
            return 'text 去除空白后不能为空（纯空白消息无法通过发送后校验）'
          }
          if (args.text.length > MAX_TEXT_LENGTH) {
            return `text 长度不能超过 ${MAX_TEXT_LENGTH} 字（请调用方分段发送）`
          }
          // 多行文本（含 \r\n）放行：驱动侧经剪贴板粘贴通道输入（attachstate Ctrl+V，
          // 2026-09-28 真机验证），副作用是覆盖用户剪贴板且不恢复
          return null
        },
        async (opCtx, tracker) => {
          const root = artifactDirFn()
          if (!root) {
            throw new CodedOperationError('INTERNAL_ERROR', '%LOCALAPPDATA% 未设置，无法定位 artifact 目录')
          }
          // 各阶段 artifact 目录独立留痕：send-<ts>（可两次，时间戳冲突加序号后缀）、
          // search-<ts> / select-<ts>（由内部 operation 自建，同一 artifact 根）
          const usedDirs = new Set<string>()
          const makeArtifactDir = (prefix: string): string => {
            const ts = new Date().toISOString().replace(/[:.]/g, '-')
            let dir = join(root, `${prefix}-${ts}`)
            for (let i = 2; usedDirs.has(dir); i++) dir = join(root, `${prefix}-${ts}-${i}`)
            usedDirs.add(dir)
            mkdirSync(dir, { recursive: true })
            return dir
          }

          const target = verifyRefFn(args.target_ref)
          opCtx.progress({ stage: 'execute', message: `解析目标「${target.name}」，发送前检查当前会话` })

          const spawnSendDriver = async (dir: string): Promise<Record<string, unknown>> => {
            try {
              return await runDriverFn({
                script: DRIVER_PATH,
                args: [
                  '-TargetName', target.name,
                  '-Subtitle', target.subtitle,
                  '-Section', target.type,
                  '-Text', args.text,
                  '-ArtifactDir', dir,
                ],
                signal: opCtx.signal,
              })
            } catch (err) {
              // 驱动超时/被取消都无法确认写是否落地：按写动作 unknown 语义上报，绝不自动重试
              if (err instanceof CodedOperationError && err.code === 'RESULT_TIMEOUT') {
                throw new CodedOperationError('EXECUTION_UNKNOWN', '发送链路超时：消息可能已发出但无法确认（不会自动重试，请人工核对）')
              }
              if (err instanceof CancelledError) {
                throw new CodedOperationError(
                  'CANCELLED',
                  '发送链路被取消：消息可能已发出但无法确认（不会自动重试，请人工核对）',
                  'unknown',
                )
              }
              throw err
            }
          }

          // 第一次调用发送阶段驱动：当前会话对则直接完成发送；不对则返回 navigate_required
          let data = await spawnSendDriver(makeArtifactDir('send'))
          let navigated = false
          if (data.navigate_required === true) {
            navigated = true
            const firstReason = typeof data.reason === 'string' ? data.reason : '当前会话不是目标'
            opCtx.progress({ stage: 'execute', message: `当前会话非目标（${firstReason}），自动 search + select 切换会话` })
            // 定位阶段：直接调用 operation 工厂（不经 CLI），注入同一 runDriverFn 与签发/验证依赖
            const query = target.name.replace(/@微信$/, '').trim()
            const searchType = target.type === 'contact' || target.type === 'group' ? target.type : 'any'
            const searchOp = createWecomChatSearchOperation({
              runDriverFn,
              createRefFn,
              artifactDirFn: () => root,
            })
            const searchRes = await searchOp.execute({ query, type: searchType }, opCtx)
            if (!searchRes.success) {
              // 编排失败透传错误码；此时未输入任何文字，effect=none
              throw new CodedOperationError(
                searchRes.code as ErrorCode,
                `定位「${target.name}」失败（搜索阶段，未发送消息）：${searchRes.message}`,
                'none',
                searchRes.data,
              )
            }
            const picked = pickLocatedTarget(searchRes.data, target)
            if (picked === null) {
              throw new CodedOperationError(
                'TARGET_NOT_FOUND',
                `搜索「${query}」结果中没有与「${target.name}」匹配的候选（未发送消息）`,
                'none',
                searchRes.data,
              )
            }
            if (picked === 'ambiguous') {
              throw new CodedOperationError(
                'TARGET_AMBIGUOUS',
                `搜索「${query}」有多个与「${target.name}」匹配的候选且无法消歧，已拒绝发送（未发送消息）`,
                'none',
                searchRes.data,
              )
            }
            const selectOp = createWecomChatSelectOperation({
              runDriverFn,
              verifyRefFn,
              artifactDirFn: () => root,
            })
            const selectRes = await selectOp.execute({ target_ref: picked.target_ref }, opCtx)
            if (!selectRes.success) {
              throw new CodedOperationError(
                selectRes.code as ErrorCode,
                `定位「${target.name}」失败（进入会话阶段，未发送消息）：${selectRes.message}`,
                'none',
                selectRes.data,
              )
            }
            // 此时已在目标会话：第二次调用发送阶段驱动
            opCtx.progress({ stage: 'execute', message: `已进入「${target.name}」会话，执行发送` })
            data = await spawnSendDriver(makeArtifactDir('send'))
            if (data.navigate_required === true) {
              const secondReason = typeof data.reason === 'string' ? data.reason : ''
              throw new CodedOperationError(
                'TARGET_NOT_FOUND',
                `两轮发送前检查均判定当前会话不是目标「${target.name}」（第一轮：${firstReason}；search+select 进入后第二轮仍不符${secondReason ? `：${secondReason}` : ''}），已中止且未发送消息`,
                'none',
              )
            }
          }
          tracker.completed = 1

          // 驱动字段防御性归一（sent_verification / input_point / timing / screenshots）
          const svRaw = (data.sent_verification ?? null) as Record<string, unknown> | null
          const sentVerification =
            svRaw !== null && typeof svRaw === 'object'
              ? {
                  method: svRaw.method === 'rule_2of3' ? 'rule_2of3' : 'jev',
                  result: typeof svRaw.result === 'string' ? svRaw.result : 'unknown',
                  ...(typeof svRaw.failure_mode === 'string' ? { failure_mode: svRaw.failure_mode } : {}),
                }
              : null
          const ipRaw = (data.input_point ?? null) as Record<string, unknown> | null
          const inputPoint =
            ipRaw !== null && typeof ipRaw === 'object'
              ? {
                  x: typeof ipRaw.x === 'number' && Number.isFinite(ipRaw.x) ? Math.round(ipRaw.x) : null,
                  y: typeof ipRaw.y === 'number' && Number.isFinite(ipRaw.y) ? Math.round(ipRaw.y) : null,
                }
              : null
          const methodDesc = sentVerification?.method === 'rule_2of3' ? '规则三选二' : 'Jev'
          return {
            message: `已向「${target.name}」发送消息，发送后校验通过（${methodDesc}校验${navigated ? '，已自动搜索并切换会话' : '，当前会话直发'}）`,
            data: {
              target: target.name,
              title: typeof data.title === 'string' && data.title.length > 0 ? data.title : target.name,
              navigated,
              sent_verification: sentVerification,
              input_point: inputPoint,
              timing_ms: sanitizeTiming(data.timing_ms),
              screenshot_paths: Array.isArray(data.screenshot_paths)
                ? data.screenshot_paths.filter((p): p is string => typeof p === 'string')
                : [],
            },
          }
        },
      )
    },
  }
}
