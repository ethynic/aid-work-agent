import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { GreetExecutor } from '../src/main/boss/GreetExecutor.js'
import { VisualRecovery, type VisualContext } from '../src/main/boss/visualRecovery.js'
import { WindowCaptureError } from '../src/main/input/windowExperiment.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import { loadedSnapshot } from './loadedCandidateFixture.js'

test('independent: a mixed approved list cannot authorize a different visible candidate', async () => {
  let inputs = 0
  const result = await new GreetExecutor({
    snapshot: async () => loadedSnapshot([
      { name: '批准重名', y: 200, advantage: '甲' },
      { name: '批准重名', y: 3000, advantage: '乙' },
      { name: '未批准者', y: 450 },
    ]),
    click: async () => { inputs++ },
    scroll: async () => { throw new Error('Unapproved candidate must not cause a search') },
    sleep: async () => {},
  }).greetVisible({ names: ['批准重名', '不存在者'] })
  assert.equal(inputs, 0)
  assert.equal(result.greeted, 0)
  assert.deepEqual(result.missingNames, ['不存在者'])
  assert.equal(result.ambiguousNames?.[0]?.name, '批准重名')
})

test('independent: an uncertain reposition invalidates previous page clicks across recovery instances', async () => {
  const root = await mkdtemp(join(tmpdir(), 'boss-independent-recovery-'))
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9gAAAABJRU5ErkJggg==', 'base64')
  const context: VisualContext = { purpose: 'diagnose', operation: { name: 'boss_open_detail', result_kind: 'page_state' }, execution: { attempts_used: 1 }, available_actions: ['dismiss_click'] }
  const firstId = '12111111-1111-4111-8111-111111111111', secondId = '12111111-1111-4111-8111-111111111112'
  const signal = new AbortController().signal, budget = { repositionsUsed: 0 }
  let inputs = 0, diagnoses = 0
  const session = {
    browserTargetRef: '11111111-1111-4111-8111-111111111111', cdpPort: 9222,
    getUrl: async () => 'https://www.zhipin.com/web/chat/recommend', getTargetId: async () => 'bound-target',
    getPageMetrics: async () => ({ width: 1, height: 1 }), activateTarget: async () => {},
    captureFullpage: async () => png, clickBrowse: async () => { inputs++ },
  } as unknown as BossSession
  try {
    const before = new VisualRecovery({ root, diagnose: async options => {
      diagnoses++
      return { status: 200, body: { api_version: 2, request_id: options.requestId, state: 'completed', diagnosis: {
        obstruction: 'present', goal_state: 'not_satisfied', proposed_action: { kind: 'dismiss', space: 'page_image_px',
          point: { x: 0.5, y: 0.5 }, target_box: { x: 0, y: 0, width: 1, height: 1 }, target_label: '关闭' },
      } } }
    } })
    const old = await before.inspect({ request_id: firstId, visual_context: context }, session, signal, budget)
    assert.equal(typeof old.recovery_ref, 'string')
    const failedMove = new VisualRecovery({ root, captureWindow: async (_path, _port, _runner, options) => {
      assert.equal(options?.budget, budget)
      options!.budget!.repositionsUsed++
      throw new WindowCaptureError('window_reposition_failed')
    } })
    await assert.rejects(failedMove.inspect({ request_id: secondId, observation_mode: 'window', visual_context: context }, session, signal, budget), /绑定窗口观察失败/)
    assert.equal(budget.repositionsUsed, 1)
    await assert.rejects(before.dismiss(String(old.recovery_ref), session, signal, budget), /归位后观察已失效/)
    const cached = await before.inspect({ request_id: firstId, retrieve_only: true }, session, signal, budget)
    assert.equal(cached.recovery_action_error, 'STALE_OBSERVATION')
    assert.equal(cached.recovery_ref, undefined)
    assert.equal(inputs, 0)
    assert.equal(diagnoses, 1, 'failed native reposition must not silently buy another model diagnosis')
  } finally { await rm(root, { recursive: true, force: true }) }
})
