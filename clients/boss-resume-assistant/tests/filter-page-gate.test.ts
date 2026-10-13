import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { createBossFilterOperation, createBossClearFilterOperation } from '../src/main/operations/bossFilter.js'
import { createBossFilterOptionsOperation } from '../src/main/operations/bossFilterOptions.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'

// 隐藏推荐iframe仍有全量会员布局及筛选徽章；真实页面URL才是动作前置条件。
const fixture: DomSnapshot = JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/boss-vip-panel-top.json', import.meta.url), 'utf8'))
fixture.strings.push('筛选·7')
function sessionAt(url: string) {
  const calls = { snapshot: 0, click: 0, drag: 0, wheel: 0, close: 0 }
  const session: BossSession = {
    snapshot: async () => { calls.snapshot++; return fixture },
    getUrl: async () => url,
    click: async () => { calls.click++ },
    drag: async () => { calls.drag++ },
    mouseWheel: async () => { calls.wheel++ },
    clickBrowse: async () => {}, clickAndType: async () => {}, pressEscape: async () => {},
    captureFullpage: async () => Buffer.alloc(0), close: async () => { calls.close++ },
  }
  return { calls, factory: async () => session }
}
const context = () => ({ signal: new AbortController().signal, progress: () => {} })

test('筛选及探查在错误页看到完整后台会员面板也必须零输入并释放session', async () => {
  for (const url of ['https://www.zhipin.com/web/chat/user-center', 'https://www.zhipin.com/web/chat/index',
    'https://www.zhipin.com/web/chat/recommend/other', 'https://www.zhipin.com.evil.example/web/chat/recommend', 'invalid-url']) {
    for (const kind of ['filter', 'options', 'clear']) {
      const f = sessionAt(url)
      const result = kind === 'filter'
        ? await createBossFilterOperation(f.factory).execute({ filters: [{ label: '性别', options: ['男'] }], age: { min: 25, max: null } }, context())
        : kind === 'options' ? await createBossFilterOptionsOperation(f.factory).execute({}, context())
        : await createBossClearFilterOperation(f.factory).execute({}, context())
      assert.equal(result.code, 'WRONG_PAGE', `${kind} ${url}`)
      assert.equal(result.effect, 'none')
      assert.deepEqual(f.calls, { snapshot: 0, click: 0, drag: 0, wheel: 0, close: 1 })
    }
  }
})

test('推荐页允许query，参数错误仍发生于动作前而不是误报WRONG_PAGE', async () => {
  const f = sessionAt('https://www.zhipin.com/web/chat/recommend?source=test')
  const result = await createBossFilterOperation(f.factory).execute({ filters: [{ label: '页面没有的条件', options: ['值'] }] }, context())
  assert.equal(result.code, 'UI_CHANGED')
  assert.equal(result.effect, 'none')
  assert.equal(f.calls.snapshot, 1)
  assert.equal(f.calls.click, 0)
  assert.equal(f.calls.close, 1)
})
