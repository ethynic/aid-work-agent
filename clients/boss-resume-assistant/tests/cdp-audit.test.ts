import assert from 'node:assert/strict'
import test from 'node:test'
import { summarizeParams } from '../src/main/cdp/audit.js'

test('Page.captureScreenshot 只记录 format 与 captureBeyondViewport', () => {
  const s = summarizeParams('Page.captureScreenshot', {
    format: 'png',
    captureBeyondViewport: false,
    clip: { x: 1, y: 2 },
    someSecret: 'no',
  })
  assert.deepEqual(s, { format: 'png', captureBeyondViewport: false })
})

test('Input.* 只记录坐标/按键摘要', () => {
  const s = summarizeParams('Input.dispatchMouseEvent', {
    type: 'mousePressed',
    x: 100,
    y: 200,
    button: 'left',
    token: 'secret-token',
  })
  assert.deepEqual(s, { type: 'mousePressed', x: 100, y: 200, deltaY: undefined, key: undefined })
})

test('其他方法脱敏 cookie/security/body/token 等敏感字段', () => {
  const s = summarizeParams('DOMSnapshot.captureSnapshot', {
    computedStyles: [],
    cookie: 'session=abc',
    securityId: 'geek-123',
    body: 'full response body',
    authorization: 'Bearer xxx',
    nested: { token: 't', ok: 1 },
  })
  const obj = s as Record<string, unknown>
  assert.equal('cookie' in obj, false)
  assert.equal('securityId' in obj, false)
  assert.equal('body' in obj, false)
  assert.equal('authorization' in obj, false)
  // computedStyles 保留
  assert.deepEqual(obj.computedStyles, [])
  // nested 内的 token 脱敏
  const nested = obj.nested as Record<string, unknown>
  assert.equal('token' in nested, false)
  assert.equal(nested.ok, 1)
})
