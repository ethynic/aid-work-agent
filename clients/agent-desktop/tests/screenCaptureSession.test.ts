/**
 * 采集窗口会话登记（设计 6.1）：open 换窗、send 只进当前会话、close 按会话销毁、
 * pageFrame 装配 Main→Host 帧、windowCrashed 结算、destroy 退出清理。
 * 窗口操作走注入的假句柄，不启动 Electron。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { ScreenCaptureSession } from '../electron/screenCaptureSession.js'

interface FakeWindow { destroyed: boolean; forwarded: unknown[] }

function harness() {
  const created: Array<{ sessionId: string; stunUrls: string[]; window: FakeWindow }> = []
  const destroyed: FakeWindow[] = []
  const activeChanges: boolean[] = []
  const ops = {
    create(sessionId: string, stunUrls: string[]): FakeWindow {
      const window: FakeWindow = { destroyed: false, forwarded: [] }
      created.push({ sessionId, stunUrls, window })
      return window
    },
    destroy(window: FakeWindow): void {
      window.destroyed = true
      destroyed.push(window)
    },
    forward(window: FakeWindow, message: unknown): void {
      window.forwarded.push(message)
    },
    onActiveChange(active: boolean): void {
      activeChanges.push(active)
    },
  }
  return { session: new ScreenCaptureSession<FakeWindow>(ops), created, destroyed, activeChanges }
}

test('open 建窗并登记会话；同一会话或新会话重复 open 都先关旧窗再建新窗', () => {
  const { session, created, destroyed, activeChanges } = harness()
  assert.equal(session.current(), null)
  session.open('sess-1', ['stun:a:3478'])
  assert.equal(created.length, 1)
  assert.deepEqual([created[0]!.sessionId, created[0]!.stunUrls], ['sess-1', ['stun:a:3478']])
  assert.equal(session.current(), created[0]!.window)
  assert.equal(destroyed.length, 0)
  assert.deepEqual(activeChanges, [true])

  session.open('sess-1', [])
  assert.equal(destroyed.length, 1, '同一会话重复 open 也先关旧窗')
  assert.equal(destroyed[0], created[0]!.window)
  assert.equal(created.length, 2)
  assert.equal(session.current(), created[1]!.window)

  session.open('sess-2', [])
  assert.equal(destroyed.length, 2, '新会话 open 先关上一扇旧窗')
  assert.equal(destroyed[1], created[1]!.window)
  assert.equal(created.length, 3)
  // 换窗先销毁旧窗（false）再建新窗（true）
  assert.deepEqual(activeChanges, [true, false, true, false, true])
})

test('send 只转发给当前会话的窗口；close 按会话销毁，不匹配的编号没有效果', () => {
  const { session, created, destroyed, activeChanges } = harness()
  session.open('sess-1', [])
  session.send('sess-1', { kind: 'answer', sdp: 'v=0' })
  assert.deepEqual(created[0]!.window.forwarded, [{ kind: 'answer', sdp: 'v=0' }])
  session.send('sess-9', { kind: 'restore' })
  assert.equal(created[0]!.window.forwarded.length, 1, '不匹配的会话编号不转发')
  session.close('sess-9')
  assert.equal(destroyed.length, 0, '不匹配的 close 没有效果')
  assert.deepEqual(activeChanges, [true])
  session.close('sess-1')
  assert.equal(destroyed.length, 1)
  assert.equal(destroyed[0], created[0]!.window)
  assert.equal(session.current(), null)
  assert.deepEqual(activeChanges, [true, false])
  session.send('sess-1', { kind: 'restore' })
  assert.equal(created[0]!.window.forwarded.length, 1, '关闭后不再转发')
  session.close('sess-1')
  assert.equal(destroyed.length, 1, 'close 可安全重复')
})

test('pageFrame 装配 Main→Host 帧：session_id 由登记处填写，页面字段不能覆盖', () => {
  const { session } = harness()
  assert.equal(session.pageFrame({ kind: 'offer', sdp: 'v=0' }), null, '没有会话时不装配')
  session.open('sess-1', [])
  const frame = session.pageFrame({ kind: 'offer', sdp: 'v=0', event: 'forged', version: 9, session_id: 'other' })
  assert.deepEqual(frame, { event: 'runtime_screen', version: 1, session_id: 'sess-1', kind: 'offer', sdp: 'v=0' })
})

test('windowCrashed 结算当前会话并销毁窗口；没有会话时为 null', () => {
  const { session, created, destroyed, activeChanges } = harness()
  assert.equal(session.windowCrashed(), null)
  session.open('sess-1', [])
  const frame = session.windowCrashed()
  assert.deepEqual(frame, { event: 'runtime_screen', version: 1, session_id: 'sess-1', kind: 'window-crashed' })
  assert.equal(destroyed.length, 1)
  assert.equal(destroyed[0], created[0]!.window)
  assert.equal(session.current(), null)
  assert.deepEqual(activeChanges, [true, false])
  assert.equal(session.windowCrashed(), null, '没有会话的崩溃不重复结算')
})

test('destroy 清理窗口与托盘状态；重复 destroy 没有效果', () => {
  const { session, created, destroyed, activeChanges } = harness()
  session.destroy()
  assert.deepEqual(destroyed, [])
  session.open('sess-1', [])
  session.destroy()
  assert.equal(destroyed.length, 1)
  assert.equal(destroyed[0], created[0]!.window)
  assert.deepEqual(activeChanges, [true, false])
  session.destroy()
  assert.equal(destroyed.length, 1)
})
