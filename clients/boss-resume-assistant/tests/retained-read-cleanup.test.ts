import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { FilterSetter } from '../src/main/boss/FilterSetter.js'
import { JobSwitcher } from '../src/main/boss/JobSwitcher.js'
import { WinClickError } from '../src/main/input/WinMouseClicker.js'
import { createBossFilterOptionsOperation } from '../src/main/operations/bossFilterOptions.js'
import { createBossListJobsOperation } from '../src/main/operations/bossListJobs.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import { indexedValues, type DomSnapshot } from '../src/main/boss/domSnapshot.js'
import { withAccount } from './filterFixture.js'

function jobs(open: boolean): DomSnapshot {
  const texts = ['', '筛选', '开发 _ 上海 10-20K', ...(open ? ['开发 _ 上海 10-20K', '测试 _ 上海 10-20K'] : [])]
  const bounds = [[0, 0, 1249, 1277], [1100, 56, 112, 28], [800, 56, 232, 28], ...(open ? [[800, 110, 232, 28], [800, 148, 232, 28]] : [])]
  return { strings: texts, documents: [{ nodes: { nodeValue: texts.map((_, index) => index), contentDocumentIndex: [] },
    layout: { nodeIndex: texts.map((_, index) => index), bounds } }] }
}
const panel: DomSnapshot = withAccount(JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/boss-vip-panel-top.json', import.meta.url), 'utf8')))
{
  const doc = panel.documents[0]!, values = doc.nodes.nodeValue as number[], index = values.length
  panel.strings.push('筛选', '#text')
  values.push(panel.strings.length - 2); (doc.nodes.nodeName as number[]).push(panel.strings.length - 1)
  ;(doc.nodes.parentIndex as number[]).push(0); (doc.nodes.attributes as number[][]).push([])
  doc.layout.nodeIndex.push(index); doc.layout.bounds.push([2200, 24, 60, 31])
}
function panelClosed(): DomSnapshot {
  const snap = structuredClone(panel)
  for (let di = 1; di < snap.documents.length; di++) {
    const doc = snap.documents[di]!, values = doc.nodes.nodeValue as number[]
    const owners = new Set(indexedValues(doc.nodes.contentDocumentIndex, 'contentDocumentIndex').filter(([, child]) => child >= 0).map(([node]) => node))
    doc.layout.bounds = doc.layout.bounds.map((bounds, index) => index === 0 || owners.has(doc.layout.nodeIndex[index]!) || /^筛选(?:·\d+)?$/.test(snap.strings[values[doc.layout.nodeIndex[index]!]!] ?? '') ? bounds : [0, 0, 0, 0])
  }
  return snap
}
for (const kind of ['filter', 'jobs'] as const) for (const outcome of ['closed', 'ignored', 'error'] as const) {
  test(`${kind} retains verified data when cleanup ${outcome}; only cleanup can retry`, async () => {
    let closeCalls = 0, reads = 0, open = true
    const priorProbe = FilterSetter.prototype.probeOptions, priorJobs = JobSwitcher.prototype.listJobs
    FilterSetter.prototype.probeOptions = async function (options) { reads++; return priorProbe.call(this, options) }
    JobSwitcher.prototype.listJobs = async function () { reads++; return priorJobs.call(this) }
    const session: BossSession = {
      snapshot: async () => kind === 'jobs' ? jobs(open) : open ? structuredClone(panel) : panelClosed(),
      click: async () => { closeCalls++; if (outcome === 'error') throw new WinClickError('收尾落点被遮挡', 2); if (outcome === 'closed') open = false },
      clickBrowse: async () => { assert.fail('不应浏览其他候选人') }, clickAndType: async () => { assert.fail('不应键入') },
      pressEscape: async () => { assert.fail('不应盲发Escape') }, mouseWheel: async () => {},
      captureFullpage: async () => Buffer.alloc(0), getUrl: async () => 'https://www.zhipin.com/web/chat/recommend', close: async () => {},
    }
    try {
      const operation = kind === 'jobs' ? createBossListJobsOperation(async () => session) : createBossFilterOptionsOperation(async () => session)
      const result = await operation.execute({}, { signal: new AbortController().signal, progress() {} })
      assert.equal(result.success, true)
      assert.equal(reads, 1, '已得资料后只重试关闭，不重新探查/读取')
      assert.ok((result.data[kind === 'jobs' ? 'jobs' : 'rows'] as unknown[]).length > 0)
      const facts = result.data.operation_recovery as any
      assert.equal(closeCalls, outcome === 'ignored' ? 2 : 1)
      assert.equal(facts.execution.ordinary_retries_used, outcome === 'ignored' ? 1 : 0)
      if (outcome === 'closed') {
        assert.equal(result.data.cleanup_error, undefined)
        assert.equal(facts.verification.status, 'satisfied')
      } else {
        assert.ok(result.data.cleanup_error)
        assert.equal(facts.operation_class, 'readonly')
        assert.equal(facts.operation.result_kind, 'business_result')
        assert.match(facts.operation.goal, /保留已读/)
        assert.equal(facts.verification.status, outcome === 'ignored' ? 'not_satisfied' : 'inconclusive')
        assert.match(result.message, /已保留读取结果/)
      }
    } finally {
      FilterSetter.prototype.probeOptions = priorProbe
      JobSwitcher.prototype.listJobs = priorJobs
    }
  })
}
