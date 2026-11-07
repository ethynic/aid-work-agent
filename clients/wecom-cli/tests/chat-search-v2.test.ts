import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { createWecomChatSearchOperation } from '../src/operations/chatSearch.js'
import { createTargetRef, verifyTargetRef } from '../src/platform/targetRef.js'
import type { OpContext } from '../src/operations/types.js'

const ctx: OpContext = { signal: new AbortController().signal, progress: () => {} }
const candidate = (name: string, section: '联系人' | '群聊') => ({ name, section })
function searchResult(query: string, match_kind: string, candidates: ReturnType<typeof candidate>[], same_name = false) {
  const op = createWecomChatSearchOperation({
    readCatalogFn: async (args) => {
      assert.equal(args.mode, 'search')
      assert.equal(args.query, query)
      return { message: 'ok', data: { self_user_id: 'account', source_generation: 'generation',
        match_kind, match_count: match_kind === 'none' ? 0 : candidates.length, same_name, candidates } }
    },
    createRefFn: (name, type, _subtitle, coords, account) => {
      assert.equal(coords, undefined, '搜索只签名称和账号，不签 OCR 坐标')
      return `ref:${account}:${type}:${name}`
    },
  })
  return op.execute({ query }, ctx)
}

test('售前客服群唯一匹配：返回可进入会话的账号绑定句柄', async () => {
  const result = await searchResult('售前客服群', 'exact', [candidate('售前客服群', '群聊')])
  assert.equal(result.success, true)
  assert.equal(result.effect, 'none')
  assert.equal((result.data.best as { target_ref: string }).target_ref, 'ref:account:group:售前客服群')
})

test('孙程未命中：把孙晨候选交给 agent，不自动改目标或签发句柄', async () => {
  const result = await searchResult('孙程', 'none', [candidate('孙晨', '联系人')])
  assert.equal(result.code, 'TARGET_NOT_FOUND')
  assert.deepEqual(result.data.candidates, [candidate('孙晨', '联系人')])
  assert.equal(result.data.best, undefined)
})

test('沟通命中两个不同会话：逐名列出，请用户指定', async () => {
  const names = [candidate('新劳务云系统项目沟通群', '群聊'), candidate('行政合同归档 智能体 沟通', '群聊')]
  const result = await searchResult('沟通', 'contains', names)
  assert.equal(result.code, 'TARGET_AMBIGUOUS')
  assert.deepEqual(result.data.candidates, names)
  assert.equal(result.data.same_name, false)
  assert.match(result.message, /指定完整名称/)
})

test('真正同名的两个身份：提示备注或重命名', async () => {
  const result = await searchResult('张三', 'exact', [candidate('张三', '联系人'), candidate('张三', '联系人')], true)
  assert.equal(result.code, 'TARGET_AMBIGUOUS')
  assert.match(result.message, /备注或重命名/)
})

test('指定联系人类型仍须跨群聊核对同名，不能签发默认 Enter 句柄', async () => {
  let signed = false
  const op = createWecomChatSearchOperation({
    readCatalogFn: async args => ({ message: '', data: { self_user_id: 'account',
      match_count: args.type === 'contact' ? 1 : 2, same_name: args.type === 'any',
      candidates: args.type === 'contact'
        ? [candidate('同名目标', '联系人')]
        : [candidate('同名目标', '联系人'), candidate('同名目标', '群聊')] } }),
    createRefFn: () => { signed = true; return 'must-not-sign' },
  })
  const result = await op.execute({ query: '同名目标', type: 'contact' }, ctx)
  assert.equal(result.code, 'TARGET_AMBIGUOUS')
  assert.match(result.message, /备注或重命名/)
  assert.equal(signed, false)
})

test('真实签发的唯一 ref 绑定账号且没有 OCR 坐标', async () => {
  const keyDir = mkdtempSync(join(tmpdir(), 'wecom-search-ref-'))
  try {
    const op = createWecomChatSearchOperation({
      readCatalogFn: async () => ({ message: '', data: { self_user_id: 'account', match_kind: 'exact',
        match_count: 1, candidates: [candidate('孙晨', '联系人')] } }),
      createRefFn: (name, type, subtitle, coords, account) => createTargetRef(name, type, subtitle, { keyDir, coords, account }),
    })
    const result = await op.execute({ query: '孙晨' }, ctx)
    const identity = verifyTargetRef((result.data.best as { target_ref: string }).target_ref, { keyDir })
    assert.equal(identity.account, 'account')
    assert.equal(identity.name, '孙晨')
    assert.equal(identity.x, undefined)
    assert.equal(identity.y, undefined)
  } finally { rmSync(keyDir, { recursive: true, force: true }) }
})
