import assert from 'node:assert/strict'
import test from 'node:test'
import { applicationMenuTemplate, resolvePlatformWindowOptions, shouldQuitWhenAllWindowsClosed } from '../electron/platform.js'

test('macOS driver reserves native traffic-light space and keeps app lifecycle alive', () => {
  assert.deepEqual(resolvePlatformWindowOptions('darwin'), { titleBarStyle: 'hiddenInset', trafficLightPosition: { x: 16, y: 15 } })
  assert.equal(applicationMenuTemplate('darwin')[0]?.role, 'appMenu')
  assert.equal(shouldQuitWhenAllWindowsClosed('darwin'), false)
})

test('Windows driver preserves native window controls and last-window quit', () => {
  assert.deepEqual(resolvePlatformWindowOptions('win32'), {})
  assert.notEqual(applicationMenuTemplate('win32')[0]?.role, 'appMenu')
  assert.equal(shouldQuitWhenAllWindowsClosed('win32'), true)
})
