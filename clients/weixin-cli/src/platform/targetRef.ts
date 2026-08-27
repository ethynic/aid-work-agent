/**
 * target_ref：好友/群/公众号的短期不透明目标 handle（设计 §4.2/§5.3）。
 *
 * 格式：base64url(JSON payload) + "." + base64url(HMAC-SHA256(payload))
 * payload：{v:1, name, type, exp}（exp = 签发 + 5 分钟，unix 秒）。
 *
 * 密钥：%LOCALAPPDATA%\aid-weixin\target-ref.key，首次使用随机生成 32 字节
 * （base64 落盘，权限尽量仅当前用户），之后复用；密钥只存本机，ref 不可跨机验证。
 *
 * 校验失败语义（operation 永不 reject，错误经 errorMapping 进 OperationResult）：
 * - 过期 → CodedOperationError('TARGET_REF_STALE')
 * - 签名/格式错误（含篡改）→ CodedOperationError('INVALID_ARGUMENT')
 */
import { createHmac, randomBytes, timingSafeEqual } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { CodedOperationError } from '../operations/types.js'

/** ref 有效期：签发后 5 分钟（设计 §5.3 默认 TTL） */
export const TARGET_REF_TTL_SECONDS = 300

export interface TargetRefIdentity {
  name: string
  type: string
}

interface TargetRefPayload {
  v: number
  name: string
  type: string
  exp: number
}

export interface TargetRefOptions {
  /** 测试注入：密钥目录（缺省 %LOCALAPPDATA%\aid-weixin） */
  keyDir?: string
  /** 测试注入：当前时间（unix 秒） */
  now?: number
}

export type CreateTargetRefFn = (name: string, type: string) => string
export type VerifyTargetRefFn = (ref: string) => TargetRefIdentity

/** 密钥目录（设计 §5.3：本机密钥签名） */
export function targetRefKeyDir(localAppData: string | undefined = process.env.LOCALAPPDATA): string {
  if (!localAppData) {
    throw new CodedOperationError('INTERNAL_ERROR', '%LOCALAPPDATA% 未设置，无法管理 target_ref 本机密钥')
  }
  return join(localAppData, 'aid-weixin')
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

/** 签发 target_ref（type：friend/group/other 等目标分类标签） */
export function createTargetRef(name: string, type: string, opts: TargetRefOptions = {}): string {
  const now = opts.now ?? Math.floor(Date.now() / 1000)
  const payload: TargetRefPayload = { v: 1, name, type, exp: now + TARGET_REF_TTL_SECONDS }
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
    typeof payload.exp !== 'number'
  ) {
    throw new CodedOperationError('INVALID_ARGUMENT', 'target_ref payload 结构非法')
  }
  const now = opts.now ?? Math.floor(Date.now() / 1000)
  if (payload.exp <= now) {
    throw new CodedOperationError('TARGET_REF_STALE', 'target_ref 已过期（有效期 5 分钟），请重新搜索获取')
  }
  return { name: payload.name, type: payload.type }
}
