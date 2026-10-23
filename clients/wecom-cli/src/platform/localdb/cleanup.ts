/**
 * 1.13 遗留产物清扫（设计 §7 清理：owner 存活校验 + 删除失败报告；评审 v7 两段发布配合）。
 *
 * 清扫 baseDir（缺省 %LOCALAPPDATA%\AidWorkAgent\wecom-cli\artifacts\localdb）下的
 * 【直接子目录】。目录分两类：
 * - 最终名产物目录（每目录内有 owner 标记 owner.json = {pid, started_at}）：最终名目录由
 *   发布协议（doctorLocaldb.defaultCreateWorkDir 两段发布）保证自出现起 owner 恒在，
 *   去留判定（评审 v6：年龄不能证明停止使用）——
 *   · owner 文件完全缺失（ENOENT）→ 删除（该目录从未有主）；
 *   · owner 文件在位但内容不可判（JSON 不可解析/非对象/pid 缺失或非法/权限读取失败）→
 *     保守跳过并计入 skipped（不把暂时不可读当确认无主——半写入/暂时不可读的 owner 可能
 *     属于存活进程，宁可漏扫不误删活动产物）；
 *   · pid 死亡 → 删除（owner 进程已退出，目录无活动所有者）；
 *   · pid 存活 → 一律跳过（不论挂名多久）。
 * - 两段发布暂存目录（<final名>.creating-<pid>-<ts>，评审 v7）：发布完成前 owner 尚未写入
 *   是合法中间态，不走 owner 判定（否则会重演「mkdir 后、owner 写入前被当从未有主误删」
 *   的竞争）——按名字内嵌 pid 判死活：pid 可解析且已死 → 删除（进程崩溃泄漏的暂存目录
 *   清扫）；pid 存活或不可解析 → 保守跳过。
 *
 * 只动 baseDir 的直接子目录（baseDir 本身与其上级/兄弟永不触碰）；非目录条目跳过。
 * 删除失败（EBUSY 等）→ 收集 {dir, error} 进返回值，不抛出、不中断其余目录的清扫；
 * 结果只含目录路径与错误消息，不含消息内容或敏感路径内容（设计 §7）。
 *
 * deps 注入 isAlive/removeFn（对齐 localdb 组件注入式替身模式）；baseDir 缺省按
 * 环境变量定位，显式 null → 空结果（模拟 LOCALAPPDATA 缺失）。
 */
import { readdirSync, readFileSync, rmSync, mkdirSync, renameSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

/** 目录内 owner 标记文件名（{pid, started_at}，由产物创建方写入） */
export const OWNER_FILE_NAME = 'owner.json'

/**
 * 两段发布暂存目录名形态：<final名>.creating-<pid>-<ts>（由 doctorLocaldb.defaultCreateWorkDir
 * 写入；发布=owner.json 就绪后原子 rename 为最终名）。最终名目录自出现起 owner 恒在，
 * 本清扫对最终名目录的「owner ENOENT 即删」不会再撞上发布空窗（评审 v7）。
 */
export const STAGING_NAME_MARKER = '.creating-'

const STAGING_PID_RE = /\.creating-(\d+)-(\d+)$/

/**
 * 目录名是否带两段发布暂存标记（含 .creating- 即按暂存目录对待——不走最终名的 owner 判定；
 * 最终名目录名形如 doctor_<ts>_<pid>，天然不含该标记）。
 */
export function isStagingDirName(name: string): boolean {
  return name.includes(STAGING_NAME_MARKER)
}

/**
 * 解析暂存目录名内嵌 pid：<final名>.creating-<pid>-<ts> → pid（正整数）；
 * pid 段非法 → null（调用方按不可解析保守跳过，不删）。
 */
export function parseStagingPid(name: string): number | null {
  const m = STAGING_PID_RE.exec(name)
  if (m === null) return null
  const pid = Number(m[1])
  return Number.isSafeInteger(pid) && pid > 0 ? pid : null
}

export interface SweepArtifactsResult {
  /** 已删除的目录（绝对路径） */
  swept: string[]
  /** 因 owner 存活或内容不可判而跳过的目录（绝对路径） */
  skipped: string[]
  /** 删除失败的目录与错误消息（EBUSY 等；不抛出） */
  failed: Array<{ dir: string; error: string }>
}

export interface SweepArtifactsDeps {
  /** 进程存活判定（缺省 process.kill(pid, 0)；EPERM 视为存活——无法验证时不删） */
  isAlive?: (pid: number) => boolean
  /** 目录删除注入（缺省 rmSync recursive+force；force 只容忍已消失，EBUSY 等仍抛出） */
  removeFn?: (dir: string) => void
}

/**
 * 本地库产物目录：%LOCALAPPDATA%\AidWorkAgent\wecom-cli\artifacts\localdb
 * （对齐 environment.ts artifactDir / matrix.ts localDbStateDir 风格）。
 */
export function localdbArtifactsDir(localAppData?: string | null): string | null {
  const base = localAppData === undefined ? process.env.LOCALAPPDATA : localAppData
  if (!base) return null
  return join(base, 'AidWorkAgent', 'wecom-cli', 'artifacts', 'localdb')
}

/** 缺省存活判定：process.kill(pid, 0)。ESRCH=不存在；EPERM=存在但无权限发信号（视为存活） */
const defaultIsAlive = (pid: number): boolean => {
  try {
    process.kill(pid, 0)
    return true
  } catch (err) {
    return (err as NodeJS.ErrnoException).code === 'EPERM'
  }
}
/** 缺省删除：递归删目录；force 只容忍已消失，EBUSY 等真实失败仍抛出（由调用方收集） */
const defaultRemove = (dir: string): void => {
  rmSync(dir, { recursive: true, force: true })
}

/**
 * 单目录去留判定：true=应删（从未有主 / owner 进程已死），false=保留（owner 存活，或内容
 * 不可判——评审 v6 保守跳过，宁可漏扫不误删活动所有者）。
 */
function shouldRemove(dir: string, isAlive: (pid: number) => boolean): boolean {
  let raw: string
  try {
    raw = readFileSync(join(dir, OWNER_FILE_NAME), 'utf8')
  } catch (err) {
    // 文件确实不存在 → 从未有主，删除；其他读取失败（权限/暂时锁）→ 保守跳过
    return (err as NodeJS.ErrnoException).code === 'ENOENT'
  }
  let owner: unknown
  try {
    owner = JSON.parse(raw)
  } catch {
    return false // 在位但 JSON 不可解析 → 保守跳过（可能是存活进程的半写入/暂时不可读）
  }
  if (owner === null || typeof owner !== 'object' || Array.isArray(owner)) return false
  const pid = (owner as Record<string, unknown>).pid
  if (typeof pid !== 'number' || !Number.isInteger(pid) || pid <= 0) return false // pid 不可判 → 保守跳过
  return !isAlive(pid) // 死亡 → 删；存活 → 一律跳过（年龄不参与判定）
}

/**
 * 清扫本地库遗留产物（同步，best-effort 不抛出）。
 * baseDir 三态：缺省 → localdbArtifactsDir()（环境变量定位）；字符串 → 显式注入根；
 * null → 空结果。baseDir 不可读（缺失/无权限）→ 空结果（无事可扫，不算失败）。
 */
export function sweepArtifacts(baseDir?: string | null, deps: SweepArtifactsDeps = {}): SweepArtifactsResult {
  const result: SweepArtifactsResult = { swept: [], skipped: [], failed: [] }
  const d = baseDir === undefined ? localdbArtifactsDir() : baseDir
  if (!d) return result
  const isAlive = deps.isAlive ?? defaultIsAlive
  const removeFn = deps.removeFn ?? defaultRemove
  let entries: Array<{ name: string; isDirectory: () => boolean }>
  try {
    entries = readdirSync(d, { withFileTypes: true })
  } catch {
    return result // 目录缺失/不可读 → 空结果
  }
  for (const entry of entries) {
    if (!entry.isDirectory()) continue // 只动目录（owner 标记挂在目录内；散文件不碰）
    const dir = join(d, entry.name)
    // 两段发布暂存目录：发布完成前 owner 尚未写入是合法中间态，不走最终名的 owner 判定
    //（否则「owner ENOENT 即删」会误删发布空窗目录）——按名字内嵌 pid 判死活：死亡 → 删
    //（崩溃泄漏清扫）；存活或 pid 不可解析 → 保守跳过。最终名目录 owner 恒在（发布协议
    // 保证），照旧走 owner 判定。
    let removable: boolean
    if (isStagingDirName(entry.name)) {
      const stagingPid = parseStagingPid(entry.name)
      removable = stagingPid !== null && !isAlive(stagingPid)
    } else {
      removable = shouldRemove(dir, isAlive)
    }
    if (!removable) {
      result.skipped.push(dir)
      continue
    }
    try {
      removeFn(dir)
      result.swept.push(dir)
    } catch (err) {
      result.failed.push({ dir, error: err instanceof Error ? err.message : String(err) })
    }
  }
  return result
}

/** 单次调用拥有的产物目录，发布与遗留清扫使用同一 owner 协议。 */
export interface LocaldbWorkDir {
  dir: string
  run_id: string
  cleanup(): boolean
}

/** 产物目录前缀（各执行器语义名；新执行器登记于此，清扫协议按目录 owner 标记与前缀无关） */
export type LocaldbWorkPrefix = 'doctor' | 'read-session' | 'unread' | 'watch' | 'send-baseline' | 'send-verify' | 'groups'

/** owner 就绪后原子发布；先等待调用的所有子进程退出，再调用 cleanup。 */
export function createLocaldbWorkDir(prefix: LocaldbWorkPrefix, workBaseDir?: string | null): LocaldbWorkDir | null {
  const base = workBaseDir === undefined ? localdbArtifactsDir() : workBaseDir
  if (!base) return null
  const nowMs = Date.now()
  const runId = `${prefix}_${nowMs}_${process.pid}`
  const dir = join(base, runId)
  const staging = `${dir}.creating-${process.pid}-${nowMs}`
  try {
    mkdirSync(staging, { recursive: true })
    writeFileSync(
      join(staging, OWNER_FILE_NAME),
      `${JSON.stringify({ pid: process.pid, started_at: new Date(nowMs).toISOString() }, null, 2)}\n`,
      'utf8',
    )
    renameSync(staging, dir) // 原子发布：最终名出现时 owner.json 必已完整
  } catch {
    // 发布失败：清掉暂存目录（best-effort），不留半成品；清理失败可忽略——暂存目录带本
    // 存活 pid 标记，后续清扫按 pid 死活兜底
    try {
      rmSync(staging, { recursive: true, force: true })
    } catch {
      // 忽略（见上）
    }
    return null
  }
  return {
    dir,
    run_id: runId,
    cleanup(): boolean {
      try {
        rmSync(dir, { recursive: true, force: true })
        return true
      } catch {
        return false
      }
    },
  }
}
