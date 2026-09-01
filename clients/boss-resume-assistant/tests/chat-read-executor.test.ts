/**
 * ChatReadExecutor 单测（§10.9）：当前会话消息解析 + 未读清单 + 总徽章 + contact 校验。
 * fixture 按真机快照形状建模（tests/chatReadFixture.ts，坐标取 .tmp/chat-snapshot-01.json 实测值）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { ChatReadError, ChatReadExecutor } from '../src/main/boss/ChatReadExecutor.js'
import { classOf } from '../src/main/boss/domSnapshot.js'
import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'
import { buildChatSnapshot, yangSnapshot } from './chatReadFixture.js'

function executorOf(snap: DomSnapshot): ChatReadExecutor {
  return new ChatReadExecutor({ snapshot: async () => snap })
}

// ---------- classOf（domSnapshot 扩展） ----------

test('classOf：按 Chrome 151 attributes 嵌套数组形状拼出 class；无 class/无 attributes 返回 undefined', () => {
  const snap = buildChatSnapshot([
    { tag: 'DIV', cls: 'conversation-message clearfix', bounds: [1, 1, 10, 10], children: [
      { tag: 'SPAN', bounds: [2, 2, 5, 5] },
      { tag: '#text', text: 'hi', bounds: [2, 2, 5, 5] },
    ] },
  ])
  // node0=#document、node1=DIV、node2=SPAN、node3=#text
  assert.equal(classOf(snap, 0, 1), 'conversation-message clearfix')
  assert.equal(classOf(snap, 0, 2), undefined) // 有 attributes 字段但节点无 class
  assert.equal(classOf(snap, 0, 3), undefined) // #text 节点 attributes=[]（真机同形）
  assert.equal(classOf(snap, 0, 99), undefined) // 不存在的节点
  // 无 attributes 字段（形状缺失容忍）：返回 undefined 而非抛错
  const noAttrs = snap as DomSnapshot
  delete (noAttrs.documents[0]!.nodes as { attributes?: unknown }).attributes
  assert.equal(classOf(noAttrs, 0, 1), undefined)
  // sparse {index,value} 形状（真机未见过，形状兼容）：按 index 取该节点条目
  const sparse = {
    strings: snap.strings,
    documents: [
      {
        nodes: {
          nodeValue: snap.documents[0]!.nodes.nodeValue,
          contentDocumentIndex: { index: [], value: [] },
          parentIndex: snap.documents[0]!.nodes.parentIndex,
          nodeName: snap.documents[0]!.nodes.nodeName,
          attributes: { index: [1], value: [[snap.strings.indexOf('class'), snap.strings.indexOf('conversation-message clearfix')]] },
        },
        layout: snap.documents[0]!.layout,
      },
    ],
  } as unknown as DomSnapshot
  assert.equal(classOf(sparse, 0, 1), 'conversation-message clearfix')
  assert.equal(classOf(sparse, 0, 2), undefined)
})

// ---------- 主场景（真机杨鸿杰会话复刻） ----------

test('杨鸿杰场景：system 职位卡(10:56) + 我方消息(已读，ts 继承 10:56) + 对方消息(11:04)，精确断言', async () => {
  const r = await executorOf(yangSnapshot()).read()
  assert.equal(r.contact, '杨鸿杰')
  assert.deepEqual(r.messages, [
    { sender: 'system', text: '8月27日 沟通的职位-PHP 开发工程师', ts: '10:56' },
    // 时间行向后继承（真机：时间分隔行描述其后所有消息）：组 2 无自身时间行 → 继承组 1 的 10:56
    { sender: 'me', text: '你好，我们正在诚招PHP 开发工程师，想跟你沟通一下', ts: '10:56', read: true },
    { sender: 'them', text: '您好，请问该岗位是外包嘛', ts: '11:04' },
  ])
})

test('杨鸿杰场景：未读清单按 y 升序（含视口外 y=3238），[送达] 拆分预览拼后去前缀，时间 trim', async () => {
  const r = await executorOf(yangSnapshot()).read()
  assert.deepEqual(r.unread, [
    { name: '席彬玮', count: 2, time: '10:42', lastPreview: '你好，我们正在诚招PHP 开发工程师，想跟你沟通一下' },
    { name: '周天一', count: 1, time: '09:15', lastPreview: '好的谢谢' },
  ])
})

test('杨鸿杰场景：总徽章=214；SVG path 数字串 / 「新」徽章 / x>188 数字徽章 / 含数字非纯数字文本均不命中', async () => {
  const r = await executorOf(yangSnapshot()).read()
  assert.equal(r.totalUnreadBadge, 214)
})

// ---------- 双消息会话（组时间挂载与噪声过滤） ----------

test('双消息会话：无时间行组 ts 向后继承上一时间行；窄行时间文本与非 text-content 文本按噪声忽略', async () => {
  const snap = buildChatSnapshot([
    { tag: 'DIV', cls: 'base-info-single-container', bounds: [548, 110, 680, 60], children: [
      { tag: '#text', text: '王五', bounds: [578, 120, 40, 18] },
      { tag: '#text', text: '3年', bounds: [640, 121, 20, 14] },
    ] },
    { tag: 'DIV', cls: 'conversation-message', bounds: [548, 277, 680, 812], children: [
      // 组 1：时间行 + 对方消息
      { tag: 'DIV', cls: 'message-item', bounds: [578, 300, 620, 60], children: [
        { tag: 'DIV', cls: 'message-time', bounds: [578, 300, 620, 20], children: [
          { tag: '#text', text: ' 09:00', bounds: [873, 302, 30, 14] },
        ] },
        { tag: 'DIV', cls: 'item-friend', bounds: [578, 330, 620, 30], children: [
          { tag: 'SPAN', cls: 'text-content', bounds: [626, 336, 120, 16], children: [
            { tag: '#text', text: '你好，在吗', bounds: [626, 336, 120, 16] },
          ] },
        ] },
      ] },
      // 组 2：无时间行，两条我方消息（不同 item-myself 行）
      { tag: 'DIV', cls: 'message-item', bounds: [578, 380, 620, 80], children: [
        { tag: 'DIV', cls: 'item-myself', bounds: [578, 380, 620, 30], children: [
          { tag: 'SPAN', cls: 'text-content', bounds: [845, 386, 60, 16], children: [
            { tag: '#text', text: '在的', bounds: [845, 386, 60, 16] },
          ] },
        ] },
        { tag: 'DIV', cls: 'item-myself', bounds: [578, 420, 620, 30], children: [
          { tag: 'SPAN', cls: 'text-content', bounds: [845, 426, 60, 16], children: [
            { tag: '#text', text: '稍等', bounds: [845, 426, 60, 16] },
          ] },
        ] },
      ] },
      // 噪声 1：窄 DIV 里的时间样式文本（宽 40 ≠ 消息区宽 680 的 80%）——不当时间行
      { tag: 'DIV', cls: 'float-tip', bounds: [600, 520, 40, 14], children: [
        { tag: '#text', text: '23:59', bounds: [600, 520, 40, 14] },
      ] },
      // 噪声 2：item-myself 行内但不在 text-content 下的文本（如草稿状态角标）——不当消息
      { tag: 'DIV', cls: 'message-item', bounds: [578, 560, 620, 40], children: [
        { tag: 'DIV', cls: 'item-myself', bounds: [578, 560, 620, 40], children: [
          { tag: 'DIV', cls: 'draft-hint', bounds: [845, 566, 60, 14], children: [
            { tag: '#text', text: '发送失败', bounds: [845, 566, 60, 14] },
          ] },
          { tag: 'SPAN', cls: 'text-content', bounds: [845, 586, 80, 16], children: [
            { tag: '#text', text: '重发一条', bounds: [845, 586, 80, 16] },
          ] },
        ] },
      ] },
    ] },
  ])
  const r = await executorOf(snap).read()
  assert.equal(r.contact, '王五')
  assert.deepEqual(r.messages, [
    { sender: 'them', text: '你好，在吗', ts: '09:00' },
    // 组 2/3 无自身时间行 → 向后继承组 1 的 09:00（真机：时间分隔行描述其后所有消息）
    { sender: 'me', text: '在的', ts: '09:00' },
    { sender: 'me', text: '稍等', ts: '09:00' },
    { sender: 'me', text: '重发一条', ts: '09:00' }, // 同行 draft-hint 文本被忽略，text-content 正文保留
  ])
  assert.deepEqual(r.unread, [])
  assert.equal(r.totalUnreadBadge, undefined) // 无纯数字导航徽章 → 缺省
})

// ---------- fail-loud：未打开会话 / 结构漂移 ----------

test('未打开会话（无 conversation-message 容器）：报「当前沟通页未打开会话」', async () => {
  const snap = buildChatSnapshot([
    { tag: 'DIV', cls: 'user-list', bounds: [188, 196, 359, 1088], children: [
      { tag: '#text', text: '杨鸿杰', bounds: [264, 300, 45, 17] },
    ] },
  ])
  await assert.rejects(executorOf(snap).read(), (err: unknown) => {
    assert.ok(err instanceof ChatReadError)
    assert.match(err.message, /当前沟通页未打开会话/)
    return true
  })
})

test('消息区存在但全是噪声（class 失效/结构漂移）：fail-loud 请人工查看', async () => {
  const snap = buildChatSnapshot([
    { tag: 'DIV', cls: 'conversation-message', bounds: [548, 277, 680, 812], children: [
      { tag: 'DIV', cls: 'unknown-bubble', bounds: [600, 300, 100, 20], children: [
        { tag: '#text', text: '无法归类的文本', bounds: [600, 300, 100, 20] },
      ] },
    ] },
  ])
  await assert.rejects(executorOf(snap).read(), (err: unknown) => {
    assert.ok(err instanceof ChatReadError)
    assert.match(err.message, /未解析出任何消息/)
    return true
  })
})

test('多个 conversation-message 容器（期望唯一）：fail-loud', async () => {
  const snap = buildChatSnapshot([
    { tag: 'DIV', cls: 'conversation-message', bounds: [548, 277, 680, 400] },
    { tag: 'DIV', cls: 'conversation-message', bounds: [548, 700, 680, 400] },
  ])
  await assert.rejects(executorOf(snap).read(), (err: unknown) => {
    assert.ok(err instanceof ChatReadError)
    assert.match(err.message, /2 个 conversation-message/)
    return true
  })
})

test('DOMSnapshot 缺 attributes 字段：明说形状缺失（而非误报未打开会话）', async () => {
  const snap = yangSnapshot() as DomSnapshot
  delete (snap.documents[0]!.nodes as { attributes?: unknown }).attributes
  await assert.rejects(executorOf(snap).read(), (err: unknown) => {
    assert.ok(err instanceof ChatReadError)
    assert.match(err.message, /缺少 nodes\.attributes/)
    return true
  })
})

test('左导航多个纯数字徽章：无法确定总未读数 → fail-loud 列出命中值', async () => {
  const snap = buildChatSnapshot([
    // 两个纯数字导航徽章（另一菜单也挂数字徽章的场景）
    { tag: '#text', text: '214', bounds: [131, 240, 20, 14] },
    { tag: '#text', text: '3', bounds: [100, 300, 10, 14] },
    // 最小可用会话（总徽章在消息解析之后读取，须先通过会话校验）
    { tag: 'DIV', cls: 'conversation-message', bounds: [548, 277, 680, 812], children: [
      { tag: 'DIV', cls: 'message-item', bounds: [578, 300, 620, 40], children: [
        { tag: 'DIV', cls: 'item-friend', bounds: [578, 300, 620, 30], children: [
          { tag: 'SPAN', cls: 'text-content', bounds: [626, 306, 60, 16], children: [
            { tag: '#text', text: '在吗', bounds: [626, 306, 60, 16] },
          ] },
        ] },
      ] },
    ] },
  ])
  await assert.rejects(executorOf(snap).read(), (err: unknown) => {
    assert.ok(err instanceof ChatReadError)
    assert.match(err.message, /2 个纯数字徽章/)
    assert.match(err.message, /214、3/)
    return true
  })
})

// ---------- contact 参数 ----------

test('contact 匹配当前会话：正常读取（trim 全等）', async () => {
  const r = await executorOf(yangSnapshot()).read({ contact: ' 杨鸿杰 ' })
  assert.equal(r.contact, '杨鸿杰')
  assert.equal(r.messages.length, 3)
})

test('contact 不匹配但会话列表存在：报「存在但未打开，请先切换（不自动切换）」', async () => {
  await assert.rejects(executorOf(yangSnapshot()).read({ contact: '席彬玮' }), (err: unknown) => {
    assert.ok(err instanceof ChatReadError)
    assert.match(err.message, /会话「席彬玮」存在但未打开/)
    assert.match(err.message, /不自动切换/)
    return true
  })
})

test('contact 不存在：报错并列出可用姓名（含视口外会话）', async () => {
  await assert.rejects(executorOf(yangSnapshot()).read({ contact: '张三丰' }), (err: unknown) => {
    assert.ok(err instanceof ChatReadError)
    assert.match(err.message, /不存在「张三丰」/)
    assert.match(err.message, /杨鸿杰/)
    assert.match(err.message, /席彬玮/)
    assert.match(err.message, /周天一/) // 视口外会话也在可用名单（列表未虚拟化全量可读）
    return true
  })
})

test('contact 给定且未打开任何会话：仍走名单校验（找不到 → 列可用姓名）', async () => {
  const snap = buildChatSnapshot([
    { tag: 'DIV', cls: 'user-list', bounds: [188, 196, 359, 1088], children: [
      { tag: 'DIV', cls: 'geek-item', bounds: [198, 300, 339, 74], children: [
        { tag: '#text', text: '李雷', bounds: [264, 315, 30, 17] },
      ] },
    ] },
  ])
  await assert.rejects(executorOf(snap).read({ contact: '韩梅梅' }), (err: unknown) => {
    assert.ok(err instanceof ChatReadError)
    assert.match(err.message, /可用联系人：李雷/)
    return true
  })
})

test('user-list 容器缺失：回退未读算法会话项全集找姓名', async () => {
  // 无 user-list 容器，只有带徽章的会话项（badge → 高 60~100 DIV 祖先）——名单来自未读项
  const snap = buildChatSnapshot([
    { tag: 'DIV', cls: 'conversation-message', bounds: [548, 277, 680, 812], children: [
      { tag: 'DIV', cls: 'message-item', bounds: [578, 300, 620, 40], children: [
        { tag: 'DIV', cls: 'item-friend', bounds: [578, 300, 620, 30], children: [
          { tag: 'SPAN', cls: 'text-content', bounds: [626, 306, 60, 16], children: [
            { tag: '#text', text: '在吗', bounds: [626, 306, 60, 16] },
          ] },
        ] },
      ] },
    ] },
    { tag: 'DIV', cls: 'geek-item', bounds: [198, 300, 339, 74], children: [
      { tag: 'SPAN', cls: 'badge-count', bounds: [238, 313, 20, 16], children: [
        { tag: '#text', text: '4', bounds: [242, 314, 8, 14] },
      ] },
      { tag: '#text', text: '赵六', bounds: [264, 315, 30, 17] },
      { tag: '#text', text: '昨天', bounds: [497, 317, 30, 14] },
      { tag: '#text', text: '收到，谢谢', bounds: [264, 342, 80, 14] },
    ] },
  ])
  const r = await executorOf(snap).read()
  assert.deepEqual(r.unread, [{ name: '赵六', count: 4, time: '昨天', lastPreview: '收到，谢谢' }])
  await assert.rejects(executorOf(snap).read({ contact: '钱七' }), (err: unknown) => {
    assert.ok(err instanceof ChatReadError)
    assert.match(err.message, /可用联系人：赵六/)
    return true
  })
})
