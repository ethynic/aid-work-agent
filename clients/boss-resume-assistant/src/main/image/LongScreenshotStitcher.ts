/**
 * 长图拼接器（设计文档 §8.2）。
 * - 相邻分片用重叠区域搜索最佳垂直位移
 * - 归一化均方误差评分，取最小者
 * - 断层/错位检测：最小误差超过阈值或位移异常 → 失败，保留分片进人工复核
 * - 纯像素计算，不依赖 Chrome
 *
 * 输入分片须等宽。位移搜索范围：minOverlap ~ maxOverlap（下图顶部与上图底部的重叠行数）。
 */
import { PNG } from 'pngjs'

export interface StitchResult {
  ok: boolean
  png?: Buffer
  width: number
  height: number
  shardCount: number
  offsets: number[] // 每片在长图中的起始 y（第 0 片为 0）
  integrity: 'CONTINUOUS' | 'GAP_DETECTED' | 'STITCH_FAILED'
  reason?: string
}

export interface StitcherOptions {
  /** 最小重叠行数（默认视口高度 25%） */
  minOverlap?: number
  /** 最大重叠行数（默认视口高度 75%） */
  maxOverlap?: number
  /** 每像素均方误差阈值，超过判为断层（0~255² 范围） */
  mismatchThreshold?: number
  /** 搜索步长（跳行采样加速，默认 1） */
  step?: number
}

/** 解码 PNG buffer 为 RGBA 像素 */
export function decodePng(buf: Buffer): { width: number; height: number; data: Buffer } {
  const png = PNG.sync.read(buf)
  return { width: png.width, height: png.height, data: png.data }
}

export function encodePng(width: number, height: number, data: Buffer): Buffer {
  // pngjs 构造不支持 data 参数（会忽略），必须创建后手动填 data
  const png = new PNG({ width, height })
  data.copy(png.data)
  return PNG.sync.write(png)
}

export class LongScreenshotStitcher {
  private readonly minOverlap: number
  private readonly maxOverlap: number
  private readonly mismatchThreshold: number
  private readonly step: number

  constructor(opts: StitcherOptions = {}) {
    this.minOverlap = opts.minOverlap ?? 0
    this.maxOverlap = opts.maxOverlap ?? 0
    this.mismatchThreshold = opts.mismatchThreshold ?? 400 // per-pixel MSE（约 20 灰度）
    this.step = opts.step ?? 1
  }

  /**
   * 拼接分片列表。返回完整长图或失败结果。
   * 位移定义：next 片从 prev 片的第 (prevHeight - overlap) 行开始重叠，
   * 即 next 在长图中的 y 偏移 = prev 偏移 + prevHeight - overlap。
   */
  stitch(shards: Buffer[]): StitchResult {
    if (shards.length === 0) {
      return { ok: false, width: 0, height: 0, shardCount: 0, offsets: [], integrity: 'STITCH_FAILED', reason: 'no shards' }
    }
    if (shards.length === 1) {
      const { width, height } = decodePng(shards[0]!)
      return { ok: true, png: shards[0], width, height, shardCount: 1, offsets: [0], integrity: 'CONTINUOUS' }
    }

    const decoded = shards.map((s) => decodePng(s))
    const width = decoded[0]!.width
    if (!decoded.every((d) => d.width === width)) {
      return { ok: false, width, height: 0, shardCount: shards.length, offsets: [], integrity: 'STITCH_FAILED', reason: 'shard width mismatch' }
    }

    const offsets: number[] = [0]
    const overlaps: number[] = []
    for (let i = 1; i < decoded.length; i++) {
      const prev = decoded[i - 1]!
      const next = decoded[i]!
      const search = this.findBestOverlap(prev, next)
      if (!search.ok) {
        return {
          ok: false,
          width,
          height: 0,
          shardCount: shards.length,
          offsets,
          integrity: 'GAP_DETECTED',
          reason: `gap/offset between shard ${i - 1} and ${i}: ${search.reason}`,
        }
      }
      overlaps.push(search.overlap)
      offsets.push(offsets[offsets.length - 1]! + prev.height - search.overlap)
    }

    // 合成
    const totalHeight = offsets[offsets.length - 1]! + decoded[decoded.length - 1]!.height
    const out = Buffer.alloc(width * totalHeight * 4)
    for (let i = 0; i < decoded.length; i++) {
      const { height, data } = decoded[i]!
      const yOff = offsets[i]!
      // 末行去重：非首片只画从 overlap 行之后的新内容？不必，逐像素覆盖即可，重叠区会被后片覆盖
      for (let y = 0; y < height; y++) {
        const srcStart = y * width * 4
        const dstStart = (yOff + y) * width * 4
        data.copy(out, dstStart, srcStart, srcStart + width * 4)
      }
    }

    const png = encodePng(width, totalHeight, out)
    return { ok: true, png, width, height: totalHeight, shardCount: shards.length, offsets, integrity: 'CONTINUOUS' }
  }

  /**
   * 在 prev 底部与 next 顶部之间搜索最佳重叠行数。
   * overlap 越大，next 越往上贴（与 prev 底部重叠越多）。
   * 评分：重叠区逐像素 MSE，取最小。
   */
  private findBestOverlap(
    prev: { width: number; height: number; data: Buffer },
    next: { width: number; height: number; data: Buffer },
  ): { ok: true; overlap: number; score: number } | { ok: false; reason: string } {
    const minO = this.minOverlap || Math.floor(next.height * 0.25)
    const maxO = this.maxOverlap || Math.floor(next.height * 0.75)
    let bestOverlap = -1
    let bestScore = Infinity

    for (let overlap = minO; overlap <= maxO; overlap += this.step) {
      // prev 底部 overlap 行 vs next 顶部 overlap 行
      const score = this.regionMse(prev, prev.height - overlap, next, 0, overlap)
      if (score < bestScore) {
        bestScore = score
        bestOverlap = overlap
      }
    }

    if (bestOverlap < 0) {
      return { ok: false, reason: 'no valid overlap in search range' }
    }
    if (bestScore > this.mismatchThreshold) {
      return { ok: false, reason: `min mse ${bestScore.toFixed(1)} exceeds threshold ${this.mismatchThreshold}` }
    }
    return { ok: true, overlap: bestOverlap, score: bestScore }
  }

  /** 计算两个等宽区域的重叠行 MSE（per-pixel，跨 RGBA） */
  private regionMse(
    a: { width: number; data: Buffer },
    aStartY: number,
    b: { width: number; data: Buffer },
    bStartY: number,
    rows: number,
  ): number {
    const width = a.width
    let sumSq = 0
    let count = 0
    for (let y = 0; y < rows; y++) {
      const aRow = (aStartY + y) * width * 4
      const bRow = (bStartY + y) * width * 4
      for (let x = 0; x < width * 4; x++) {
        const d = a.data[aRow + x]! - b.data[bRow + x]!
        sumSq += d * d
        count++
      }
    }
    return count === 0 ? Infinity : sumSq / count
  }
}
