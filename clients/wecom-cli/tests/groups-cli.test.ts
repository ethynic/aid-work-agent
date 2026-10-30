/** 参数缺值或拼写错误不得把有范围查询悄悄改为全量查询。 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const cli = fileURLToPath(new URL('../src/cli/index.js', import.meta.url))
test('群CLI缺值/未知参数/额外位置参数在任何本地库读取前拒绝', () => {
  for (const args of [
    ['list-groups', '--query', '--limit', '3'],
    ['list-groups', '--limit'],
    ['list-groups', '--qurey', '测试'],
    ['read-group', '--target-name'],
    ['read-group', '--target-name', 'synthetic', '--from-time', '--to-time', '2026-10-22 00:00:00'],
    ['read-group', '--target-name', 'synthetic', '--to-time'],
    ['read-group', '--target-name', 'synthetic', '--from-tim', '2026-10-22 00:00:00'],
    ['list-groups', 'unexpected'],
  ]) {
    const result = spawnSync(process.execPath, [cli, ...args, '--json'], {
      encoding: 'utf8', timeout: 10000, env: { ...process.env, AID_WECOM_MUTEX_SCOPE: 'test-group-cli' },
    })
    assert.equal(result.status, 2, JSON.stringify(args))
    const data = JSON.parse(result.stdout.trim())
    assert.equal(data.code, 'INVALID_ARGUMENT')
    assert.equal(data.effect, 'none')
    assert.deepEqual(data.data, {})
  }
})
