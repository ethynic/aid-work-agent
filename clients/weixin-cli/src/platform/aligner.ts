/** Stable message identities anchored to the acknowledged watermark. Damaged older
 * history is ignored; all already-seen unacknowledged messages must remain.
 * Repeated anchors require distinct, unique context rather than longest overlap.
 */
import { randomUUID } from 'node:crypto'


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

export class ConversationAligner {
  private window: KnownEntry[] = []

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
      if (sigs.length < this.window.length || !this.window.every((e, i) => e.sig===sigs[i]) || bubbles.slice(this.window.length).some(b=>b.unsupported)) {
        return { kind: 'gap', reason: 'alignment_broken' }
      }
      this.window = sigs.map((sig, i) => this.window[i] ?? ({ sig, id: `m-${randomUUID()}`, unsupported:bubbles[i]?.unsupported }))
      return { kind: 'ok', messages: this.materialize(bubbles), anchorMatched: true }
    }
    if (anchorLocalId === null) {
      // 基线：同签名序列的重复基线观察复用 ID；否则整窗新 ID
      if (this.window.length === sigs.length && this.window.every((e, i) => e.sig===sigs[i])) {
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
      if (tail.every((entry, i) => entry.sig === sigs[at + i])) starts.push(at)
    }
    if (!starts.length) return { kind: 'gap', reason: 'alignment_broken' }
    const anchorRepeated = this.window.some((entry, i) => i !== anchorIdx && entry.sig === tail[0]!.sig)
    if (starts.length > 1 || anchorRepeated) {
      // A distinct earlier context message may establish the boundary of a run
      // of repeated replies. Never choose the longest run of identical text.
      const contexts = this.window.slice(0, anchorIdx).map((entry, oldAt) => ({entry,oldAt})).filter(({entry}) =>
        !entry.unsupported && this.window.filter(other => entry.sig === other.sig).length === 1 && sigs.filter(sig => entry.sig === sig).length === 1)
      starts = starts.filter(at => contexts.some(({oldAt}) => {
        const newAt = at - (anchorIdx - oldAt)
        if (newAt < 0) return false
        return this.window.slice(oldAt, anchorIdx).every((prior, i) => !bubbles[newAt + i]!.unsupported && prior.sig === sigs[newAt + i])
      }))
    }
    if (starts.length !== 1) return { kind: 'gap', reason: 'duplicate_unalignable' }
    const at = starts[0]!
    if (bubbles.slice(at+tail.length).some(b => b.unsupported)) return { kind: 'gap', reason: 'alignment_broken' }
    // Retain any immediately preceding recognizable context and its original ID.
    let oldStart = anchorIdx, currentStart = at
    while (oldStart > 0 && currentStart > 0 && !bubbles[currentStart - 1]!.unsupported && this.window[oldStart - 1]!.sig === sigs[currentStart - 1]) {
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
