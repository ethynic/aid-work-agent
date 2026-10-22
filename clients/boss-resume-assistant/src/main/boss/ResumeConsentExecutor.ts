import { createHash } from 'node:crypto'
import { membershipAccountKey } from './MembershipInspector.js'
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
  classOf, isDescendantOf,
} from './domSnapshot.js'
import { viewportOf } from './FilterSetter.js'
import { CancelledError } from '../operations/types.js'
import { BusinessOperationState } from './BusinessOperationState.js'
import { headerContactOf, conversationListNamesOf } from './ChatReadExecutor.js'
import { locateResumeCanvas } from './ResumeReader.js'

export class ConsentError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ConsentError'
  }
}

export interface ConsentDeps {
  state?: BusinessOperationState
  checkpoint?: ConsentCheckpoint
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

interface ConsentRequest {
  key: string
  name: string
  ids: Record<string, string>
  demographics: Record<string, string>
}

export interface ConsentCheckpoint {
  pendingRequest?: ConsentRequest | null
  accepted?: number
  previewed?: number
  current?: { contact: string; previewsBefore: number; noticesBefore: number; processingBarOnly: boolean; accepted?: boolean; previewPhase?: 'opening' | 'closing' | 'closed' }
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
    const checkpoint = this.deps.checkpoint ?? {}
    let accepted = checkpoint.accepted ?? 0
    let previewed = checkpoint.previewed ?? 0
    const fulfill = async () => {
      const current = checkpoint.current!
      if (!current.accepted) {
        let snap = await this.deps.snapshot()
        this.state.bindContact(snap)
        if (this.acceptedSince(snap, current)) {
          current.accepted = true
        } else {
          const agree = this.locateAgreeButton(snap, current.processingBarOnly)
          if (!agree || this.previewCount(snap) !== current.previewsBefore || this.acceptedNoticeCount(snap) !== current.noticesBefore) {
            this.state.unconfirmed('恢复后原请求没有确定的接受或尚未接受证据')
            throw new ConsentError('原接收请求结果仍未知，未重复点击同意')
          }
          this.state.notSubmitted('原会话同意处理条仍在且附件及接受提示基线未变，原请求尚未接受')
          await this.state.input(() => this.deps.click(agree, viewportOf(snap)), true)
          await this.sleep(1500)
          for (let poll = 0; poll < 3; poll++) {
            snap = await this.deps.snapshot()
            this.state.bindContact(snap)
            if (this.acceptedSince(snap, current)) { current.accepted = true; break }
            if (poll < 2) await this.sleep(1000)
          }
          if (!current.accepted) {
            this.state.unconfirmed('原会话缺少处理条消失及新附件/明确同意结果的联合证据')
            throw new ConsentError('无法确认原同意已成功，未以其他同意按钮减少报成功')
          }
        }
        accepted++
        checkpoint.accepted = accepted
        this.state.confirmed({ contact: current.contact, accepted: true }, '原会话处理条消失且新附件或明确同意结果出现')
        this.deps.onProgress?.(accepted)
      }
      if (preview) {
        await this.previewResume(accepted, current)
        previewed++
        checkpoint.previewed = previewed
      }
      checkpoint.current = undefined
    }
    if (checkpoint.pendingRequest === null) throw new ConsentError('恢复后原请求列表行身份无法确认，未选择其他请求人')
    if (checkpoint.current) {
      if (this.deps.signal?.aborted) throw new CancelledError()
      await this.sleep(1500)
      await fulfill()
    }
    for (;;) {
      // 1. 处理当前可见的全部目标会话
      for (;;) {
        if (this.deps.signal?.aborted) throw new CancelledError(`已取消：同意接收 ${accepted} 人后中止`)
        if (accepted >= limit) return { accepted, previewed, reachedEnd: false }
        const snap = await this.deps.snapshot()
        const targets = this.findListTargets(snap)
        if (targets.length === 0) break

        const pending = checkpoint.pendingRequest
        const matched = pending ? targets.filter(point => this.requestIdentity(snap, point)?.key === pending.key) : targets
        if (pending && matched.length !== 1) throw new ConsentError('恢复后原请求列表行不再唯一，未选择其他请求人')
        const target = matched[0]!
        checkpoint.pendingRequest ??= this.requestIdentity(snap, target) ?? null
        this.state.startItem()
        await this.state.input(() => this.deps.click(target, viewportOf(snap)))
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
        if (checkpoint.pendingRequest && !this.requestMatchesHeader(agreeSnap, checkpoint.pendingRequest)) throw new ConsentError('原请求人稳定身份未匹配，未对当前其他会话同意')
        this.state.bindContact(agreeSnap, accepted === 0 ? opts.expectedContactRef : undefined)
        checkpoint.pendingRequest = undefined
        checkpoint.current = { contact: headerContactOf(agreeSnap), previewsBefore: this.previewCount(agreeSnap),
          noticesBefore: this.acceptedNoticeCount(agreeSnap), processingBarOnly: agree.source === 'processing_bar' }
        await fulfill()
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

  private stableIdentity(snap: DomSnapshot, di: number, root: number): Pick<ConsentRequest, 'ids' | 'demographics'> {
    const doc = snap.documents[di]!, ids: Record<string, string> = {}, demographics: Record<string, string> = {}
    for (const [node, attributes] of indexedValues(doc.nodes.attributes, 'attributes')) {
      if (isDescendantOf(doc, node, root) !== true) continue
      for (let i = 0; i + 1 < attributes.length; i += 2) {
        const key = snap.strings[attributes[i]!] ?? ''
        if (/^data-(?:geek-id|uid|user-id|encrypt-geek-id)$/.test(key)) ids[key] = snap.strings[attributes[i + 1]!] ?? ''
      }
    }
    const texts = indexedValues(doc.nodes.nodeValue, 'nodeValue').filter(([node, index]) => index >= 0 && isDescendantOf(doc, node, root) === true)
    for (const [, index] of texts) for (const value of (snap.strings[index] ?? '').split(/[|·\s]+/u)) {
      const kind = /^\d+岁$/.test(value) ? 'age' : /^\d+(?:-\d+)?年(?:经验)?$|^应届生$/.test(value) ? 'experience'
        : /^(?:本科|硕士|博士|大专|高中|中专)$/.test(value) ? 'education' : /^(?:男|女)$/.test(value) ? 'gender' : null
      if (kind) demographics[kind] = kind === 'experience' ? value.replace(/经验$/u, '') : value
    }
    return { ids, demographics }
  }

  private requestIdentities(snap: DomSnapshot, point?: ClickPoint): ConsentRequest[] {
    const names = conversationListNamesOf(snap), matches: ConsentRequest[] = []
    snap.documents.forEach((doc, di) => {
      for (const [row] of indexedValues(doc.nodes.attributes, 'attributes')) {
        if (!(classOf(snap, di, row) ?? '').split(/\s+/).includes('geek-item')) continue
        const texts = indexedValues(doc.nodes.nodeValue, 'nodeValue').filter(([ni, si]) => si >= 0 && isDescendantOf(doc, ni, row) === true)
        if (point && !texts.some(([ni]) => {
          const bounds = doc.layout.bounds[doc.layout.nodeIndex.indexOf(ni)]
          if (!bounds) return false
          const offset = accumulateOwnerOffset(snap, di)
          const x = offset.x + bounds[0]! - (doc.scrollOffsetX ?? 0), y = offset.y + bounds[1]! - (doc.scrollOffsetY ?? 0)
          return point.x >= x && point.x <= x + bounds[2]! && point.y >= y && point.y <= y + bounds[3]!
        })) continue
        const values = texts.map(([, si]) => snap.strings[si]?.trim()).filter(Boolean)
        const rowNames = names.filter(name => values.includes(name))
        if (rowNames.length !== 1) continue
        const identity = this.stableIdentity(snap, di, row)
        // Unread badges, last message and activity times may change as soon as the row is opened.
        matches.push({ key: createHash('sha256').update(JSON.stringify([membershipAccountKey(snap), rowNames[0], identity])).digest('hex'),
          name: rowNames[0]!, ...identity })
      }
    })
    return matches
  }

  private requestIdentity(snap: DomSnapshot, point: ClickPoint): ConsentRequest | undefined {
    const matches = this.requestIdentities(snap, point)
    return matches.length === 1 ? matches[0] : undefined
  }

  private requestMatchesHeader(snap: DomSnapshot, request: ConsentRequest): boolean {
    if (headerContactOf(snap) !== request.name) return false
    const doc = snap.documents[0]!
    const roots = indexedValues(doc.nodes.attributes, 'attributes').filter(([node]) =>
      (classOf(snap, 0, node) ?? '').split(/\s+/).includes('base-info-single-container'))
    if (roots.length !== 1) return false
    const header = this.stableIdentity(snap, 0, roots[0]![0])
    const compatible = (row: ConsentRequest) => (['ids', 'demographics'] as const).every(kind =>
      Object.entries(row[kind]).every(([key, value]) => header[kind][key] === undefined || header[kind][key] === value))
    if (!compatible(request)) return false
    // Opening an unread request may remove its row from that filtered list. A common stable
    // identity already proves the original contact; absence of the list row cannot undo it.
    if (Object.keys(request.ids).some(key => !!request.ids[key] && header.ids[key] === request.ids[key])) return true
    const candidates = this.requestIdentities(snap).filter(row => row.name === request.name && compatible(row))
    // Use fields both surfaces expose; absent header fields do not invalidate a unique name,
    // but cannot disambiguate two otherwise compatible same-name rows.
    return candidates.length === 1 && candidates[0]!.key === request.key
  }

  private acceptedSince(snap: DomSnapshot, current: NonNullable<ConsentCheckpoint['current']>): boolean {
    return !this.locateAgreeButton(snap, current.processingBarOnly)
      && (this.previewCount(snap) > current.previewsBefore || this.acceptedNoticeCount(snap) > current.noticesBefore)
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
  private async previewResume(nth: number, checkpoint: NonNullable<ConsentCheckpoint['current']>): Promise<void> {
    if (checkpoint.previewPhase === 'closed') return
    if (checkpoint.previewPhase === 'closing') {
      const snap = await this.deps.snapshot()
      if (attachmentPreviewState(snap) === 'closed') { checkpoint.previewPhase = 'closed'; return }
    }
    const existing = checkpoint.previewPhase ? await this.waitForPreview() : undefined
    if (!existing || attachmentPreviewState(existing) === 'closed') {
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
      checkpoint.previewPhase = 'opening'
      await this.state.input(() => this.deps.click(freshPreview, viewportOf(snap)))
      await this.sleep(2000)

    }
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
    checkpoint.previewPhase = 'closing'
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
    checkpoint.previewPhase = 'closed'
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
