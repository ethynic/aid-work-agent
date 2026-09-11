/**
 * 候选人在线简历读取执行器（设计文档 §10.8，CLI resume-detail 命令 / boss_resume_detail tool）。
 *
 * 推荐牛人页点开候选人后，简历详情渲染在 /web/frame/c-resume/ iframe 内的一个 <CANVAS> 上
 * （WASM fillText 画像素），DOM/Shadow DOM/DOMSnapshot 任何手段都读不到文字（真机 2026-08-14
 * 实证：captureShadowTrees、DOM.getDocument pierce 全 0 命中）。因此链路是：
 *   Win32 滚轮回顶 → 分段整页截图（每段向下滚 8 格）→ 相邻段重叠像素对齐拼接 → OCR 提取全文
 *   （P2 起 RapidOCR 批量推理为主引擎，机器上不可用时自动回退 Windows WinRT OCR，见
 *   bossResumeDetail.ts 的引擎解析；OCR 原文经 cleanOcrText 清理字符间空格后合并）。
 *
 * 真机校准（2026-08-14，窗口 1249x1277，简历总高 3062、canvas 727x1237，3 段覆盖全文）：
 *
 * ⚠️ 坐标系大坑（本项目最重要的坑之一）：Page.captureScreenshot 无 clip 整页截图的输出就是
 * **device px，与 DOMSnapshot bounds 同一坐标系**（真机实证：整页 1249x1277 = viewport bounds）。
 * 截图后按 device 坐标直接裁剪，**绝不做 DPI 换算**——曾按 ÷1.5 换算导致右侧截掉一半内容，
 * OCR 从 3974 字掉到 1865 字。DPI 缩放只在「device px → 屏幕物理像素」一步发生（cv-wheel.ps1
 * 用 GetWindowRect 比例换算，与 win-click.ps1 同款），本执行器内不出现。
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
 * OCR 拼接对齐为什么用颜色距离容差：canvas WASM 重绘有亚像素差异，严格像素相等永远 ~20% 不匹配，
 * 必须用 |dR|+|dG|+|dB|>60 视为不匹配找重叠最小的对齐（实现见 scripts/cv-stitch.ps1，降采样加速）。
 * 每个接缝输出 overlapNN/mis，mis 大 = 接缝错位（内容重复/缺失）→ suspectSeams 元信息警告。
 *
 * OCR 为什么逐段而不是整张拼接长图：本机 WinRT OCR MaxImageDimension=10000px，长简历拼接图
 * 可能超限直接失败；且单点失败无容错。P1 改为 stitch 时让 cv-stitch.ps1 顺带落盘每段裁剪图
 * （-CropDir），Node 对每个 crop 逐个 OCR，再用 mergeSegmentTexts 在归一化文本上做重叠去重合并。
 *
 * fail-loud：找不到大尺寸 CANVAS、0 段、全部段 OCR 空文本都抛 ResumeReadError，绝不返回半成品；
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

/** 简历 OCR 引擎（P2 起 RapidOCR 为主、WinRT 兜底；见 bossResumeDetail.ts 引擎解析） */
export type OcrEngine = 'rapid' | 'winrt'
/** rapid 的加速后端（P2 提速：装 onnxruntime-directml 自动走 DirectML GPU，否则 CPU；winrt=none） */
export type OcrAccel = 'dml' | 'cpu' | 'none'

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
  /** 裁剪 + 重叠对齐 + 垂直拼接（真实实现调 scripts/cv-stitch.ps1）；cropDir 可选：把每段
   *  裁剪出的 canvas 区域另存 crop-00.png..crop-NN.png（零填充两位，序号与 parts 一致），供逐段 OCR */
  stitch(
    parts: string[],
    rect: { x: number; y: number; w: number; h: number },
    outFile: string,
    cropDir?: string,
  ): Promise<{ width: number; height: number; overlaps: number[]; seamMis: number[] }>
  /** 批量 OCR（P2 起一次调用处理全部段：真实实现优先 RapidOCR 单进程批量推理
   *  （scripts/cv-ocr-rapid.py，读回各 <img>.rapid.txt），不可用/失败整批回退 WinRT 逐段
   *  （scripts/cv-ocr.ps1））。engine 标识实际使用的引擎，accel 标识 rapid 的加速后端
   *  （dml=DirectML GPU / cpu=CPU / none=非 rapid），透传到结果元信息 */
  ocrBatch(files: string[]): Promise<{ texts: string[]; engine: OcrEngine; accel?: OcrAccel }>
  /** 协作式取消信号：入口与分段循环（含到底确认路径）检查，触发即抛 CancelledError */
  signal?: AbortSignal
  /** 阶段进度回调（operation 层转发 ctx.progress；可选，测试省略） */
  onProgress?: (stage: 'stitch' | 'ocr', info: { segments: number }) => void
  /** 可注入 sleep（测试） */
  sleep?(ms: number): Promise<void>
}

export interface ResumeReadResult {
  /** OCR 提取的简历全文（逐段 OCR 后按归一化重叠去重合并；Windows OCR 水平，可能 ~20% 错字） */
  text: string
  /** 文本字符数（合并后文本长度） */
  chars: number
  /** 分段截图段数 */
  segments: number
  /** 是否确认滚到了简历底部（字节差 + 像素级 + 连续两次相同）。false = 打满上限仍未到底，内容可能截断 */
  bottomReached: boolean
  /** 拼接图宽（device px） */
  width: number
  /** 拼接图高（device px） */
  height: number
  /** 拼接长图 PNG 字节（返回给调用方做 base64 入库；临时文件在 finally 已清理） */
  imageBuffer: Buffer
  /** 每接缝错配率（cv-stitch.ps1 输出 mis=，按接缝顺序） */
  seamMis: number[]
  /** 可疑接缝序号（1-based）：mis > SEAM_MIS_SUSPECT 的接缝，非空时调用方应警告人工核对 */
  suspectSeams: number[]
  /** 文本接缝未对上的序号（1-based）：mergeSegmentTexts 未命中重叠的接缝（可能有重复内容） */
  textSeamUnmatched: number[]
  /** 逐段 OCR 空文本的段数（简历可能真有图片/空白段，不炸整体；全部段都空才 fail-loud） */
  ocrEmptySegments: number
  /** 实际使用的 OCR 引擎（P2：rapid 主 / winrt 兜底；元信息随 payload 透传 ocr_engine） */
  ocrEngine: OcrEngine
  /** rapid 的加速后端（dml=DirectML GPU / cpu=CPU / none=非 rapid；payload ocr_accel） */
  ocrAccel: OcrAccel
}

/** 回顶防护格数：向上滚 50 格（DeltaY=+120×50）确保从简历顶部开始分段（防护值，多余滚动无害） */
export const SCROLL_TOP_NOTCHES = 50
/** 回顶后等 canvas 重绘稳定（ms） */
export const SCROLL_TOP_SETTLE_DELAY = 1500
/**
 * 每段向下滚 5 格（-120×5）：真机校准 2026-09-02（黄钰鑫简历）——旧值 8 格 ≈ 1000px 接近整视口
 * （canvas ~1230px），wheel 惯性/累积时不时会跃过整整一个视口 → 相邻段**零重叠** → cv-stitch 在
 * 空白区滑到假对齐（mis~0.21，密文区真对齐是 0.03）、文本合并接缝 100% 失配（实测 0.968/0.973
 * 差异率）。5 格 ≈ 625px 保证 ≥~50% 重叠（~700 归一化字，MERGE_WINDOW=1024 窗口内），
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
/** 文本合并重叠匹配窗口（归一化字符数）：段间重叠在该窗口内找最大 k。
 *  校准依据（真机 2026-09-02）：相邻段像素 overlap ~746px、字密度 ~1.1-1.3 字/纵向 px →
 *  归一化重叠 ≈750-970 字，取上限 ×1.05 余量 = 1024。旧值 400 必然失配：命中需 k≥0.909L
 *  （容差 0.2k）而 k 被 400 封顶，L≥~440 时永不命中，且 L>~480 时 A 尾窗与 B 头窗内容坐标
 *  不相交 → 真机每个文本接缝都 unmatched 全量直拼（每接缝重复 ~800 字）。
 *  窗口开大的性能代价由 levenshteinAtMost 的行最小值提前放弃兜底（见该函数注释）。 */
export const MERGE_WINDOW = 1024
/** 重叠匹配 k 下限：更短的重叠不可信（短串极易 ~80% 相似误命中），不算命中直接拼接 */
export const K_MIN = 8
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
   * 读取当前打开的候选人在线简历全文：回顶 → 分段截图（到底自动停止）→ 拼接 → OCR。
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
      //    相同则进入确认路径（再滚 8 格 + 等更久 + 再截一张），确认截图仍相同才判到底并丢弃两张
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

      // 3. 拼接（scripts/cv-stitch.ps1：按 canvas device 区域裁剪 + 颜色容差重叠对齐 + 垂直拼接）。
      //    cropDir 让 PS 顺带落盘每段裁剪图（crop-00.png..），供第 4 步逐段 OCR（长图超 WinRT
      //    MaxImageDimension=10000px 的风险 + 单点失败容错），并取回每接缝错配率 seamMis。
      this.deps.onProgress?.('stitch', { segments: parts.length })
      const stitchedFile = path.join(tmpDir, 'stitched.png')
      const cropDir = path.join(tmpDir, 'crops')
      const { width, height, seamMis } = await this.deps.stitch(parts, rect, stitchedFile, cropDir)
      if (opts.saveImageTo) {
        await fs.copyFile(stitchedFile, opts.saveImageTo)
      }

      // 4. 逐段 OCR（P2 起一次批量调用：deps.ocrBatch 优先 RapidOCR 单进程批量推理，
      //    不可用/失败自动回退 WinRT 逐段——见 bossResumeDetail.ts 的 ocrBatch 实现）。
      //    每段文本先过 cleanOcrText 清理字符间空格（WinRT 兜底文本「5 年 工 作 经 验」全靠它；
      //    RapidOCR 行重建输出基本已干净，再过一遍幂等无害），再 mergeSegmentTexts 合并。
      //    单段空文本不炸整体（图片/空白段是真实存在的），只计数；全部段都空才 fail-loud。
      this.deps.onProgress?.('ocr', { segments: parts.length })
      if (this.deps.signal?.aborted) throw new CancelledError('已取消：逐段 OCR 阶段中止')
      const cropFiles = parts.map((_, i) => path.join(cropDir, `crop-${String(i).padStart(2, '0')}.png`))
      const { texts: rawTexts, engine, accel = 'cpu' } = await this.deps.ocrBatch(cropFiles)
      const texts = rawTexts.map(cleanOcrText)
      let ocrEmptySegments = 0
      for (const text of texts) {
        if (!text.trim()) ocrEmptySegments++
      }
      const { text, seamMatches } = mergeSegmentTexts(texts)
      if (!text.trim()) {
        throw new ResumeReadError(
          'OCR 未识别到任何文字：拼接图可能为空白，或 OCR 引擎异常（WinRT 兜底需中文语言包；RapidOCR 见 doctor 检查项），请人工查看',
        )
      }
      // 接缝质量元信息：错配率可疑接缝（图像层）+ 文本接缝未对上（合并层），非空时调用方警告人工核对
      const suspectSeams = seamMis.map((mis, i) => (mis > SEAM_MIS_SUSPECT ? i + 1 : 0)).filter((v) => v > 0)
      const textSeamUnmatched = seamMatches.map((ok, i) => (ok ? 0 : i + 1)).filter((v) => v > 0)
      // 拼接图读进内存返回（调用方 base64 入库/保存）；临时文件在 finally 清理
      const imageBuffer = await fs.readFile(stitchedFile)
      return {
        text,
        chars: text.length,
        segments: parts.length,
        bottomReached,
        width,
        height,
        imageBuffer,
        seamMis,
        suspectSeams,
        textSeamUnmatched,
        ocrEmptySegments,
        ocrEngine: engine,
        ocrAccel: accel,
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

/**
 * 逐段 OCR 文本合并（P1）：在归一化文本上找相邻段的重叠区并去重，替代「整张拼接长图单点 OCR」。
 *
 * 背景：分段截图相邻段重叠 ~15-25% 内容，逐段 OCR 后直接拼接会重复一段内容；而图像拼接
 * （cv-stitch.ps1）虽已按重叠对齐，但接缝错位（mis 高）时文本仍可能重复/缺失。因此在文本层
 * 再做一次独立的重叠去重，两者互为校验（seamMatches=false 即文本层也没对上 → 元信息警告）。
 *
 * 匹配策略（相邻段 A、B，归一化=去全部空白）：
 * - 取 A 归一化尾串与 B 归一化头串（各取前/后 MERGE_WINDOW 个归一化字符）；
 * - 从 k=min(两侧长度) 降到 K_MIN，找最大 k 使 levenshtein(A尾k, B头k) ≤ max(1, ⌊k*0.2⌋)
 *   （≥80% 相似，容忍同内容两次 OCR 的零星差异——Windows OCR ~20% 错字率下同一行字两次识别
 *   常有 1-2 字不同；k < K_MIN 的短重叠极易 ~80% 相似误命中，不参与匹配）；
 * - 过伸修正：命中的 k 再做下降细化（缩小 1 能让 levenshtein 严格变小就继续缩），
 *   近似定位真实重叠长度——纯 max-k 会把接缝两侧的非重叠字符算进容差，系统性吞掉
 *   B 段接缝后约 10% 重叠长度的正文（见函数内注释）；
 * - 命中：保留 A 的重叠区版本，从 B 原始文本跳过被匹配的归一化前缀对应的原始字符
 *   （「归一化字符 → 原始下标」映射定位切割点），B 剩余部分（含其原始空格/换行）直接接上；
 * - 未命中：A 原文 + '\n' + B 原文直拼，该接缝记 seamMatches=false（可能有重复内容）。
 *
 * 空段（无可见字符）不参与匹配也不追加内容（简历真有图片/空白段），该接缝记 true（无重复风险）。
 * 典型开销（MERGE_WINDOW=1024）：初筛布尔判定走 levenshteinAtMost 行最小值提前放弃（无关文本
 * ~0.2k 行即弃；命中即停于 k≈1.1×真实重叠），细化保留精确 levenshtein（只在命中 k 附近小幅下降）；
 * 真机规模重叠（700-970 归一化字 + ~2% 错字）单接缝典型 ~10-100ms、最坏 ~0.7s，未命中全扫描
 * ~0.3s，段数 ≤ MAX_SEGMENTS 可接受。
 */

/** 归一化文本 + 「归一化字符 → 原始下标」映射（定位 B 的切割点用） */
function normalizeWithMap(s: string): { norm: string; map: number[] } {
  let norm = ''
  const map: number[] = []
  for (let i = 0; i < s.length; i++) {
    const ch = s[i]!
    if (/\s/.test(ch)) continue
    norm += ch
    map.push(i)
  }
  return { norm, map }
}

const hasVisible = (s: string): boolean => /\S/.test(s)

/**
 * OCR 文本空格清理（P2 纯函数）：逐段清洗 OCR 原文里的字符间空格，在 mergeSegmentTexts 之前应用。
 *
 * 背景：WinRT 兜底引擎的中文输出每个字符间都插空格（真机样例 "5 年 工 作 经 验"、"康 嘉 润"），
 * 存储文本满是噪声；RapidOCR 行重建（box 直接拼接）天然无此问题，输出基本已干净——本函数对
 * rapid 文本幂等无害、对 winrt 文本是必需的清洗层。
 *
 * 规则（保守，只动空格/tab/全角空格/CR，**不动换行**，逐行处理）：
 * - 空白 run 的两侧都是 ASCII 字母/数字 → 保留单个空格（"Linuw Windows"、"10 15" 这类英文
 *   词/数字对不误伤；连续多空格折叠成一个）——这是**唯一保留**的情形；
 * - 其余一律删除：某侧是 CJK 相关字符（\u4e00-\u9fa5 汉字（含丨）、间隔号 ·、CJK 标点
 *   \u3000-\u303f、全角形式 \uff00-\uffef）或 ASCII 标点（"30岁 | 大专" 的 | 侧、"5 年" 的
 *   年侧都属噪声空格；真机 RapidOCR 干净输出形如 "30岁|大专丨9年"，无字间空格）；
 * - 行首/行尾空白删除（等效逐行 trim；紧邻换行的空白随之消失，换行结构原样保留）。WinRT
 *   cv-ocr.ps1 落盘的 OcrResult.Text 行尾是 CRLF——\r 按行尾空白一并删除，输出统一 \n 行尾
 *   （与 cv-ocr-rapid.py 的 \n 行尾一致）。
 *
 * mergeSegmentTexts 的归一化匹配本来就先去全部空白，不受本函数影响；ocrNameMatches 同理。
 */
const OCR_SPACE_RE = /[ \t\u3000\r]/
const ASCII_ALNUM_RE = /[0-9A-Za-z]/

export function cleanOcrText(text: string): string {
  return text
    .split('\n')
    .map((line) => {
      let out = ''
      let i = 0
      while (i < line.length) {
        if (!OCR_SPACE_RE.test(line[i]!)) {
          out += line[i]
          i++
          continue
        }
        let j = i
        while (j < line.length && OCR_SPACE_RE.test(line[j]!)) j++
        const before = i > 0 ? line[i - 1]! : ''
        const after = j < line.length ? line[j]! : ''
        // 唯一保留情形：两侧都是 ASCII 字母/数字（英文词/数字对），折叠为单空格；
        // 其余（任一侧 CJK 相关/ASCII 标点、行首、行尾）一律删除
        if (before && after && ASCII_ALNUM_RE.test(before) && ASCII_ALNUM_RE.test(after)) {
          out += ' '
        }
        i = j
      }
      return out
    })
    .join('\n')
}


export function mergeSegmentTexts(texts: string[]): { text: string; seamMatches: Array<boolean> } {
  const seamMatches: Array<boolean> = []
  if (texts.length === 0) return { text: '', seamMatches }
  let merged = texts[0]!
  for (let i = 1; i < texts.length; i++) {
    const b = texts[i]!
    if (!hasVisible(b)) {
      seamMatches.push(true) // 空段：无内容可对齐也无重复风险
      continue
    }
    if (!hasVisible(merged)) {
      seamMatches.push(true) // 累计文本全空白（如首段是纯图片）：直接换成 B，避免前导 '\n' 噪声
      merged = b
      continue
    }
    const aNorm = normalizeWithMap(merged)
    const bNorm = normalizeWithMap(b)
    const aTail = aNorm.norm.slice(-MERGE_WINDOW)
    const bHead = bNorm.norm.slice(0, MERGE_WINDOW)
    const tolAt = (k: number): number => Math.max(1, Math.floor(k * 0.2))
    const levAt = (k: number): number =>
      levenshtein(aTail.slice(aTail.length - k), bHead.slice(0, k))
    // 初筛：找最大 k 满足 ≥80% 相似容差（k 从 min(两侧) 降到 K_MIN，大 k 优先）。
    // 布尔判定走 levenshteinAtMost（行最小值提前放弃）：MERGE_WINDOW=1024 下 naive 全量 DP
    // 一步 ~1e6 格 × ~1000 步 ≈ 3.6e8 字符操作/接缝（秒级）不可接受；提前放弃的布尔语义与
    // 精确计算完全等价（见该函数注释），命中结果不受影响
    let matchedK = 0
    for (let k = Math.min(aTail.length, bHead.length); k >= K_MIN; k--) {
      if (
        levenshteinAtMost(aTail.slice(aTail.length - k), bHead.slice(0, k), tolAt(k))
      ) {
        matchedK = k
        break
      }
    }
    // 过伸修正（关键取舍）：max-k 规则会把接缝两侧各自的非重叠字符也算进「重叠」——每侧
    // 1 字符贡献 lev +2，而容差 floor(0.2k) 允许 ~10% 过伸，即系统性吞掉 B 段接缝后约
    // 10% 重叠长度的正文（干净重叠必丢 1 字；100 字重叠可丢 ~11 字）。因此对命中的 k 做
    // 下降细化：缩小 1 能让 levenshtein 严格变小就继续缩（真实重叠边界即距离的平台期），
    // 近似定位真实重叠长度，消除系统性正文丢失。
    if (matchedK > K_MIN) {
      let lev = levAt(matchedK)
      while (matchedK > K_MIN) {
        const levSmaller = levAt(matchedK - 1)
        if (levSmaller < lev) {
          matchedK--
          lev = levSmaller
        } else {
          break
        }
      }
    }
    let cutOrig = -1
    if (matchedK >= K_MIN) {
      // 命中：跳过 B 被匹配的归一化前缀（其原始字符止于第 k 个归一化字符），其后内容（含
      // 紧随的空格/换行）原样保留——B 段自身的格式在切割点之后不受影响
      cutOrig = bNorm.map[matchedK - 1]! + 1
    }
    if (cutOrig >= 0) {
      merged = merged + b.slice(cutOrig)
      seamMatches.push(true)
    } else {
      merged = merged + '\n' + b
      seamMatches.push(false)
    }
  }
  return { text: merged, seamMatches }
}

/**
 * 姓名交叉校验（防张冠李戴）：判断「姓名是否在 OCR 文本头部模糊命中」。
 *
 * 为什么需要：批量读简历时姓名唯一来源 = 卡片 DOM 配对，单份读取时 = 显式入参——但点开的
 * 简历详情可能因弹层残留/点击错位与卡片/入参不是同一个人。姓名错了入库，打招呼就会打错人
 * （用户铁律：候选人姓名绝不能错）。因此 DOM/入参姓名必须在 OCR 文本头部模糊命中才放行入库；
 * 未命中 = 疑似详情与卡片不符 → 该份记 failure（batch）/报错（detail），宁跳过不错存。
 *
 * 容差取舍 = 1 字 OCR 误差（Windows OCR 约 ~20% 错字）：真机样例首行
 * "最 近 关 注 工 作 经 历 0 0 康 嘉 润 飓 飓 活 跃…"——姓名常被空格打散，且可能错 1 字
 * （"康嘉润"→"庭嘉润" 替换）、带误识噪声尾巴（"康嘉润飓" 插入）、或漏 1 字（"康嘉"）。
 * 用 Levenshtein ≤1 覆盖这三种情况；容差再放宽会把无关姓名误放行（错存比跳过危害大得多）。
 *
 * 只在归一化文本前 400 字符内找：姓名在简历头部第一行，限制窗口避免长文正文里随机出现的
 * 相似串误命中。n ≤ 30、窗口 ≤ 400，滑动窗口 × 小型 Levenshtein 性能无忧。
 *
 * 空姓名/空文本 → false（无法校验即不放行，fail-safe）。
 */

/** 交叉校验只看 OCR 文本头部前 400 字符（姓名在简历第一行，限窗口避免长文误命中） */
const OCR_NAME_MATCH_WINDOW = 400

/** 经典 Levenshtein（两行滚动数组，精确距离）。调用方保证 |a|、|b| ≤ MERGE_WINDOW（ocrNameMatches
 *  ≤31 / mergeSegmentTexts 细化 ≤1024）。仅用于「需要精确距离值」的场合：mergeSegmentTexts 命中后的
 * 下降细化（严格不等式「缩小 1 距离严格变小」不能被提前放弃破坏——细化只在命中 k 附近小幅下降，量小）。 */
function levenshtein(a: string, b: string): number {
  const m = a.length
  const n = b.length
  let prev = Array.from({ length: n + 1 }, (_, j) => j)
  for (let i = 1; i <= m; i++) {
    const cur = [i]
    const ac = a.charCodeAt(i - 1)
    for (let j = 1; j <= n; j++) {
      const cost = ac === b.charCodeAt(j - 1) ? 0 : 1
      cur[j] = Math.min(prev[j]! + 1, cur[j - 1]! + 1, prev[j - 1]! + cost)
    }
    prev = cur
  }
  return prev[n]!
}

/**
 * 布尔判定「lev(a,b) ≤ tol」（mergeSegmentTexts 初筛专用）：在经典两行 DP 上加 Ukkonen 行最小值
 * 提前放弃——当前行最小值已 > tol 时立即返回 false。
 *
 * 正确性（布尔语义与精确计算等价）：Levenshtein DP 中 d[i][j] ≥ min(r_{i-1}, i)（三种转移都 ≥ 前一行
 * 最小值或本行前缀），而 r_{i-1} ≤ i-1，故行最小值单调不减；当前行最小值 > tol ⇒ 最终 d[m][n] ≥
 * r_m > tol，提前返回 false 不改变布尔结果。附带 |m-n| > tol 快速拒绝（长度差是距离下界）。
 *
 * 为什么需要：MERGE_WINDOW=1024 下初筛是 k≈1024 降到 K_MIN 的全量扫描，naive 每步精确 DP ~1e6 格
 * → 单接缝 ~3.6e8 字符操作（秒级）。加提前放弃后：无关文本行最小值 ≈i，约 0.2k 行即放弃；错位对齐的
 * 相似文本行最小值低（对角线带内 ≈错字数）需跑完整 DP，但扫描在命中 k（≈1.1×真实重叠）即停。
 * 基准（1024 窗、2% 错字、真机规模重叠 700-970 字）：初筛+细化单接缝典型 ~10-100ms、最坏 ~0.7s，
 * 15 接缝最坏 ~10s 量级——相对整条 ~30s/份 的滚动截图管线可接受。
 */
function levenshteinAtMost(a: string, b: string, tol: number): boolean {
  if (Math.abs(a.length - b.length) > tol) return false
  const m = a.length
  const n = b.length
  let prev = Array.from({ length: n + 1 }, (_, j) => j)
  for (let i = 1; i <= m; i++) {
    const cur = new Array<number>(n + 1)
    cur[0] = i
    let rowMin = i
    const ac = a.charCodeAt(i - 1)
    for (let j = 1; j <= n; j++) {
      const v = Math.min(
        prev[j]! + 1,
        cur[j - 1]! + 1,
        prev[j - 1]! + (ac === b.charCodeAt(j - 1) ? 0 : 1),
      )
      cur[j] = v
      if (v < rowMin) rowMin = v
    }
    if (rowMin > tol) return false
    prev = cur
  }
  return prev[n]! <= tol
}

export function ocrNameMatches(name: string, ocrText: string): boolean {
  // 归一化：去所有空白（OCR 原文形如 "康 嘉 润"，姓名常被空格打散）
  const target = name.replace(/\s+/g, '')
  const normalized = ocrText.replace(/\s+/g, '')
  if (!target || !normalized) return false
  const head = normalized.slice(0, OCR_NAME_MATCH_WINDOW)
  // 精确包含（最常见：姓名只是被空格打散）
  if (head.includes(target)) return true
  // 模糊命中：长度 n-1 / n / n+1 的滑动窗口与姓名 Levenshtein ≤1，
  // 分别覆盖 OCR 漏 1 字 / 错 1 字（替换）/ 多 1 字噪声尾巴（插入）
  const n = target.length
  for (const w of [n - 1, n, n + 1]) {
    if (w < 1 || w > head.length) continue // n-1<1（单字姓名）跳过该档
    for (let i = 0; i + w <= head.length; i++) {
      if (levenshtein(head.slice(i, i + w), target) <= 1) return true
    }
  }
  return false
}
