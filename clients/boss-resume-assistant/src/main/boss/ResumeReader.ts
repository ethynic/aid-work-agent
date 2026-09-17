/**
 * 候选人在线简历读取执行器（设计文档 §10.8，CLI resume-detail 命令 / boss_resume_detail tool）。
 *
 * 推荐牛人页点开候选人后，简历详情渲染在 /web/frame/c-resume/ iframe 内的一个 <CANVAS> 上
 * （WASM fillText 画像素），DOM/Shadow DOM/DOMSnapshot 任何手段都读不到文字（真机 2026-08-14
 * 实证：captureShadowTrees、DOM.getDocument pierce 全 0 命中）。因此链路是：
 *   Win32 滚轮回顶 → 分段整页截图（每段向下滚 5 格）→ 相邻段重叠像素对齐拼接 → 返回拼接长图。
 * 文本识别在云端（GLM-5.3-Flash 多模态，2026-09-17 去 OCR 化）：客户端整条本地 OCR 链路
 * （RapidOCR 主引擎 / WinRT 兜底 / ocr-python 捆绑包）已下线——本地 OCR 在大量客户机报
 * DLL/DirectML 环境错误，是简历链路最大的不稳定源，云端识别质量更高且零本地依赖。
 *
 * 真机校准（2026-08-14，窗口 1249x1277，简历总高 3062、canvas 727x1237，3 段覆盖全文）：
 *
 * ⚠️ 坐标系大坑（本项目最重要的坑之一）：Page.captureScreenshot 无 clip 整页截图的输出就是
 * **device px，与 DOMSnapshot bounds 同一坐标系**（真机实证：整页 1249x1277 = viewport bounds）。
 * 截图后按 device 坐标直接裁剪，**绝不做 DPI 换算**——曾按 ÷1.5 换算导致右侧截掉一半内容。
 * DPI 缩放只在「device px → 屏幕物理像素」一步发生（cv-wheel.ps1 用 GetWindowRect 比例换算，
 * 与 win-click.ps1 同款），本执行器内不出现。
 *
 * 为什么滚动走 Win32 而不是 CDP（真机实证）：BOSS 风控拦截合成事件——CDP
 * Input.dispatchMouseEvent mouseWheel + 键盘 PageDown/ArrowDown/Space 全部无效（canvas
 * scrollOffsetY 纹丝不动），只有 Win32 mouse_event(MOUSEEVENTF_WHEEL) 真实滚轮有效。
 *
 * 为什么到底检测用「文件字节大小 + 像素确认 + 双重确认」而不是 scrollOffset：canvas 是 WASM
 * 虚拟滚动，DOMSnapshot 的 scrollOffsetY 恒为 0 不变，滚动时 canvas 整体重绘对应内容——不能靠
 * scrollOffset 判断滚动/到底。P0 用「相邻两段整页 PNG 字节数差 < 200 即到底」，但 2026-09-02 真机
 * 发现假到底：滚动被吞（Win32 滚轮事件偶发失效，重试即成功）或 canvas 懒加载未跟上时相邻截图也
 * 相同 ≠ 真到底，两份真机简历都在 3-4 段处被误判、底部「专业技能」被切半。P1 加固为三层：
 *   ① 字节差 ≥ BOTTOM_SIZE_EPSILON → 直接判「不同」（快路径，跳过像素比对）；
 *   ② 字节差 < epsilon → sameView dep（scripts/cv-segdiff.ps1）裁剪 canvas 区域做像素级同画面确认；
 *   ③ 像素相同不立即判到底——再滚一次、等更久（懒加载留时间）再截一张，仍相同才确认到底
 *     （连续两次相同），否则说明上次滚动被吞，把确认截图入列继续。
 *
 * 拼接对齐为什么用颜色距离容差：canvas WASM 重绘有亚像素差异，严格像素相等永远 ~20% 不匹配，
 * 必须用 |dR|+|dG|+|dB|>60 视为不匹配找重叠最小的对齐（实现见 scripts/cv-stitch.ps1，降采样加速）。
 * 每个接缝输出 overlapNN/mis，mis 大 = 接缝错位（内容重复/缺失）→ suspectSeams 元信息警告
 * （存库拼接图质量仍有意义，云端据此可提示人工核对）。
 *
 * fail-loud：找不到大尺寸 CANVAS、0 段都抛 ResumeReadError，绝不返回半成品；
 * 打满 MAX_SEGMENTS 段仍未到底时不报错但置 bottomReached=false，由调用方显式警告可能截断。
 */
import { randomUUID } from 'node:crypto'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import {
  type DomSnapshot,
  accumulateOwnerOffset,
  indexedValues,
} from './domSnapshot.js'
import { viewportOf } from './FilterSetter.js'
import { CancelledError } from '../operations/types.js'

export class ResumeReadError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ResumeReadError'
  }
}

export interface ResumeReadDeps {
  /** 采集 fresh DOMSnapshot（定位简历 canvas） */
  snapshot(): Promise<DomSnapshot>
  /** 无 clip 整页截图（PNG 字节，device px，与 DOMSnapshot bounds 同坐标系） */
  captureFullpage(): Promise<Buffer>
  /** Win32 滚轮（真实实现调 scripts/cv-wheel.ps1；deltaY<0 向下，单位=格×120） */
  wheel(deltaY: number, notches: number): Promise<void>
  /** 像素级同画面确认（真实实现调 scripts/cv-segdiff.ps1）：比对两段整页截图的 canvas 区域
   *  是否同一画面。只在相邻段字节差 < BOTTOM_SIZE_EPSILON 时被调（快路径跳过 PS 调用） */
  sameView(a: string, b: string): Promise<boolean>
  /** 裁剪 + 重叠对齐 + 垂直拼接（真实实现调 scripts/cv-stitch.ps1） */
  stitch(
    parts: string[],
    rect: { x: number; y: number; w: number; h: number },
    outFile: string,
  ): Promise<{ width: number; height: number; overlaps: number[]; seamMis: number[] }>
  /** 协作式取消信号：入口与分段循环（含到底确认路径）检查，触发即抛 CancelledError */
  signal?: AbortSignal
  /** 阶段进度回调（operation 层转发 ctx.progress；可选，测试省略） */
  onProgress?: (stage: 'stitch', info: { segments: number }) => void
  /** 可注入 sleep（测试） */
  sleep?(ms: number): Promise<void>
}

export interface ResumeReadResult {
  /** 分段截图段数 */
  segments: number
  /** 是否确认滚到了简历底部（字节差 + 像素级 + 连续两次相同）。false = 打满上限仍未到底，内容可能截断 */
  bottomReached: boolean
  /** 拼接图宽（device px） */
  width: number
  /** 拼接图高（device px） */
  height: number
  /** 拼接长图 PNG 字节（返回给调用方做 base64 入库；临时文件在 finally 已清理）。
   *  文本识别在云端（多模态模型读这张图），客户端不产出文本 */
  imageBuffer: Buffer
  /** 每接缝错配率（cv-stitch.ps1 输出 mis=，按接缝顺序） */
  seamMis: number[]
  /** 可疑接缝序号（1-based）：mis > SEAM_MIS_SUSPECT 的接缝，非空时调用方应警告人工核对 */
  suspectSeams: number[]
}

/** 回顶防护格数：向上滚 50 格（DeltaY=+120×50）确保从简历顶部开始分段（防护值，多余滚动无害） */
export const SCROLL_TOP_NOTCHES = 50
/** 回顶后等 canvas 重绘稳定（ms） */
export const SCROLL_TOP_SETTLE_DELAY = 1500
/**
 * 每段向下滚 5 格（-120×5）：真机校准 2026-09-02（黄钰鑫简历）——旧值 8 格 ≈ 1000px 接近整视口
 * （canvas ~1230px），wheel 惯性/累积时不时会跃过整整一个视口 → 相邻段**零重叠** → cv-stitch 在
 * 空白区滑到假对齐（mis~0.21，密文区真对齐是 0.03）。5 格 ≈ 625px 保证 ≥~50% 重叠，
 * 代价是段数增多（~3662px 简历 4 段→约 6-7 段，每段 +~1.2s）。
 */
export const SEGMENT_WHEEL_NOTCHES = 5
/** 滚动后等 canvas WASM 重绘（ms）。太早截图会拿到上一段内容 */
export const SEGMENT_REPAINT_DELAY = 900
/** 相邻段整页 PNG 字节数差 < 200 → 候选「相同」，需 sameView 像素级确认（快路径：差 ≥ 200 直接判不同） */
export const BOTTOM_SIZE_EPSILON = 200
/** 到底确认路径的额外等待（ms）：比常规重绘等待更久，给 canvas 懒加载留时间（假到底主因之一） */
export const BOTTOM_CONFIRM_DELAY = 1800
/** 分段数上限（防护：异常页面无限不重复时终止）。P1 2026-09-02 从 12 提到 16：假到底修复后
 *  （滚动被吞不再被误判到底），单份简历实际段数会比 P0 观测值更多 */
export const MAX_SEGMENTS = 16
/** 总截图次数安全上限（含到底确认路径的额外截图）：防「确认截图永远不同」等异常页死循环 */
export const MAX_CAPTURES = 2 * MAX_SEGMENTS + 8
/** 接缝错配率可疑阈值：mis > 0.35 的接缝进 suspectSeams（cv-stitch 真机正常接缝 mis ≤ ~0.15） */
export const SEAM_MIS_SUSPECT = 0.35
/** 简历 canvas 最小尺寸（**随视口自适应**，不再写死固定像素）。
 *  职责：把几十像素的图标 canvas 与简历详情画布区分开。画布尺寸随窗口自适应
 *  （参考机 760×572~1264，占视口宽 ~61%/高 ~45%+；小屏笔记本同比例缩小），固定值
 *  无法覆盖不同分辨率——历史两次踩坑：固定 600 误杀 572 高真实画布（2026-08-18），
 *  固定 400 对小屏笔记本偏高（2026-09-11 客户机）。规则：宽/高各 ≥ 视口对应边 ×25%，
 *  另设 200px 绝对下限防极端小视口放进图标 canvas。 */
export function canvasMinSize(viewport: { width: number; height: number }): {
  minW: number
  minH: number
} {
  return {
    minW: Math.max(200, Math.round(viewport.width * 0.25)),
    minH: Math.max(200, Math.round(viewport.height * 0.25)),
  }
}

/** 屏幕上的 device px 矩形 */
export interface DeviceRect {
  x: number
  y: number
  w: number
  h: number
}

/**
 * 定位简历详情 canvas：遍历所有 document（隐藏 iframe 的 owner 无可见 bounds，
 * accumulateOwnerOffset 抛错则跳过），找 nodeName=CANVAS 且超过自适应最小尺寸（canvasMinSize）的可见节点中
 * **面积最大**者（详情 iframe 里通常只有一个大 canvas；页面其他小 canvas 是图标）。
 *
 * 屏幕区域（device px）= accumulateOwnerOffset(documentIndex) + bounds[0,1] − scrollOffset。
 * 真机：canvas 在 iframe 顶部，scrollOffset 恒为 0，但公式上仍按 − scrollOffset 处理保持与
 * 项目其他定位一致。
 *
 * 2026-09-11 小屏修复：返回矩形与视口求交（截断出屏部分）。开发机大屏画布恰好完整可见；
 * 客户小屏笔记本弹层画布底部出屏 → 原样返回会导致 cv-segdiff/cv-stitch 裁剪越界（报
 * 「像素级同画面比对失败」）或 Win32 滚轮落到视口外。滚轮点取交集中心恒在可见区内；
 * 裁剪恒在截图内。交集为空返回 null。
 */
export function locateResumeCanvas(snap: DomSnapshot): DeviceRect | null {
  let best: DeviceRect | null = null
  let bestArea = 0
  const vp = viewportOf(snap)
  const { minW, minH } = canvasMinSize(vp)
  const documents = snap.documents
  for (let documentIndex = 0; documentIndex < documents.length; documentIndex++) {
    const document = documents[documentIndex]!
    if (!document.nodes.nodeName) continue
    let offset: { x: number; y: number }
    try {
      offset = accumulateOwnerOffset(snap, documentIndex)
    } catch {
      continue // 隐藏 iframe owner 无可见 bounds（后台标签页），跳过
    }
    for (const [nodeIndex, nameValueIndex] of indexedValues(document.nodes.nodeName, 'nodeName')) {
      if (snap.strings[nameValueIndex] !== 'CANVAS') continue
      const layoutIndex = document.layout.nodeIndex.indexOf(nodeIndex)
      if (layoutIndex < 0) continue
      const b = document.layout.bounds[layoutIndex]
      if (!b || b[2]! <= minW || b[3]! <= minH) continue // 小图标 canvas / 无 bounds
      const area = b[2]! * b[3]!
      if (area <= bestArea) continue
      bestArea = area
      best = {
        x: Math.round(offset.x + b[0]! - (document.scrollOffsetX ?? 0)),
        y: Math.round(offset.y + b[1]! - (document.scrollOffsetY ?? 0)),
        w: Math.round(b[2]!),
        h: Math.round(b[3]!),
      }
    }
  }
  if (!best) return null
  // 与视口求交（2026-09-11 小屏修复）：出屏部分截断，滚轮/裁剪恒在可见区内
  const x1 = Math.max(0, best.x)
  const y1 = Math.max(0, best.y)
  const x2 = Math.min(vp.width, best.x + best.w)
  const y2 = Math.min(vp.height, best.y + best.h)
  if (x2 - x1 <= 0 || y2 - y1 <= 0) return null
  return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 }
}

/**
 * 诊断用：页面全部可见 CANVAS 尺寸（面积降序，最多 5 个，只取 w/h 不含坐标——坐标不进日志）。
 * 「详情未打开」排障关键：若列表里出现接近阈值的大画布（如 380x560），说明弹层实际已打开、
 * 只是没过 canvasMinSize 自适应门槛；全是几十像素的图标 canvas 则是详情真的没开。
 */
export function canvasCandidates(snap: DomSnapshot): Array<{ w: number; h: number }> {
  const out: Array<{ w: number; h: number }> = []
  snap.documents.forEach((document) => {
    if (!document.nodes.nodeName) return
    for (const [nodeIndex, nameValueIndex] of indexedValues(document.nodes.nodeName, 'nodeName')) {
      if (snap.strings[nameValueIndex] !== 'CANVAS') continue
      const layoutIndex = document.layout.nodeIndex.indexOf(nodeIndex)
      if (layoutIndex < 0) continue
      const b = document.layout.bounds[layoutIndex]
      if (!b || b[2]! <= 0 || b[3]! <= 0) continue
      out.push({ w: Math.round(b[2]!), h: Math.round(b[3]!) })
    }
  })
  return out.sort((a, b) => b.w * b.h - a.w * a.h).slice(0, 5)
}

export class ResumeReader {
  private readonly sleep: (ms: number) => Promise<void>

  constructor(private readonly deps: ResumeReadDeps) {
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)))
  }

  /** 定位简历 canvas（代理到模块级 locateResumeCanvas，便于单测/operation 前置校验复用） */
  locateCanvas(snap: DomSnapshot): DeviceRect | null {
    return locateResumeCanvas(snap)
  }

  /**
   * 读取当前打开的候选人在线简历拼接长图：回顶 → 分段截图（到底自动停止）→ 拼接 → 返回图片。
   * 文本识别在云端（boss_resume_detail/batch 工具层调多模态模型）。
   * 任何一步失败抛 ResumeReadError / CancelledError（fail-loud），临时目录在 finally 清理。
   */
  async readResume(opts: { saveImageTo?: string } = {}): Promise<ResumeReadResult> {
    if (this.deps.signal?.aborted) throw new CancelledError()
    const snap0 = await this.deps.snapshot()
    const rect = this.locateCanvas(snap0)
    if (!rect) {
      throw new ResumeReadError(
        '未找到简历详情画布（页面中无大尺寸 CANVAS）：请先在推荐牛人页点开一个候选人的在线简历详情，再执行读取',
      )
    }

    const tmpDir = path.join(os.tmpdir(), `boss-cv-${randomUUID()}`)
    await fs.mkdir(tmpDir, { recursive: true })
    try {
      // 1. 回顶（防护值，多余滚动无害；canvas 虚拟滚动 scrollOffset 不可用，只能盲滚回顶）
      await this.deps.wheel(120, SCROLL_TOP_NOTCHES)
      await this.sleep(SCROLL_TOP_SETTLE_DELAY)

      // 2. 分段循环（P1 三层到底判定，见类头注释）：
      //    截图 → 字节差 ≥ epsilon 直接入列（快路径）；字节差 < epsilon → sameView 像素确认 →
      //    相同则进入确认路径（再滚 5 格 + 等更久 + 再截一张），确认截图仍相同才判到底并丢弃两张
      //    重复截图；不同则说明上次滚动被吞，确认截图入列继续。段数由 parts.length < MAX_SEGMENTS
      //    控制，总截图次数另设 MAX_CAPTURES 安全上限（防确认路径死循环）。
      const parts: string[] = []
      let prevBytes = -1
      let bottomReached = false
      let captures = 0
      for (;;) {
        if (this.deps.signal?.aborted) throw new CancelledError('已取消：分段截图阶段中止')
        if (parts.length >= MAX_SEGMENTS) break // 段数上限：未确认到底，bottomReached=false（调用方警告）
        if (captures >= MAX_CAPTURES) break // 总截图次数安全上限（同上，不静默当到底）
        const cap = await this.capturePart(tmpDir, captures++)
        let buf = cap.buf
        let file = cap.file
        if (parts.length > 0 && Math.abs(buf.length - prevBytes) < BOTTOM_SIZE_EPSILON) {
          const lastFile = parts[parts.length - 1]!
          const same = await this.sameViewOrFail(file, lastFile)
          if (same) {
            // 疑似到底 → 确认路径：再滚一次（同段距）+ 等更久（懒加载）+ 再截一张
            if (this.deps.signal?.aborted) throw new CancelledError('已取消：分段截图阶段中止')
            if (captures >= MAX_CAPTURES) {
              await fs.rm(file, { force: true })
              break
            }
            await this.deps.wheel(-120, SEGMENT_WHEEL_NOTCHES)
            await this.sleep(BOTTOM_CONFIRM_DELAY)
            const cap2 = await this.capturePart(tmpDir, captures++)
            const confirmed =
              Math.abs(cap2.buf.length - prevBytes) < BOTTOM_SIZE_EPSILON &&
              (await this.sameViewOrFail(cap2.file, lastFile))
            if (confirmed) {
              // 连续两次相同 → 真到底：丢弃两张重复截图（本张 + 确认截图）
              await fs.rm(file, { force: true })
              await fs.rm(cap2.file, { force: true })
              bottomReached = true
              break
            }
            // 确认截图与上一段不同 → 上次滚动被吞（真机已知「重试即成功」）：
            // 丢弃第一张重复截图，确认截图入列代替它，继续正常循环
            await fs.rm(file, { force: true })
            buf = cap2.buf
            file = cap2.file
          }
        }
        parts.push(file)
        prevBytes = buf.length
        if (parts.length >= MAX_SEGMENTS) break // 已满段：后面不允许再截图，不再滚动（滚了也白滚）
        await this.deps.wheel(-120, SEGMENT_WHEEL_NOTCHES)
        await this.sleep(SEGMENT_REPAINT_DELAY)
      }
      if (parts.length === 0) {
        throw new ResumeReadError('分段截图 0 段：未产出任何截图，请人工查看页面（Chrome 窗口是否可见）')
      }

      // 3. 拼接（scripts/cv-stitch.ps1：按 canvas device 区域裁剪 + 颜色容差重叠对齐 + 垂直拼接），
      //    取回每接缝错配率 seamMis（质量元信息，mis 大的接缝内容可能重复/缺失）。
      this.deps.onProgress?.('stitch', { segments: parts.length })
      const stitchedFile = path.join(tmpDir, 'stitched.png')
      const { width, height, seamMis } = await this.deps.stitch(parts, rect, stitchedFile)
      if (opts.saveImageTo) {
        await fs.copyFile(stitchedFile, opts.saveImageTo)
      }

      // 接缝质量元信息：错配率可疑接缝（图像层）非空时调用方警告人工核对
      const suspectSeams = seamMis.map((mis, i) => (mis > SEAM_MIS_SUSPECT ? i + 1 : 0)).filter((v) => v > 0)
      // 拼接图读进内存返回（调用方 base64 入库/保存）；临时文件在 finally 清理
      const imageBuffer = await fs.readFile(stitchedFile)
      return {
        segments: parts.length,
        bottomReached,
        width,
        height,
        imageBuffer,
        seamMis,
        suspectSeams,
      }
    } finally {
      // 临时目录清理（失败路径也清；清理本身的错误吞掉，不掩盖业务错误）
      await fs.rm(tmpDir, { recursive: true, force: true }).catch(() => {})
    }
  }

  /** 截一段整页图落盘（文件名按截图次序编号，含到底确认路径的额外截图） */
  private async capturePart(
    tmpDir: string,
    index: number,
  ): Promise<{ buf: Buffer; file: string }> {
    const buf = await this.deps.captureFullpage()
    const file = path.join(tmpDir, `part-raw-${index}.png`)
    await fs.writeFile(file, buf)
    return { buf, file }
  }

  /** sameView dep 包装：非取消类异常统一转 ResumeReadError（fail-loud，如 cv-segdiff.ps1 裁剪越界） */
  private async sameViewOrFail(a: string, b: string): Promise<boolean> {
    try {
      return await this.deps.sameView(a, b)
    } catch (err) {
      if (err instanceof CancelledError) throw err
      throw new ResumeReadError(
        `像素级同画面比对失败（${a} vs ${b}）：${err instanceof Error ? err.message : String(err)}`,
      )
    }
  }
}
