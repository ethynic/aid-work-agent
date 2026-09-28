/**
 * target_ref：搜索结果的短期不透明目标 handle（与 weixin-cli 同源机制）。
 *
 * 格式：base64url(JSON payload) + "." + base64url(HMAC-SHA256(payload))
 * payload：{v:1, name, type, subtitle, x?, y?, exp}（exp = 签发 + 5 分钟，unix 秒）。
 * - name：结果项名称（联系人/群名）；
 * - type：目标分类（contact/group/other，由搜索结果分区推导）；
 * - subtitle：结果项副标题（如「微信联系人」「其他（待设置部门）」），
 *   同名多项时 message_send 用它 + 分区做精确匹配，消歧失败 → TARGET_AMBIGUOUS；
 * - x/y：M4 起可选——搜索结果条目在 overlay 图像内的相对坐标（select 消费用）。
 *   老签发的 ref 没有这对字段，verify 侧不因未知/缺失字段失败（只校验已知字段）；
 *   message-send 只读 name/subtitle/type，不受新增字段影响。
 *
 * 密钥：%LOCALAPPDATA%\AidWorkAgent\wecom-cli\target-ref.key，首次使用随机生成
 * 32 字节（base64 落盘，权限尽量仅当前用户），之后复用；密钥只存本机，ref 不可跨机验证。
 *
 * 校验失败语义（operation 永不 reject，错误经 errorMapping 进 OperationResult）：
 * - 过期 → CodedOperationError('TARGET_REF_STALE')
 * - 签名/格式错误（含篡改）→ CodedOperationError('INVALID_ARGUMENT')
 */
import { createHmac, randomBytes, timingSafeEqual } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { CodedOperationError } from '../operations/types.js'

/** ref 有效期：签发后 5 分钟 */
export const TARGET_REF_TTL_SECONDS = 300

export interface TargetRefIdentity {
  name: string
  type: string
  /** 搜索结果副标题（消歧用；可能为空字符串） */
  subtitle: string
  /** 搜索结果条目在 overlay 图像内的相对坐标（M4 起可选；老 ref 签发时无此对字段） */
  x?: number
  y?: number
}

interface TargetRefPayload {
  v: number
  name: string
  type: string
  subtitle: string
  x?: number
  y?: number
  exp: number
}

export interface TargetRefOptions {
  /** 测试注入：密钥目录（缺省 %LOCALAPPDATA%\AidWorkAgent\wecom-cli） */
  keyDir?: string
  /** 测试注入：当前时间（unix 秒） */
  now?: number
}

/** createTargetRef 专用选项：在 TargetRefOptions 基础上可携带条目 overlay 相对坐标 */
export interface CreateTargetRefOptions extends TargetRefOptions {
  coords?: TargetRefCoords
}

export type CreateTargetRefFn = (
  name: string,
  type: string,
  subtitle?: string,
  /** 条目 overlay 相对坐标（M4 起；缺省签发的 payload 不含 x/y，向后兼容） */
  coords?: TargetRefCoords,
) => string
export type VerifyTargetRefFn = (ref: string) => TargetRefIdentity

/** overlay 图像内相对坐标（search 结果条目行中心，select 点击换算用） */
export interface TargetRefCoords {
  x: number
  y: number
}

/** 密钥目录（本机密钥签名，ref 不可跨机验证） */
export function targetRefKeyDir(localAppData: string | undefined = process.env.LOCALAPPDATA): string {
  if (!localAppData) {
    throw new CodedOperationError('INTERNAL_ERROR', '%LOCALAPPDATA% 未设置，无法管理 target_ref 本机密钥')
  }
  return join(localAppData, 'AidWorkAgent', 'wecom-cli')
}

/** 读取或首次生成 32 字节本机密钥（base64 落盘；损坏视为实现/部署错误） */
function loadOrCreateKey(keyDir: string): Buffer {
  mkdirSync(keyDir, { recursive: true })
  const keyPath = join(keyDir, 'target-ref.key')
  if (existsSync(keyPath)) {
    const key = Buffer.from(readFileSync(keyPath, 'utf8').trim(), 'base64')
    if (key.length === 32) return key
    throw new CodedOperationError('INTERNAL_ERROR', 'target_ref 密钥文件损坏（长度非法），请删除后重试')
  }
  const key = randomBytes(32)
  // mode 0o600 尽力限制为当前用户（Windows ACL 下 Node mode 仅尽语义义务）
  writeFileSync(keyPath, key.toString('base64'), { mode: 0o600 })
  return key
}

function sign(body: string, key: Buffer): string {
  return createHmac('sha256', key).update(body, 'utf8').digest('base64url')
}

/** 签发 target_ref（type：contact/group/other；subtitle 缺省空串；opts.coords 缺省不含坐标字段） */
export function createTargetRef(name: string, type: string, subtitle = '', opts: CreateTargetRefOptions = {}): string {
  const now = opts.now ?? Math.floor(Date.now() / 1000)
  const payload: TargetRefPayload = { v: 1, name, type, subtitle, exp: now + TARGET_REF_TTL_SECONDS }
  // 坐标成对写入（undefined 字段 JSON.stringify 自动省略，老格式 ref 完全兼容）
  if (opts.coords) {
    payload.x = opts.coords.x
    payload.y = opts.coords.y
  }
  const body = Buffer.from(JSON.stringify(payload), 'utf8').toString('base64url')
  return `${body}.${sign(body, loadOrCreateKey(opts.keyDir ?? targetRefKeyDir()))}`
}

/** 验证 target_ref 并解出目标身份；过期/篡改/格式错误抛 CodedOperationError */
export function verifyTargetRef(ref: string, opts: TargetRefOptions = {}): TargetRefIdentity {
  if (typeof ref !== 'string') {
    throw new CodedOperationError('INVALID_ARGUMENT', 'target_ref 必须是字符串')
  }
  const parts = ref.split('.')
  if (parts.length !== 2 || !parts[0] || !parts[1]) {
    throw new CodedOperationError('INVALID_ARGUMENT', 'target_ref 格式非法（应为 <payload>.<signature>）')
  }
  const [body, sig] = parts as [string, string]
  const expected = sign(body, loadOrCreateKey(opts.keyDir ?? targetRefKeyDir()))
  const sigBuf = Buffer.from(sig, 'utf8')
  const expectedBuf = Buffer.from(expected, 'utf8')
  if (sigBuf.length !== expectedBuf.length || !timingSafeEqual(sigBuf, expectedBuf)) {
    throw new CodedOperationError('INVALID_ARGUMENT', 'target_ref 签名校验失败（无效或已被篡改）')
  }
  let payload: TargetRefPayload
  try {
    payload = JSON.parse(Buffer.from(body, 'base64url').toString('utf8')) as TargetRefPayload
  } catch {
    throw new CodedOperationError('INVALID_ARGUMENT', 'target_ref payload 无法解析')
  }
  if (
    payload === null ||
    typeof payload !== 'object' ||
    payload.v !== 1 ||
    typeof payload.name !== 'string' ||
    payload.name.length === 0 ||
    typeof payload.type !== 'string' ||
    typeof payload.subtitle !== 'string' ||
    typeof payload.exp !== 'number'
  ) {
    throw new CodedOperationError('INVALID_ARGUMENT', 'target_ref payload 结构非法')
  }
  const now = opts.now ?? Math.floor(Date.now() / 1000)
  if (payload.exp <= now) {
    throw new CodedOperationError('TARGET_REF_STALE', 'target_ref 已过期（有效期 5 分钟），请重新搜索获取')
  }
  const identity: TargetRefIdentity = { name: payload.name, type: payload.type, subtitle: payload.subtitle }
  // 坐标可选：成对出现且均为数字才透出（老 ref 无此对字段；未知字段不导致失败）
  if (typeof payload.x === 'number' && typeof payload.y === 'number') {
    identity.x = payload.x
    identity.y = payload.y
  } else if (payload.x !== undefined || payload.y !== undefined) {
    throw new CodedOperationError('INVALID_ARGUMENT', 'target_ref payload 坐标字段非法（x/y 必须成对为数字）')
  }
  return identity
}
