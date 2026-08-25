/**
 * 候选人在线简历读取执行器（设计文档 §10.8，CLI resume-detail 命令 / boss_resume_detail tool）。
 *
 * 推荐牛人页点开候选人后，简历详情渲染在 /web/frame/c-resume/ iframe 内的一个 <CANVAS> 上
 * （WASM fillText 画像素），DOM/Shadow DOM/DOMSnapshot 任何手段都读不到文字（真机 2026-08-14
 * 实证：captureShadowTrees、DOM.getDocument pierce 全 0 命中）。因此链路是：
 *   Win32 滚轮回顶 → 分段整页截图（每段向下滚 8 格）→ 相邻段重叠像素对齐拼接 → Windows OCR 提取全文。
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
 * 为什么到底检测用「文件字节大小」而不是 scrollOffset：canvas 是 WASM 虚拟滚动，DOMSnapshot
 * 的 scrollOffsetY 恒为 0 不变，滚动时 canvas 整体重绘对应内容——不能靠 scrollOffset 判断滚动/到底。
 * 滚到底后再滚 canvas 不重绘、截图完全相同 → 相邻两段整页 PNG 字节数差 < 200 即到底
 * （真机实证：part-2 之后两段字节完全相同 268788）。
 *
 * OCR 拼接对齐为什么用颜色距离容差：canvas WASM 重绘有亚像素差异，严格像素相等永远 ~20% 不匹配，
 * 必须用 |dR|+|dG|+|dB|>60 视为不匹配找重叠最小的对齐（实现见 scripts/cv-stitch.ps1，降采样加速）。
 *
 * fail-loud：找不到大尺寸 CANVAS、0 段、OCR 空文本都抛 ResumeReadError，绝不返回半成品；
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
  /** 裁剪 + 重叠对齐 + 垂直拼接（真实实现调 scripts/cv-stitch.ps1） */
  stitch(
    parts: string[],
    rect: { x: number; y: number; w: number; h: number },
    outFile: string,
  ): Promise<{ width: number; height: number; overlaps: number[] }>
  /** OCR 识别（真实实现调 scripts/cv-ocr.ps1，读回 UTF-8 文本） */
  ocr(imgFile: string): Promise<string>
  /** 协作式取消信号：入口与分段循环检查，触发即抛 CancelledError */
  signal?: AbortSignal
  /** 阶段进度回调（operation 层转发 ctx.progress；可选，测试省略） */
  onProgress?: (stage: 'stitch' | 'ocr', info: { segments: number }) => void
  /** 可注入 sleep（测试） */
  sleep?(ms: number): Promise<void>
}

export interface ResumeReadResult {
  /** OCR 提取的简历全文（Windows OCR 水平，可能 ~20% 错字） */
  text: string
  /** 文本字符数 */
  chars: number
  /** 分段截图段数 */
  segments: number
  /** 是否确认滚到了简历底部（相邻段字节相同判定）。false = 打满 MAX_SEGMENTS 段仍未到底，内容可能截断 */
  bottomReached: boolean
  /** 拼接图宽（device px） */
  width: number
  /** 拼接图高（device px） */
  height: number
  /** 拼接长图 PNG 字节（返回给调用方做 base64 入库；临时文件在 finally 已清理） */
  imageBuffer: Buffer
}

/** 回顶防护格数：向上滚 50 格（DeltaY=+120×50）确保从简历顶部开始分段（防护值，多余滚动无害） */
export const SCROLL_TOP_NOTCHES = 50
/** 回顶后等 canvas 重绘稳定（ms） */
export const SCROLL_TOP_SETTLE_DELAY = 1500
/** 每段向下滚 8 格（-120×8）：每段前进 ~15-25% 内容，相邻段重叠很大（真机 overlap ~746/1237） */
export const SEGMENT_WHEEL_NOTCHES = 8
/** 滚动后等 canvas WASM 重绘（ms）。太早截图会拿到上一段内容 */
export const SEGMENT_REPAINT_DELAY = 900
/** 相邻段整页 PNG 字节数差 < 200 视为到底（滚到底后 canvas 不重绘、截图完全相同） */
export const BOTTOM_SIZE_EPSILON = 200
/** 分段数上限（防护：异常页面无限不重复时终止） */
export const MAX_SEGMENTS = 12
/** 简历 canvas 最小尺寸（device px），排除小图标 canvas。
 *  MIN_H 真机 2026-08-18 从 600 放宽到 400：详情弹层画布高度随窗口/内容自适应，
 *  实测 760×572 的合法简历画布被 600 卡掉（判「详情未打开」连环失败）；
 *  图标类 canvas 仅几十像素，400 仍能安全区分 */
export const CANVAS_MIN_W = 400
export const CANVAS_MIN_H = 400

/** 屏幕上的 device px 矩形 */
export interface DeviceRect {
  x: number
  y: number
  w: number
  h: number
}

/**
 * 定位简历详情 canvas：遍历所有 document（隐藏 iframe 的 owner 无可见 bounds，
 * accumulateOwnerOffset 抛错则跳过），找 nodeName=CANVAS 且 bounds w>400 h>600 的可见节点中
 * **面积最大**者（详情 iframe 里通常只有一个大 canvas；页面其他小 canvas 是图标）。
 *
 * 屏幕区域（device px）= accumulateOwnerOffset(documentIndex) + bounds[0,1] − scrollOffset。
 * 真机：canvas 在 iframe 顶部，scrollOffset 恒为 0，但公式上仍按 − scrollOffset 处理保持与
 * 项目其他定位一致。返回 null 表示当前页面没有打开简历详情。
 */
export function locateResumeCanvas(snap: DomSnapshot): DeviceRect | null {
  let best: DeviceRect | null = null
  let bestArea = 0
  snap.documents.forEach((document, documentIndex) => {
    if (!document.nodes.nodeName) return
    let offset: { x: number; y: number }
    try {
      offset = accumulateOwnerOffset(snap, documentIndex)
    } catch {
      return // 隐藏 iframe owner 无可见 bounds（后台标签页），跳过
    }
    for (const [nodeIndex, nameValueIndex] of indexedValues(document.nodes.nodeName, 'nodeName')) {
      if (snap.strings[nameValueIndex] !== 'CANVAS') continue
      const layoutIndex = document.layout.nodeIndex.indexOf(nodeIndex)
      if (layoutIndex < 0) continue
      const b = document.layout.bounds[layoutIndex]
      if (!b || b[2]! <= CANVAS_MIN_W || b[3]! <= CANVAS_MIN_H) continue // 小图标 canvas / 无 bounds
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
  })
  return best
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

      // 2. 分段循环：截图 → 与上段字节数差 <200 即到底（删本段停止）；否则向下滚 8 格再截。
      //    打满 MAX_SEGMENTS 段仍未到底时停止并标记 bottomReached=false（由调用方警告可能截断，
      //    绝不静默当作完整简历返回；最后一段入库后不再多滚——第 MAX+1 次截图已被禁止，滚了也白滚）
      const parts: string[] = []
      let prevBytes = -1
      let bottomReached = false
      for (let n = 0; n < MAX_SEGMENTS; n++) {
        if (this.deps.signal?.aborted) throw new CancelledError('已取消：分段截图阶段中止')
        const buf = await this.deps.captureFullpage()
        const file = path.join(tmpDir, `part-raw-${n}.png`)
        await fs.writeFile(file, buf)
        if (n > 0 && Math.abs(buf.length - prevBytes) < BOTTOM_SIZE_EPSILON) {
          // 到底：本段与上段内容完全相同，删除后停止（段 0 无上段可比，总是保留）
          await fs.rm(file, { force: true })
          bottomReached = true
          break
        }
        parts.push(file)
        prevBytes = buf.length
        if (n === MAX_SEGMENTS - 1) break // 已是允许的最后一段，后面不允许再截图，不再滚动
        await this.deps.wheel(-120, SEGMENT_WHEEL_NOTCHES)
        await this.sleep(SEGMENT_REPAINT_DELAY)
      }
      if (parts.length === 0) {
        throw new ResumeReadError('分段截图 0 段：未产出任何截图，请人工查看页面（Chrome 窗口是否可见）')
      }

      // 3. 拼接（scripts/cv-stitch.ps1：按 canvas device 区域裁剪 + 颜色容差重叠对齐 + 垂直拼接）
      this.deps.onProgress?.('stitch', { segments: parts.length })
      const stitchedFile = path.join(tmpDir, 'stitched.png')
      const { width, height } = await this.deps.stitch(parts, rect, stitchedFile)
      if (opts.saveImageTo) {
        await fs.copyFile(stitchedFile, opts.saveImageTo)
      }

      // 4. OCR（scripts/cv-ocr.ps1：WinRT zh-Hans-CN；PaddleOCR 云服务暂不可用，后续可选增强）
      this.deps.onProgress?.('ocr', { segments: parts.length })
      const text = await this.deps.ocr(stitchedFile)
      if (!text.trim()) {
        throw new ResumeReadError('OCR 未识别到任何文字：拼接图可能为空白，或 Windows 中文 OCR 语言包异常，请人工查看')
      }
      // 拼接图读进内存返回（调用方 base64 入库/保存）；临时文件在 finally 清理
      const imageBuffer = await fs.readFile(stitchedFile)
      return { text, chars: text.length, segments: parts.length, bottomReached, width, height, imageBuffer }
    } finally {
      // 临时目录清理（失败路径也清；清理本身的错误吞掉，不掩盖业务错误）
      await fs.rm(tmpDir, { recursive: true, force: true }).catch(() => {})
    }
  }
}

/**
 * 简历详情 canvas 顶部布局（真机 2026-08-14 OCR 实证）的首行姓名启发式提取。
 *
 * 首行实际样例（OCR 原文带空格）："最 近 关 注 工 作 经 历 0 0 康 嘉 润 飓 飓 活 跃 严 24 《 大 亏 4 年 …"
 * 结构 = [左侧栏 tab 词（最近关注/工作经历…）] + [杂字符] + 姓名 + 「刚刚活跃」(OCR 常误识"刚刚"二字但
 * 「活跃」稳定) + 年龄/学历…。解析步骤：去空白 → 截「活跃」前 → 剥离已知侧栏 tab 前缀 → 去开头非中文
 * 前缀 → 取开头连续中文段（含·）：2-4 字全取；5-6 字取前 n-2（视为"刚刚"误识尾巴）；>6 字视为噪音返回 null。
 *
 * 这是兜底手段（candidate_name 参数优先，智能体上下文通常已知姓名）；返回 null 表示无法识别，
 * 由调用方 fail-loud 要求显式传参，绝不瞎猜入库。
 */
const OCR_SIDEBAR_TABS = ['最近关注', '工作经历', '项目经历', '教育经历', '资格证书', '基本信息']

export function extractCandidateNameFromOcr(ocrText: string): string | null {
  const firstLine = ocrText
    .split('\n')
    .map((l) => l.trim())
    .find((l) => l.length > 0)
  if (!firstLine) return null
  let s = firstLine.replace(/\s+/g, '')
  const activeIdx = s.indexOf('活跃')
  if (activeIdx > 0) s = s.slice(0, activeIdx)
  let stripped = true
  while (stripped) {
    stripped = false
    for (const tab of OCR_SIDEBAR_TABS) {
      if (s.startsWith(tab)) {
        s = s.slice(tab.length)
        stripped = true
      }
    }
  }
  s = s.replace(/^[^\u4e00-\u9fa5·]+/, '') // 去开头非中文前缀（如 "00"）
  const m = /^[\u4e00-\u9fa5·]+/.exec(s)
  if (!m) return null
  const run = m[0]!
  if (run.length >= 2 && run.length <= 4) return run
  if (run.length === 5 || run.length === 6) return run.slice(0, run.length - 2)
  return null // >6 字视为噪音（不像姓名），交回调用方显式传参
}
