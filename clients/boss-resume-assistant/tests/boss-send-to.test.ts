/**
 * boss_send_to operation 测试：send-to 已统一走 ChatOpenExecutor（already/搜索/列表兜底+身份校验，
 * 2026-08-31 与 open-chat 同源），本文件只验 operation 编排——already 零搜索零点击链路
 * （避开搜索路径真延时）与参数 fail-fast。搜索/列表兜底/身份校验在 chat-open-executor.test.ts
 * 已全覆盖，发送链路在 chat-send-executor.test.ts 全覆盖。全程离线 fixture，不连 Chrome。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { createBossSendToOperation } from '../src/main/operations/bossSendTo.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import type { OpContext } from '../src/main/operations/types.js'
import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'
import { chatContactRef } from '../src/main/boss/BusinessOperationState.js'
import { actionSnapshot } from './chatActionFixture.js'

function silentCtx(): OpContext {
  return { signal: new AbortController().signal, progress: () => {} }
}

function fakeFactory(url: string, snaps: DomSnapshot[]) {
  const calls: string[] = []
  let i = 0
  const session: BossSession = {
    snapshot: async () => {
      calls.push('snapshot')
      return snaps[Math.min(i++, snaps.length - 1)]!
    },
    click: async () => {
      calls.push('click')
    },
    clickBrowse: async () => {},
    mouseWheel: async () => {
      calls.push('wheel')
    },
    pressEscape: async () => {
      calls.push('escape')
    },
    clickAndType: async () => {
      calls.push('type')
    },
    captureFullpage: async () => Buffer.alloc(0),
    getUrl: async () => {
      calls.push('getUrl')
      return url
    },
    close: async () => {
      calls.push('close')
    },
  }
  return { factory: async () => session, calls }
}

const CHAT_URL = 'https://www.zhipin.com/web/chat/index'
const MSG = '你好，我是 HR'

test('already + dry-run：头部已是目标联系人 → 零搜索零点击，只输入不发送，data 带 via=already', async () => {
  const f = fakeFactory(CHAT_URL, [
    actionSnapshot({ contact: '杨鸿杰' }),
    actionSnapshot({ contact: '杨鸿杰' }),
    actionSnapshot({ contact: '杨鸿杰', editor: MSG }),
  ])
  const op = createBossSendToOperation(f.factory)
  const r = await op.execute({ to: '杨鸿杰', message: MSG, dry_run: true }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.effect, 'none')
  assert.deepEqual({ to: r.data.to, via: r.data.via, sent: r.data.sent, dry_run: r.data.dry_run },
    { to: '杨鸿杰', via: 'already', sent: false, dry_run: true })
  assert.match(r.message, /dry-run/)
  // already：无 click/wheel/escape（不进搜索、不滚列表），type 仅来自消息输入的 clickAndType
  assert.equal(f.calls.filter((c) => c === 'click').length, 0)
  assert.equal(f.calls.filter((c) => c === 'wheel').length, 0)
  assert.equal(f.calls.filter((c) => c === 'escape').length, 0)
  assert.deepEqual(f.calls.filter((c) => c === 'type'), ['type'])
})

test('to 空白 → INVALID_ARGUMENT，不连 Chrome', async () => {
  const f = fakeFactory(CHAT_URL, [])
  const op = createBossSendToOperation(f.factory)
  const r = await op.execute({ to: '   ', message: MSG }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'INVALID_ARGUMENT')
  assert.match(r.message, /to（联系人姓名）不能为空/)
  assert.deepEqual(f.calls, [])
})

test('message 空 → INVALID_ARGUMENT，不连 Chrome', async () => {
  const f = fakeFactory(CHAT_URL, [])
  const op = createBossSendToOperation(f.factory)
  const r = await op.execute({ to: '杨鸿杰', message: ' ' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'INVALID_ARGUMENT')
  assert.match(r.message, /message（消息内容）不能为空/)
  assert.deepEqual(f.calls, [])
})


test('recovery cannot send to a different same-named contact after page repair',async()=>{
 const old=actionSnapshot({contact:'合成甲',demographic:'28岁'}),current=actionSnapshot({contact:'合成甲',demographic:'37岁'})
 const f=fakeFactory(CHAT_URL,[current,current])
 const result=await createBossSendToOperation(f.factory).execute({to:'合成甲',message:MSG},{...silentCtx(),expectedContactRef:chatContactRef(old)})
 assert.equal(result.success,false);assert.equal(result.effect,'none');assert.equal(result.code,'UI_CHANGED')
 assert.equal(f.calls.filter(c=>c==='type'||c==='click').length,0)
})
