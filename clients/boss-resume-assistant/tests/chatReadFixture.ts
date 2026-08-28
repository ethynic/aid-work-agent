/**
 * 沟通页 DOMSnapshot 测试 fixture 构造器（ChatReadExecutor / boss_read_chat 共用）。
 *
 * 按 Chrome 151 真机快照（.tmp/chat-snapshot-01.json，2026-08-27 采）的**真实形状**建模全字段：
 * - strings 去重字符串表；nodeValue / parentIndex / nodeName / attributes 均为 dense 数组
 *   （attributes[nodeIdx] = [nameIdx, valIdx, ...]，无属性节点为 []，与真机 5203 项一致）；
 * - layout.nodeIndex + bounds 只收录有 bounds 的节点（真机视口外节点同样带 bounds——列表未虚拟化）；
 * - 坐标取真机实测值（窗口 1249x1277）：左导航徽章 x≈131、会话列表 x∈[188,547]（姓名 x≈264 /
 *   时间 x≈497）、聊天面板 x=548 起、消息区 (548,277,680,812)、时间行高 20 宽 620。
 */
import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'

export interface FixtureNode {
  /** 元素标签名（大写如 'DIV'）；'#text' 表示文本节点（配 text） */
  tag: string
  /** class 属性值（生成 attributes 条目） */
  cls?: string
  /** #text 的文本内容（仅 tag='#text'） */
  text?: string
  /** 额外属性（如 SVG path 的 d="M12 214..."——数字片段进 strings 表但不是文本节点） */
  attrs?: Record<string, string>
  bounds?: [number, number, number, number]
  children?: FixtureNode[]
}

/** 构造真实形状的 DomSnapshot：node0=#document，其余按树序展开 */
export function buildChatSnapshot(rootChildren: FixtureNode[]): DomSnapshot {
  const strings: string[] = []
  const stringIndexOf = (s: string): number => {
    let i = strings.indexOf(s)
    if (i < 0) {
      strings.push(s)
      i = strings.length - 1
    }
    return i
  }

  const nodeValue: number[] = []
  const parentIndex: number[] = []
  const nodeNameIdx: number[] = []
  const attributes: number[][] = []
  const layoutNodeIndex: number[] = []
  const layoutBounds: Array<[number, number, number, number]> = []

  const append = (node: FixtureNode, parent: number): number => {
    const nodeIndex = nodeValue.length
    parentIndex.push(parent)
    if (node.tag === '#text') {
      nodeValue.push(stringIndexOf(node.text ?? ''))
      nodeNameIdx.push(stringIndexOf('#text'))
      attributes.push([])
    } else {
      nodeValue.push(-1)
      nodeNameIdx.push(stringIndexOf(node.tag))
      const attrPairs: number[] = []
      if (node.cls !== undefined) {
        attrPairs.push(stringIndexOf('class'), stringIndexOf(node.cls))
      }
      for (const [name, value] of Object.entries(node.attrs ?? {})) {
        attrPairs.push(stringIndexOf(name), stringIndexOf(value))
      }
      attributes.push(attrPairs)
    }
    if (node.bounds) {
      layoutNodeIndex.push(nodeIndex)
      layoutBounds.push(node.bounds)
    }
    for (const child of node.children ?? []) append(child, nodeIndex)
    return nodeIndex
  }

  // node0 = #document（真机根节点，无父节点 -1）
  nodeValue.push(-1)
  parentIndex.push(-1)
  nodeNameIdx.push(stringIndexOf('#document'))
  attributes.push([])
  layoutNodeIndex.push(0)
  layoutBounds.push([0, 0, 1249, 1277])
  for (const child of rootChildren) append(child, 0)

  return {
    strings,
    documents: [
      {
        nodes: {
          nodeValue,
          contentDocumentIndex: { index: [], value: [] },
          parentIndex,
          nodeName: nodeNameIdx,
          attributes,
        },
        layout: { nodeIndex: layoutNodeIndex, bounds: layoutBounds },
        scrollOffsetX: 0,
        scrollOffsetY: 0,
      },
    ],
  }
}

/** 左导航「沟通」总徽章（真机实测 214，x≈131） */
export function navBadge(count: string): FixtureNode {
  return {
    tag: 'DIV',
    cls: 'menu-chat-badge badge',
    bounds: [80, 230, 100, 40], // 高 40：不在会话项 60~100 区间，locateUnreadItems 不会误收
    children: [
      { tag: 'SPAN', cls: 'badge-count badge-count-alone', bounds: [128, 239, 26, 16], children: [
        { tag: '#text', text: count, bounds: [131, 240, 20, 14] },
      ] },
    ],
  }
}

/** 左列表会话项（geek-item 高 74，真机几何：姓名 x≈264 / 职位 x≈313 / 时间 x≈497 / 预览第二行 x≈264） */
export function conversationItem(opts: {
  y: number
  name: string
  job?: string
  time?: string
  preview?: string
  /** 未读数（缺省=无徽章，即已读会话） */
  count?: number
  /** 预览拆成 "[送达]" + 正文两个同排文本节点（真机实测形态） */
  deliverPrefix?: boolean
  selected?: boolean
}): FixtureNode {
  const children: FixtureNode[] = []
  if (opts.count !== undefined) {
    children.push({
      tag: 'SPAN',
      cls: 'badge-count',
      bounds: [238, opts.y + 13, 20, 16],
      children: [{ tag: '#text', text: String(opts.count), bounds: [242, opts.y + 14, 8, 14] }],
    })
  }
  const nameRowY = opts.y + 15
  children.push({ tag: '#text', text: opts.name, bounds: [264, nameRowY, 45, 17] })
  if (opts.job) children.push({ tag: '#text', text: opts.job, bounds: [313, nameRowY + 2, 88, 14] })
  if (opts.time) {
    // 真机时间文本带换行缩进："\n                    10:42\n                "（trim 后才是时间）
    children.push({ tag: '#text', text: `\n                    ${opts.time}\n                `, bounds: [497, nameRowY + 2, 30, 14] })
  }
  if (opts.preview) {
    const previewY = opts.y + 42
    if (opts.deliverPrefix) {
      // 拆分形态："[送达]" 与正文两个同排 #text（真机 node 2689/2691）
      children.push({ tag: '#text', text: '[送达]', bounds: [264, previewY, 31, 14] })
      children.push({ tag: '#text', text: opts.preview, bounds: [299, previewY, 292, 14] })
    } else {
      children.push({ tag: '#text', text: opts.preview, bounds: [264, previewY, 264, 14] })
    }
  }
  return {
    tag: 'DIV',
    cls: `geek-item${opts.selected ? ' selected' : ''}`,
    bounds: [198, opts.y, 339, 74],
    children,
  }
}

/**
 * 主场景（真机杨鸿杰会话复刻，§10.9）：10:56 时间行 + 职位卡 system + 我方消息（已读）+ 11:04 + 对方消息；
 * 未读列表 3 项（席彬玮 [送达] 拆分预览 / 视口外周天一 y=3238 / 当前打开项杨鸿杰无徽章）；
 * 左导航总徽章 214 + 干扰项（SVG path 数字串、非数字徽章「新」、x>188 数字徽章、含数字非纯数字文本）。
 *
 * open-chat 场景扩展（2026-08-27）：contact 换头部姓名（切换会话后）、searchAnchor 追加搜索入口
 * 锚点（批量 + 图标 DIV，locateSearchEntry 主锚）、extra 追加搜索路径节点（搜索框 INPUT/结果浮层/
 * 发送按钮）、listItems 替换会话列表（滚动场景把视口外项移入视口）。
 */
export interface YangSnapshotOpts {
  /** 头部联系人姓名（默认 杨鸿杰；open-chat 切换会话后的快照用） */
  contact?: string
  /** 追加搜索入口锚点：批量 #text + 图标容器 DIV（ChatSearchExecutor.locateSearchEntry 主锚） */
  searchAnchor?: boolean
  /** 追加到 chat-page 的额外节点（搜索框 INPUT、结果浮层、发送按钮等搜索路径快照用） */
  extra?: FixtureNode[]
  /** 替换会话列表项（默认真机三件套；滚动场景把周天一移入视口用） */
  listItems?: FixtureNode[]
}

/** 搜索入口锚点（真机嗅探：批量 #text @(510,171,26,15) + 图标 DIV 34x34 @(503,124)） */
function searchAnchorNodes(): FixtureNode[] {
  return [
    { tag: '#text', text: '批量', bounds: [510, 171, 26, 15] },
    { tag: 'DIV', cls: 'chat-op-btn', bounds: [503, 124, 34, 34] },
  ]
}

export function yangSnapshot(opts: YangSnapshotOpts = {}): DomSnapshot {
  const contact = opts.contact ?? '杨鸿杰'
  const listItems = opts.listItems ?? [
    conversationItem({ y: 289, name: '杨鸿杰', job: 'PHP 开发工程师', time: '11:04', preview: '您好，请问该岗位是外包嘛', selected: true }),
    conversationItem({ y: 430, name: '席彬玮', job: 'PHP 开发工程师', time: '10:42', preview: '你好，我们正在诚招PHP 开发工程师，想跟你沟通一下', count: 2, deliverPrefix: true }),
    // 视口外（y=3238 > 1277）——列表未虚拟化，bounds 全量可读（真机实测 26/37 徽章在视口外）
    conversationItem({ y: 3238, name: '周天一', time: '09:15', preview: '好的谢谢', count: 1 }),
  ]
  return buildChatSnapshot([
    {
      tag: 'DIV',
      cls: 'chat-page',
      children: [
        ...(opts.searchAnchor ? searchAnchorNodes() : []),
        ...(opts.extra ?? []),
        // ---- 左导航（x<188）：总徽章 214 + 干扰 ----
        {
          tag: 'DIV',
          cls: 'left-nav',
          bounds: [0, 0, 188, 1277],
          children: [
            navBadge('214'),
            // 非数字徽章（「新」）——不应进总徽章/未读清单
            { tag: 'SPAN', cls: 'badge-count badge-count-alone', bounds: [136, 287, 18, 16], children: [
              { tag: '#text', text: '新', bounds: [138, 288, 14, 14] },
            ] },
            // 含数字但非纯数字文本（锁死「全等匹配」：includes 会误命中）
            { tag: '#text', text: 'M214 5L', bounds: [100, 320, 40, 14] },
            // SVG path 干扰：d 属性纯数字片段进 strings 表，但不是文本节点（禁 includes 的真机坑来源）
            { tag: 'SVG', bounds: [10, 400, 20, 20], children: [
              { tag: 'PATH', attrs: { d: 'M12 214 L32 14 88 5 214' } },
            ] },
          ],
        },
        // ---- 会话列表（user-list）：当前打开项 + 2 个未读项 + 1 个视口外未读项 ----
        {
          tag: 'DIV',
          cls: 'user-list b-scroll-stable',
          bounds: [188, 196, 359, 1088],
          children: listItems,
        },
        // ---- 顶部导航干扰徽章（x>188 且无 60~100 DIV 祖先）：数字也不进总徽章/未读清单 ----
        { tag: 'SPAN', cls: 'badge-count badge-count-common-less', bounds: [714, 5, 16, 16], children: [
          { tag: '#text', text: '5', bounds: [715, 6, 10, 14] },
        ] },
        // ---- 右侧：头部识别带 + 消息区 ----
        {
          tag: 'DIV',
          cls: 'base-info-single-container',
          bounds: [548, 110, 680, 167],
          children: [
            { tag: '#text', text: contact, bounds: [578, 130, 54, 20] },
            { tag: '#text', text: '26岁', bounds: [663, 131, 30, 16] },
            { tag: '#text', text: '5年', bounds: [712, 131, 22, 16] },
            { tag: '#text', text: '本科', bounds: [754, 131, 28, 16] },
            { tag: '#text', text: '在线简历', bounds: [1034, 131, 52, 15] },
            { tag: '#text', text: '附件简历', bounds: [1136, 131, 52, 15] },
          ],
        },
        {
          tag: 'DIV',
          cls: 'conversation-message',
          bounds: [548, 277, 680, 812],
          children: [
            {
              tag: 'DIV',
              cls: 'chat-message-list',
              bounds: [548, 277, 680, 812],
              children: [
                // 消息组 1：时间行 10:56 + item-system 职位卡（#text→item-system 深度 6→message-item 深度 8，真机同构）
                {
                  tag: 'DIV',
                  cls: 'message-item',
                  bounds: [578, 297, 620, 126],
                  children: [
                    { tag: 'DIV', cls: 'message-time', bounds: [578, 297, 620, 20], children: [
                      { tag: 'SPAN', cls: 'time', bounds: [873, 300, 30, 14], children: [
                        { tag: '#text', text: ' 10:56', bounds: [873, 300, 30, 14] },
                      ] },
                    ] },
                    { tag: 'DIV', cls: 'item-resume', bounds: [578, 337, 620, 86], children: [
                      { tag: 'DIV', cls: 'item-system', bounds: [578, 337, 620, 86], children: [
                        { tag: 'DIV', cls: 'message-card-wrap blue', bounds: [758, 337, 260, 86], children: [
                          { tag: 'DIV', cls: 'message-card-top-wrap', bounds: [771, 358, 234, 44], children: [
                            { tag: 'DIV', cls: 'message-card-top-content', bounds: [827, 360, 178, 40], children: [
                              { tag: 'H3', cls: 'message-card-top-title', bounds: [827, 360, 178, 40], children: [
                                { tag: '#text', text: '8月27日 沟通的职位-PHP 开发工程师', bounds: [827, 362, 176, 36] },
                              ] },
                            ] },
                          ] },
                        ] },
                      ] },
                    ] },
                  ],
                },
                // 消息组 2：我方消息 + I.status「已读」（无时间行 → ts 缺省）
                {
                  tag: 'DIV',
                  cls: 'message-item',
                  bounds: [578, 443, 620, 36],
                  children: [
                    { tag: 'DIV', cls: 'item-myself clearfix', bounds: [578, 443, 620, 36], children: [
                      { tag: 'DIV', cls: 'text', bounds: [833, 443, 364, 36], children: [
                        { tag: 'I', cls: 'status status-read', bounds: [804, 462, 24, 17], children: [
                          { tag: '#text', text: '已读', bounds: [804, 463, 24, 14] },
                        ] },
                        { tag: 'SPAN', cls: 'text-content', bounds: [845, 453, 340, 16], children: [
                          { tag: '#text', text: '你好，我们正在诚招PHP 开发工程师，想跟你沟通一下', bounds: [845, 453, 340, 16] },
                        ] },
                      ] },
                    ] },
                  ],
                },
                // 消息组 3：时间行 11:04 + 对方消息
                {
                  tag: 'DIV',
                  cls: 'message-item',
                  bounds: [578, 499, 620, 76],
                  children: [
                    { tag: 'DIV', cls: 'message-time', bounds: [578, 499, 620, 20], children: [
                      { tag: 'SPAN', cls: 'time', bounds: [873, 502, 29, 14], children: [
                        { tag: '#text', text: ' 11:04', bounds: [873, 502, 29, 14] },
                      ] },
                    ] },
                    { tag: 'DIV', cls: 'item-friend clearfix', bounds: [578, 539, 620, 36], children: [
                      { tag: 'DIV', cls: 'text', bounds: [626, 539, 192, 36], children: [
                        { tag: 'SPAN', cls: 'text-content', bounds: [638, 549, 168, 16], children: [
                          { tag: '#text', text: '您好，请问该岗位是外包嘛', bounds: [638, 549, 168, 16] },
                        ] },
                      ] },
                    ] },
                  ],
                },
              ],
            },
          ],
        },
      ],
    },
  ])
}
