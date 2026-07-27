import assert from 'node:assert/strict'
import test from 'node:test'
import {
  assertMethodAllowed,
  isForbidden,
  ALLOWED_METHODS,
  ForbiddenCdpMethodError,
} from '../src/main/cdp/methodPolicy.js'

test('白名单内全部 method 通过', () => {
  for (const method of ALLOWED_METHODS) {
    assert.doesNotThrow(() => assertMethodAllowed(method), `${method} should be allowed`)
  }
})

test('Runtime.* 一律拒绝（即使误加白名单）', () => {
  assert.throws(() => assertMethodAllowed('Runtime.enable'), ForbiddenCdpMethodError)
  assert.throws(() => assertMethodAllowed('Runtime.evaluate'), ForbiddenCdpMethodError)
  assert.throws(() => assertMethodAllowed('Runtime.callFunctionOn'), ForbiddenCdpMethodError)
  assert.equal(isForbidden('Runtime.enable'), true)
})

test('Debugger.* 一律拒绝', () => {
  assert.throws(() => assertMethodAllowed('Debugger.enable'), ForbiddenCdpMethodError)
  assert.throws(() => assertMethodAllowed('Debugger.setBreakpoint'), ForbiddenCdpMethodError)
})

test('脚本注入方法拒绝（OCR 是唯一正文提取路径）', () => {
  assert.throws(() => assertMethodAllowed('Page.addScriptToEvaluateOnNewDocument'), ForbiddenCdpMethodError)
  assert.throws(() => assertMethodAllowed('Page.removeScriptToEvaluateOnNewDocument'), ForbiddenCdpMethodError)
  assert.throws(() => assertMethodAllowed('Page.createIsolatedWorld'), ForbiddenCdpMethodError)
})

test('RemoteObject 方法拒绝', () => {
  assert.throws(() => assertMethodAllowed('DOM.resolveNode'), ForbiddenCdpMethodError)
})

test('未知 method 默认拒绝（默认 deny）', () => {
  assert.throws(() => assertMethodAllowed('Some.unknown.method'), ForbiddenCdpMethodError)
  assert.throws(() => assertMethodAllowed('Network.setCookie'), ForbiddenCdpMethodError)
  assert.equal(isForbidden('Totally.made.up'), true)
})

test('非字符串 method 拒绝', () => {
  assert.throws(() => assertMethodAllowed(undefined), ForbiddenCdpMethodError)
  assert.throws(() => assertMethodAllowed(123), ForbiddenCdpMethodError)
})

test('白名单 method 的 isForbidden 返回 false', () => {
  assert.equal(isForbidden('Page.captureScreenshot'), false)
  assert.equal(isForbidden('DOMSnapshot.captureSnapshot'), false)
  assert.equal(isForbidden('Input.dispatchMouseEvent'), false)
})
