import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { FilterSetter } from '../src/main/boss/FilterSetter.js'
import { withAccount, withoutRowOptions } from './filterFixture.js'
import { membershipAccountKey } from '../src/main/boss/MembershipInspector.js'
import type { DomSnapshot, ClickPoint } from '../src/main/boss/domSnapshot.js'

// 2026-10-13 会员真机 DOMSnapshot，仅保留面板/祖先；无候选人、Cookie或凭据。
const top: DomSnapshot = withAccount(JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/boss-vip-panel-top.json', import.meta.url), 'utf8')))
const down: DomSnapshot = withAccount(JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/boss-vip-panel-down.json', import.meta.url), 'utf8')))
function selected(snap: DomSnapshot, texts: string[]): DomSnapshot {
  const copy = structuredClone(snap)
  copy.strings.push('class', 'option active')
  const name = copy.strings.length - 2, value = copy.strings.length - 1
  for (const doc of copy.documents) {
    const vals = doc.nodes.nodeValue as number[], parents = doc.nodes.parentIndex as number[]
    const attrs = doc.nodes.attributes as number[][]
    for (let i = 0; i < vals.length; i++) {
      if (texts.includes(copy.strings[vals[i]!] ?.trim() ?? '')) attrs[parents[i]!] = [name, value]
    }
  }
  return copy
}
function done(count: number): DomSnapshot {
  return withAccount({ strings: ['', `筛选·${count}`], documents: [{ nodes: { nodeValue: [0, 1], contentDocumentIndex: [] }, layout: { nodeIndex: [0, 1], bounds: [[0, 0, 2506, 1476], [2200, 24, 60, 31]] } }] })
}
function setup(snaps: DomSnapshot[], drag?: (from: ClickPoint, to: ClickPoint) => Promise<void>) {
  let index = 0
  const clicks: ClickPoint[] = [], wheels: Array<{ x: number; y: number; deltaY: number }> = []
  const setter = new FilterSetter({ snapshot: async () => snaps[Math.min(index++, snaps.length - 1)]!, click: async (p) => { clicks.push(p) }, sleep: async () => {}, mouseWheel: async (x, y, deltaY) => { wheels.push({ x, y, deltaY }) }, ...(drag ? { drag } : {}) })
  return { setter, clicks, wheels }
}

test('真实会员结构：发现13行，薪资不串入求职状态，折行经验完整，年龄为范围控件', () => {
  for (const snap of [top, down]) {
    const { setter } = setup([snap])
    const rows = setter.describePanel(snap)
    assert.equal(rows.length, 13)
    assert.equal(rows.find((r) => r.label === '年龄')?.control, 'range')
    assert.deepEqual(rows.find((r) => r.label === '性别')?.options.map((o) => o.text), ['不限', '男', '女'])
    assert.deepEqual(rows.find((r) => r.label === '薪资待遇[单选]')?.options.map((o) => o.text), ['不限', '3K以下', '3-5K', '5-10K', '10-20K', '20-50K', '50K以上'])
    assert.ok(rows.find((r) => r.label === '经验要求')?.options.some((o) => o.text === '10年以上'))
    assert.ok(rows.find((r) => r.label === '求职状态')?.options.some((o) => o.text === '离职-随时到岗'))
  }
})

test('真实结构增加无控件行：探查保留13个已确认行并报告局部不可读', async () => {
  const snap = structuredClone(top), doc = snap.documents[2]!
  const values = doc.nodes.nodeValue as number[], parents = doc.nodes.parentIndex as number[]
  const age = values.findIndex((value) => snap.strings[value] === '年龄')
  const panel = parents[parents[parents[parents[age]!]!]!]!
  const row = values.length
  snap.strings.push('未加载条件')
  values.push(-1, snap.strings.length - 1)
  parents.push(panel, row)
  doc.layout.nodeIndex.push(row, row + 1)
  doc.layout.bounds.push([412, 1150, 1602, 56], [412, 1162, 120, 31])
  const f = setup([snap, done(0)])
  const rows = await f.setter.probeOptions()
  assert.equal(rows.length, 13)
  assert.deepEqual(f.setter.unreadableRows.map((row) => row.label), ['未加载条件'])
  assert.match(f.setter.unreadableRows[0]!.reason, /未完整解析/)
  assert.equal(f.clicks.length, 1) // 只收起面板，不修改筛选。
})

test('真实结构部分行无选项：继续探查其余行，下一次完整探查清除旧诊断', async () => {
  const broken = withoutRowOptions(top, ['性别'])
  const f = setup([broken, done(0), top, done(0)])
  assert.equal((await f.setter.probeOptions()).length, 12)
  assert.deepEqual(f.setter.unreadableRows.map((row) => row.label), ['性别'])
  assert.equal((await f.setter.probeOptions()).length, 13)
  assert.deepEqual(f.setter.unreadableRows, [])
})

test('真实结构全部行无控件：拒绝声称探查成功', async () => {
  const labels = setup([top]).setter.describePanel(top).map((row) => row.label)
  const f = setup([withoutRowOptions(top, labels)])
  await assert.rejects(f.setter.probeOptions(), /未解析到任何可选档位/)
  assert.equal(f.setter.unreadableRows.length, 13)
  assert.equal(f.clicks.length, 0)
})

test('不可读年龄只排除年龄；普通薪资仍可完成设置，显式请求年龄仍在清除前拒绝', async () => {
  const breakAge = (snapshot: DomSnapshot) => {
    const snap = structuredClone(snapshot)
    // 真机年龄轨道宽760、高8；保留tooltip文本但破坏轨道，不能仅凭文字报告可设置范围。
    for (const bounds of snap.documents[2]!.layout.bounds) {
      if (bounds[2] === 760 && bounds[3] === 8) bounds[2] = 10
    }
    return snap
  }
  const broken = breakAge(top)
  const probe = setup([broken, done(0)])
  const rows = await probe.setter.probeOptions()
  assert.equal(rows.length, 12)
  assert.equal(rows.some((row) => row.label === '年龄'), false)
  assert.deepEqual(probe.setter.unreadableRows.map((row) => row.label), ['年龄'])
  assert.match(probe.setter.unreadableRows[0]!.reason, /年龄轨道/)
  const apply = setup([broken, broken, breakAge(down), breakAge(selected(down, ['20-50K'])), done(1)])
  assert.equal((await apply.setter.apply({ salary: '20-50K' })).filterCount, 1)
  const age = setup([broken], async () => { assert.fail('不可读年龄禁止拖动') })
  await assert.rejects(age.setter.apply({ membership_hint: 'vip', membership_account_ref: membershipAccountKey(top)!, age: { min: 25 } }), /年龄轨道/)
  assert.equal(age.clicks.length, 0)
})

test('无控件的无关行不阻塞普通薪资；显式引用该行仍在清除前拒绝', async () => {
  const broken = withoutRowOptions(top, ['性别'])
  const f = setup([broken, broken, withoutRowOptions(down, ['性别']), withoutRowOptions(selected(down, ['20-50K']), ['性别']), done(1)])
  assert.equal((await f.setter.apply({ salary: '20-50K' })).filterCount, 1)
  const invalid = setup([broken])
  await assert.rejects(invalid.setter.apply({ membership_hint: 'vip', membership_account_ref: membershipAccountKey(top)!, filters: [{ label: '性别', options: ['男'] }] }), /未在当前面板完整解析/)
  assert.equal(invalid.clicks.length, 0)
})

test('真实顶部薪资：使用真实overflow scrollport和实测2倍CSS坐标，滚后点击', async () => {
  const f = setup([top, top, down, selected(down, ['20-50K']), done(1)])
  const result = await f.setter.apply({ salary: '20-50K' })
  assert.equal(result.filterCount, 1)
  assert.equal(f.wheels.length, 1)
  // doc2 owner(336,80)，实际scrollport全局[688,186,1730,1096]，轮滚x为标签中心/2。
  assert.equal(f.wheels[0]!.x, (336 + 412 + 183.5625 / 2) / 2)
  assert.equal(f.wheels[0]!.y, (80 + 106 + 1096 - 100) / 2)
  assert.ok(f.wheels[0]!.deltaY > 0)
  assert.ok(f.clicks[1]!.y < 1282)
})

test('真实下滚后设置性别：先反向滚动，扩展行不被三维LCA排除', async () => {
  const f = setup([down, down, top, selected(top, ['男']), done(1)])
  const result = await f.setter.apply({ membership_hint: 'vip', membership_account_ref: membershipAccountKey(top)!, filters: [{ label: '性别', options: ['男'] }] })
  assert.equal(result.filterCount, 1)
  assert.equal(f.wheels.length, 1)
  assert.ok(f.wheels[0]!.deltaY < 0)
})

test('未知条件、范围伪装与单选多项均在清除前拒绝', async () => {
  for (const filters of [[{ label: '不存在的行', options: ['选项'] }], [{ label: '年龄', options: ['25岁以上'] }], [{ label: '活跃度[单选]', options: ['今日活跃', '3日内活跃'] }]]) {
    const f = setup([top])
    await assert.rejects(f.setter.apply({ membership_hint: 'vip', membership_account_ref: membershipAccountKey(top)!, filters }))
    assert.equal(f.clicks.length, 0)
  }
})

test('旧三字段与同维通用行冲突，连接页面前拒绝', async () => {
  const f = setup([top])
  await assert.rejects(f.setter.apply({ salary: '20-50K', filters: [{ label: '薪资待遇[单选]', options: ['10-20K'] }] }), /重复/)
  assert.equal(f.clicks.length, 0)
})

test('CSS矩形缺失时绝不向错误坐标发送滚轮', async () => {
  const broken = structuredClone(top)
  broken.documents[0]!.layout.clientRects = []
  const f = setup([broken])
  await assert.rejects(f.setter.apply({ salary: '20-50K' }), /CSS 几何/)
  assert.equal(f.wheels.length, 0)
  assert.equal(f.clicks.length, 0) // 完整几何门禁在清除前。
})

test('年龄25以上：必须拖动且读取真实tooltip精确25/不限才提交', async () => {
  const age25 = structuredClone(top)
  age25.strings = age25.strings.map((s) => s === '16' ? '25' : s)
  const drags: Array<{ from: ClickPoint; to: ClickPoint }> = []
  const f = setup([top, top, age25, age25, done(1)], async (from, to) => { drags.push({ from, to }) })
  const result = await f.setter.apply({ membership_hint: 'vip', membership_account_ref: membershipAccountKey(top)!, age: { min: 25, max: null } })
  assert.deepEqual(result.age, { min: 25, max: null })
  assert.equal(result.filterCount, 1)
  assert.equal(drags.length, 1)
  assert.deepEqual(drags[0]!.from, { x: 996, y: 392 })
  assert.equal(f.clicks.length, 2)
})

test('年龄拖动未改变tooltip：有限重试后拒绝确认，不能只按落点声称精确值', async () => {
  let drags = 0
  const f = setup([top], async () => { drags++ })
  await assert.rejects(f.setter.apply({ membership_hint: 'vip', membership_account_ref: membershipAccountKey(top)!, age: { min: 25, max: null } }), /未达到精确值/)
  assert.equal(drags, 7)
  assert.equal(f.clicks.length, 1)
})

test('真机偏档轨迹26→24→25：基于输入x与真实反馈收敛，不追随输出句柄重复26', async () => {
  const ageAt = (min: number) => {
    const snap = structuredClone(top)
    snap.strings = snap.strings.map((s) => s === '16' ? String(min) : s)
    for (const bounds of snap.documents[2]!.layout.bounds) {
      if (bounds[0] === 636 && bounds[1] === 288 && bounds[2] === 48) bounds[0] = 636 + (min - 16) * 760 / 30
    }
    return snap
  }
  const inputs: number[] = []
  const f = setup([top, top, ageAt(26), ageAt(24), ageAt(25), ageAt(25), done(1)], async (_from, to) => { inputs.push(to.x) })
  assert.deepEqual((await f.setter.apply({ membership_hint: 'vip', membership_account_ref: membershipAccountKey(top)!, age: { min: 25, max: null } })).age, { min: 25, max: null })
  assert.equal(inputs.length, 3)
  assert.ok(inputs[1]! < inputs[0]!)
  assert.ok(inputs[2]! > inputs[1]! && inputs[2]! < inputs[0]!)
})

test('超出实测年龄范围或缺少拖动能力：清除之前拒绝', async () => {
  for (const age of [{ min: 46 }, { min: 25 }]) {
    const f = setup([top])
    await assert.rejects(f.setter.apply({ membership_hint: 'vip', membership_account_ref: membershipAccountKey(top)!, age }))
    assert.equal(f.clicks.length, 0)
  }
})

test('徽章数量不能替代行勾选状态核验，未激活选项不确认', async () => {
  const f = setup([top])
  await assert.rejects(f.setter.apply({ membership_hint: 'vip', membership_account_ref: membershipAccountKey(top)!, filters: [{ label: '性别', options: ['男'] }] }), /实际已选值校验失败/)
  assert.equal(f.clicks.length, 2) // 清除+选项，尚未确认。
})

test('曾解析到真实结构，提交前结构消失不能回退跳过已选值校验', async () => {
  const f = setup([top, top, done(1)])
  await assert.rejects(f.setter.apply({ membership_hint: 'vip', membership_account_ref: membershipAccountKey(top)!, filters: [{ label: '性别', options: ['男'] }] }), /结构在校验前丢失/)
  assert.equal(f.clicks.length, 2)
})

test('年龄曾精确到25，提交前被页面重置也必须拒绝', async () => {
  const age25 = structuredClone(top)
  age25.strings = age25.strings.map((s) => s === '16' ? '25' : s)
  const f = setup([top, top, age25, top], async () => {})
  await assert.rejects(f.setter.apply({ membership_hint: 'vip', membership_account_ref: membershipAccountKey(top)!, age: { min: 25, max: null } }), /提交前年龄范围已变化/)
  assert.equal(f.clicks.length, 1)
})
