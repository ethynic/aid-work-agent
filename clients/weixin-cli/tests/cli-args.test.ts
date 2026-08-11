/**
 * cli/args.ts 解析行为测试（与 BOSS reference 同范式 + --domain 命名铁律）。
 *
 * 意图：解析规则（--key value / --flag / 位置参数）一旦被改坏，所有命令的参数都会静默错位；
 * --domain 是「操作对象只能是参数值」铁律（设计 §4.1）的承载点，非法值必须被识别为 invalid。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { parseArgs, flagString, hasFlag, parseDomainFlag, CLI_DOMAINS } from '../src/cli/args.js'

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

test('argv 解析：多个位置参数保持顺序', () => {
  const args = parseArgs(['search', '--domain', 'souyisou', '--query', '年报'])
  assert.deepEqual(args.positional, ['search'])
  assert.equal(args.flags.get('domain'), 'souyisou')
  assert.equal(args.flags.get('query'), '年报')
})

test('flagString：布尔 flag 返回 undefined（防止把 true 当成 domain/参数值）', () => {
  const args = parseArgs(['probe', '--verbose'])
  assert.equal(flagString(args, 'verbose'), undefined)
  assert.equal(flagString(args, 'domain'), undefined)
})

test('hasFlag：布尔与带值 flag 都视为存在', () => {
  const args = parseArgs(['doctor', '--json'])
  assert.equal(hasFlag(args, 'json'), true)
  assert.equal(hasFlag(args, 'stdio'), false)
})

test('parseDomainFlag：合法值原样返回（souyisou/article/chat）', () => {
  for (const d of CLI_DOMAINS) {
    const args = parseArgs(['search', '--domain', d])
    assert.equal(parseDomainFlag(args), d)
  }
})

test('parseDomainFlag：未提供返回 undefined', () => {
  assert.equal(parseDomainFlag(parseArgs(['probe'])), undefined)
})

test('parseDomainFlag：非法值与布尔占位都拒绝为 invalid（对象永远不能成为子命令或自由文本）', () => {
  assert.equal(parseDomainFlag(parseArgs(['search', '--domain', 'contacts'])), 'invalid')
  assert.equal(parseDomainFlag(parseArgs(['search', '--domain', 'SOUYISOU'])), 'invalid')
  assert.equal(parseDomainFlag(parseArgs(['search', '--domain', ''])), 'invalid')
  // --domain 后无值（布尔 true）→ flagString 为 undefined → 视为未提供
  assert.equal(parseDomainFlag(parseArgs(['search', '--domain'])), undefined)
})
