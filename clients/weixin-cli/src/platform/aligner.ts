/** Stable OCR identities anchored to the acknowledged watermark. Damaged older
 * history is ignored; all already-seen unacknowledged messages must remain.
 * Repeated anchors require distinct, unique context rather than longest overlap.
 */
import { randomUUID } from 'node:crypto'
import { ocrTextMatches } from './ocrTextMatch.js'

export interface AlignBox {
  text: string
  x0: number
  y0: number
  x1: number
  y1: number
}

export interface Bubble {
  unsupported?: boolean
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
  | { kind: 'gap'; reason: 'alignment_broken' | 'sender_ambiguous' | 'duplicate_unalignable' }

interface KnownEntry {
  sig: string
  id: string
  unsupported?: boolean
}

/** All placements of a retained watermark/tail, stopping after ambiguity is proven. */
export function uniqueTailPositions<A, B>(tail: readonly A[], current: readonly B[], matches: (a: A, b: B) => boolean): number[] {
  if (!tail.length) return []
  const positions: number[] = []
  for (let start = 0; start + tail.length <= current.length; start++) {
    if (tail.every((entry, i) => matches(entry, current[start + i]!))) positions.push(start)
    if (positions.length > 1) break
  }
  return positions
}

const LINE_RATIO = 1.2

export class ConversationAligner {
  private window: KnownEntry[] = []

  constructor(private readonly matchText:(first:string,current:string)=>boolean=ocrTextMatches){}

  private matches(first:string,current:string):boolean{
    if(first.startsWith('opaque:')||current.startsWith('opaque:'))return first===current
    const boundary=first.indexOf('\u0000'),other=current.indexOf('\u0000')
    return first.slice(0,boundary)===current.slice(0,other)
      &&this.matchText(first.slice(boundary+1),current.slice(other+1))
  }

  /**
   * bubbles：本轮观察按 y 序重建后的气泡（调用方完成区域映射/裁剪）。
   * anchorLocalId：水位锚（null = 建基线：整窗分配 ID 并记忆，不作为新消息）。
   */
  align(bubbles: Bubble[], anchorLocalId: string | null, emptyBaselineContinuation = false): AlignOutcome {
    if (bubbles.some((b) => b.sender === 'unknown' && !b.unsupported)) {
      return { kind: 'gap', reason: 'sender_ambiguous' }
    }
    // Opaque baseline slots remember order/count only, never pixel identity.
    // Same-position media replacement is outside this text observer's capability.
    const sigs = bubbles.map((b) => b.unsupported ? `opaque:${b.sender}\u0000` : `${b.sender}\u0000${b.text}`)
    if (anchorLocalId === null && emptyBaselineContinuation) {
      // 空基线后的聚合期尚未ACK首条消息：保留已见前缀ID，不再建立新基线。
      // 没有已ACK锚点时不能证明滚动连续性，任何前缀丢失均拒绝。
      if (sigs.length < this.window.length || !this.window.every((e, i) => this.matches(e.sig,sigs[i]!)) || bubbles.slice(this.window.length).some(b=>b.unsupported)) {
        return { kind: 'gap', reason: 'alignment_broken' }
      }
      this.window = sigs.map((sig, i) => this.window[i] ?? ({ sig, id: `m-${randomUUID()}`, unsupported:bubbles[i]?.unsupported }))
      return { kind: 'ok', messages: this.materialize(bubbles), anchorMatched: true }
    }
    if (anchorLocalId === null) {
      // 基线：同签名序列的重复基线观察复用 ID；否则整窗新 ID
      if (this.window.length === sigs.length && this.window.every((e, i) => this.matches(e.sig,sigs[i]!))) {
        return { kind: 'ok', messages: this.materialize(bubbles), anchorMatched: true }
      }
      this.window = sigs.map((sig,i) => ({ sig, id: `m-${randomUUID()}`, unsupported:bubbles[i]?.unsupported }))
      return { kind: 'ok', messages: this.materialize(bubbles), anchorMatched: true }
    }
    const anchorIdx = this.window.findIndex((e) => e.id === anchorLocalId)
    if (anchorIdx < 0) {
      // 锚点不在记忆窗口（滚动断层/重启丢态）：无法对齐 → gap
      return { kind: 'gap', reason: 'alignment_broken' }
    }
    // Only acknowledged history before the watermark may be damaged. Every
    // already-seen, unacknowledged message after it must remain in order.
    const tail = this.window.slice(anchorIdx)
    let starts: number[] = []
    for (let at = 0; at + tail.length <= sigs.length; at++) {
      if (tail.every((entry, i) => this.matches(entry.sig, sigs[at + i]!))) starts.push(at)
    }
    if (!starts.length) return { kind: 'gap', reason: 'alignment_broken' }
    const anchorRepeated = this.window.some((entry, i) => i !== anchorIdx && this.matches(entry.sig, tail[0]!.sig))
    if (starts.length > 1 || anchorRepeated) {
      // A distinct earlier context message may establish the boundary of a run
      // of repeated replies. Never choose the longest run of identical text.
      const contexts = this.window.slice(0, anchorIdx).map((entry, oldAt) => ({entry,oldAt})).filter(({entry}) =>
        !entry.unsupported && this.window.filter(other => this.matches(entry.sig, other.sig)).length === 1 && sigs.filter(sig => this.matches(entry.sig, sig)).length === 1)
      starts = starts.filter(at => contexts.some(({oldAt}) => {
        const newAt = at - (anchorIdx - oldAt)
        if (newAt < 0) return false
        return this.window.slice(oldAt, anchorIdx).every((prior, i) => !bubbles[newAt + i]!.unsupported && this.matches(prior.sig, sigs[newAt + i]!))
      }))
    }
    if (starts.length !== 1) return { kind: 'gap', reason: 'duplicate_unalignable' }
    const at = starts[0]!
    if (bubbles.slice(at+tail.length).some(b => b.unsupported)) return { kind: 'gap', reason: 'alignment_broken' }
    // Retain any immediately preceding recognizable context and its original ID.
    let oldStart = anchorIdx, currentStart = at
    while (oldStart > 0 && currentStart > 0 && !bubbles[currentStart - 1]!.unsupported && this.matches(this.window[oldStart - 1]!.sig, sigs[currentStart - 1]!)) {
      oldStart--; currentStart--
    }
    const retained = this.window.slice(oldStart)
    const current = bubbles.slice(currentStart)
    const currentSigs = sigs.slice(currentStart)
    this.window = currentSigs.map((sig, i) => retained[i] ?? ({ sig, id: `m-${randomUUID()}` }))
    return { kind: 'ok', messages: this.materialize(current), anchorMatched: true }
  }

  private materialize(bubbles: Bubble[]): AlignedMessage[] {
    if (this.window.length !== bubbles.length) {
      // 理论不可达（基线分支已同步 window）；防御：视为断层
      return []
    }
    return bubbles.flatMap((b, i) => this.window[i]!.unsupported ? [] : [{
      sender: b.sender as AlignedMessage['sender'],
      text: this.window[i]!.sig.slice(this.window[i]!.sig.indexOf('\u0000')+1),
      local_message_id: (this.window[i] as KnownEntry).id,
    }])
  }

  /** 最近对齐窗口的末位 ID（供调用方推进水位） */
  get lastKnownId(): string | null {
    return [...this.window].reverse().find(entry=>!entry.unsupported)?.id ?? null
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
