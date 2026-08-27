/**
 * cli/args.ts 解析行为测试（原 cli-rendering.test.ts 中 parseArgs 用例的迁移 + 补齐）。
 * 意图：7 个保留命令的全部参数解析都经过 parseArgs/flagString/hasFlag，
 * 解析规则（--key value / --flag / 位置参数）一旦被改坏，所有命令的参数都会静默错位。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { parseArgs, flagString, hasFlag } from '../src/cli/args.js'

test('argv 解析：位置参数 + --key value + --flag', () => {
  const args = parseArgs(['filter', '--education', '本科,硕士', '--clear'])
  assert.deepEqual(args.positional, ['filter'])
  assert.equal(args.flags.get('education'), '本科,硕士')
  assert.equal(args.flags.get('clear'), true)
})

test('argv 解析：--flag 后紧跟另一个 --flag 时按布尔处理（不吞掉下一个 key）', () => {
  // 若把下一个 --key 误当作 value，--no-preview 会变成字符串且 accept 的 preview 逻辑被反转
  const args = parseArgs(['accept', '--no-preview', '--limit', '5'])
  assert.equal(args.flags.get('no-preview'), true)
  assert.equal(args.flags.get('limit'), '5')
})

test('argv 解析：多个位置参数保持顺序（goto recommend / interview 场景）', () => {
  const args = parseArgs(['goto', 'recommend', '--cdp-port', '9223'])
  assert.deepEqual(args.positional, ['goto', 'recommend'])
  assert.equal(args.flags.get('cdp-port'), '9223')
})

test('flagString：布尔 flag 返回 undefined（防止把 true 当成端口/限制值）', () => {
  const args = parseArgs(['greet', '--limit'])
  assert.equal(flagString(args, 'limit'), undefined)
  assert.equal(flagString(args, 'cdp-port'), undefined)
})

test('hasFlag：布尔与带值 flag 都视为存在', () => {
  const args = parseArgs(['filter', '--clear', '--salary', '10-20K'])
  assert.equal(hasFlag(args, 'clear'), true)
  assert.equal(hasFlag(args, 'salary'), true)
  assert.equal(hasFlag(args, 'probe'), false)
})

test('argv 解析：-h 是唯一单横线短 flag（--help 别名），不落进位置参数', () => {
  // 若 -h 被当位置参数，`greet -h` 会因多余位置参数被拒而不是显示帮助
  const args = parseArgs(['greet', '-h'])
  assert.equal(args.flags.get('h'), true)
  assert.deepEqual(args.positional, ['greet'])
})

test('argv 解析：--limit -3 的负值仍按 value 处理（-h 特判不影响其它单横线 token）', () => {
  const args = parseArgs(['greet', '--all', '--limit', '-3'])
  assert.equal(args.flags.get('limit'), '-3')
  assert.deepEqual(args.positional, ['greet'])
})
