import assert from 'node:assert/strict'
import test from 'node:test'
import {
  focusExistingWindow,
  getZoomCommand,
  nextZoomFactor,
  restoreWindowState,
} from '../electron/windowState.js'

const displays = [{ x: 0, y: 0, width: 1920, height: 1080 }]

test('窗口状态只在仍有可见区域时恢复，避免应用完全移出屏幕', () => {
  const valid = { bounds: { x: 100, y: 120, width: 1200, height: 800 }, maximized: true }
  assert.deepEqual(restoreWindowState(valid, displays), valid)
  assert.deepEqual(restoreWindowState({ bounds: { x: 4000, y: 4000, width: 1200, height: 800 } }, displays), {
    bounds: { x: 360, y: 140, width: 1200, height: 800 },
    maximized: false,
  })
  assert.deepEqual(restoreWindowState({ bounds: { x: 0, y: 0, width: 100, height: 100 } }, displays), {
    bounds: { x: 360, y: 140, width: 1200, height: 800 },
    maximized: false,
  })
})

test('窗口内缩放快捷键有上下限且不劫持普通输入', () => {
  assert.equal(getZoomCommand({ control: true, meta: false, key: '+' }), 'in')
  assert.equal(getZoomCommand({ control: true, meta: false, key: '-' }), 'out')
  assert.equal(getZoomCommand({ control: false, meta: false, key: '+' }), null)
  assert.equal(nextZoomFactor(2, 'in'), 2)
  assert.equal(nextZoomFactor(0.5, 'out'), 0.5)
  assert.equal(nextZoomFactor(1.4, 'reset'), 1)
})

test('第二实例只恢复并聚焦已有窗口，不创建第二个窗口', () => {
  const calls: string[] = []
  focusExistingWindow({
    isMinimized: () => true,
    restore: () => calls.push('restore'),
    focus: () => calls.push('focus'),
  })
  assert.deepEqual(calls, ['restore', 'focus'])
})
