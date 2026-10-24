import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { FilterSetter } from '../src/main/boss/FilterSetter.js'
import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'
import { withAccount } from './filterFixture.js'

// Real 2026-10-23 failure: only filter panel/ancestors, no candidate or account data.
const fixture: DomSnapshot = JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/boss-filter-document-scroll-20261023.json', import.meta.url), 'utf8'))
function styled(snapshot: DomSnapshot, node: number, axis: number, value: string): void {
  snapshot.strings.push(value)
  const doc = snapshot.documents[2]!
  doc.layout.styles![doc.layout.nodeIndex.indexOf(node)]![axis] = snapshot.strings.length - 1
}
function selected(snapshot: DomSnapshot, texts: string[]): DomSnapshot {
  const s = structuredClone(snapshot), d = s.documents[2]!
  s.strings.push('class', 'option active')
  const values = d.nodes.nodeValue as number[], parents = d.nodes.parentIndex as number[], attrs = d.nodes.attributes as number[][]
  values.forEach((value, n) => {
    if (texts.includes(s.strings[value]?.trim() ?? '')) attrs[parents[n]!] = [s.strings.length - 2, s.strings.length - 1]
  })
  return s
}
function clips(snapshot: DomSnapshot, label = '经验要求', option = '3-5年') {
  const setter = new FilterSetter({} as any) as any
  const hit = setter.locateRowOptionHit(snapshot, label, option)
  return { click: setter.visibleClipOf(snapshot, 'panel', hit), wheel: setter.visibleClipOf(snapshot, 'panel', hit, true) }
}

test('root BODY viewport propagation preserves the visible fixed filter after 1344px of list scrolling', () => {
  for (const [label, option] of [['经验要求', '3-5年'], ['学历要求', '本科']]) {
    const result = clips(fixture, label, option)
    assert.deepEqual(result.click, { left: 499, right: 1364, top: 93, bottom: 889 })
    assert.deepEqual(result.wheel, result.click)
  }
})

test('the retained scrolled-page state can apply both real options and confirm without scrolling or resetting the list', async () => {
  const start = withAccount(fixture)
  const done = withAccount({ strings: ['', '筛选·2'], documents: [{ nodes: { nodeValue: [0, 1], contentDocumentIndex: [] }, layout: { nodeIndex: [0, 1], bounds: [[0, 0, 1410, 1266], [1294, 52, 70, 36]] } }] })
  const snaps = [start, start, selected(start, ['3-5年']), selected(start, ['3-5年', '本科']), done]
  let i = 0, wheels = 0
  const clicks: Array<{ x: number; y: number }> = []
  const setter = new FilterSetter({ snapshot: async () => snaps[Math.min(i++, snaps.length - 1)]!, click: async p => { clicks.push(p) }, mouseWheel: async () => { wheels++ }, sleep: async () => {} })
  const result = await setter.apply({ experience: '3-5年', educations: ['本科'] })
  assert.equal(result.applied, true)
  assert.equal(result.filterCount, 2)
  assert.equal(wheels, 0, 'already visible options must not scroll the list or the panel')
  assert.equal(clicks.length, 4, 'clear, experience, education, confirm')
  assert.deepEqual(clicks[1], { x: 1251.9609375, y: 671.5 })
  assert.deepEqual(clicks[2], { x: 1042.625, y: 756.5 })
})

test('ordinary internal clipping remains binding even when BODY overflow propagates to the viewport', () => {
  const s = structuredClone(fixture), doc = s.documents[2]!
  styled(s, 604, 1, 'hidden')
  const b = doc.layout.bounds[doc.layout.nodeIndex.indexOf(604)]!
  b[3] = 20
  assert.equal(clips(s).click.bottom, 653, 'the actual inner clip must not be expanded to admit the experience target')
  assert.equal(clips(s).wheel.bottom, 889, 'wheel still starts at the real outer scrollport')
})

test('a BODY that does not propagate keeps its real element clip', () => {
  for (const [node, style, value] of [[2, 1, 'hidden'], [2, 7, 'paint'], [28, 7, 'layout']] as const) {
    const s = structuredClone(fixture)
    styled(s, node, style, value)
    assert.equal(clips(s).click.bottom, -78, `${node}/${style}/${value}: no unconditional BODY bypass`)
  }
})

test('an invisible iframe owner still clips out the fixed filter despite valid root overflow propagation', () => {
  const s = structuredClone(fixture), root = s.documents[0]!
  const owners = root.nodes.contentDocumentIndex as { index: number[]; value: number[] }
  const node = owners.index[owners.value.indexOf(2)]!
  root.layout.bounds[root.layout.nodeIndex.indexOf(node)]![1] = 2000
  const c = clips(s)
  assert.ok(c.click.top > c.click.bottom)
  assert.ok(c.wheel.top > c.wheel.bottom)
})
