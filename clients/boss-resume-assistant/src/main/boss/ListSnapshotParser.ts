/**
 * 推荐列表解析器（设计文档 §7）。
 * - 从 DOMSnapshot 定位候选卡片正文安全点击点
 * - 避开打招呼等写动作按钮（点击卡片左侧正文区域）
 * - 卡片必须完整落入视口安全内容区（顶部 inset 150px、底部 inset 20px）
 * - 无法唯一定位 → UNLOCATABLE，不猜坐标
 * - 生成候选人 HMAC 指纹（设计文档 §7.2）
 *
 * 重要：BOSS 页面动态变化。每次操作前必须获取 fresh snapshot 重新确认目标，
 * 禁止复用上一次的姓名、节点或坐标。
 */
import { createHmac } from 'node:crypto'
import {
  type DomSnapshot,
  type ClickPoint,
  stringIndexOf,
  findNodesByString,
  accumulateOwnerOffset,
  boundsCenter,
} from './domSnapshot.js'

/** 招聘写动作文案（点击点不能落在这些文字上） */
export const WRITE_ACTION_TEXT = /打招呼|沟通|不合适|不感兴趣|发送|确认|确定|邀约|约面|交换|电话|微信/i

/** 视口安全内容区 inset（设计文档 §7.1：顶部被固定 header 遮挡） */
export const SAFE_INSET_TOP = 150
export const SAFE_INSET_BOTTOM = 20

export type LocateResult =
  | { status: 'LOCATED'; point: ClickPoint; fingerprint: string; name: string }
  | { status: 'UNLOCATABLE'; reason: string }

export interface LocateOptions {
  /** 根文档视口尺寸（用于安全区判断） */
  viewport: { width: number; height: number }
  /** HMAC 指纹密钥（本地去重用，不可作为全局身份） */
  fingerprintKey: string
  /** 额外指纹字段（岗位ID等），默认空 */
  fingerprintContext?: string
}

export class ListSnapshotParser {
  /** 校验候选文本不是招聘写动作 */
  static assertSafeCandidateText(text: string): void {
    if (typeof text !== 'string' || !text.trim()) {
      throw new Error('candidate text is empty')
    }
    if (WRITE_ACTION_TEXT.test(text)) {
      throw new Error('candidate text resembles a recruiting write action')
    }
  }

  /**
   * 在 snapshot 中定位唯一候选文本的安全点击点。
   * - 文本必须在 snapshot 中存在且唯一可见
   * - 点击点（文本 bounds 中心 + owner 偏移）必须落入视口安全内容区
   * - 不满足返回 UNLOCATABLE，绝不猜坐标
   */
  locateUniqueCandidate(snapshot: DomSnapshot, candidateName: string, opts: LocateOptions): LocateResult {
    try {
      ListSnapshotParser.assertSafeCandidateText(candidateName)
    } catch (e) {
      return { status: 'UNLOCATABLE', reason: (e as Error).message }
    }

    const stringIndex = safeStringIndexOf(snapshot, candidateName)
    if (stringIndex < 0) {
      return { status: 'UNLOCATABLE', reason: 'candidate text absent from snapshot' }
    }

    // 收集所有可见匹配（跨 document）
    const matches: Array<{ documentIndex: number; bounds: [number, number, number, number] }> = []
    snapshot.documents.forEach((document, documentIndex) => {
      for (const { bounds } of findNodesByString(document, stringIndex)) {
        matches.push({ documentIndex, bounds })
      }
    })
    if (matches.length !== 1) {
      return { status: 'UNLOCATABLE', reason: `candidate text must have exactly one visible match; found ${matches.length}` }
    }

    const match = matches[0]!
    let offset
    try {
      offset = accumulateOwnerOffset(snapshot, match.documentIndex)
    } catch (e) {
      return { status: 'UNLOCATABLE', reason: (e as Error).message }
    }

    const center = boundsCenter(match.bounds)
    const point: ClickPoint = { x: offset.x + center.x, y: offset.y + center.y }

    const [x, y, w, h] = match.bounds
    if (![x, y, w, h, point.x, point.y].every(Number.isFinite) || w <= 0 || h <= 0 || point.x < 0 || point.y < 0) {
      return { status: 'UNLOCATABLE', reason: 'candidate has no safe visible click area' }
    }

    // 安全区校验：点击点必须在视口安全内容区内（避开顶部 header / 底部操作栏）
    if (
      point.y < SAFE_INSET_TOP ||
      point.y > opts.viewport.height - SAFE_INSET_BOTTOM ||
      point.x < 0 ||
      point.x > opts.viewport.width
    ) {
      return { status: 'UNLOCATABLE', reason: `click point ${point.x},${point.y} outside safe viewport area` }
    }

    const fingerprint = makeFingerprint(opts.fingerprintKey, opts.fingerprintContext ?? '', candidateName)
    return { status: 'LOCATED', point, fingerprint, name: candidateName }
  }
}

/** 安全的 indexOf：找不到返回 -1 而非抛错（locateUniqueCandidate 用 UNLOCATABLE 处理） */
function safeStringIndexOf(snapshot: DomSnapshot, text: string): number {
  try {
    return stringIndexOf(snapshot, text)
  } catch {
    return -1
  }
}

/** HMAC-SHA256 指纹（前 16 hex） */
export function makeFingerprint(key: string, context: string, name: string): string {
  return createHmac('sha256', key).update(`${context}|${name}`).digest('hex').slice(0, 16)
}
