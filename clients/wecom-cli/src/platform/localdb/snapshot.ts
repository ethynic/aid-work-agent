/**
 * 可信快照捕获（设计 §3.3 捕获协议，任务 1.4 TS 编排侧）。
 *
 * 捕获窗口判定（逐字遵循 §3.3；支持条件 = 窗口内无 WAL reset 且无撕裂写入）：
 *   ① 基线：主库 size D0；WAL 在位时预读源 WAL 头 H0（前 32B，含 magic/salt/ckptseq）与 size S0
 *   ② 复制主库（恰 D0 字节）→ 复制 WAL（按复制时刻实际大小）
 *   ③ 复读：WAL 头 H1（要求与 H0 一致——reset 判定只依据头比较，不依据尾帧 salt，评审 v3 #24）、
 *      WAL size S1（要求 ≥ 已复制 WAL 字节数）、主库 size（要求 == D0；增长由调用方有限重试，
 *      本组件单次语义直接报 db-changed）
 * 任一不满足 → ok:false + 明确 reason；有限重试（计数与秒级退避）归 operation 层（§2 快照类
 * 短退避，不进长负缓存），本组件只做单次捕获语义。
 *
 * 边界语义（诚实边界，§3.3）：
 * - 源无 WAL（调用方显式传 srcWal=null）→ 合法：ok:true + walSnapshot=null（只有主库，无帧可放）；
 *   复制后仍复读主库大小核验 D1==D0。
 * - 调用方传了 WAL 路径但预读/复制时已消失 → ok:false + 'no-wal'（窗口内 checkpoint+delete 语义；
 *   调用方可重试，若 WAL 永久消失应改传 null 表达"已知无 WAL"）。
 * - WAL 头不完整（0 < size < 32B）：按实际字节捕获并复读比较（增长只允许追加，公共前缀一致即通过），
 *   ok:true 透传——头部语义合法性（魔数/页大小/校验和、帧链）由 py 侧 snapshot-verify 最终判定，
 *   半可信快照不会进入解密（py decrypt 先验证后解密，不产生半解密产物）。
 * - 窗口内 checkpoint 回填不改 WAL 头、帧仍在有效前缀内，属支持条件内（§3.3 说明）。
 *
 * 依赖全部可注入（statSize/readRange/writeFile/joinPath），单测可注入替身模拟"复制期间并发变化"
 * （reset/缩小/增长/写失败），无需真实企微库。原始库文件只复制不改（§8）。
 */
import { closeSync, mkdirSync, openSync, readSync, statSync, writeFileSync } from 'node:fs'
import { basename, dirname, join } from 'node:path'

/** SQLite WAL 头长度（magic/version/pagesize/ckptseq/salt1/salt2/cksum 各 4B） */
export const WAL_HEADER_BYTES = 32

export interface CaptureSnapshotDeps {
  /** 文件大小（字节）；不可得（不存在/无权限）→ null。缺省 statSync */
  statSize?: (path: string) => number | null
  /**
   * 读取 [offset, offset+length) 字节；IO 失败 → null；EOF 截短返回更短 Buffer（长度由调用方
   * 判定，截短 = 复制期间文件缩小的信号）。缺省为真实 fs 分段读。
   */
  readRange?: (path: string, offset: number, length: number) => Buffer | null
  /** 写快照文件（含父目录创建）；失败 → false。缺省 mkdir+writeFileSync */
  writeFile?: (path: string, data: Buffer) => boolean
  /** 路径拼接（缺省 node:path.join；测试注入假分隔符用） */
  joinPath?: (...parts: string[]) => string
}

/** 捕获一致性证据（H0/H1 为 WAL 头 hex；无 WAL=null；短头按实际字节 hex） */
export interface SnapshotCapture {
  h0: string | null
  h1: string | null
  sizes: {
    /** 主库基线 D0（=快照主库实际字节数） */
    db_size: number
    /** WAL 实际复制字节数（无 WAL=0） */
    wal_size: number
  }
}

export interface CaptureSnapshotOk {
  ok: true
  /** workDir 下的主库快照路径（<basename>.db） */
  dbSnapshot: string
  /** workDir 下的 WAL 快照路径（<basename>.db-wal）；源无 WAL=null */
  walSnapshot: string | null
  capture: SnapshotCapture
}

export type CaptureSnapshotReason =
  | 'no-wal' // 调用方传了 WAL 路径但捕获窗口内 WAL 消失（checkpoint+delete 语义）
  | 'wal-header-changed' // H0≠H1：窗口内发生 WAL reset
  | 'wal-shrunk' // S1 < 已复制 WAL 字节数（或复制期间缩短）
  | 'db-changed' // 主库大小 != D0（含增长——增长重试归调用方，本组件单次语义报变化）
  | 'copy-failed' // 读/写 IO 失败

export interface CaptureSnapshotFailure {
  ok: false
  reason: CaptureSnapshotReason
  detail: string
}

export type CaptureSnapshotResult = CaptureSnapshotOk | CaptureSnapshotFailure

/** 缺省 stat 大小 */
function defaultStatSize(path: string): number | null {
  try {
    return statSync(path).size
  } catch {
    return null
  }
}

/** 缺省分段读：EOF 截短返回更短 Buffer；打开/读失败 → null */
function defaultReadRange(path: string, offset: number, length: number): Buffer | null {
  let fd: number
  try {
    fd = openSync(path, 'r')
  } catch {
    return null
  }
  try {
    const buf = Buffer.alloc(length)
    let total = 0
    while (total < length) {
      const n = readSync(fd, buf, total, length - total, offset + total)
      if (n <= 0) break
      total += n
    }
    return buf.subarray(0, total)
  } catch {
    return null
  } finally {
    try {
      closeSync(fd)
    } catch {
      // 关闭失败忽略（只读 fd）
    }
  }
}

/** 缺省写快照：同目录临时文件语义不在此处（快照为一次性读取产物），保证父目录存在后直接写 */
function defaultWriteFile(path: string, data: Buffer): boolean {
  try {
    mkdirSync(dirname(path), { recursive: true })
    writeFileSync(path, data)
    return true
  } catch {
    return false
  }
}

/**
 * 可信快照捕获入口（单次语义）。srcWal=null 表达"已知源无 WAL"（合法场景）；
 * 传路径即承诺捕获窗口开始时 WAL 在位，中途消失按失败报告（'no-wal'）。
 * 全程不抛出（对齐 localdb 组件 best-effort 约定），失败以结构化 reason/detail 返回。
 */
export async function captureSnapshot(
  srcDb: string,
  srcWal: string | null,
  workDir: string,
  deps: CaptureSnapshotDeps = {},
): Promise<CaptureSnapshotResult> {
  const fs = {
    statSize: deps.statSize ?? defaultStatSize,
    readRange: deps.readRange ?? defaultReadRange,
    writeFile: deps.writeFile ?? defaultWriteFile,
    joinPath: deps.joinPath ?? join,
  }
  const fail = (reason: CaptureSnapshotReason, detail: string): CaptureSnapshotFailure => ({ ok: false, reason, detail })
  const dbSnapshotPath = fs.joinPath(workDir, basename(srcDb))
  const walSnapshotPath = fs.joinPath(workDir, `${basename(srcDb)}-wal`)

  // ① 基线 D0
  const d0 = fs.statSize(srcDb)
  if (d0 === null) return fail('copy-failed', `主库不可读（stat 失败）：${srcDb}`)

  // ② 源无 WAL（合法）：复制主库 + 复读核验 D1==D0
  if (srcWal === null) {
    const dbData = fs.readRange(srcDb, 0, d0)
    if (dbData === null) return fail('copy-failed', `主库读取失败：${srcDb}`)
    if (dbData.length < d0) return fail('db-changed', `主库复制期间缩小（基线 ${d0} 字节，实得 ${dbData.length}）`)
    if (!fs.writeFile(dbSnapshotPath, dbData)) return fail('copy-failed', `主库快照写入失败：${dbSnapshotPath}`)
    const d1 = fs.statSize(srcDb)
    if (d1 !== d0) return fail('db-changed', `主库大小变化：基线 D0=${d0}，当前 D1=${String(d1)}`)
    return {
      ok: true,
      dbSnapshot: dbSnapshotPath,
      walSnapshot: null,
      capture: { h0: null, h1: null, sizes: { db_size: d0, wal_size: 0 } },
    }
  }

  // ③ 预读源 WAL 头 H0 与基线 S0（设计步骤 1）
  const s0 = fs.statSize(srcWal)
  if (s0 === null) return fail('no-wal', `传入 WAL 路径但预读时已不存在（窗口内 checkpoint+delete）：${srcWal}`)
  const h0 = fs.readRange(srcWal, 0, WAL_HEADER_BYTES)
  if (h0 === null) return fail('no-wal', `WAL 头预读失败：${srcWal}`)
  if (h0.length < Math.min(WAL_HEADER_BYTES, s0)) {
    return fail('wal-shrunk', `WAL 预读阶段已缩小（stat=${s0} 字节，实读 ${h0.length}）`)
  }
  // 立即快照 H0 基线：后续源变化不得污染已取得的 H0（也防注入替身返回别名视图）
  const h0Hex = h0.toString('hex')

  // ④ 复制主库（恰 D0 字节）
  const dbData = fs.readRange(srcDb, 0, d0)
  if (dbData === null) return fail('copy-failed', `主库读取失败：${srcDb}`)
  if (dbData.length < d0) return fail('db-changed', `主库复制期间缩小（基线 ${d0} 字节，实得 ${dbData.length}）`)
  if (!fs.writeFile(dbSnapshotPath, dbData)) return fail('copy-failed', `主库快照写入失败：${dbSnapshotPath}`)

  // ⑤ 复制 WAL（按复制时刻实际大小）
  const s0Copy = fs.statSize(srcWal)
  if (s0Copy === null) return fail('no-wal', `复制主库后源 WAL 已消失（窗口内 checkpoint+delete）：${srcWal}`)
  const walData = fs.readRange(srcWal, 0, s0Copy)
  if (walData === null) return fail('copy-failed', `WAL 读取失败：${srcWal}`)
  if (walData.length < s0Copy) return fail('wal-shrunk', `WAL 复制期间缩小（期望 ${s0Copy} 字节，实得 ${walData.length}）`)
  if (!fs.writeFile(walSnapshotPath, walData)) return fail('copy-failed', `WAL 快照写入失败：${walSnapshotPath}`)

  // ⑥ 复读核验 H1/S1/D1（设计步骤 3）
  const s1 = fs.statSize(srcWal)
  if (s1 === null) return fail('no-wal', `复制完成后源 WAL 已消失（窗口内 checkpoint+delete）：${srcWal}`)
  const h1 = fs.readRange(srcWal, 0, WAL_HEADER_BYTES)
  if (h1 === null) return fail('no-wal', `WAL 头复读失败：${srcWal}`)
  if (s1 < walData.length) {
    return fail('wal-shrunk', `WAL 缩小：已复制 ${walData.length} 字节，当前仅 ${s1} 字节`)
  }
  // 头比较只看 H0 覆盖的公共前缀（WAL 追加增长合法；H0 本身可能因源 WAL 短于 32B 而不完整）。
  // reset 判定只依据头比较（评审 v3 #24），不依据尾帧 salt——尾帧 salt 语义在 py 有效前缀判定里。
  const h1Common = h1.length >= h0.length ? h1.subarray(0, h0.length).toString('hex') : h1.toString('hex')
  if (h1Common !== h0Hex) {
    return fail(
      'wal-header-changed',
      `WAL 头变化（捕获窗口内发生 reset）：H0=${h0Hex} H1=${h1.toString('hex')}`,
    )
  }
  const d1 = fs.statSize(srcDb)
  if (d1 !== d0) return fail('db-changed', `主库大小变化：基线 D0=${d0}，当前 D1=${String(d1)}（增长归调用方有限重试）`)

  return {
    ok: true,
    dbSnapshot: dbSnapshotPath,
    walSnapshot: walSnapshotPath,
    capture: {
      h0: h0Hex,
      h1: h1.toString('hex'),
      sizes: { db_size: d0, wal_size: walData.length },
    },
  }
}
