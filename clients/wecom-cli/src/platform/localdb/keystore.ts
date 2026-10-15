/**
 * C6 密钥 DPAPI 本地存储（设计 §3.4：存储/绑定/失效）。
 *
 * - 存储：本地库主密钥（wxaes128，16B hex）经 DPAPI（CurrentUser）加密后存
 *   `%LOCALAPPDATA%\AidWorkAgent\wecom-cli\localdb\key.bin`（dir 可注入，对齐 matrix.ts
 *   的 localDbStateDir 惯例）；文件内容 JSON：{binding:{user_id,data_dir,algorithm},
 *   key_blob:<dpapi base64>, saved_at}。明文密钥只在单次调用进程内存与 TS→py stdin
 *   管道流转：不落日志、不进命令行参数、不进异常消息、不进返回值之外的业务输出。
 * - 绑定：{user_id, data_dir, algorithm} 三元组精确匹配，任一变化即失效——loadKey 发现
 *   不匹配时顺带 clearKey（旧账号残留不留盘）并返回 null。
 * - 失效语义：文件缺失/损坏/binding 不匹配/DPAPI 解密失败（含 exit≠0）一律返回 null，
 *   视同失效，调用方走重扫；**仅页 1 核验明确失败才删除密钥属调用方职责**——本组件
 *   不做任何 crypto（页 1 先知核验在 py 驱动），binding 不匹配的清除是唯一例外。
 * - 全程 best-effort 不抛出：saveKey → boolean，loadKey → StoredKey | null，
 *   clearKey → boolean（true = 存储已无密钥：已删除或本就不存在；dir 不可定位 → true，
 *   目标位置不存在可清之物）。
 * - deps 注入 dpapi runner（测试全 mock，零 PowerShell 依赖）；真机 DPAPI 冒烟不在默认
 *   测试链，见 scripts/smoke-dpapi.mjs（手动运行）。
 */
import { mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { protectText, unprotectText, type DpapiRunnerFn } from '../../security/dpapi.js'
import { localDbStateDir } from './matrix.js'

/** 密钥存储文件名（位于 localDbStateDir 目录下；扩展名 .bin 仅表用途，内容为 JSON） */
export const KEY_FILE_NAME = 'key.bin'

/** wxaes128 主密钥的 hex 形态：32 个 hex 字符（16 字节） */
const KEY_HEX_RE = /^[0-9a-fA-F]{32}$/

/** 密钥绑定三元组（设计 §3.4：任一变化即失效；algorithm 取矩阵条目 algorithm 字段） */
export interface KeyBinding {
  user_id: string
  data_dir: string
  algorithm: string
}

export interface SaveKeyArgs {
  /** 主密钥 hex（16 字节 = 32 个 hex 字符；wxaes128） */
  keyHex: string
  binding: KeyBinding
  /** 存储目录；缺省 → localDbStateDir()（环境变量定位），显式 null → 模拟 LOCALAPPDATA 缺失 */
  dir?: string | null
}

export interface LoadKeyArgs {
  binding: KeyBinding
  dir?: string | null
}

export interface KeystoreDeps {
  /** dpapi 执行器注入（缺省 runDpapiCommand；测试用 mock，捕获参数断言密钥不外泄） */
  runFn?: DpapiRunnerFn
}

export interface StoredKey {
  /** 主密钥 hex（DPAPI 解密产物；页 1 先知核验由调用方执行） */
  keyHex: string
  /** 保存时间（saveKey 写入的 ISO 字符串，诊断用） */
  savedAt: string
}

/** 存储文件反序列化形态（组件内契约校验前的原始 shape） */
interface StoredFile {
  binding?: unknown
  key_blob?: unknown
  saved_at?: unknown
}

function isNonEmptyString(v: unknown): v is string {
  return typeof v === 'string' && v.length > 0
}

/** binding 三元组合法性：三个字段都必须是非空字符串（缺一即无法参与精确匹配） */
function isValidBinding(b: unknown): b is KeyBinding {
  if (b === null || typeof b !== 'object') return false
  const obj = b as Record<string, unknown>
  return isNonEmptyString(obj['user_id']) && isNonEmptyString(obj['data_dir']) && isNonEmptyString(obj['algorithm'])
}

/** binding 精确相等（三元组逐一字符串比较，不做路径归一——归一反而可能放过语义变化） */
function sameBinding(a: KeyBinding, b: KeyBinding): boolean {
  return a.user_id === b.user_id && a.data_dir === b.data_dir && a.algorithm === b.algorithm
}

/** 解析存储目录（对齐 matrix.ts 的三态 dir 参数语义）；不可定位 → null */
function resolveDir(dir?: string | null): string | null {
  return dir === undefined ? localDbStateDir() : dir
}

/**
 * 保存主密钥：dpapi.protectText(密钥 hex) → JSON 落盘（同目录临时文件 + rename 覆盖，
 * 对齐 matrix.ts writeLocalState 原子路径）。
 * 返回 false 的情形：dir 不可定位 / keyHex 或 binding 不合法 / dpapi 保护失败 / 写盘失败
 * （全部 best-effort，不抛出——密钥与 binding 不进任何异常消息）。
 */
export async function saveKey(args: SaveKeyArgs, deps: KeystoreDeps = {}): Promise<boolean> {
  const d = resolveDir(args.dir)
  if (!d) return false
  if (!KEY_HEX_RE.test(args.keyHex)) return false
  if (!isValidBinding(args.binding)) return false
  let blob: string
  try {
    blob = await protectText(args.keyHex, deps.runFn)
  } catch {
    return false // dpapi 失败（含 exit≠0 reject）：不外泄 runner 错误细节，落 false
  }
  if (!blob) return false
  const record = {
    binding: { user_id: args.binding.user_id, data_dir: args.binding.data_dir, algorithm: args.binding.algorithm },
    key_blob: blob,
    saved_at: new Date().toISOString(),
  }
  const target = join(d, KEY_FILE_NAME)
  const tmp = join(d, `.${KEY_FILE_NAME}.${process.pid}.${Date.now()}.tmp`)
  try {
    mkdirSync(d, { recursive: true })
    writeFileSync(tmp, `${JSON.stringify(record, null, 2)}\n`, 'utf8')
    renameSync(tmp, target)
    return true
  } catch {
    try {
      unlinkSync(tmp)
    } catch {
      // 临时文件清理失败忽略（不能因清扫掩盖主结果）
    }
    return false
  }
}

/**
 * 读取主密钥。文件缺失/JSON 损坏/shape 不合法/binding 三元组不匹配/DPAPI 解密失败/
 * 解密产物不是合法主密钥 hex → null（binding 不匹配时顺带 clearKey，旧账号残留不留盘；
 * 其余情形保留现场，不臆测清除）。密钥解密后的页 1 先知核验属调用方（本组件不做 crypto）。
 */
export async function loadKey(args: LoadKeyArgs, deps: KeystoreDeps = {}): Promise<StoredKey | null> {
  const d = resolveDir(args.dir)
  if (!d) return null
  if (!isValidBinding(args.binding)) return null
  let raw: unknown
  try {
    raw = JSON.parse(readFileSync(join(d, KEY_FILE_NAME), 'utf8'))
  } catch {
    return null
  }
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) return null
  const record = raw as StoredFile
  if (!isValidBinding(record.binding)) return null
  if (!isNonEmptyString(record.key_blob)) return null
  if (typeof record.saved_at !== 'string') return null
  if (!sameBinding(args.binding, record.binding)) {
    clearKey(d) // 三元组任一变化即失效；旧账号残留不留盘（best-effort，清除结果不影响返回）
    return null
  }
  let keyHex: string
  try {
    keyHex = await unprotectText(record.key_blob, deps.runFn)
  } catch {
    return null // dpapi 失败（exit≠0/损坏 blob）→ 视同失效，调用方走重扫
  }
  if (!KEY_HEX_RE.test(keyHex)) return null
  return { keyHex, savedAt: record.saved_at }
}

/**
 * 清除密钥存储文件。返回 true = 存储已无密钥（删除成功 / 本就不存在 / dir 不可定位——
 * 无可清之物）；删除失败（权限等）→ false。不抛出。
 */
export function clearKey(dir?: string | null): boolean {
  const d = resolveDir(dir)
  if (!d) return true
  try {
    unlinkSync(join(d, KEY_FILE_NAME))
    return true
  } catch (err) {
    return (err as NodeJS.ErrnoException).code === 'ENOENT'
  }
}
