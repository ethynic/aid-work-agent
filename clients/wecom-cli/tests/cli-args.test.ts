/**
 * cli/args.ts 解析行为测试（与 weixin-cli 同范式）。
 *
 * 意图：解析规则（--key value / --flag / 位置参数）一旦被改坏，所有命令的参数都会静默错位。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { parseArgs, flagString, hasFlag } from '../src/cli/args.js'

test('argv 解析：位置参数 + --key value + --flag', () => {
  const args = parseArgs(['probe', '--verbose'])
  assert.deepEqual(args.positional, ['probe'])
  assert.equal(args.flags.get('verbose'), true)
})

test('argv 解析：--flag 后紧跟另一个 --flag 时按布尔处理（不吞掉下一个 key）', () => {
  const args = parseArgs(['mcp', '--stdio', '--verbose'])
  assert.equal(args.flags.get('stdio'), true)
  assert.equal(args.flags.get('verbose'), true)
})

test('argv 解析：--key value 与位置参数混合保持顺序', () => {
  const args = parseArgs(['search', '--query', '售前客服群', '--json'])
  assert.deepEqual(args.positional, ['search'])
  assert.equal(args.flags.get('query'), '售前客服群')
  assert.equal(args.flags.get('json'), true)
})

test('flagString：布尔 flag 返回 undefined（防止把 true 当成参数值）', () => {
  const args = parseArgs(['send', '--json'])
  assert.equal(flagString(args, 'json'), undefined)
  assert.equal(flagString(args, 'query'), undefined)
})

test('hasFlag：布尔与带值 flag 都视为存在', () => {
  const args = parseArgs(['doctor', '--json'])
  assert.equal(hasFlag(args, 'json'), true)
  assert.equal(hasFlag(args, 'stdio'), false)
})
