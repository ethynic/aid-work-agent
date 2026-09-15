/** Conservative text-bubble segmentation for a calibrated desktop layout.
 * No UI, identity verification, file persistence or completeness claim is made here.
 * Layout/color calibration belongs to the trusted capture layer, never model args.
 */
import type { AlignBox, Bubble } from './aligner.js'

export interface PixelFrame { width: number; height: number; rgba: Uint8Array }
export interface Bounds { x0: number; y0: number; x1: number; y1: number }
export interface BubbleLayout {
  messageRegion: Bounds
  peerLeft: number
  selfRight: number
  edgeTolerance: number
  peerFill: readonly [number, number, number]
  selfFill: readonly [number, number, number]
  minWidth: number
  minHeight: number
}
export interface TextBubbleRegion { bounds: Bounds; sender: 'peer' | 'self' }

/** Find solid-background candidates docked to a supplied bubble edge.
 * Color and docking are heuristics, not media-type or identity authentication.
 * Images can visually imitate text bubbles; callers must not treat candidates
 * as proof of text-message provenance or viewport completeness.
 * An excluded item is not proof that the viewport contains no new messages.
 */
export function segmentTextBubbles(frame: PixelFrame, layout: BubbleLayout): TextBubbleRegion[] {
  const { width, height, rgba } = frame
  const r = layout.messageRegion
  const validColor = (c: readonly number[]) => c.length === 3 && c.every(v => Number.isInteger(v) && v >= 0 && v <= 255) && c[1]! > 0
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 ||
      width * height > 16_000_000 || rgba.length !== width * height * 4 ||
      ![r.x0, r.y0, r.x1, r.y1, layout.peerLeft, layout.selfRight, layout.edgeTolerance, layout.minWidth, layout.minHeight].every(Number.isInteger) ||
      r.x0 < 0 || r.y0 < 0 || r.x1 > width || r.y1 > height || r.x0 >= r.x1 || r.y0 >= r.y1 ||
      layout.edgeTolerance < 0 || layout.minWidth < 1 || layout.minHeight < 1 ||
      layout.peerLeft >= layout.selfRight || layout.peerLeft <= r.x0 || layout.selfRight >= r.x1 ||
      !validColor(layout.peerFill) || !validColor(layout.selfFill) || layout.peerFill.every((v, i) => v === layout.selfFill[i])) throw new Error('Invalid pixel frame or layout')
  const kinds = new Uint8Array(width * height)
  const match = (i: number, c: readonly number[]) => c.every((v, k) => Math.abs(rgba[i * 4 + k]! - v) <= 2)
  for (let y = r.y0; y < r.y1; y++) for (let x = r.x0; x < r.x1; x++) {
    const i = y * width + x
    kinds[i] = match(i, layout.peerFill) ? 1 : match(i, layout.selfFill) ? 2 : 0
  }
  const queue = new Int32Array(width * height)
  const results: TextBubbleRegion[] = []
  for (let y = r.y0; y < r.y1; y++) for (let x = r.x0; x < r.x1; x++) {
    const root = y * width + x, kind = kinds[root]!
    if (!kind) continue
    let head = 0, tail = 1, x0 = x, x1 = x, y0 = y, y1 = y
    queue[0] = root; kinds[root] = 0
    while (head < tail) {
      const i = queue[head++]!, px = i % width, py = Math.floor(i / width)
      x0 = Math.min(x0, px); x1 = Math.max(x1, px); y0 = Math.min(y0, py); y1 = Math.max(y1, py)
      for (const n of [px > r.x0 ? i - 1 : -1, px + 1 < r.x1 ? i + 1 : -1,
        py > r.y0 ? i - width : -1, py + 1 < r.y1 ? i + width : -1]) {
        if (n >= 0 && kinds[n] === kind) { kinds[n] = 0; queue[tail++] = n }
      }
    }
    const bw = x1 - x0 + 1, bh = y1 - y0 + 1
    if (bw < layout.minWidth || bh < layout.minHeight || x0 <= r.x0 || x1 >= r.x1 - 1 || y0 <= r.y0 || y1 >= r.y1 - 1) continue
    if (Math.abs((kind === 1 ? x0 : x1 + 1) - (kind === 1 ? layout.peerLeft : layout.selfRight)) > layout.edgeTolerance) continue
    if (tail / (bw * bh) < 0.7) continue
    // Text anti-aliasing is a mix of the fill and near-black ink. Bright/colorful
    // non-fill interiors indicate media, icons or unsupported content.
    const fill = kind === 1 ? layout.peerFill : layout.selfFill
    let foreign = 0
    // Rounded exterior corners are page background, not contents.
    const inset = Math.min(16, Math.floor(Math.min(bw, bh) / 4))
    for (let iy = y0 + inset; iy < y1 - inset; iy++) for (let ix = x0 + inset; ix < x1 - inset; ix++) {
      const i = (iy * width + ix) * 4
      const scale = Math.min(1, rgba[i + 1]! / fill[1])
      if ([0, 1, 2].some(k => Math.abs(rgba[i + k]! - fill[k]! * scale) > 18)) foreign++
    }
    if (foreign > bw * bh * 0.003) continue
    results.push({ bounds: { x0, y0, x1: x1 + 1, y1: y1 + 1 }, sender: kind === 1 ? 'peer' : 'self' })
  }
  return results.sort((a, b) => a.bounds.y0 - b.bounds.y0)
}

export type CandidateTextResult =
  | { kind: 'candidates'; messages: Bubble[] }
  | { kind: 'gap'; messages: []; reason: 'invalid_geometry' | 'overlapping_boxes' | 'crossing_boundary' | 'missing_ocr' | 'resource_limit' }

/** Assemble OCR for candidates only. Success is NOT complete_window: candidate
 * detection cannot authenticate media types or prove whole-viewport coverage.
 * Any damaged candidate causes an all-or-nothing gap, never partial text output.
 */
export function textFromBubbleRegions(regions: TextBubbleRegion[], boxes: AlignBox[]): CandidateTextResult {
  const gap = (reason: Extract<CandidateTextResult, { kind: 'gap' }>['reason']): CandidateTextResult => ({ kind: 'gap', messages: [], reason })
  if (regions.length > 512 || boxes.length > 2048 || boxes.reduce((n, b) => n + (typeof b.text === 'string' ? b.text.length : 0), 0) > 20_000) return gap('resource_limit')
  const valid = (b: Bounds) => [b.x0, b.x1, b.y0, b.y1].every(Number.isFinite) && b.x0 >= 0 && b.y0 >= 0 && b.x0 < b.x1 && b.y0 < b.y1
  const overlap = (a: Bounds, b: Bounds) => a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0
  if (regions.some(r => !valid(r.bounds) || !['peer', 'self'].includes(r.sender)) ||
      boxes.some(b => !valid(b) || typeof b.text !== 'string' || !b.text.trim())) return gap('invalid_geometry')
  if (regions.some((r, i) => regions.slice(i + 1).some(other => overlap(r.bounds, other.bounds)))) return gap('invalid_geometry')
  // Each OCR box belongs to exactly one bubble by its center. OCR rectangles
  // may overlap or extend slightly across a bubble edge without losing text.
  const assigned = regions.map(() => [] as AlignBox[])
  for (const box of boxes) {
    const x = (box.x0 + box.x1) / 2, y = (box.y0 + box.y1) / 2
    const owner = regions.findIndex(r => x >= r.bounds.x0 && x < r.bounds.x1 && y >= r.bounds.y0 && y < r.bounds.y1)
    if (owner >= 0) assigned[owner]!.push(box)
  }
  const messages: Bubble[] = []
  for (const [index, region] of regions.entries()) {
    const items = assigned[index]!
    if (!items.length) return gap('missing_ocr')
    const rows: AlignBox[][] = []
    for (const box of items.sort((a, b) => a.y0 - b.y0 || a.x0 - b.x0)) {
      const row = rows.find(parts => parts.every(other =>
        Math.min(box.y1, other.y1) - Math.max(box.y0, other.y0) >= Math.min(box.y1 - box.y0, other.y1 - other.y0) * 0.5))
      if (row) row.push(box)
      else rows.push([box])
    }
    const text = rows.map(row => row.sort((a, b) => a.x0 - b.x0).map(box => box.text).join(' ')).join('\n')
    messages.push({ sender: region.sender, text })
  }
  return { kind: 'candidates', messages }
}
