import assert from 'node:assert/strict'
import test from 'node:test'
import { sendEscapeClose } from '../src/main/boss/DetailCloser.js'

/** dispatchKey stub：记录调用，可注入失败 */
class KeyStub {
  calls: Array<Record<string, unknown>> = []
  mouseCalls = 0
  failOn = -1 // 第几次调用抛错（-1 不抛）
  async dispatchKey(opts: Record<string, unknown>): Promise<void> {
    if (this.failOn === this.calls.length) throw new Error('cdp disconnected')
    this.calls.push(opts)
  }
}

test('Escape 关闭详情：rawKeyDown + keyUp，vk=27', async () => {
  const stub = new KeyStub()
  await sendEscapeClose(stub)
  assert.equal(stub.calls.length, 2)
  assert.equal(stub.calls[0]!.type, 'rawKeyDown')
  assert.equal(stub.calls[1]!.type, 'keyUp')
  for (const c of stub.calls) {
    assert.equal(c.key, 'Escape')
    assert.equal(c.code, 'Escape')
    assert.equal(c.windowsVirtualKeyCode, 27)
  }
})

test('Escape 发送失败 → 抛错，不补发后续按键，绝不退化为点击不确定位置', async () => {
  const stub = new KeyStub()
  stub.failOn = 0
  await assert.rejects(() => sendEscapeClose(stub), /cdp disconnected/)
  assert.equal(stub.calls.length, 0, 'rawKeyDown 失败后 keyUp 不应再发')
  assert.equal(stub.mouseCalls, 0, '禁止任何鼠标补救点击')
})
