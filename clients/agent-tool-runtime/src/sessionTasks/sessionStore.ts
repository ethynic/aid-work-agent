/**
 * 会话任务本地事件日志（C2，设计 §8）。
 *
 * 单写入者、追加型 JSONL：`<runtimeHome>/session-tasks/<assignment_id>/events.jsonl`，
 * 与既有单动作 journal/ 与结果 outbox 分离；不删改既有 journal。
 *
 * 不变量（实现不得放宽）：
 * - 每条记录：schema_version=1, assignment_id, fence, local_seq, event_id, type,
 *   encrypted_payload（DPAPI CurrentUser 密文，base64；不写 device/permit/claim token）。
 * - local_seq 从 1 连续；完整行写入且 fsync 成功后才推进内存状态/允许下一步。
 * - 一个记录封装一次相互依赖更新（observation/水位/批次/相位），序号连续性即原子性。
 * - 回放确定性：内存状态由日志重建；仅最后一条未完整落盘的尾行可作未提交丢弃
 *   （丢弃前留诊断）；中间损坏、序号缺口、assignment/fence 不符 → CorruptError
 *   （调用方须把任务置 blocked，禁止空状态启动）。
 * - 日志同时是同步 outbox：ackedLocalSeq 只前进；未 ACK 记录保留并重投，
 *   不因云端已见终态删除（删除走终态保留期清理，不在本模块）。
 */
import { mkdirSync, openSync, writeSync, closeSync, readFileSync, existsSync, statSync, fsyncSync, truncateSync, renameSync, unlinkSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { randomUUID } from 'node:crypto'

export const SESSION_STORE_SCHEMA_VERSION = 1
export const SESSION_TASKS_DIR = 'session-tasks'
export const ASSIGNMENT_META_SCHEMA_VERSION = 1

export interface SessionEventRecord {
  schema_version: number
  assignment_id: string
  fence: number
  local_seq: number
  event_id: string
  type: string
  encrypted_payload: string
}

/** 回放产出的已解密事件（payload 为明文对象） */
export interface ReplayedEvent {
  record: Omit<SessionEventRecord, 'encrypted_payload'>
  payload: unknown
}

/**
 * assignment 恢复身份信息（meta.json）：仅含接续所需身份引用，不含消息正文；
 * spec 业务正文以 encrypted_spec 密文字段落盘（接口返回解密后的 spec）。
 * 用途：Runtime 重启后旧 assignment 租约仍有效时，用其中的
 * fence/control_epoch 调 C1 renew（renew 不校验 runtime_instance_id）续租
 * 接续，避免过期换代重建基线（旧水位/已决策批次丢失）。
 */
export interface AssignmentMeta {
  input_version_base?: number
  fresh_baseline?: boolean
  task_id: string
  conversation_binding_id: string
  binding_version: number
  account_identity_version: number
  spec_revision: number
  spec: Record<string, unknown>
  fence: number
  control_epoch: number
}

/** readMeta 失败原因（审计 P1：区分 meta 不可用原因，供恢复扫描分派） */
export type MetaReadStatus = 'missing' | 'corrupt' | 'no_task_id' | 'decrypt_failed'

export type MetaReadResult = { status: 'ok'; meta: AssignmentMeta } | { status: MetaReadStatus }

/** readMetaTaskId 结果（不 decrypt spec，ok 时仅携带 task_id） */
export type MetaTaskIdResult = { status: 'ok'; taskId: string } | { status: 'missing' | 'corrupt' | 'no_task_id' }

export class SessionStoreCorruptError extends Error {
  constructor(
    readonly code:
      | 'MID_FILE_CORRUPT' // 中间行损坏（仅尾行可丢弃）
      | 'SEQ_GAP' // 序号缺口/重复
      | 'IDENTITY_MISMATCH' // assignment_id / fence 与目录上下文不符
      | 'DECRYPT_FAILED', // 密文解密失败（blocked，禁止空状态启动）
    message: string,
  ) {
    super(message)
    this.name = 'SessionStoreCorruptError'
  }
}

export class SessionStoreWriteError extends Error {
  constructor(message: string, readonly cause?: unknown) {
    super(message)
    this.name = 'SessionStoreWriteError'
  }
}

/** DPAPI 依赖注入（生产用 src/dpapi.ts；测试注入恒等/可故障实现） */
export interface SessionCrypto {
  protect(plaintext: string): Promise<string>
  unprotect(cipherBase64: string): Promise<string>
}

export interface SessionStoreOptions {
  /** runtime home（默认 runtimeHomeDir()；测试注入临时目录） */
  runtimeHome: string
  assignmentId: string
  crypto: SessionCrypto
}

export function sessionTaskDir(runtimeHome: string, assignmentId: string): string {
  return resolve(runtimeHome, SESSION_TASKS_DIR, assignmentId)
}

function eventLogPath(dir: string): string {
  return join(dir, 'events.jsonl')
}

/**
 * 单 assignment 事件日志。一个实例独占一个 assignment（engine 保证单写入者）。
 */
export class SessionStore {
  private readonly dir: string
  private readonly file: string
  private readonly metaFile: string
  private localSeq = 0
  private ackedSeq = 0
  private readonly seenEventIds = new Set<string>()

  constructor(private readonly opts: SessionStoreOptions) {
    this.dir = sessionTaskDir(opts.runtimeHome, opts.assignmentId)
    this.file = eventLogPath(this.dir)
    this.metaFile = join(this.dir, 'meta.json')
  }

  /** 日志是否存在（存在则必须先 replay 再 append） */
  exists(): boolean {
    return existsSync(this.file)
  }

  /**
   * 启动回放：确定性重建（local_seq 连续性、assignment/fence 身份、尾行容错）。
   * 返回解密后的事件序列与终止游标；损坏抛 SessionStoreCorruptError。
   */
  async replay(): Promise<{ events: ReplayedEvent[]; localSeq: number; droppedTailDiagnostic?: string }> {
    if (!this.exists()) return { events: [], localSeq: 0 }
    const raw = readFileSync(this.file, 'utf-8')
    const lines = raw.split('\n')
    // 尾部空行/未完整行：仅允许最后一行非空且解析失败时丢弃（留诊断）
    let droppedTailDiagnostic: string | undefined
    if (lines.length > 0 && lines[lines.length - 1] === '') lines.pop()
    const events: ReplayedEvent[] = []
    let expected = 1
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i] as string
      const isTail = i === lines.length - 1
      let parsed: SessionEventRecord | null = null
      try {
        parsed = JSON.parse(line) as SessionEventRecord
      } catch {
        parsed = null
      }
      if (parsed === null || !isValidRecord(parsed)) {
        if (isTail) {
          droppedTailDiagnostic = `tail-incomplete len=${line.length} seq=${expected}`
          // 物理截断残缺尾行（评审 P1-4）：否则后续 append 接在损坏内容之后，
          // 下次恢复会把有效新记录一并当残缺尾行丢弃
          const validBytes = lines
            .slice(0, i)
            .reduce((acc, l) => acc + Buffer.byteLength(l + '\n'), 0)
          truncateSync(this.file, validBytes)
          break
        }
        throw new SessionStoreCorruptError('MID_FILE_CORRUPT', `第 ${i + 1} 行损坏且非尾行`)
      }
      if (parsed.assignment_id !== this.opts.assignmentId) {
        throw new SessionStoreCorruptError('IDENTITY_MISMATCH', `assignment_id 不符: ${parsed.assignment_id}`)
      }
      if (parsed.local_seq !== expected) {
        throw new SessionStoreCorruptError(
          'SEQ_GAP',
          `local_seq 不连续: 期望 ${expected} 实得 ${parsed.local_seq}`,
        )
      }
      let payload: unknown
      try {
        payload = JSON.parse(await this.opts.crypto.unprotect(parsed.encrypted_payload))
      } catch (err) {
        throw new SessionStoreCorruptError(
          'DECRYPT_FAILED',
          `local_seq=${parsed.local_seq} 解密失败（密文损坏或 Windows 用户不符）: ${err instanceof Error ? err.message : String(err)}`,
        )
      }
      const { encrypted_payload: _cipher, ...recordHead } = parsed
      events.push({ record: recordHead, payload })
      this.seenEventIds.add(parsed.event_id)
      this.localSeq = parsed.local_seq
      expected++
    }
    return { events, localSeq: this.localSeq, droppedTailDiagnostic }
  }

  /**
   * 追加一条事务记录：payload 先加密（调用方 await），本方法同步写行 + fsync，
   * 成功后才返回记录（供调用方推进内存状态/触发同步）。写失败抛
   * SessionStoreWriteError——调用方不得推进任何状态或执行下一步动作。
   */
  append(eventId: string, type: string, encryptedPayload: string, fence: number): SessionEventRecord {
    const seq = this.localSeq + 1
    if (this.seenEventIds.has(eventId)) {
      // 幂等重放保护：同 event_id 不得二次落盘（调用方应先查重）
      throw new SessionStoreWriteError(`event_id 重复: ${eventId}`)
    }
    const record: SessionEventRecord = {
      schema_version: SESSION_STORE_SCHEMA_VERSION,
      assignment_id: this.opts.assignmentId,
      fence,
      local_seq: seq,
      event_id: eventId,
      type,
      encrypted_payload: encryptedPayload,
    }
    let fd: number | undefined
    try {
      mkdirSync(this.dir, { recursive: true })
      fd = openSync(this.file, 'a')
      const bytes = writeSync(fd, JSON.stringify(record) + '\n')
      if (bytes !== Buffer.byteLength(JSON.stringify(record) + '\n')) {
        throw new Error('部分写入')
      }
fsyncSync(fd)
      closeSync(fd)
      fd = undefined
    } catch (err) {
      if (fd !== undefined) {
        try {
          closeSync(fd)
        } catch {
          /* 尽力关闭 */
        }
      }
      throw new SessionStoreWriteError(
        `事件写入失败（禁止推进状态/执行下一步）: ${err instanceof Error ? err.message : String(err)}`,
        err,
      )
    }
    this.localSeq = seq
    this.seenEventIds.add(eventId)
    return record
  }

  /** 便捷：加密 + 追加（await 版本，供 engine） */
  async appendEncrypted(eventId: string, type: string, payload: unknown, fence: number): Promise<SessionEventRecord> {
    const cipher = await this.opts.crypto.protect(JSON.stringify(payload ?? null))
    return this.append(eventId, type, cipher, fence)
  }

  /** 已落盘最大序号 */
  get lastLocalSeq(): number {
    return this.localSeq
  }

  /** 云端连续前缀 ACK 游标（只前进；不删除未 ACK 记录） */
  ackUpTo(seq: number): void {
    if (seq > this.ackedSeq) this.ackedSeq = seq
  }

  get ackedLocalSeq(): number {
    return this.ackedSeq
  }

  /** 未 ACK 记录数（重投压力观测） */
  get pendingSyncCount(): number {
    return this.localSeq - this.ackedSeq
  }

  /** 本日志目录磁盘占用（字节；retention 统计用） */
  diskBytes(): number {
    if (!existsSync(this.file)) return 0
    return statSync(this.file).size
  }

  hasEvent(eventId: string): boolean {
    return this.seenEventIds.has(eventId)
  }

  /**
   * 写恢复元数据（meta.json）：非敏感身份字段明文，spec（goal/reply_policy
   * 等业务正文）加密为 encrypted_spec（base64 密文），不绕过 DPAPI 保护。
   * 唯一随机后缀 tmp 文件 + fsync + rename 原子替换，避免崩溃留下半写文件；
   * 随机后缀防止并发写入竞争同一 tmp（审计十轮）。失败抛错——调用方可降级
   * （重启后走过期重领路径），但不降级为明文。
   */
  async writeMeta(meta: AssignmentMeta): Promise<void> {
    const publicMeta: Record<string, unknown> = { ...meta }
    delete publicMeta['spec']
    const encryptedSpec = meta.spec ? await this.opts.crypto.protect(JSON.stringify(meta.spec)) : null
    const doc = JSON.stringify({ schema_version: ASSIGNMENT_META_SCHEMA_VERSION, ...publicMeta, encrypted_spec: encryptedSpec })
    mkdirSync(this.dir, { recursive: true })
    const tmp = `${this.metaFile}.tmp.${randomUUID()}`
    let fd: number | undefined
    try {
      fd = openSync(tmp, 'w')
      const bytes = writeSync(fd, doc)
      if (bytes !== Buffer.byteLength(doc)) throw new Error('部分写入')
      fsyncSync(fd)
      closeSync(fd)
      fd = undefined
      renameSync(tmp, this.metaFile)
    } catch (err) {
      if (fd !== undefined) {
        try {
          closeSync(fd)
        } catch {
          /* 尽力关闭 */
        }
      }
      try {
        unlinkSync(tmp)
      } catch {
        /* 尽力清理：残留 tmp 不影响 meta.json 读取（readMeta 只读正式文件） */
      }
      throw new SessionStoreWriteError(
        `meta.json 写入失败: ${err instanceof Error ? err.message : String(err)}`,
        err,
      )
    }
  }

  /**
   * 读恢复元数据（不抛错，返回区分原因的状态）。missing=文件不存在/不可读；
   * corrupt=JSON 损坏（截断）/版本不符/身份字段校验失败；no_task_id=可解析但缺
   * task_id；decrypt_failed=encrypted_spec 解密失败——不降级读明文，调用方按
   * 不可恢复处理（blocked）。
   */
  async readMeta(): Promise<MetaReadResult> {
    let raw: string
    try {
      raw = readFileSync(this.metaFile, 'utf-8')
    } catch {
      return { status: 'missing' }
    }
    let parsed: Record<string, unknown>
    try {
      parsed = JSON.parse(raw) as Record<string, unknown>
    } catch {
      return { status: 'corrupt' }
    }
    if (parsed['schema_version'] !== ASSIGNMENT_META_SCHEMA_VERSION) return { status: 'corrupt' }
    if (typeof parsed['task_id'] !== 'string' || parsed['task_id'].length === 0) return { status: 'no_task_id' }
    const encryptedSpec = parsed['encrypted_spec']
    if (
      typeof parsed['conversation_binding_id'] !== 'string' ||
      typeof parsed['binding_version'] !== 'number' ||
      typeof parsed['account_identity_version'] !== 'number' ||
      typeof parsed['spec_revision'] !== 'number' ||
      typeof parsed['fence'] !== 'number' ||
      typeof parsed['control_epoch'] !== 'number' ||
      typeof encryptedSpec !== 'string' ||
      encryptedSpec.length === 0
    ) {
      return { status: 'corrupt' }
    }
    let spec: unknown
    try {
      spec = JSON.parse(await this.opts.crypto.unprotect(encryptedSpec))
    } catch {
      return { status: 'decrypt_failed' }
    }
    if (typeof spec !== 'object' || spec === null) return { status: 'decrypt_failed' }
    return {
      status: 'ok',
      meta: {
        input_version_base: typeof parsed['input_version_base'] === 'number' ? parsed['input_version_base'] : 0,
        fresh_baseline: parsed['fresh_baseline'] === true,
        task_id: parsed['task_id'],
        conversation_binding_id: parsed['conversation_binding_id'],
        binding_version: parsed['binding_version'],
        account_identity_version: parsed['account_identity_version'],
        spec_revision: parsed['spec_revision'],
        spec: spec as Record<string, unknown>,
        fence: parsed['fence'],
        control_epoch: parsed['control_epoch'],
      },
    }
  }

  /**
   * 从 meta.json 明文基础字段提取 task_id（不 decrypt spec，不抛错，区分失败
   * 原因）：readMeta 失败时仍可定位任务（如 decrypt_failed），供恢复扫描分派
   * （新领取 blocked / 不可归属阻断，不静默重建基线）。
   */
  readMetaTaskId(): MetaTaskIdResult {
    let raw: string
    try {
      raw = readFileSync(this.metaFile, 'utf-8')
    } catch {
      return { status: 'missing' }
    }
    let parsed: Record<string, unknown>
    try {
      parsed = JSON.parse(raw) as Record<string, unknown>
    } catch {
      return { status: 'corrupt' }
    }
    if (parsed['schema_version'] !== ASSIGNMENT_META_SCHEMA_VERSION) return { status: 'corrupt' }
    const taskId = parsed['task_id']
    if (typeof taskId !== 'string' || taskId.length === 0) return { status: 'no_task_id' }
    return { status: 'ok', taskId }
  }
}

function isValidRecord(v: unknown): v is SessionEventRecord {
  if (typeof v !== 'object' || v === null) return false
  const r = v as Record<string, unknown>
  return (
    r.schema_version === SESSION_STORE_SCHEMA_VERSION &&
    typeof r.assignment_id === 'string' &&
    typeof r.fence === 'number' &&
    typeof r.local_seq === 'number' &&
    typeof r.event_id === 'string' &&
    typeof r.type === 'string' &&
    typeof r.encrypted_payload === 'string'
  )
}
