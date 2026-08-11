/**
 * 附件简历同意执行器（CLI accept 子命令）。
 *
 * 沟通页左列会话中，最后一条消息为「对方想发送（加密）附件简历给您，您是否同意」的，
 * 逐个打开会话并点击底部处理条的「同意」接收简历。
 *
 * 定位规则（真机 2026-08-06 校准）：
 * - 左列目标会话：两种提示文案精确命中，屏幕 x < 850（右侧面板也有同文案，必须排除）
 * - 「同意」按钮配对：右侧（x>850）提示文案同一行（|Δy|<30）且在其右侧的「同意」。
 *   消息卡片里也有一对灰化的 同意/拒绝（Δy≈95），点它无效，必须点底部处理条
 * - fail-loud：点完「同意」后右侧可见「同意」数必须减少（处理条消失），
 *   未减少说明弹了二次确认或点击被拦截，立即停止请人工查看
 * - 左列点完后滚动左列加载更多（签名比对判到底：列表区域全部文本 y 坐标，滚动后不变=到底）
 */
import {
  type DomSnapshot,
  type DomDocument,
  type ClickPoint,
  findNodesByString,
  accumulateOwnerOffset,
  boundsCenter,
} from './domSnapshot.js'
import { viewportOf } from './FilterSetter.js'
import { CancelledError } from '../operations/types.js'

export class ConsentError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ConsentError'
  }
}

export interface ConsentDeps {
  snapshot(): Promise<DomSnapshot>
  click(point: ClickPoint, viewport: { width: number; height: number }): Promise<void>
  /** CDP mouseWheel 滚动左列会话列表；缺省时只处理当前可见的 */
  scroll?(deltaY: number): Promise<void>
  /** 按 Escape 关闭简历预览弹层（CDP dispatchKey）；preview 开启时必需 */
  pressEscape?(): Promise<void>
  /** 协作式取消信号：每轮循环顶部检查，触发即抛 CancelledError */
  signal?: AbortSignal
  /** 每成功同意 1 人回调一次（done 为累计同意数） */
  onProgress?(done: number): void
  sleep?(ms: number): Promise<void>
}

const MARKER_TEXTS = ['对方想发送附件简历给您，您是否同意', '对方想发送加密附件简历给您，您是否同意']
const AGREE_TEXT = '同意'
const PREVIEW_TEXT = '点击预览附件简历'
/** 简历预览弹层标志文案：弹层打开时成组出现（≥2），关闭后只剩聊天页自身的 1 个（真机实测） */
const MODAL_MARKERS = ['个人优势', '工作经历', '教育经历', '项目经历']
/** 左列会话列表与右侧聊天面板的分界（真机：列表命中 cx≈549~567，面板文案 cx≥1013）；ChatRejectExecutor 同用 */
export const LIST_MAX_X = 850
/** 处理条「同意」与提示文案同一行的容差（消息卡片按钮 Δy≈95，被此排除） */
const SAME_ROW_DY = 30

interface TextHit extends ClickPoint {
  documentIndex: number
}

export class ResumeConsentExecutor {
  private readonly sleep: (ms: number) => Promise<void>

  constructor(private readonly deps: ConsentDeps) {
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)))
  }

  /** 逐个同意，到底或达 limit 结束。preview=true 时每个同意后接着点开附件预览再关闭。返回同意数、预览数与是否到底 */
  async acceptAll(opts: { limit?: number; preview?: boolean } = {}): Promise<{ accepted: number; previewed: number; reachedEnd: boolean }> {
    const limit = opts.limit ?? 20
    const preview = opts.preview ?? false
    let accepted = 0
    let previewed = 0
    for (;;) {
      // 1. 处理当前可见的全部目标会话
      for (;;) {
        if (this.deps.signal?.aborted) throw new CancelledError(`已取消：同意接收 ${accepted} 人后中止`)
        if (accepted >= limit) return { accepted, previewed, reachedEnd: false }
        const snap = await this.deps.snapshot()
        const targets = this.findListTargets(snap)
        if (targets.length === 0) break

        await this.deps.click(targets[0]!, viewportOf(snap))
        await this.sleep(1800)

        // 打开会话后定位处理条「同意」（找不着等 1s 再试一次：面板可能加载慢）
        let agreeSnap = await this.deps.snapshot()
        let agree = this.locateAgreeButton(agreeSnap)
        if (!agree) {
          await this.sleep(1000)
          agreeSnap = await this.deps.snapshot()
          agree = this.locateAgreeButton(agreeSnap)
        }
        if (!agree) {
          throw new ConsentError(
            `第 ${accepted + 1} 个会话打开后未找到「同意」处理条（右侧面板无同行配对的同意按钮），` +
              '页面结构可能已变，已停止，请人工查看',
          )
        }
        const agreeCountBefore = this.countVisibleAgree(agreeSnap)
        await this.deps.click(agree, viewportOf(agreeSnap))
        await this.sleep(1500)

        const afterSnap = await this.deps.snapshot()
        const agreeCountAfter = this.countVisibleAgree(afterSnap)
        if (agreeCountAfter >= agreeCountBefore) {
          throw new ConsentError(
            `第 ${accepted + 1} 个「同意」点击后按钮未消失（${agreeCountBefore}→${agreeCountAfter}）：` +
              '可能出现二次确认弹层或点击被拦截，已停止，请人工查看页面',
          )
        }
        accepted++
        this.deps.onProgress?.(accepted)

        if (preview) {
          await this.previewResume(accepted)
          previewed++
        }
      }

      // 2. 可见的处理完了，滚动左列加载更多；签名不变（含一次重试）= 到底
      if (!this.deps.scroll) return { accepted, previewed, reachedEnd: true }
      for (let attempt = 0; attempt < 2; attempt++) {
        const before = await this.deps.snapshot()
        await this.deps.scroll(Math.floor(viewportOf(before).height / 2))
        await this.sleep(attempt === 0 ? 1200 : 1800)
        const after = await this.deps.snapshot()
        if (listSignatureOf(before) !== listSignatureOf(after)) break
        if (attempt === 1) return { accepted, previewed, reachedEnd: true }
      }
    }
  }

  /**
   * 同意后点开附件简历预览再关闭（真机 2026-08-06 校准）：
   * - 「点击预览附件简历」取右侧面板最下方一个（聊天历史里可能有旧附件的同名按钮，最新送达的在最底）
   * - 弹层打开校验：MODAL_MARKERS 成组出现（≥2）；Escape 关闭后 <2
   */
  private async previewResume(nth: number): Promise<void> {
    if (!this.deps.pressEscape) throw new ConsentError('preview 模式缺少 pressEscape 依赖')
    let previewBtn: ClickPoint | null = null
    for (let attempt = 0; attempt < 3 && !previewBtn; attempt++) {
      if (attempt > 0) await this.sleep(1500)
      const snap = await this.deps.snapshot()
      const viewport = viewportOf(snap)
      const candidates = this.findText(snap, PREVIEW_TEXT).filter(
        (h) => h.x > LIST_MAX_X && h.x < viewport.width && h.y > 0 && h.y < viewport.height,
      )
      if (candidates.length > 0) {
        const bottom = candidates.reduce((a, b) => (a.y > b.y ? a : b)) // 最下方 = 最新送达
        previewBtn = { x: bottom.x, y: bottom.y }
      }
    }
    if (!previewBtn) {
      throw new ConsentError(`第 ${nth} 个同意后等待 4.5s 仍未出现「${PREVIEW_TEXT}」按钮，附件可能未送达，已停止，请人工查看`)
    }
    const snap = await this.deps.snapshot()
    await this.deps.click(previewBtn, viewportOf(snap))
    await this.sleep(2000)

    const openSnap = await this.deps.snapshot()
    if (modalMarkerCount(openSnap) < 2) {
      throw new ConsentError(`第 ${nth} 个点击预览后弹层未打开（简历标志文案未成组出现），已停止，请人工查看`)
    }
    await this.deps.pressEscape()
    await this.sleep(800)
    const closedSnap = await this.deps.snapshot()
    if (modalMarkerCount(closedSnap) >= 2) {
      throw new ConsentError(`第 ${nth} 个预览弹层 Escape 后未关闭，已停止，请人工查看`)
    }
  }

  /** 左列（x<850）可见的两种提示文案命中，按 y 从上到下 */
  private findListTargets(snap: DomSnapshot): ClickPoint[] {
    const viewport = viewportOf(snap)
    const hits: ClickPoint[] = []
    for (const text of MARKER_TEXTS) {
      for (const hit of this.findText(snap, text)) {
        if (hit.x > 0 && hit.x < LIST_MAX_X && hit.y > 0 && hit.y < viewport.height) {
          hits.push({ x: hit.x, y: hit.y })
        }
      }
    }
    return hits.sort((a, b) => a.y - b.y)
  }

  /**
   * 底部处理条「同意」：右侧（x>850）提示文案同行（|Δy|<30）且在其右侧的「同意」。
   * 优先同行配对（处理条）；无同行时回退到文案下方 150px 内的（消息卡片激活态场景）。
   */
  private locateAgreeButton(snap: DomSnapshot): ClickPoint | null {
    const viewport = viewportOf(snap)
    const panelMarkers = MARKER_TEXTS.flatMap((t) => this.findText(snap, t)).filter(
      (h) => h.x > LIST_MAX_X && h.x < viewport.width && h.y > 0 && h.y < viewport.height,
    )
    if (panelMarkers.length === 0) return null
    const agrees = this.findText(snap, AGREE_TEXT).filter(
      (h) => h.x > LIST_MAX_X && h.x < viewport.width && h.y > 0 && h.y < viewport.height,
    )
    for (const marker of panelMarkers) {
      const sameRow = agrees.find((a) => Math.abs(a.y - marker.y) <= SAME_ROW_DY && a.x > marker.x)
      if (sameRow) return { x: sameRow.x, y: sameRow.y }
    }
    for (const marker of panelMarkers) {
      const below = agrees.find((a) => a.y > marker.y && a.y - marker.y < 150)
      if (below) return { x: below.x, y: below.y }
    }
    return null
  }

  /** 右侧可见「同意」数（点击成功校验用：处理条消失则减少） */
  private countVisibleAgree(snap: DomSnapshot): number {
    const viewport = viewportOf(snap)
    return this.findText(snap, AGREE_TEXT).filter(
      (h) => h.x > LIST_MAX_X && h.x < viewport.width && h.y > 0 && h.y < viewport.height,
    ).length
  }

  /** 全文精确命中（trim），返回屏幕坐标（已减文档滚动偏移） */
  private findText(snap: DomSnapshot, text: string): TextHit[] {
    const stringIndex = snap.strings.findIndex((s) => s.trim() === text)
    if (stringIndex < 0) return []
    const hits: TextHit[] = []
    snap.documents.forEach((document, documentIndex) => {
      for (const { bounds } of findNodesByString(document, stringIndex)) {
        if (bounds[2] <= 0 || bounds[3] <= 0) continue
        const offset = accumulateOwnerOffset(snap, documentIndex)
        const c = boundsCenter(bounds)
        hits.push({
          documentIndex,
          x: offset.x + c.x - (document.scrollOffsetX ?? 0),
          y: offset.y + c.y - (document.scrollOffsetY ?? 0),
        })
      }
    })
    return hits
  }
}

/** 弹层标志文案命中数（≥2 视为预览弹层打开） */
function modalMarkerCount(snap: DomSnapshot): number {
  return MODAL_MARKERS.filter((m) => snap.strings.some((s) => s.trim() === m)).length
}

/**
 * 左列滚动签名：列表区域（cx<850）全部可见文本节点的 y 坐标拼接。
 * 左列是内层滚动容器（doc scrollOffsetY 恒 0，不能像推荐页那样比 offset），
 * 用文本位置签名判到底：滚动后签名不变 = 列表没动 = 到底。
 */
function listSignatureOf(snap: DomSnapshot): string {
  const ys: number[] = []
  for (const document of snap.documents) {
    collectListYs(document, ys)
  }
  return ys.sort((a, b) => a - b).join(',')
}

function collectListYs(document: DomDocument, ys: number[]): void {
  const scrollY = document.scrollOffsetY ?? 0
  document.layout.nodeIndex.forEach((nodeIndex, layoutIndex) => {
    const b = document.layout.bounds[layoutIndex]
    if (!b || b[2]! <= 0 || b[3]! <= 0) return
    const cx = b[0]! + b[2]! / 2
    if (cx >= LIST_MAX_X) return
    ys.push(Math.round(b[1]! + b[3]! / 2 - scrollY))
  })
}
