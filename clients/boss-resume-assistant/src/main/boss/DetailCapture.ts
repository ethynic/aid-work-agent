/**
 * 详情截图器（设计文档 §8.2）。
 * 流程：
 * 1. 向上滚轮到顶（连续两次截图相似 → 确认顶部）
 * 2. 截顶部视口
 * 3. 每次向下滚动正文可视高度 65%~75%（保留 ≥25% 重叠）
 * 4. 等 Canvas 稳定：连续两次小图差异低于阈值，最长 2s
 * 5. 保存分片
 * 6. 连续两次滚动后正文主体不变 → 到底；最大 20 片硬上限
 *
 * 截图算法不依赖 scrollTop / DOM evaluate / 页面注入（设计文档硬约束）。
 * 稳定/到底检测用相邻小图 MSE。
 *
 * 注意：CDP Input.dispatchMouseEvent 的滚轮必须用 type=mouseWheel（deltaX/deltaY 仅对
 * mouseWheel 生效；mouseMoved 会忽略 delta，不滚动）。真机 spike（live-detail-scroll.mjs）
 * 验证过的形态即 mouseWheel，这里统一用 dispatchMouse({ type:'mouseWheel', deltaY })。
 */
import type { CdpGateway } from '../cdp/CdpGateway.js'
import { decodePng, encodePng } from '../image/LongScreenshotStitcher.js'
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)

export interface CaptureResult {
  shards: Buffer[]
  shardCount: number
  reachedBottom: boolean
  hitMaxShards: boolean
}

/** 结构子集，便于测试 mock；CdpGateway 天然满足 */
export type DetailCaptureGateway = Pick<CdpGateway, 'dispatchMouse' | 'captureScreenshot'>

export interface CaptureOptions {
  /** 视口高度（用于滚动步长） */
  viewportHeight: number
  /** 滚动步长比例（默认 0.7，留 30% 重叠） */
  scrollRatio?: number
  /** 最大分片数（设计文档硬上限 20） */
  maxShards?: number
  /** 稳定检测：相邻小图 MSE 阈值 */
  stableMseThreshold?: number
  /** 到底检测：连续两次主体不变的 MSE 阈值 */
  bottomMseThreshold?: number
  /** 稳定等待最长 ms */
  stableWaitMs?: number
  /** 截图裁剪区域（裁掉导航/操作栏），视口像素；不传则不裁 */
  crop?: { left: number; top: number; right: number; bottom: number }
}

export class DetailCapture {
  constructor(private gateway: DetailCaptureGateway) {}

  /**
   * 从顶部开始分段截图直到底部。返回分片 Buffer。
   */
  async captureScrolling(opts: CaptureOptions): Promise<CaptureResult> {
    const scrollRatio = opts.scrollRatio ?? 0.7
    const maxShards = opts.maxShards ?? 20
    const stableMse = opts.stableMseThreshold ?? 100
    const bottomMse = opts.bottomMseThreshold ?? 60
    const stableWait = opts.stableWaitMs ?? 2000
    const step = Math.floor(opts.viewportHeight * scrollRatio)

    // 1. 滚到顶
    await this.scrollToTop(opts.viewportHeight, stableMse)

    // 2~6. 分段截图
    const shards: Buffer[] = []
    let prevShot: Buffer | null = null

    for (let i = 0; i < maxShards; i++) {
      await this.waitStable(stableMse, stableWait)
      const shot = await this.shot(opts.crop)
      shards.push(shot)

      // 到底检测：本次与上次主体几乎一致
      if (prevShot && this.mse(prevShot, shot) < bottomMse) {
        return { shards, shardCount: shards.length, reachedBottom: true, hitMaxShards: false }
      }
      prevShot = shot

      // 向下滚一屏
      await this.gateway.dispatchMouse({ type: 'mouseWheel', x: 0, y: 0, deltaY: step })
      await this.wait(180)
    }

    const hitMax = shards.length >= maxShards
    return { shards, shardCount: shards.length, reachedBottom: !hitMax, hitMaxShards: hitMax }
  }

  /** 向上滚到顶：连续两次向上滚后截图相似 */
  private async scrollToTop(viewportHeight: number, stableMse: number): Promise<void> {
    let prev: Buffer | null = null
    for (let i = 0; i < 25; i++) {
      await this.gateway.dispatchMouse({ type: 'mouseWheel', x: 0, y: 0, deltaY: -viewportHeight })
      await this.wait(150)
      const shot = await this.shot()
      if (prev && this.mse(prev, shot) < stableMse) return
      prev = shot
    }
  }

  /** 等 Canvas 稳定：连续两次小图差异低于阈值，最长 waitMs */
  private async waitStable(threshold: number, waitMs: number): Promise<void> {
    const start = Date.now()
    let prev: Buffer | null = null
    while (Date.now() - start < waitMs) {
      const shot = await this.shot()
      if (prev && this.mse(prev, shot) < threshold) return
      prev = shot
      await this.wait(120)
    }
  }

  private async shot(crop?: CaptureOptions['crop']): Promise<Buffer> {
    const base64 = await this.gateway.captureScreenshot({ format: 'png' })
    const buf = Buffer.from(base64, 'base64')
    return crop ? this.cropPng(buf, crop) : buf
  }

  private cropPng(buf: Buffer, crop: NonNullable<CaptureOptions['crop']>): Buffer {
    const { PNG } = require('pngjs') as typeof import('pngjs')
    const png = PNG.sync.read(buf)
    const w = png.width - crop.left - crop.right
    const h = png.height - crop.top - crop.bottom
    if (w <= 0 || h <= 0) return buf
    const out = new PNG({ width: w, height: h })
    PNG.bitblt(png, out, crop.left, crop.top, w, h, 0, 0)
    return encodePng(w, h, out.data)
  }

  /** 两图 per-pixel MSE（尺寸不同返回 Infinity） */
  static mse(a: Buffer, b: Buffer): number {
    const da = decodePng(a)
    const db = decodePng(b)
    if (da.width !== db.width || da.height !== db.height) return Infinity
    let sumSq = 0
    const n = Math.min(da.data.length, db.data.length)
    for (let i = 0; i < n; i++) {
      const d = da.data[i]! - db.data[i]!
      sumSq += d * d
    }
    return n === 0 ? Infinity : sumSq / n
  }

  private mse(a: Buffer, b: Buffer): number {
    return DetailCapture.mse(a, b)
  }

  private wait(ms: number): Promise<void> {
    return new Promise((r) => setTimeout(r, ms))
  }
}
