/**
 * 会话窗口对齐器（C2，设计 §6；语义与 C0 冻结契约一致）。
 *
 * - 拆行重建：相邻同侧文本框（y 间距 < 行高×1.2）按 y/x 序合并为同一气泡。
 * - 稳定本地 ID：对齐器记住最近已对齐窗口的（签名→ID）序列；重复观察同一
 *   窗口复用既有 ID；水位锚点之后的新气泡分配新 ID（m-<uuid>）。
 * - 重复项序号：连续相同签名按出现位置逐一对位（10 条相同文本 = 10 个 ID）。
 * - 水位锚点不在已记忆窗口 → 无法对齐 → gap（alignment_broken，阻断不猜测；
 *   对应重启断档场景，需显式重建基线）。
 * - sender 无法判定 → unknown → 调用方必须整体降级 gap（契约不变量）。
 */
import { randomUUID } from 'node:crypto'

export interface AlignBox {
  text: string
  x0: number
  y0: number
  x1: number
  y1: number
}

export interface Bubble {
  sender: 'peer' | 'self' | 'system' | 'unknown'
  text: string
}

export interface AlignedMessage {
  sender: 'peer' | 'self' | 'system'
  text: string
  local_message_id: string
}

export type AlignOutcome =
  | { kind: 'ok'; messages: AlignedMessage[]; anchorMatched: boolean }
  | { kind: 'gap'; reason: 'alignment_broken' | 'sender_ambiguous' }

interface KnownEntry {
  sig: string
  id: string
}

const LINE_RATIO = 1.2

export class ConversationAligner {
  private window: KnownEntry[] = []

  /**
   * bubbles：本轮观察按 y 序重建后的气泡（调用方完成区域映射/裁剪）。
   * anchorLocalId：水位锚（null = 建基线：整窗分配 ID 并记忆，不作为新消息）。
   */
  align(bubbles: Bubble[], anchorLocalId: string | null): AlignOutcome {
    if (bubbles.some((b) => b.sender === 'unknown')) {
      return { kind: 'gap', reason: 'sender_ambiguous' }
    }
    const sigs = bubbles.map((b) => `${b.sender}\u0000${b.text}`)
    if (anchorLocalId === null) {
      // 基线：同签名序列的重复基线观察复用 ID；否则整窗新 ID
      if (this.window.length === sigs.length && this.window.every((e, i) => e.sig === sigs[i])) {
        return { kind: 'ok', messages: this.materialize(bubbles), anchorMatched: true }
      }
      this.window = sigs.map((sig) => ({ sig, id: `m-${randomUUID()}` }))
      return { kind: 'ok', messages: this.materialize(bubbles), anchorMatched: true }
    }
    const anchorIdx = this.window.findIndex((e) => e.id === anchorLocalId)
    if (anchorIdx < 0) {
      // 锚点不在记忆窗口（滚动断层/重启丢态）：无法对齐 → gap
      return { kind: 'gap', reason: 'alignment_broken' }
    }
    // 前缀校验：新窗口必须以已记忆前缀（至锚点）开始（按签名逐一对位）
    const prefixLen = anchorIdx + 1
    if (sigs.length < prefixLen || !sigs.slice(0, prefixLen).every((s, i) => s === this.window[i]!.sig)) {
      return { kind: 'gap', reason: 'alignment_broken' }
    }
    // 后缀 = 新消息：优先复用已记忆后缀的 ID（评审 P1-8：聚合期间引擎不推水位，
    // 同一后缀会被重复观察——签名对位一致的条目必须复用既有 ID，仅真正新增的
    // 条目分配新 ID），再推进记忆窗口
    const suffix: KnownEntry[] = []
    for (let i = prefixLen; i < sigs.length; i++) {
      const remembered = this.window[i]
      const sig = sigs[i] as string
      if (remembered && remembered.sig === sig) {
        suffix.push({ sig, id: remembered.id })
      } else {
        suffix.push({ sig, id: `m-${randomUUID()}` })
      }
    }
    const ids = [...this.window.slice(0, prefixLen).map((e) => e.id), ...suffix.map((e) => e.id)]
    this.window = sigs.map((sig, i) => ({ sig, id: ids[i] as string }))
    const messages: AlignedMessage[] = bubbles.map((b, i) => ({
      sender: b.sender as AlignedMessage['sender'],
      text: b.text,
      local_message_id: ids[i] as string,
    }))
    return { kind: 'ok', messages, anchorMatched: true }
  }

  private materialize(bubbles: Bubble[]): AlignedMessage[] {
    if (this.window.length !== bubbles.length) {
      // 理论不可达（基线分支已同步 window）；防御：视为断层
      return []
    }
    return bubbles.map((b, i) => ({
      sender: b.sender as AlignedMessage['sender'],
      text: b.text,
      local_message_id: (this.window[i] as KnownEntry).id,
    }))
  }

  /** 最近对齐窗口的末位 ID（供调用方推进水位） */
  get lastKnownId(): string | null {
    return this.window.length > 0 ? (this.window[this.window.length - 1] as KnownEntry).id : null
  }
}

/**
 * OCR 框 → 气泡：按 y 聚合拆行重建（同侧相邻行合并），左右分区推断 sender。
 * side 判定基于区域配置（selfRightRegionX：自己气泡起点阈值）；判定不保守的
 * 框返回 unknown 由对齐器整体降级。系统消息（居中短行）标记 system。
 */
export function boxesToBubbles(
  boxes: AlignBox[],
  region: { minX: number; maxX: number; selfStartX: number },
): Bubble[] {
  const sorted = [...boxes].sort((a, b) => a.y0 - b.y0 || a.x0 - b.x0)
  const heights = sorted.map((b) => Math.max(1, b.y1 - b.y0)).sort((a, b) => a - b)
  const lineH = heights[Math.floor(heights.length / 2)] ?? 20
  const lines: Array<{ sender: 'peer' | 'self' | 'system' | 'unknown'; parts: AlignBox[] }> = []
  for (const box of sorted) {
    const center = (box.x0 + box.x1) / 2
    const regionCenter = (region.minX + region.maxX) / 2
    let sender: Bubble['sender']
    if (box.x0 >= region.selfStartX) sender = 'self'
    else if (Math.abs(center - regionCenter) < (region.maxX - region.minX) * 0.08 && box.x1 - box.x0 < (region.maxX - region.minX) * 0.6) {
      sender = 'system' // 居中短行（时间/系统提示）
    } else if (box.x0 < region.selfStartX) sender = 'peer'
    else sender = 'unknown'
    const last = lines[lines.length - 1]
    if (last && last.sender === sender && Math.abs(box.y0 - (last.parts[last.parts.length - 1] as AlignBox).y0) < lineH * LINE_RATIO) {
      last.parts.push(box)
    } else {
      lines.push({ sender, parts: [box] })
    }
  }
  return lines.map((line) => ({
    sender: line.sender,
    text: line.parts
      .slice()
      .sort((a, b) => a.y0 - b.y0 || a.x0 - b.x0)
      .map((p) => p.text)
      .join(''),
  }))
}
