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
 * - fail-loud：同会话的原处理条消失且新增附件/明确同意结果才确认成功，
 *   历史消息卡片中的灰色「同意」不作为处理条仍在的证据
 * - 左列点完后滚动左列加载更多（签名比对判到底：列表区域全部文本 y 坐标，滚动后不变=到底）
 */
import {
  type DomSnapshot,
  type DomDocument,
  type ClickPoint,
  findNodesByString,
  accumulateOwnerOffset,
  boundsCenter,
  indexedValues,
  classOf,
} from './domSnapshot.js'
import { viewportOf } from './FilterSetter.js'
import { CancelledError } from '../operations/types.js'
import { BusinessOperationState } from './BusinessOperationState.js'
import { headerContactOf } from './ChatReadExecutor.js'
import { locateResumeCanvas } from './ResumeReader.js'

export class ConsentError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ConsentError'
  }
}

export interface ConsentDeps {
  state?: BusinessOperationState
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
const PREVIEW_SECTIONS = ['个人优势', '工作经历', '教育经历', '项目经历']
/**
 * 左列会话列表与右侧聊天面板的分界（2026-09-01 相对化修订：旧规则绝对像素 cx=850 按真机
 * 1917 宽窗口校准——列表命中 cx≈549~567、面板文案 cx≥1013；另一真机 1249 宽窗口实测
 * 列表右缘≈547/面板起点≈548。两点换算比例一致（850/1917≈547/1249≈0.44）：沟通页整体
 * 随窗口等比缩放，绝对分界在其它窗口宽度必然失配，比例分界恒落在列表右缘与面板左缘
 * 之间的宽松带内）。ChatRejectExecutor / InterviewDemoExecutor 同用。
 */
export function listMaxX(viewportWidth: number): number {
  return (viewportWidth * 850) / 1917
}
/** 处理条「同意」与提示文案同一行的容差（消息卡片按钮 Δy≈95，被此排除） */
const SAME_ROW_DY = 30

interface TextHit extends ClickPoint {
  documentIndex: number
}

interface AgreeButton extends ClickPoint {
  source: 'processing_bar' | 'message_card'
}

export class ResumeConsentExecutor {
  private readonly sleep: (ms: number) => Promise<void>
  readonly state: BusinessOperationState

  constructor(private readonly deps: ConsentDeps) {
    this.state = deps.state ?? new BusinessOperationState()
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)))
  }

  /** 逐个同意，到底或达 limit 结束。preview=true 时每个同意后接着点开附件预览再关闭。返回同意数、预览数与是否到底 */
  async acceptAll(opts: { limit?: number; preview?: boolean; expectedContactRef?: string } = {}): Promise<{ accepted: number; previewed: number; reachedEnd: boolean }> {
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

        this.state.startItem()
        await this.state.input(() => this.deps.click(targets[0]!, viewportOf(snap)))
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
        this.state.bindContact(agreeSnap, accepted === 0 ? opts.expectedContactRef : undefined)
        const contact = headerContactOf(agreeSnap)
        const previewsBefore = this.previewCount(agreeSnap)
        const noticesBefore = this.acceptedNoticeCount(agreeSnap)
        const processingBarOnly = agree.source === 'processing_bar'
        await this.state.input(() => this.deps.click(agree!, viewportOf(agreeSnap)), true)
        await this.sleep(1500)

        let afterSnap = await this.deps.snapshot()
        let confirmed = false
        for (let poll = 0; poll < 3; poll++) {
          this.state.bindContact(afterSnap)
          const explicitAccepted = this.acceptedNoticeCount(afterSnap) > noticesBefore
          if (!this.locateAgreeButton(afterSnap, processingBarOnly) && (this.previewCount(afterSnap) > previewsBefore || explicitAccepted)) { confirmed = true; break }
          if (poll < 2) { await this.sleep(1000); afterSnap = await this.deps.snapshot() }
        }
        if (!confirmed) {
          this.state.unconfirmed('原会话缺少处理条消失及新附件/明确同意结果的联合证据')
          throw new ConsentError(
            `无法确认第 ${accepted + 1} 个「同意」已成功，未以其他同意按钮减少报成功`,
          )
        }
        accepted++
        this.state.confirmed({ contact, accepted: true }, '原会话处理条消失且新附件或明确同意结果出现')
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

  private previewCount(snap: DomSnapshot): number {
    const viewport = viewportOf(snap)
    return this.findText(snap, PREVIEW_TEXT).filter(h => h.x > listMaxX(viewport.width)).length
  }

  private acceptedNoticeCount(snap: DomSnapshot): number {
    const boundary = listMaxX(viewportOf(snap).width)
    return ['已同意接收附件简历', '已同意接收简历']
      .reduce((count, text) => count + this.findText(snap, text).filter(hit => hit.x > boundary).length, 0)
  }

  /**
   * 同意后点开附件简历预览再关闭（真机 2026-08-06 校准）：
   * - 「点击预览附件简历」取右侧面板最下方一个（聊天历史里可能有旧附件的同名按钮，最新送达的在最底）
   * - 弹层打开校验：可见附件 iframe 中已加载简历画布；Escape 后附件 iframe 不再可见
   */
  private async previewResume(nth: number): Promise<void> {
    if (!this.deps.pressEscape) throw new ConsentError('preview 模式缺少 pressEscape 依赖')
    let previewBtn: ClickPoint | null = null
    for (let attempt = 0; attempt < 3 && !previewBtn; attempt++) {
      if (attempt > 0) await this.sleep(1500)
      const snap = await this.deps.snapshot()
      const viewport = viewportOf(snap)
      const candidates = this.findText(snap, PREVIEW_TEXT).filter(
        (h) => h.x > listMaxX(viewport.width) && h.x < viewport.width && h.y > 0 && h.y < viewport.height,
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
    this.state.bindContact(snap)
    const freshPreviews = this.findText(snap, PREVIEW_TEXT).filter(h => h.x > listMaxX(viewportOf(snap).width)
      && h.x < viewportOf(snap).width && h.y > 0 && h.y < viewportOf(snap).height)
    if (!freshPreviews.length) throw new ConsentError('已接受简历，但预览入口已变化，未使用缓存坐标点击')
    const freshPreview = freshPreviews.reduce((a, b) => a.y > b.y ? a : b)
    await this.state.input(() => this.deps.click(freshPreview, viewportOf(snap)))
    await this.sleep(2000)

    let openSnap = await this.waitForPreview()
    if (attachmentPreviewState(openSnap) === 'closed' && this.state.retry()) {
      this.state.bindContact(openSnap)
      const retryButtons = this.findText(openSnap, PREVIEW_TEXT).filter(h => h.x > listMaxX(viewportOf(openSnap).width)
        && h.y > 0 && h.y < viewportOf(openSnap).height)
      if (!retryButtons.length) throw new ConsentError('已接受简历，但预览重试前无法定位原入口')
      const retryButton = retryButtons.reduce((a,b) => a.y > b.y ? a : b)
      await this.state.input(() => this.deps.click(retryButton, viewportOf(openSnap)))
      await this.sleep(2000)
      openSnap = await this.waitForPreview()
    }
    if (attachmentPreviewState(openSnap) !== 'ready') {
      throw new ConsentError(`第 ${nth} 个点击预览后未确认附件画布已加载，已停止，请人工查看`)
    }
    await this.state.input(() => this.deps.pressEscape!())
    await this.sleep(800)
    let closedSnap = await this.deps.snapshot()
    if (attachmentPreviewState(closedSnap) !== 'closed' && this.state.retry()) {
      await this.state.input(() => this.deps.pressEscape!())
      await this.sleep(800)
      closedSnap = await this.deps.snapshot()
    }
    if (attachmentPreviewState(closedSnap) !== 'closed') {
      throw new ConsentError(`第 ${nth} 个预览弹层 Escape 后未关闭，已停止，请人工查看`)
    }
  }

  /** 已出现附件框时只等内容加载，不再点击背景里的预览入口。 */
  private async waitForPreview(): Promise<DomSnapshot> {
    let snap = await this.deps.snapshot()
    for (let poll = 0; poll < 2 && attachmentPreviewState(snap) === 'loading'; poll++) {
      await this.sleep(1500)
      snap = await this.deps.snapshot()
    }
    return snap
  }

  /** 左列（x<850）可见的两种提示文案命中，按 y 从上到下 */
  private findListTargets(snap: DomSnapshot): ClickPoint[] {
    const viewport = viewportOf(snap)
    const hits: ClickPoint[] = []
    for (const text of MARKER_TEXTS) {
      for (const hit of this.findText(snap, text)) {
        if (hit.x > 0 && hit.x < listMaxX(viewport.width) && hit.y > 0 && hit.y < viewport.height) {
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
  private locateAgreeButton(snap: DomSnapshot, processingBarOnly = false): AgreeButton | null {
    const viewport = viewportOf(snap)
    const panelMarkers = MARKER_TEXTS.flatMap((t) => this.findText(snap, t)).filter(
      (h) => h.x > listMaxX(viewport.width) && h.x < viewport.width && h.y > 0 && h.y < viewport.height,
    )
    if (panelMarkers.length === 0) return null
    const agrees = this.findText(snap, AGREE_TEXT).filter(
      (h) => h.x > listMaxX(viewport.width) && h.x < viewport.width && h.y > 0 && h.y < viewport.height,
    )
    for (const marker of panelMarkers) {
      const sameRow = agrees.find((a) => Math.abs(a.y - marker.y) <= SAME_ROW_DY && a.x > marker.x)
      if (sameRow) return { x: sameRow.x, y: sameRow.y, source: 'processing_bar' }
    }
    if (processingBarOnly) return null
    for (const marker of panelMarkers) {
      const below = agrees.find((a) => a.y > marker.y && a.y - marker.y < 150)
      if (below) return { x: below.x, y: below.y, source: 'message_card' }
    }
    return null
  }

  /** 右侧可见「同意」数（点击成功校验用：处理条消失则减少） */
  private countVisibleAgree(snap: DomSnapshot): number {
    const viewport = viewportOf(snap)
    return this.findText(snap, AGREE_TEXT).filter(
      (h) => h.x > listMaxX(viewport.width) && h.x < viewport.width && h.y > 0 && h.y < viewport.height,
    ).length
  }

  /** 全文精确命中（trim），返回屏幕坐标（已减文档滚动偏移） */
  private findText(snap: DomSnapshot, text: string): TextHit[] {
    const hits: TextHit[] = []
    for (const [stringIndex, value] of snap.strings.entries()) {
    if (value.trim() !== text) continue
    snap.documents.forEach((document, documentIndex) => {
      let offset: ClickPoint
      try { offset = accumulateOwnerOffset(snap, documentIndex) } catch { return }
      for (const { bounds } of findNodesByString(document, stringIndex)) {
        if (bounds[2] <= 0 || bounds[3] <= 0) continue
        const c = boundsCenter(bounds)
        hits.push({
          documentIndex,
          x: offset.x + c.x - (document.scrollOffsetX ?? 0),
          y: offset.y + c.y - (document.scrollOffsetY ?? 0),
        })
      }
    })
    }
    return hits
  }
}

/** PDF 不保证暴露章节文字：限定真实附件 frame 及其关联文档，避免背景推荐画布误报。 */
export function attachmentPreviewState(snap: DomSnapshot): 'closed' | 'loading' | 'ready' {
  const viewport = viewportOf(snap)
  let present = false
  for (const [documentIndex, document] of snap.documents.entries()) {
    const nodeNames = new Map(indexedValues(document.nodes.nodeName ?? [], 'nodeName'))
    const children = new Map(indexedValues(document.nodes.contentDocumentIndex, 'contentDocumentIndex'))
    for (const [nodeIndex, nameIndex] of nodeNames) {
      if (snap.strings[nameIndex] !== 'IFRAME') continue
      if (!classOf(snap, documentIndex, nodeIndex)?.split(/\s+/).includes('attachment-iframe')) continue
      const bounds = document.layout.bounds[document.layout.nodeIndex.indexOf(nodeIndex)]
      if (!bounds || bounds[2]! <= 0 || bounds[3]! <= 0) continue
      let offset: ClickPoint
      try { offset = accumulateOwnerOffset(snap, documentIndex) } catch { continue }
      const x = offset.x + bounds[0]! - (document.scrollOffsetX ?? 0)
      const y = offset.y + bounds[1]! - (document.scrollOffsetY ?? 0)
      const left = Math.max(0, x), top = Math.max(0, y)
      const right = Math.min(viewport.width, x + bounds[2]!), bottom = Math.min(viewport.height, y + bounds[3]!)
      if (right <= left || bottom <= top) continue
      present = true
      const childIndex = children.get(nodeIndex)
      if (childIndex === undefined) continue
      // 保留所有 owner 的几何，只让已绑定附件 document 参与画布定位。
      const scoped = { ...snap, documents: snap.documents.map((doc, index) => index === childIndex ? doc : {
        ...doc, nodes: { ...doc.nodes, nodeName: [] },
      }) }
      const canvas = locateResumeCanvas(scoped)
      if (canvas && Math.min(right, canvas.x + canvas.w) > Math.max(left, canvas.x)
        && Math.min(bottom, canvas.y + canvas.h) > Math.max(top, canvas.y)) return 'ready'
      const child = snap.documents[childIndex]
      if (!child) continue
      let childOffset: ClickPoint
      try { childOffset = accumulateOwnerOffset(snap, childIndex) } catch { continue }
      const visibleSections = PREVIEW_SECTIONS.filter(section => snap.strings.some((value, stringIndex) => value.trim() === section
        && findNodesByString(child, stringIndex).some(hit => {
          const [bx, by, width, height] = hit.bounds
          const sx = childOffset.x + bx - (child.scrollOffsetX ?? 0)
          const sy = childOffset.y + by - (child.scrollOffsetY ?? 0)
          return width > 0 && height > 0 && Math.min(right, sx + width) > Math.max(left, sx)
            && Math.min(bottom, sy + height) > Math.max(top, sy)
        })))
      if (visibleSections.length >= 2) return 'ready'
    }
  }
  return present ? 'loading' : 'closed'
}

/**
 * 左列滚动签名：列表区域（cx<850）全部可见文本节点的 y 坐标拼接。
 * 左列是内层滚动容器（doc scrollOffsetY 恒 0，不能像推荐页那样比 offset），
 * 用文本位置签名判到底：滚动后签名不变 = 列表没动 = 到底。
 */
function listSignatureOf(snap: DomSnapshot): string {
  const ys: number[] = []
  const listBoundary = listMaxX(viewportOf(snap).width)
  for (const document of snap.documents) {
    collectListYs(document, ys, listBoundary)
  }
  return ys.sort((a, b) => a - b).join(',')
}

function collectListYs(document: DomDocument, ys: number[], listBoundary: number): void {
  const scrollY = document.scrollOffsetY ?? 0
  document.layout.nodeIndex.forEach((nodeIndex, layoutIndex) => {
    const b = document.layout.bounds[layoutIndex]
    if (!b || b[2]! <= 0 || b[3]! <= 0) return
    const cx = b[0]! + b[2]! / 2
    if (cx >= listBoundary) return
    ys.push(Math.round(b[1]! + b[3]! / 2 - scrollY))
  })
}
