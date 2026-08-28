/**
 * ChatOpenExecutor 单测（open-chat：already / 搜索优先 / 列表兜底，2026-08-27）。
 * 全程离线 fixture（tests/chatReadFixture.ts 的 yang 真机复刻），fake 注入
 * snapshot 队列/click/clickAndType/mouseWheel/pressEscape/clearInput/sleep。
 *
 * 快照流约定（snapshotQueue 按调用次序出队，耗尽后重复最后一个）：
 * 1=ChatOpenExecutor 头部检查，2=ChatSearchExecutor 自己的 snap0（入口定位），
 * 3/4=搜索框定位（首击后/重击后），5=搜索结果/进入对话，6=搜索成功后的头部身份校验，
 * 7+=列表兜底定位/滚动每轮/头部轮询每轮。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { ChatOpenExecutor, ChatOpenError } from '../src/main/boss/ChatOpenExecutor.js'
import type { DomSnapshot, ClickPoint } from '../src/main/boss/domSnapshot.js'
import type { ChatOpenDeps } from '../src/main/boss/ChatOpenExecutor.js'
import { yangSnapshot, conversationItem } from './chatReadFixture.js'

function snapshotQueue(snaps: DomSnapshot[]): () => Promise<DomSnapshot> {
  let i = 0
  return async () => snaps[Math.min(i++, snaps.length - 1)]!
}

interface Recorder {
  clicks: ClickPoint[]
  clickAndTypes: Array<{ point: ClickPoint; text: string }>
  wheels: Array<{ x: number; y: number; deltaY: number }>
  escapes: number
  clears: number
}

function recorder() {
  const r: Recorder = { clicks: [], clickAndTypes: [], wheels: [], escapes: 0, clears: 0 }
  return r
}

function makeDeps(r: Recorder, snaps: DomSnapshot[], opts: { signal?: AbortSignal; noWheel?: boolean } = {}): ChatOpenDeps {
  return {
    snapshot: snapshotQueue(snaps),
    click: async (p: ClickPoint) => { r.clicks.push(p) },
    clickAndType: async (p: ClickPoint, _viewport: { width: number; height: number }, text: string) => { r.clickAndTypes.push({ point: p, text }) },
    ...(opts.noWheel ? {} : { mouseWheel: async (x: number, y: number, deltaY: number) => { r.wheels.push({ x, y, deltaY }) } }),
    pressEscape: async () => { r.escapes++ },
    clearInput: async () => { r.clears++ },
    ...(opts.signal ? { signal: opts.signal } : {}),
    sleep: async () => {},
  }
}

// ---- 搜索路径 fixture 快照（真机几何：入口 (520,141)，搜索框 INPUT @(248,126,240,30)） ----
/** 搜索入口锚点快照：yang + 批量 #text + 图标 DIV */
const ANCHOR = () => yangSnapshot({ searchAnchor: true })
/** 搜索框 INPUT 出现（2026-08-27 原位变形模型） */
const BOX = () => yangSnapshot({ searchAnchor: true, extra: [{ tag: 'INPUT', cls: 'ipt-search', bounds: [248, 126, 240, 30] }] })
/** 搜索结果浮层：联系人标签 + 公司名 + 姓名逐字节点 */
const RESULTS = () => yangSnapshot({
  extra: [
    { tag: '#text', text: '联系人', bounds: [206, 173, 60, 26] },
    { tag: '#text', text: '_测试公司', bounds: [304, 210, 200, 16] },
    { tag: '#text', text: '张', bounds: [300, 188, 16, 16] },
    { tag: '#text', text: '三', bounds: [318, 188, 16, 16] },
  ],
})
/** 进入对话（contact=进入的会话联系人——搜索路径成功后头部身份校验用）：右下发送按钮出现 */
const SENT = (contact: string) => yangSnapshot({
  contact,
  extra: [{ tag: '#text', text: '发送', bounds: [1086, 1215, 60, 20] }],
})

test('already：头部已是目标联系人 → 零点击零输入，via=already', async () => {
  const r = recorder()
  const executor = new ChatOpenExecutor(makeDeps(r, [yangSnapshot()]))
  const result = await executor.open({ contact: '杨鸿杰' })
  assert.deepEqual(result, { contact: '杨鸿杰', via: 'already' })
  assert.equal(r.clicks.length, 0)
  assert.equal(r.clickAndTypes.length, 0)
  assert.equal(r.escapes, 0)
})

test('搜索成功：点入口 → 输入姓名 → 点结果公司名 → 头部=目标 → via=search', async () => {
  const r = recorder()
  const executor = new ChatOpenExecutor(makeDeps(r, [ANCHOR(), ANCHOR(), BOX(), RESULTS(), SENT('张三')]))
  const result = await executor.open({ contact: '张三' })
  assert.deepEqual(result, { contact: '张三', via: 'search' })
  // 点击序列：搜索入口 (520,141) → 结果公司名 (404,218)；无列表点击、无滚动
  assert.deepEqual(r.clicks, [{ x: 520, y: 141 }, { x: 404, y: 218 }])
  assert.equal(r.clickAndTypes.length, 1)
  assert.deepEqual(r.clickAndTypes[0]!.point, { x: 368, y: 141 })
  assert.equal(r.clickAndTypes[0]!.text, '张三')
  assert.equal(r.wheels.length, 0)
})

test('搜索进入错会话（头部≠目标）→ 视同搜索失败不报成功，列表兜底点击正确项 → via=list', async () => {
  const r = recorder()
  // 流：头部杨鸿杰→搜索全链路成功但进入的是杨鸿杰（错卡）→ 身份校验快照仍杨鸿杰 →
  // 兜底清场快照（列表含张三）→ 点击张三 → 轮询头部=张三
  const executor = new ChatOpenExecutor(makeDeps(r, [
    ANCHOR(), ANCHOR(), BOX(), RESULTS(), SENT('杨鸿杰'),
    yangSnapshot({ listItems: [conversationItem({ y: 289, name: '杨鸿杰', selected: true }), conversationItem({ y: 430, name: '张三' })] }),
    yangSnapshot({ contact: '张三', listItems: [conversationItem({ y: 289, name: '杨鸿杰', selected: true }), conversationItem({ y: 430, name: '张三' })] }),
  ]))
  const result = await executor.open({ contact: '张三' })
  assert.deepEqual(result, { contact: '张三', via: 'list' })
  // 入口 + 结果公司名（搜索链路）+ 列表项张三（兜底）= 3 次点击
  assert.deepEqual(r.clicks, [
    { x: 520, y: 141 },
    { x: 404, y: 218 },
    { x: 346.5, y: 453.5 },
  ])
  assert.equal(r.clickAndTypes.length, 1)
})

test('搜索失败 → 列表兜底：先 Escape 清场残留浮层，点击席彬玮列表项（姓名中心 x+60）→ 头部切换 → via=list', async () => {
  const r = recorder()
  const executor = new ChatOpenExecutor(makeDeps(r, [ANCHOR(), ANCHOR(), yangSnapshot(), yangSnapshot(), yangSnapshot(), yangSnapshot({ contact: '席彬玮' })]))
  const result = await executor.open({ contact: '席彬玮' })
  assert.deepEqual(result, { contact: '席彬玮', via: 'list' })
  // 搜索点击序列：入口 2 次（首击+重试仍无搜索框），随后列表项点击 = 姓名中心 (286.5,453.5) 右移 60
  assert.deepEqual(r.clicks, [
    { x: 520, y: 141 },
    { x: 520, y: 141 },
    { x: 346.5, y: 453.5 },
  ])
  assert.equal(r.clickAndTypes.length, 0)
  // Escape 2 次：搜索链路开头清场 1 次 + 列表兜底前清场（关残留搜索浮层恢复会话列表）1 次
  assert.equal(r.escapes, 2)
})

test('双失败：搜索失败 + 列表 0 命中 → 汇总报错（可用联系人名单 + 搜索失败原因）', async () => {
  const r = recorder()
  const executor = new ChatOpenExecutor(makeDeps(r, [ANCHOR(), ANCHOR(), yangSnapshot(), yangSnapshot(), yangSnapshot()]))
  await assert.rejects(
    executor.open({ contact: '张三丰' }),
    (e: unknown) => {
      assert.ok(e instanceof ChatOpenError)
      assert.match(e.message, /会话列表中不存在「张三丰」/)
      assert.match(e.message, /可用联系人：杨鸿杰、席彬玮、周天一/)
      assert.match(e.message, /搜索路径失败：/)
      return true
    },
  )
})

test('同名多命中：列表 2 个王五 → fail-loud 列坐标，不点击', async () => {
  const r = recorder()
  const dup = yangSnapshot({
    listItems: [conversationItem({ y: 430, name: '王五' }), conversationItem({ y: 630, name: '王五' })],
  })
  const executor = new ChatOpenExecutor(makeDeps(r, [dup]))
  await assert.rejects(executor.open({ contact: '王五' }), /2 个「王五」姓名命中/)
  assert.equal(r.clicks.length, 0)
})

test('视口外目标：mouseWheel 滚动（锚点=容器 bounds 中心）→ 可见后点击 → via=list', async () => {
  const r = recorder()
  const scrolled = yangSnapshot({
    listItems: [
      conversationItem({ y: 289, name: '杨鸿杰', job: 'PHP 开发工程师', time: '11:04', preview: '您好，请问该岗位是外包嘛', selected: true }),
      conversationItem({ y: 430, name: '席彬玮', job: 'PHP 开发工程师', time: '10:42', preview: '你好，我们正在诚招PHP 开发工程师，想跟你沟通一下', count: 2, deliverPrefix: true }),
      conversationItem({ y: 500, name: '周天一', time: '09:15', preview: '好的谢谢', count: 1 }),
    ],
  })
  // 流：头部/入口锚点 ×2 → 无框 ×2（搜索失败）→ 兜底定位（周天一 y=3238 视口外）→ 滚动后可见 → 轮询头部切换
  const executor = new ChatOpenExecutor(makeDeps(r, [ANCHOR(), ANCHOR(), yangSnapshot(), yangSnapshot(), yangSnapshot(), scrolled, yangSnapshot({ contact: '周天一' })]))
  const result = await executor.open({ contact: '周天一' })
  assert.deepEqual(result, { contact: '周天一', via: 'list' })
  // wheel 锚点来自 .user-list 容器 bounds [188,196,359,1088] 中心 (367.5,740) 取整；目标在下方 → deltaY 正
  assert.deepEqual(r.wheels, [{ x: 368, y: 740, deltaY: 600 }])
  // 滚动后周天一 name @(264,515,45,17) 中心 (286.5,523.5)，点击 x+60
  assert.deepEqual(r.clicks[r.clicks.length - 1], { x: 346.5, y: 523.5 })
})

test('视口外且未注入 mouseWheel → fail-loud，不点击列表项', async () => {
  const r = recorder()
  const executor = new ChatOpenExecutor(makeDeps(r, [ANCHOR(), ANCHOR(), yangSnapshot(), yangSnapshot(), yangSnapshot()], { noWheel: true }))
  await assert.rejects(executor.open({ contact: '周天一' }), /视口外且本环境无滚动原语/)
  assert.equal(r.clicks.length, 2, '只有搜索入口的 2 次点击（首击+重试）')
})

test('点击列表项后头部轮询超时（8 次仍杨鸿杰）→ ChatOpenError（头部未切换）', async () => {
  const r = recorder()
  const executor = new ChatOpenExecutor(makeDeps(r, [ANCHOR(), ANCHOR(), yangSnapshot(), yangSnapshot(), yangSnapshot()]))
  await assert.rejects(executor.open({ contact: '席彬玮' }), /头部未切换为「席彬玮」/)
  // 列表项点击已发出（入口 2 次 + 列表 1 次），只是头部校验失败
  assert.equal(r.clicks.length, 3)
})

test('取消（signal 已 abort）→ CancelledError，不点不输入', async () => {
  const r = recorder()
  const executor = new ChatOpenExecutor(makeDeps(r, [yangSnapshot()], { signal: AbortSignal.abort() }))
  await assert.rejects(
    executor.open({ contact: '杨鸿杰' }),
    (e: unknown) => { assert.equal((e as Error).name, 'CancelledError'); return true },
  )
  assert.equal(r.clicks.length, 0)
})
