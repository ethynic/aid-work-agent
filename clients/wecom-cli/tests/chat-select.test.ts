import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { createWecomChatSelectOperation, selectedTitleMatches } from '../src/operations/chatSelect.js'
import { createTargetRef, verifyTargetRef } from '../src/platform/targetRef.js'
import { CodedOperationError, type OpContext } from '../src/operations/types.js'

const ctx: OpContext = { signal: new AbortController().signal, progress: () => {} }
const keyDir = mkdtempSync(join(tmpdir(), 'wecom-select-'))
const root = mkdtempSync(join(tmpdir(), 'wecom-select-artifact-'))
test.after(() => { rmSync(keyDir, { recursive: true, force: true }); rmSync(root, { recursive: true, force: true }) })
const verifyRefFn = (ref: string) => verifyTargetRef(ref, { keyDir })
function ref(account = 'account', name = '售前客服群') {
  return createTargetRef(name, 'group', '', { keyDir, account })
}
function catalog(name = '售前客服群', count = 1, account = 'account') {
  return async () => ({ message: '', data: { self_user_id: account, self_name: '本人', match_count: count,
    candidates: [{ name, section: name === '陆伟' ? '联系人' : '群聊' }] } })
}

test('唯一会话才调用无 OCR 选择驱动，传完整名称并复制标题核对', async () => {
  let called = 0
  const op = createWecomChatSelectOperation({ verifyRefFn, readCatalogFn: catalog(), artifactDirFn: () => root,
    runDriverFn: async opts => {
      called++
      assert.ok(opts.script.endsWith('chat-select.ps1'))
      assert.deepEqual(opts.args?.slice(0, 6), ['-TargetName', '售前客服群', '-Section', 'group', '-SelfName', '本人'])
      assert.equal(opts.timeoutMs, 120_000)
      return { title: '售前客服群', timing_ms: { total: 1 } }
    } })
  const result = await op.execute({ target_ref: ref() }, ctx)
  assert.equal(result.code, 'OK')
  assert.equal(called, 1)
  assert.equal(result.data.title, '售前客服群')
})

test('同名增多或账号已切换时，在 UI 写入前拒绝进入', async () => {
  for (const [readCatalogFn, expected] of [
    [catalog('售前客服群', 2), 'TARGET_AMBIGUOUS'],
    [catalog('售前客服群', 1, 'other'), 'TARGET_REF_STALE'],
  ] as const) {
    let called = 0
    const op = createWecomChatSelectOperation({ verifyRefFn, readCatalogFn,
      runDriverFn: async () => { called++; return { title: '售前客服群' } } })
    const result = await op.execute({ target_ref: ref() }, ctx)
    assert.equal(result.code, expected)
    assert.equal(called, 0)
  }
})

test('联系人与群聊跨类型同名时 select 拒绝 Ctrl+F', async () => {
  const contactRef = createTargetRef('同名目标', 'contact', '', { keyDir, account: 'account' })
  let driverCalls = 0
  const op = createWecomChatSelectOperation({ verifyRefFn,
    readCatalogFn: async args => {
      assert.equal(args.type, 'any', '默认 Enter 不保证分区，须查全类型')
      return { message: '', data: { self_user_id: 'account', self_name: '本人', match_count: 2,
        candidates: [{ name: '同名目标', section: '联系人' }, { name: '同名目标', section: '群聊' }] } }
    },
    runDriverFn: async () => { driverCalls++; return { title: '同名目标' } },
  })
  const result = await op.execute({ target_ref: contactRef }, ctx)
  assert.equal(result.code, 'TARGET_AMBIGUOUS')
  assert.equal(driverCalls, 0)
})

test('旧无账号句柄虽有 OCR 坐标也不能进入', async () => {
  let called = 0
  const op = createWecomChatSelectOperation({ verifyRefFn, readCatalogFn: catalog(),
    runDriverFn: async () => { called++; return {} } })
  const legacy = createTargetRef('售前客服群', 'group', '', { keyDir, coords: { x: 10, y: 20 } })
  const result = await op.execute({ target_ref: legacy }, ctx)
  assert.equal(result.code, 'TARGET_REF_STALE')
  assert.equal(called, 0)
})

test('标题不符或驱动超时不得报告进入成功', async () => {
  const wrong = createWecomChatSelectOperation({ verifyRefFn, readCatalogFn: catalog(), artifactDirFn: () => root,
    runDriverFn: async () => ({ title: '另一个群' }) })
  assert.equal((await wrong.execute({ target_ref: ref() }, ctx)).code, 'UI_CHANGED')
  const slow = createWecomChatSelectOperation({ verifyRefFn, readCatalogFn: catalog(), artifactDirFn: () => root,
    runDriverFn: async () => { throw new CodedOperationError('RESULT_TIMEOUT', 'timeout') } })
  assert.equal((await slow.execute({ target_ref: ref() }, ctx)).code, 'EXECUTION_UNKNOWN')
})

test('联系人标题只接受全名或两个明确微信后缀，拒绝同前缀其他人', async () => {
  for (const title of ['陆伟', '陆伟@微信', '陆伟 @微信'])
    assert.equal(selectedTitleMatches(title, '陆伟', 'contact'), true)
  for (const title of ['陆伟民', '陆伟+微信', '陆伟（微信）', '陆伟@微信其他'])
    assert.equal(selectedTitleMatches(title, '陆伟', 'contact'), false)
  assert.equal(selectedTitleMatches('售前客服群@微信', '售前客服群', 'group'), false)
  const contactRef = createTargetRef('陆伟', 'contact', '', { keyDir, account: 'account' })
  const op = createWecomChatSelectOperation({ verifyRefFn, readCatalogFn: catalog('陆伟'),
    artifactDirFn: () => root, runDriverFn: async () => ({ title: '陆伟@微信' }) })
  assert.equal((await op.execute({ target_ref: contactRef }, ctx)).code, 'OK')
})
