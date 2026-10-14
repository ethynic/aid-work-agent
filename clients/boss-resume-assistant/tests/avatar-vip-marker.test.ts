import assert from 'node:assert/strict'
import test from 'node:test'
import { inspectMembership, membershipAccountKey, effectiveMembership } from '../src/main/boss/MembershipInspector.js'
import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'
import { withAccount } from './filterFixture.js'

const ASSET = 'https://img.bosszhipin.com/static/zhipin/recommend-geek/955336197567184833.png'
// Real 2026-10-13 marker geometry/ancestry, only synthetic account identity.
function fixture(status: 'vip' | 'non_vip' | 'unknown' = 'unknown'): DomSnapshot {
  const snap = withAccount({ strings: ['HTML'], documents: [{ nodes: {
    nodeValue: [-1], nodeName: [0], parentIndex: [-1], attributes: [[]], contentDocumentIndex: [-1],
  }, layout: { nodeIndex: [0], bounds: [[0, 0, 3200, 1618]], styles: [[]] } }] }, 'synthetic-account', status)
  const root = snap.documents[0]!
  root.layout.bounds[1] = [2908, 11.78125, 60, 60]
  root.layout.bounds[2] = [2908, 11.78125, 60, 60]
  const str = (text: string) => { snap.strings.push(text); return snap.strings.length - 1 }
  const owner = (root.nodes.nodeValue as number[]).length
  ;(root.nodes.nodeValue as number[]).push(-1); (root.nodes.nodeName as number[]).push(str('IFRAME'))
  ;(root.nodes.parentIndex as number[]).push(0); (root.nodes.attributes as number[][]).push([])
  root.nodes.contentDocumentIndex = Array(owner + 1).fill(-1)
  ;(root.nodes.contentDocumentIndex as number[])[owner] = 1
  root.layout.nodeIndex.push(owner); root.layout.bounds.push([336, 80, 2864, 1538]); root.layout.styles!.push([])
  const classes = ['', 'op-category head-wrap', 'fl recommend-filter op-filter', 'filter-wrap', 'filter-label-wrap active', 'vip-icon-v2']
  snap.documents.push({ nodes: {
    nodeValue: classes.map(() => -1), nodeName: classes.map((_, i) => str(i === 5 ? 'IMG' : 'DIV')),
    parentIndex: [-1, 0, 1, 2, 3, 4], contentDocumentIndex: classes.map(() => -1),
    attributes: classes.map((cls, i) => [...(cls ? [str('class'), str(cls)] : []), ...(i === 5 ? [str('src'), str(ASSET)] : [])]),
  }, layout: { nodeIndex: [0, 1, 2, 3, 4, 5], styles: classes.map(() => []), bounds: [
    [0, 0, 2864, 1538], [242, 24, 2368, 72], [2429.53125, 24, 180.46875, 72],
    [2429.53125, 24, 180.46875, 72], [2429.53125, 24, 180.46875, 72], [2574, 2, 66, 32],
  ] } })
  return snap
}

test('实测跨iframe头像下方徽标自动识别VIP，保留原账号摘要', () => {
  const snap = fixture(), before = membershipAccountKey(snap), missing = structuredClone(snap)
  ;(missing.documents[1]!.nodes.attributes as number[][])[5] = []
  assert.equal(inspectMembership(snap).status, 'vip')
  assert.equal(inspectMembership(snap).source, 'avatar')
  assert.match(inspectMembership(snap).evidence[0]!, /跨iframe/)
  assert.equal(inspectMembership(missing).status, 'unknown')
  assert.equal(membershipAccountKey(missing), before)
})

test('同一结构和头像相对位置支持不同DPI/窗口比例', () => {
  for (const scale of [0.5, 1, 1.5, 2]) {
    const snap = fixture()
    for (const doc of snap.documents) doc.layout.bounds = doc.layout.bounds.map((b) => b.map((v) => v * scale))
    assert.equal(inspectMembership(snap).status, 'vip')
  }
})

test('徽标/祖先/iframe隐藏均保留未知，不把隐藏或缺少徽标判为非VIP', () => {
  for (const [style, value] of [[2, 'none'], [5, 'hidden'], [5, 'collapse'], [6, '0']] as const) {
    for (const target of ['marker', 'label', 'owner', 'avatar']) {
      const snap = fixture(), di = target === 'owner' || target === 'avatar' ? 0 : 1
      const doc = snap.documents[di]!, node = target === 'marker' ? 5 : target === 'label' ? 4 : target === 'avatar' ? 1 : doc.layout.nodeIndex.at(-1)!
      snap.strings.push(value); doc.layout.styles![doc.layout.nodeIndex.indexOf(node)]![style] = snap.strings.length - 1
      assert.equal(inspectMembership(snap).status, 'unknown', `${target}/${value}`)
    }
  }
})

test('任意VIP图片/类名/错位图标、不可见owner或iframe裁剪均不能证明会员', () => {
  for (const variant of ['asset', 'structure', 'left', 'below', 'above', 'size', 'no-owner', 'zero-owner', 'clip']) {
    const snap = fixture(), marker = snap.documents[1]!, root = snap.documents[0]!, b = marker.layout.bounds[5]!
    if (variant === 'asset') snap.strings = snap.strings.map((s) => s === ASSET ? 'https://fixture.invalid/vip-ad.png' : s)
    if (variant === 'structure') (marker.nodes.attributes as number[][])[2] = []
    if (variant === 'left') b[0]! -= 500
    if (variant === 'below') b[1]! += 100
    if (variant === 'above') b[1]! -= 60
    if (variant === 'size') b[2] = 400
    if (variant === 'no-owner') root.nodes.contentDocumentIndex = []
    if (variant === 'zero-owner') root.layout.bounds.at(-1)![3] = 0
    if (variant === 'clip') root.layout.bounds.at(-1)![3] = 15
    assert.equal(inspectMembership(snap).status, 'unknown', variant)
  }
})

test('重复徽标拒绝确定状态，明确非VIP仍覆盖可见徽标和用户VIP提示', () => {
  const repeated = fixture(), doc = repeated.documents[1]!
  ;(doc.nodes.nodeValue as number[]).push(-1)
  ;(doc.nodes.nodeName as number[]).push((doc.nodes.nodeName as number[])[5]!)
  ;(doc.nodes.attributes as number[][]).push([...(doc.nodes.attributes as number[][])[5]!])
  ;(doc.nodes.parentIndex as number[]).push(4); (doc.nodes.contentDocumentIndex as number[]).push(-1)
  doc.layout.nodeIndex.push(6); doc.layout.bounds.push([...doc.layout.bounds[5]!]); doc.layout.styles!.push([])
  assert.equal(inspectMembership(repeated).status, 'unknown')
  const negative = fixture('non_vip')
  assert.equal(inspectMembership(negative).status, 'non_vip')
  assert.equal(effectiveMembership(inspectMembership(negative), 'vip'), 'non_vip')
})

test('iframe滚动后按可见位置重测，不沿用旧会员证据', () => {
  const snap = fixture()
  assert.equal(inspectMembership(snap).status, 'vip')
  const fresh = structuredClone(snap)
  fresh.documents[1]!.scrollOffsetY = 80
  assert.equal(inspectMembership(fresh).status, 'unknown')
  assert.equal(inspectMembership(snap).status, 'vip')
})

test('徽标越出父标签但被overflow裁剪时不可作为会员证据', () => {
  for (const overflow of ['hidden', 'clip', 'auto', 'scroll']) {
    const snap = fixture()
    snap.strings.push(overflow)
    // Real badge y=2..34 overhangs filter label y=24..96; visible only with overflow visible.
    snap.documents[1]!.layout.styles![4]![1] = snap.strings.length - 1
    assert.equal(inspectMembership(snap).status, 'unknown', overflow)
  }
})

test('头像本身被主页面祖先裁剪时，不使用下方徽标授予确定会员状态', () => {
  const snap = fixture(), root = snap.documents[0]!, node = (root.nodes.nodeValue as number[]).length
  snap.strings.push('hidden')
  ;(root.nodes.nodeValue as number[]).push(-1); (root.nodes.nodeName as number[]).push(-1)
  ;(root.nodes.parentIndex as number[]).push(0); (root.nodes.attributes as number[][]).push([])
  ;(root.nodes.contentDocumentIndex as number[]).push(-1)
  ;(root.nodes.parentIndex as number[])[1] = node
  root.layout.nodeIndex.push(node); root.layout.bounds.push([0, 0, 10, 10])
  root.layout.styles!.push([snap.strings.length - 1])
  assert.equal(inspectMembership(snap).status, 'unknown')
})
