import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { FilterSetter, pickClosestOption } from '../src/main/boss/FilterSetter.js'
import { effectiveMembership, inspectMembership, membershipAccountKey } from '../src/main/boss/MembershipInspector.js'
import { closeFilterPaywall, inspectFilterPaywall } from '../src/main/boss/FilterPaywall.js'
import { snapshotNodeVisible } from '../src/main/boss/SnapshotVisibility.js'
import { accumulateOwnerOffset, boundsCenter, classOf, findNodesByString, type DomSnapshot, type ClickPoint } from '../src/main/boss/domSnapshot.js'
import { withAccount } from './filterFixture.js'

const load = (name: string) => withAccount(JSON.parse(fs.readFileSync(new URL(`../../tests/fixtures/${name}.json`, import.meta.url), 'utf8')))
const top = load('boss-vip-panel-top'), down = load('boss-vip-panel-down')
const schoolBefore = load('boss-vip-schools-before'), schoolSelected = load('boss-vip-schools-selected'), firstChecked = load('boss-vip-first-degree-checked')
function pointOf(snap: DomSnapshot, text: string): ClickPoint | null {
  for (let si = 0; si < snap.strings.length; si++) {
    if (snap.strings[si]?.trim() !== text) continue
    for (let di = 0; di < snap.documents.length; di++) {
      for (const hit of findNodesByString(snap.documents[di]!, si)) {
        if (hit.bounds[2] <= 0 || hit.bounds[3] <= 0) continue
        const offset = accumulateOwnerOffset(snap, di), center = boundsCenter(hit.bounds)
        return { x: offset.x + center.x, y: offset.y + center.y }
      }
    }
  }
  return null
}
const same = (a: ClickPoint, b: ClickPoint | null) => !!b && Math.abs(a.x - b.x) < 1 && Math.abs(a.y - b.y) < 1
function selected(snap: DomSnapshot, texts: string[]) {
  const copy = structuredClone(snap)
  copy.strings.push('class', 'option active')
  const key = copy.strings.length - 2, value = key + 1
  for (const doc of copy.documents) {
    const values = doc.nodes.nodeValue as number[], parents = doc.nodes.parentIndex as number[], attrs = doc.nodes.attributes as number[][]
    for (let i = 0; i < values.length; i++) if (texts.includes(copy.strings[values[i]!] ?.trim() ?? '')) attrs[parents[i]!] = [key, value]
  }
  return copy
}
function done(count: number, width = 2506, height = 1476): DomSnapshot {
  return withAccount({ strings: ['', `筛选·${count}`], documents: [{ nodes: { nodeValue: [0, 1], contentDocumentIndex: [] }, layout: { nodeIndex: [0, 1], bounds: [[0, 0, width, height], [width - 300, 24, 90, 31]] } }] })
}
function queue(snaps: DomSnapshot[]) { let i = 0; return async () => snaps[Math.min(i++, snaps.length - 1)]! }
function modal(snap: DomSnapshot, closeX = 1450): DomSnapshot {
  const copy = structuredClone(snap), doc = copy.documents[0]!
  const values = doc.nodes.nodeValue as number[], names = doc.nodes.nodeName as number[], parents = doc.nodes.parentIndex as number[], attrs = doc.nodes.attributes as number[][]
  const str = (s: string) => { copy.strings.push(s); return copy.strings.length - 1 }
  const add = (parent: number, bounds: number[], text: string, properties: string[] = []) => {
    const ni = values.length; values.push(text ? str(text) : -1); names[ni] = str(text ? '#text' : 'DIV'); parents[ni] = parent; attrs[ni] = properties.map(str)
    doc.layout.nodeIndex.push(ni); doc.layout.bounds.push(bounds); doc.layout.styles?.push([]); doc.layout.clientRects?.push([])
    return ni
  }
  const root = add(0, [900, 400, 700, 450], '', ['role', 'dialog', 'class', 'member-dialog'])
  add(root, [980, 480, 260, 32], 'VIP会员筛选权益')
  add(root, [1100, 650, 180, 40], '立即购买')
  add(root, [closeX, 430, 60, 30], '关闭')
  // 付费窗出现时底层面板可完全不再可读；头像仍属于原账号。
  copy.documents.slice(1).forEach((d) => { d.nodes = { nodeValue: [], parentIndex: [], contentDocumentIndex: [] }; d.layout = { nodeIndex: [], bounds: [] } })
  return copy
}

function hidden(snap: DomSnapshot, selector: (s: DomSnapshot, node: number) => boolean, styleIndex: number, value: string): DomSnapshot {
  const copy = structuredClone(snap), doc = copy.documents[0]!
  copy.strings.push(value)
  doc.layout.styles ??= doc.layout.nodeIndex.map(() => [])
  doc.layout.nodeIndex.forEach((node, i) => { if (selector(copy, node)) { doc.layout.styles![i] ??= []; doc.layout.styles![i]![styleIndex] = copy.strings.length - 1 } })
  return copy
}

test('隐藏徽标及其祖先不会证明会员，隐藏付费窗或购买文案不会触发降级', () => {
  const raw = JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/boss-vip-panel-top.json', import.meta.url), 'utf8'))
  const vip = withAccount(raw, 'account-a', 'vip')
  for (const [index, value] of [[2, 'none'], [5, 'hidden'], [5, 'collapse'], [6, '0']] as const) {
    const avatar = hidden(vip, (s, n) => (classOf(s, 0, n) ?? '').includes('avatar-content'), index, value)
    assert.equal(inspectMembership(avatar).status, 'unknown')
    const badge = hidden(vip, (s, n) => s.strings[(s.documents[0]!.nodes.nodeValue as number[])[n]!] === 'VIP会员', index, value)
    assert.equal(inspectMembership(badge).status, 'unknown')
    const paid = hidden(modal(top), (s, n) => (classOf(s, 0, n) ?? '').includes('member-dialog'), index, value)
    assert.equal(inspectFilterPaywall(paid), null)
  }
  const hiddenBuy = hidden(modal(top), (s, n) => s.strings[(s.documents[0]!.nodes.nodeValue as number[])[n]!] === '立即购买', 5, 'hidden')
  assert.equal(inspectFilterPaywall(hiddenBuy), null)
})

test('万节点快照复用父索引、iframe owner与祖先可见性结果，避免同步CPU阻塞', () => {
  const large = structuredClone(top), doc = large.documents[0]!
  const parents = doc.nodes.parentIndex as number[], values = doc.nodes.nodeValue as number[], attrs = doc.nodes.attributes as number[][]
  const start = values.length
  for (let i = 0; i < 10000; i++) {
    const node = start + i
    values[node] = -1; parents[node] = i === 0 ? 0 : node - 1; attrs[node] = []
    doc.layout.nodeIndex.push(node); doc.layout.bounds.push([200, 250, 10, 10]); doc.layout.styles?.push([])
  }
  const started = performance.now()
  for (let pass = 0; pass < 3; pass++) {
    for (const [di, document] of large.documents.entries()) for (const node of document.layout.nodeIndex) snapshotNodeVisible(large, di, node)
    assert.equal(inspectMembership(large).status, 'unknown')
    assert.equal(inspectFilterPaywall(large), null)
  }
  assert.ok(performance.now() - started < 5000, '万节点结构分析不应阻塞数秒以上')
})

test('非根父节点自环或互环不可见，保留精简fixture根节点0自指兼容', () => {
  const cyclic = structuredClone(top), doc = cyclic.documents[0]!, parents = doc.nodes.parentIndex as number[]
  const [first, second] = doc.layout.nodeIndex.filter((node) => node !== 0).slice(-2)
  parents[first!] = first!
  assert.equal(snapshotNodeVisible(cyclic, 0, first!), false)
  const mutual = structuredClone(top), other = mutual.documents[0]!.nodes.parentIndex as number[]
  other[first!] = second!; other[second!] = first!
  assert.equal(snapshotNodeVisible(mutual, 0, first!), false)
  assert.equal(snapshotNodeVisible(mutual, 0, second!), false)
  const root = done(0)
  assert.equal(snapshotNodeVisible(root, 0, 0), true)
  const cached = done(0)
  const avatar = cached.documents[0]!.layout.nodeIndex.find((node) => (classOf(cached, 0, node) ?? '') === 'avatar-content')!
  assert.equal(snapshotNodeVisible(cached, 0, avatar), true)
  const freshHidden = hidden(cached, (_s, node) => node === avatar, 5, 'hidden')
  assert.equal(snapshotNodeVisible(freshHidden, 0, avatar), false)
  assert.equal(snapshotNodeVisible(cached, 0, avatar), true)
})

test('会员证据只来自主导航头像，推广VIP图标及面板标题不是会员证明', () => {
  assert.equal(inspectMembership(top).status, 'unknown')
  assert.equal(inspectMembership(withAccount(JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/boss-vip-panel-top.json', import.meta.url), 'utf8')), 'a', 'vip')).status, 'vip')
  const nonVip = withAccount(JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/boss-vip-panel-top.json', import.meta.url), 'utf8')), 'a', 'non_vip')
  assert.equal(effectiveMembership(inspectMembership(nonVip), 'vip'), 'non_vip')
  assert.ok(membershipAccountKey(top))
  assert.notEqual(membershipAccountKey(top), membershipAccountKey(withAccount(JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/boss-vip-panel-top.json', import.meta.url), 'utf8')), 'other')))
})

test('未知会员VIP请求在任何clear/高级项点击前要求澄清', async () => {
  let clicks = 0
  const setter = new FilterSetter({ snapshot: async () => top, click: async () => { clicks++ }, sleep: async () => {} })
  await assert.rejects(setter.apply({ filters: [{ label: '性别', options: ['男'] }] }), (error: unknown) => (error as { code?: string }).code === 'MEMBERSHIP_REQUIRED')
  assert.equal(clicks, 0)
})

test('跨调用会员提示必须绑定探查账号，缺失或旧账号引用均在清除前停止', async () => {
  const other = withAccount(JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/boss-vip-panel-top.json', import.meta.url), 'utf8')), 'other-account')
  const raw = JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/boss-vip-panel-top.json', import.meta.url), 'utf8'))
  for (const status of ['vip', 'non_vip', 'unknown'] as const) for (const ref of [undefined, membershipAccountKey(other)!]) {
    let clicks = 0
    const setter = new FilterSetter({ snapshot: async () => withAccount(raw, 'account-a', status), click: async () => { clicks++ } })
    await assert.rejects(setter.apply({ membership_hint: 'vip', membership_account_ref: ref, filters: [{ label: '性别', options: ['男'] }] }), /membership_account_ref/)
    assert.equal(clicks, 0)
  }
  let clicks = 0
  const setter = new FilterSetter({ snapshot: async () => top, click: async () => { clicks++ } })
  await assert.rejects(setter.apply({ membership_account_ref: membershipAccountKey(other)!, salary: '20-50K' }), /membership_account_ref/)
  assert.equal(clicks, 0)
})

test('非VIP提示只屏蔽高级条件，没有普通条件时保留原筛选', async () => {
  let clicks = 0
  const setter = new FilterSetter({ snapshot: async () => top, click: async () => { clicks++ }, sleep: async () => {} })
  const result = await setter.apply({ membership_hint: 'non_vip', membership_account_ref: membershipAccountKey(top)!, filters: [{ label: '性别', options: ['男'] }], age: { min: 25, max: null } })
  assert.equal(result.applied, false)
  assert.equal(result.membership.source, 'user_hint')
  assert.equal(result.membership.status, 'non_vip')
  assert.deepEqual(result.skippedFilters.map((s) => s.label), ['性别', '年龄'])
  assert.equal(clicks, 0)
})

test('明确头像非VIP覆盖用户vip提示且不点击任何高级项', async () => {
  const nonVip = withAccount(JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/boss-vip-panel-top.json', import.meta.url), 'utf8')), 'account-a', 'non_vip')
  let clicks = 0
  const result = await new FilterSetter({ snapshot: async () => nonVip, click: async () => { clicks++ } }).apply({ membership_hint: 'vip', membership_account_ref: membershipAccountKey(top)!, filters: [{ label: '性别', options: ['男'] }] })
  assert.equal(result.membership.source, 'avatar')
  assert.equal(result.membership.status, 'non_vip')
  assert.equal(result.skippedFilters[0]!.reason, 'non_vip')
  assert.equal(clicks, 0)
})

test('普通经验/学历/求职状态及院校/关键词均按实证多选，薪资上限不扩大', () => {
  const setter = new FilterSetter({ snapshot: async () => top, click: async () => {} })
  const rows = setter.describePanel(top)
  for (const label of ['经验要求', '学历要求', '求职状态', '院校', '牛人关键词']) assert.equal(rows.find((r) => r.label === label)?.selection, 'multiple')
  assert.equal(pickClosestOption('10K以下', ['3K以下', '3-5K', '5-10K', '10-20K']), '5-10K')
  assert.equal(pickClosestOption('2K以下', ['3K以下', '3-5K']), null)
})

test('普通经验与求职状态各多选两项，逐项点击并核对四个真实选中值', async () => {
  let state = top
  const choices = ['1年以内', '1-3年', '离职-随时到岗', '在职-考虑机会'], applied: string[] = []
  const setter = new FilterSetter({ snapshot: async () => state, sleep: async () => {},
    mouseWheel: async (_x, _y, delta) => { state = selected(delta > 0 ? down : top, applied) },
    click: async (point) => {
      if (same(point, pointOf(state, '清除'))) { applied.length = 0; state = top; return }
      const choice = choices.find((text) => same(point, pointOf(state, text)))
      if (choice) { applied.push(choice); state = selected(state, applied); return }
      if (same(point, pointOf(state, '确定'))) { state = done(4); return }
      assert.fail('未确认落点')
    },
  })
  const result = await setter.apply({ filters: [{ label: '经验要求', options: choices.slice(0, 2) }, { label: '求职状态', options: choices.slice(2) }] })
  assert.deepEqual(applied, choices)
  assert.equal(result.filterCount, 4)
  assert.deepEqual(result.skippedFilters, [])
})

test('985选择后动态第一学历控件读取checked并单独计入徽章，caption不会混入院校选项', async () => {
  const clicks: ClickPoint[] = []
  const setter = new FilterSetter({ snapshot: queue([schoolBefore, schoolBefore, schoolSelected, firstChecked, firstChecked, done(2, 3200, 1618)]), click: async (p) => { clicks.push(p) }, sleep: async () => {} })
  const result = await setter.apply({ membership_hint: 'vip', membership_account_ref: membershipAccountKey(top)!, filters: [{ label: '院校', options: ['985'] }], first_degree_only: true })
  assert.equal(result.firstDegreeOnly, true)
  assert.equal(result.filterCount, 2)
  assert.equal(clicks.length, 4)
  assert.ok(!setter.describePanel(firstChecked).find((r) => r.label === '院校')!.options.some((o) => o.text.startsWith('只看第一学历')))
})

test('用户hint不能跨账号沿用，已变更后的VIP动作立即停止', async () => {
  let clicks = 0
  const other = withAccount(JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/boss-vip-panel-top.json', import.meta.url), 'utf8')), 'account-b')
  const setter = new FilterSetter({ snapshot: queue([top, other]), click: async () => { clicks++ }, sleep: async () => {} })
  await assert.rejects(setter.apply({ membership_hint: 'vip', membership_account_ref: membershipAccountKey(top)!, filters: [{ label: '性别', options: ['男'] }] }), /账号已变化/)
  assert.equal(clicks, 1) // 仅clear；未点击新账号的高级条件。
})

test('购买文案需要明确modal结构；关闭限定弹窗子树并fresh确认完整消失', async () => {
  let state = modal(top), clicks = 0
  assert.ok(inspectFilterPaywall(state))
  assert.equal(inspectFilterPaywall(top), null)
  await closeFilterPaywall({ snapshot: async () => state, click: async (point) => { assert.ok(same(point, pointOf(state, '关闭'))); clicks++; state = top }, sleep: async () => {} })
  assert.equal(clicks, 1)
})

test('关闭首次无效且移动后，禁止使用旧关闭落点重试（原点可变成购买按钮）', async () => {
  let state = modal(top), clicks = 0
  await assert.rejects(closeFilterPaywall({ snapshot: async () => state, click: async () => { clicks++; state = modal(top, 1500) }, sleep: async () => {} }), /禁止使用缓存坐标/)
  assert.equal(clicks, 1)
})

test('真实付费结构出现且底层年龄控件消失：安全关闭后只执行普通薪资，不受vip提示覆盖', async () => {
  let state = top, paid = false, closed = false, salary = false, clicks = 0
  const setter = new FilterSetter({
    snapshot: async () => state,
    sleep: async () => {},
    drag: async () => { paid = true; state = modal(top) },
    mouseWheel: async (_x, _y, delta) => { state = salary ? selected(delta > 0 ? down : top, ['20-50K']) : delta > 0 ? down : top },
    click: async (point) => {
      clicks++
      if (same(point, pointOf(state, '关闭'))) { closed = true; state = top; return }
      if (same(point, pointOf(state, '清除'))) { salary = false; state = top; return }
      if (same(point, pointOf(state, '20-50K'))) { salary = true; state = selected(state, ['20-50K']); return }
      if (same(point, pointOf(state, '确定'))) { state = done(1); return }
      assert.fail('出现未确认或购买类落点')
    },
  })
  const result = await setter.apply({ membership_hint: 'vip', membership_account_ref: membershipAccountKey(top)!, salary: '20-50K', age: { min: 25, max: null } })
  assert.ok(paid && closed)
  assert.equal(result.membership.status, 'non_vip')
  assert.equal(result.membership.source, 'paywall')
  assert.deepEqual(result.skippedFilters, [{ label: '年龄', range: { min: 25, max: null }, reason: 'paywall' }])
  assert.deepEqual(result.appliedRows, [{ label: '薪资待遇', options: ['20-50K'] }])
  assert.equal(result.filterCount, 1)
  assert.equal(clicks, 5) // 首次clear、关闭、普通clear、薪资、确认。
})

test('同一账号会员在最终确认前到期：不确认高级选中状态，转普通薪资并回执非VIP', async () => {
  const rawTop = JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/boss-vip-panel-top.json', import.meta.url), 'utf8'))
  const rawDown = JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/boss-vip-panel-down.json', import.meta.url), 'utf8'))
  let state = withAccount(rawTop, 'account-a', 'vip'), expired = false, salary = false, confirms = 0
  const make = (isDown = false) => withAccount(isDown ? rawDown : rawTop, 'account-a', expired ? 'non_vip' : 'vip')
  const setter = new FilterSetter({ snapshot: async () => state, sleep: async () => {},
    mouseWheel: async (_x, _y, delta) => { state = salary ? selected(make(delta > 0), ['20-50K']) : make(delta > 0) },
    click: async (point) => {
      if (same(point, pointOf(state, '清除'))) { salary = false; state = make(); return }
      if (same(point, pointOf(state, '男'))) { expired = true; state = selected(make(), ['男']); return }
      if (same(point, pointOf(state, '20-50K'))) { salary = true; state = selected(state, ['20-50K']); return }
      if (same(point, pointOf(state, '确定'))) { assert.ok(salary); confirms++; state = done(1); return }
      assert.fail('未确认落点')
    },
  })
  const result = await setter.apply({ membership_hint: 'vip', membership_account_ref: membershipAccountKey(top)!, salary: '20-50K', filters: [{ label: '性别', options: ['男'] }] })
  assert.equal(confirms, 1)
  assert.equal(result.membership.status, 'non_vip')
  assert.deepEqual(result.skippedFilters, [{ label: '性别', options: ['男'], reason: 'non_vip' }])
  assert.deepEqual(result.appliedRows, [{ label: '薪资待遇', options: ['20-50K'] }])
})

test('第一学历点击触发付费窗且学校控件消失：关闭后只执行普通条件', async () => {
  let state = schoolBefore, firstPaid = false, salary = false
  const setter = new FilterSetter({ snapshot: async () => state, sleep: async () => {},
    mouseWheel: async (_x, _y, delta) => { state = salary ? selected(delta > 0 ? down : top, ['20-50K']) : delta > 0 ? down : top },
    click: async (point) => {
      if (same(point, pointOf(state, '关闭'))) { state = top; return }
      if (same(point, pointOf(state, '清除'))) { salary = false; state = firstPaid ? top : schoolBefore; return }
      if (same(point, pointOf(state, '985'))) { state = schoolSelected; return }
      const doc = state.documents[2]
      const control = doc?.layout.nodeIndex.find((node) => (classOf(state, 2, node) ?? '').split(/\s+/).includes('check-box') && (classOf(state, 2, (doc!.nodes.parentIndex as number[])[node]!) ?? '').includes('first-degree-wrap'))
      if (control !== undefined) {
        const b = doc!.layout.bounds[doc!.layout.nodeIndex.indexOf(control)]!, o = accumulateOwnerOffset(state, 2)
        if (same(point, { x: o.x + b[0]! + b[2]! / 2, y: o.y + b[1]! + b[3]! / 2 })) { firstPaid = true; state = modal(state); return }
      }
      if (same(point, pointOf(state, '20-50K'))) { salary = true; state = selected(state, ['20-50K']); return }
      if (same(point, pointOf(state, '确定'))) { assert.ok(firstPaid && salary); state = done(1); return }
      assert.fail('未确认落点')
    },
  })
  const result = await setter.apply({ membership_hint: 'vip', membership_account_ref: membershipAccountKey(top)!, salary: '20-50K', filters: [{ label: '院校', options: ['985'] }], first_degree_only: true })
  assert.equal(result.membership.source, 'paywall')
  assert.equal(result.firstDegreeOnly, false)
  assert.deepEqual(result.skippedFilters.map((s) => s.label), ['院校', '只看第一学历'])
  assert.deepEqual(result.appliedRows, [{ label: '薪资待遇', options: ['20-50K'] }])
})
