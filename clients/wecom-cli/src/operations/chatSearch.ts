/**
 * wecom_chat_search operation（M4）：企业微信搜索联系人/群聊，Jev 决策最优候选。
 *
 * 流程：TS 校验参数 → spawn drivers/ps1/chat-search.ps1（解析主窗口 → attachstate
 * Ctrl+F 聚焦搜索框（降级 OCR+Jev/规则点击）→ 动态裁切 OCR 判态 + Ctrl+A+Delete 清残留
 * → 输入 query + OCR 回读验证 → 等 SearchResultWindow2 高度稳定 → 稳定帧 OCR 条目 →
 * Jev 单次合并调用（best_result + is_ambiguous）→ DRIVER_JSON 输出）→ TS 按 type/limit
 * 过滤、重选 best、为每个 item 签发带 overlay 相对坐标的 target_ref。
 *
 * 语义：只读，不打开会话、不读取历史消息（effect 恒为 none）；
 * 无结果（面板空或过滤后空）→ TARGET_NOT_FOUND，data 附 query/jev/timing_ms 透传；
 * overlay 未出现 / 页面结构变化 → UI_CHANGED。
 *
 * **副作用（M4 起）**：搜索 overlay 保持打开不清理——条目坐标与 overlay rect 是后续
 * select 命令的消费句柄；下一次 search 开头的残留清空会自动关掉旧 overlay。
 */
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { runWecomOperation } from './context.js'
import { CodedOperationError, type OperationResult, type OpContext, type WecomOperation } from './types.js'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'
import { createTargetRef, type CreateTargetRefFn, type TargetRefCoords } from '../platform/targetRef.js'
import { artifactDir } from '../platform/environment.js'

/** dist/src/operations → 包根 drivers/ps1 */
const DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/chat-search.ps1', import.meta.url))

export interface WecomChatSearchArgs {
  query: string
  /** contact=联系人 / group=群聊 / any=全部（含聊天记录等分区），默认 any */
  type?: 'contact' | 'group' | 'any'
  limit?: number
}

/** 搜索结果条目（x/y = overlay 图像内行中心坐标，select 点击换算用） */
export interface ChatSearchItem {
  name: string
  subtitle: string
  /** 搜索结果分区原名：联系人 / 群聊 / 聊天记录 等 */
  section: string
  x: number
  y: number
  /** Jev best_result 概率分布中该项的置信度（0..1）；降级/未知为 null */
  probability: number | null
  target_ref: string
}

/** Jev 决策信息透传（used=false 时带 reason，概率全 null 走规则 best） */
export interface ChatSearchJevInfo {
  used: boolean
  latency_ms: number
  reason?: string
  is_ambiguous?: boolean
  ambiguous_confidence?: number
  best_confidence?: number
}

/** 最优候选：在 ChatSearchItem 基础上附屏幕参考坐标与 Jev 概率分布 */
export interface ChatSearchBest extends ChatSearchItem {
  /** overlay 左上角 + 条目相对坐标（参考值；select 实际按 overlay 实时 rect + 相对坐标换算） */
  screen_x: number | null
  screen_y: number | null
  /** Jev 对所选条目的置信度（choice confidence；降级时取条目概率或 null） */
  confidence: number | null
  /** 概率分布：R<驱动条目序号> → probability（含被 type 过滤掉的条目；降级全 null） */
  probabilities: Record<string, number | null>
}

const SEARCH_TYPES = ['contact', 'group', 'any'] as const
const DEFAULT_LIMIT = 10
const MAX_LIMIT = 20
const MAX_QUERY_LENGTH = 100

/** 搜索结果分区 → target_ref 目标分类（ref 的 type 仅作提示，发送时仍由驱动 OCR 校验标题） */
function sectionToTargetType(section: string): string {
  if (section === '联系人') return 'contact'
  if (section === '群聊') return 'group'
  return 'other'
}

/** type 过滤：contact 只留联系人分区，group 只留群聊分区，any 全留 */
function matchType(section: string, type: 'contact' | 'group' | 'any'): boolean {
  if (type === 'any') return true
  return sectionToTargetType(section) === type
}

/** 驱动字段防御性解析（PS 5.1 ConvertTo-Json 单元素数组解包等怪癖由调用方归一） */
function toNum(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function toIntOrNull(v: unknown): number | null {
  const n = toNum(v)
  return n !== null ? Math.round(n) : null
}

function sanitizeJev(raw: unknown): ChatSearchJevInfo {
  const out: ChatSearchJevInfo = { used: false, latency_ms: 0 }
  if (raw === null || typeof raw !== 'object') return out
  const r = raw as Record<string, unknown>
  out.used = r.used === true
  const latency = toIntOrNull(r.latency_ms)
  if (latency !== null && latency >= 0) out.latency_ms = latency
  if (typeof r.reason === 'string' && r.reason.length > 0) out.reason = r.reason
  if (typeof r.is_ambiguous === 'boolean') out.is_ambiguous = r.is_ambiguous
  const ambConf = toNum(r.ambiguous_confidence)
  if (ambConf !== null) out.ambiguous_confidence = ambConf
  const bestConf = toNum(r.best_confidence)
  if (bestConf !== null) out.best_confidence = bestConf
  return out
}

function sanitizeTiming(raw: unknown): Record<string, number> {
  const out: Record<string, number> = {}
  if (raw === null || typeof raw !== 'object') return out
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof v === 'number' && Number.isFinite(v)) out[k] = Math.round(v)
  }
  return out
}

/** 内部解析形态：_i = 驱动条目原始序号（best_index 对齐 + 概率分布键） */
interface ParsedItem {
  _i: number
  name: string
  subtitle: string
  section: string
  x: number
  y: number
  probability: number | null
}

export function createWecomChatSearchOperation(
  deps: { runDriverFn?: RunPowerShellDriverFn; createRefFn?: CreateTargetRefFn; artifactDirFn?: () => string | null } = {},
): WecomOperation<WecomChatSearchArgs> {
  const runDriverFn = deps.runDriverFn ?? runPowerShellDriver
  // 默认签发适配：CreateTargetRefFn 的第 4 参是裸坐标，createTargetRef 的坐标在 opts 内
  const createRefFn: CreateTargetRefFn =
    deps.createRefFn ?? ((name, type, subtitle, coords) => createTargetRef(name, type, subtitle, { coords }))
  const artifactDirFn = deps.artifactDirFn ?? (() => artifactDir())
  return {
    name: 'wecom_chat_search',
    execute(args: WecomChatSearchArgs, ctx: OpContext): Promise<OperationResult> {
      return runWecomOperation(
        'readonly',
        ctx,
        () => {
          if (args === null || typeof args !== 'object' || Array.isArray(args)) {
            return '参数必须是对象（query 必填；type/limit 可选）'
          }
          if (typeof args.query !== 'string' || args.query.trim().length === 0) {
            return 'query 必填且必须是非空字符串'
          }
          if (args.query.length > MAX_QUERY_LENGTH) {
            return `query 长度不能超过 ${MAX_QUERY_LENGTH} 字`
          }
          if (args.type !== undefined && !(SEARCH_TYPES as readonly string[]).includes(args.type)) {
            return 'type 必须是 contact/group/any'
          }
          if (args.limit !== undefined && (!Number.isInteger(args.limit) || args.limit < 1 || args.limit > MAX_LIMIT)) {
            return `limit 必须是 1..${MAX_LIMIT} 的整数`
          }
          return null
        },
        async () => {
          const type = args.type ?? 'any'
          const limit = args.limit ?? DEFAULT_LIMIT
          // 稳定帧截图 + driver-log 存档目录（真机排障，同 message-send）
          const root = artifactDirFn()
          if (!root) {
            throw new CodedOperationError('INTERNAL_ERROR', '%LOCALAPPDATA% 未设置，无法定位 artifact 目录')
          }
          const dir = join(root, `search-${new Date().toISOString().replace(/[:.]/g, '-')}`)
          mkdirSync(dir, { recursive: true })

          ctx.progress({ stage: 'execute', message: `搜索「${args.query}」（type=${type}）` })
          const data = await runDriverFn({
            script: DRIVER_PATH,
            args: ['-Query', args.query.trim(), '-ArtifactDir', dir],
            signal: ctx.signal,
          })
          // PS 5.1 ConvertTo-Json 对单元素数组有解包怪癖，TS 侧防御性归一
          const rawItems = Array.isArray(data.items) ? data.items : data.items != null ? [data.items] : []
          const parsedItems: ParsedItem[] = rawItems.map((raw, i) => {
            const it = (raw ?? {}) as Record<string, unknown>
            return {
              _i: i,
              name: String(it.name ?? ''),
              subtitle: String(it.subtitle ?? ''),
              section: String(it.section ?? ''),
              x: toIntOrNull(it.x) ?? 0,
              y: toIntOrNull(it.y) ?? 0,
              probability: toNum(it.probability),
            }
          })
          const jev = sanitizeJev(data.jev)
          const timingMs = sanitizeTiming(data.timing_ms)
          const bestIndex =
            typeof data.best_index === 'number' && Number.isInteger(data.best_index) && data.best_index >= 0
              ? data.best_index
              : null
          const ovRaw = (data.overlay ?? null) as Record<string, unknown> | null
          const overlay =
            ovRaw !== null && typeof ovRaw === 'object'
              ? {
                  x: toIntOrNull(ovRaw.x) ?? 0,
                  y: toIntOrNull(ovRaw.y) ?? 0,
                  w: toIntOrNull(ovRaw.w) ?? 0,
                  h: toIntOrNull(ovRaw.h) ?? 0,
                }
              : null
          // 概率分布（对驱动全部原始条目建键，含被过滤掉的；降级时全 null）
          const probabilities: Record<string, number | null> = {}
          for (const it of parsedItems) probabilities[`R${it._i}`] = it.probability

          const filtered = parsedItems
            .filter((it) => it.name.length > 0 && matchType(it.section, type))
            .slice(0, limit)

          // 面板空 / 过滤后空 → TARGET_NOT_FOUND（data 透传 jev/timing 供调用方诊断）
          if (rawItems.length === 0 || filtered.length === 0) {
            const reason = rawItems.length === 0 ? 'no_results' : 'filtered_out'
            throw new CodedOperationError(
              'TARGET_NOT_FOUND',
              `搜索「${args.query}」无匹配结果（type=${type}${reason === 'no_results' ? '，面板无结果条目' : '，结果均被 type 过滤'}）`,
              undefined,
              { query: args.query, search_successful: false, reason, items: [], best: null, jev, timing_ms: timingMs },
            )
          }

          // best 选择：驱动 best_index（Jev 或规则）落在过滤后集合内 → 用之；
          // 否则从过滤后 items 重选：probability 最高，全 null 则第一条
          let bestSrc = filtered.find((it) => it._i === bestIndex) ?? null
          if (bestSrc === null) {
            bestSrc = filtered[0]!
            for (const it of filtered) {
              if (it.probability !== null && (bestSrc.probability === null || it.probability > bestSrc.probability)) {
                bestSrc = it
              }
            }
          }

          const coordsOf = (it: ParsedItem): TargetRefCoords => ({ x: it.x, y: it.y })
          const items: ChatSearchItem[] = filtered.map((it) => ({
            name: it.name,
            subtitle: it.subtitle,
            section: it.section,
            x: it.x,
            y: it.y,
            probability: it.probability,
            target_ref: createRefFn(it.name, sectionToTargetType(it.section), it.subtitle, coordsOf(it)),
          }))
          const bestIdx = filtered.indexOf(bestSrc)
          const bestBase = items[bestIdx]!
          // Jev 的 best_confidence 只在「best 就是驱动选中项」时可信；重选后它指向的是
          // 被滤掉的条目，不得转挂到新 best 上（回落到条目自身概率）
          const fromDriverBest = bestSrc._i === bestIndex
          const best: ChatSearchBest = {
            ...bestBase,
            screen_x: overlay !== null ? overlay.x + bestBase.x : null,
            screen_y: overlay !== null ? overlay.y + bestBase.y : null,
            confidence: fromDriverBest ? (jev.best_confidence ?? bestSrc.probability ?? null) : (bestSrc.probability ?? null),
            probabilities,
          }
          return {
            message: `搜索「${args.query}」完成：${items.length} 个结果${jev.used ? `，Jev 最优「${best.name}」` : ''}`,
            data: { query: args.query, search_successful: true, best, items, jev, timing_ms: timingMs },
          }
        },
      )
    },
  }
}
