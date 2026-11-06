import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { DRIVER_PATH } from '../src/cli/commands/calibrate.js'

test('编译后的 calibrate 命令能找到实际 PowerShell 脚本，避免标定前因路径错误失败', () => {
  const expected = fileURLToPath(new URL('../../drivers/ps1/point-calibrate.ps1', import.meta.url))
  assert.equal(DRIVER_PATH, expected)
  assert.equal(existsSync(DRIVER_PATH), true)
})
