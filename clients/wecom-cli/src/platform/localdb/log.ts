/**
 * 1.12 结构化日志（设计 §2 兜底留痕 / §7 轮转保留上限）。
 *
 * - 追加 JSONL 到 %LOCALAPPDATA%\AidWorkAgent\wecom-cli\logs\localdb.log，行含 ISO UTC
 *   时间戳（toISOString）；事件类型为 §2 规定的五类（mode_decided/db_attempt/db_fallback/
 *   db_success/keyscan_result），未知 type 拒写（返回 false）——防调用方笔误污染日志契约；
 * - 脱敏：整行经 redactSensitive（手机号 + 32-hex 密钥样式串兜底，设计 §8）后落盘，
 *   reason/detail 里混入的密钥 hex 不会出现在盘上；
 * - 轮转：写入前检查现文件 >5MB → 改名 .1（保留 1 个历史，更旧的 .1 先删/被替换）；
 *   轮转失败不阻断本次写入（降级为继续追加，下次再试轮转）；
 * - 全程 best-effort：目录不可定位（LOCALAPPDATA 缺失）/写入失败 → false，绝不抛出——
 *   日志是诊断旁路，不能反向影响主流程（对齐 localdb 组件约定）。
 *
 * deps 注入 fs/clock/dir（对齐 snapshot.ts 注入式替身模式）：测试用假 fs 覆盖追加、轮转、
 * 写失败，真实 fs 走临时目录验证落盘形态；clock 注入固定时间戳。
 */
import { appendFileSync, mkdirSync, renameSync, statSync, unlinkSync } from 'node:fs'
import { join } from 'node:path'
import { redactSensitive } from '../../security/redaction.js'

/** 日志文件名（位于 localdbLogDir 目录下） */
export const LOCALDB_LOG_FILE_NAME = 'localdb.log'

/** 轮转阈值：单文件 >5MB 时改名 .1（设计 §7 大小分段；保留 1 个历史） */
export const LOCALDB_LOG_ROTATE_BYTES = 5 * 1024 * 1024

/** 日志事件类型（设计 §2 兜底留痕的五类） */
export const LOCALDB_LOG_EVENT_TYPES = [
  'mode_decided',
  'db_attempt',
  'db_fallback',
  'db_success',
  'keyscan_result',
] as const
export type LocaldbLogEventType = (typeof LOCALDB_LOG_EVENT_TYPES)[number]

export interface LocaldbLogEvent {
  type: LocaldbLogEventType
  /** 关联 run_id（可选，诊断聚合用） */
  run_id?: string
  /** 阶段标记（db_fallback 的 stage+reason 是设计 §2 明确要求的事件形态） */
  stage?: string
  /** 原因（稳定分类码或短语；写入前经脱敏） */
  reason?: string
  /** 补充细节（短字符串；写入前经脱敏） */
  detail?: string
}

/** fs 替身接口（只暴露本组件用到的最小面；缺省为真实 node:fs 的等义包装） */
export interface LocaldbLogFs {
  mkdirSync(dir: string, opts: { recursive: true }): void
  appendFileSync(path: string, data: string, encoding: BufferEncoding): void
  statSync(path: string): { size: number }
  renameSync(from: string, to: string): void
  unlinkSync(path: string): void
}

export interface LogLocaldbEventDeps {
  /** 日志目录；缺省 → localdbLogDir()（环境变量定位），显式 null → 模拟 LOCALAPPDATA 缺失 */
  dir?: string | null
  /** 时钟注入（时间戳来源；缺省系统时钟） */
  now?: () => Date
  /** fs 替身（缺省真实 node:fs） */
  fs?: LocaldbLogFs
}

/** 本地库日志目录：%LOCALAPPDATA%\AidWorkAgent\wecom-cli\logs（对齐 matrix.ts localDbStateDir 风格） */
export function localdbLogDir(localAppData?: string | null): string | null {
  const base = localAppData === undefined ? process.env.LOCALAPPDATA : localAppData
  if (!base) return null
  return join(base, 'AidWorkAgent', 'wecom-cli', 'logs')
}

/** 缺省 fs（等义包装真实 node:fs，隔离签名差异） */
const defaultLogFs: LocaldbLogFs = {
  mkdirSync: (dir, opts) => {
    mkdirSync(dir, opts)
  },
  appendFileSync: (path, data, encoding) => {
    appendFileSync(path, data, { encoding })
  },
  statSync: (path) => ({ size: statSync(path).size }),
  renameSync: (from, to) => {
    renameSync(from, to)
  },
  unlinkSync: (path) => {
    unlinkSync(path)
  },
}

/**
 * 追加一条结构化日志事件（同步，best-effort）。
 * 返回 true = 已写入；false = 目录不可定位 / type 非法 / 写入失败（含轮转与追加的任何异常）。
 * 行格式：单行 JSON（{ts, type, run_id?, stage?, reason?, detail?}）+ '\n'，可定义字段缺省
 * 时省略键；ts 为 ISO UTC。整行在序列化后经 redactSensitive 脱敏再写盘。
 */
export function logLocaldbEvent(event: LocaldbLogEvent, deps: LogLocaldbEventDeps = {}): boolean {
  const d = deps.dir === undefined ? localdbLogDir() : deps.dir
  if (!d) return false
  if (event === null || typeof event !== 'object') return false
  if (typeof event.type !== 'string' || !(LOCALDB_LOG_EVENT_TYPES as readonly string[]).includes(event.type)) {
    return false
  }
  const fsx = deps.fs ?? defaultLogFs
  const now = deps.now ?? (() => new Date())
  const logPath = join(d, LOCALDB_LOG_FILE_NAME)
  const rotatedPath = logPath + '.1'
  try {
    fsx.mkdirSync(d, { recursive: true })
    // 轮转：现文件 >5MB → .1（旧 .1 先删，保留 1 个历史）。stat 失败按 0 处理（文件不存在）；
    // 删旧/改名失败均忽略——轮转是卫生措施，不能挡住本次写入。
    let size = 0
    try {
      size = fsx.statSync(logPath).size
    } catch {
      // 文件不存在 → 无需轮转
    }
    if (size > LOCALDB_LOG_ROTATE_BYTES) {
      try {
        fsx.unlinkSync(rotatedPath)
      } catch {
        // 无历史或删除失败：rename 会直接覆盖（Windows MOVEFILE_REPLACE_EXISTING 语义）
      }
      try {
        fsx.renameSync(logPath, rotatedPath)
      } catch {
        // 轮转失败：继续追加（超限文件本轮不轮转，下次写入再试）
      }
    }
    const record: Record<string, unknown> = { ts: now().toISOString(), type: event.type }
    if (event.run_id !== undefined) record.run_id = event.run_id
    if (event.stage !== undefined) record.stage = event.stage
    if (event.reason !== undefined) record.reason = event.reason
    if (event.detail !== undefined) record.detail = event.detail
    const line = redactSensitive(JSON.stringify(record)) + '\n'
    fsx.appendFileSync(logPath, line, 'utf8')
    return true
  } catch {
    return false // 写入失败不抛：日志不能反向影响主流程
  }
}
