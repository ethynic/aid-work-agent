import test from 'node:test'
import assert from 'node:assert/strict'
import { detectRunning, parseTasklistCsv } from '../src/platform/localdb/running.js'

test('parseTasklistCsv：单/多进程、进程名大小写不敏感、千分位逗号内存列（代表性断言合并）', () => {
  // 多进程 + 千分位逗号内存列（"12,345 K"/"987,654 K"）不切断字段
  assert.deepEqual(
    parseTasklistCsv('"WXWork.exe","1234","Console","1","12,345 K"\n"WXWork.exe","5678","Console","1","987,654 K"\n'),
    [1234, 5678],
  )
  // 进程名匹配大小写不敏感
  assert.deepEqual(parseTasklistCsv('"wxwork.EXE","99","Console","1","1 K"'), [99])
})

test('parseTasklistCsv：空输出/INFO 无任务行 → 空数组', () => {
  assert.deepEqual(parseTasklistCsv(''), [])
  assert.deepEqual(parseTasklistCsv('\r\n  \n'), [])
  assert.deepEqual(parseTasklistCsv('INFO: 无运行的任务匹配指定的标准。\r\n'), [])
  assert.deepEqual(parseTasklistCsv('INFO: No tasks are running which match the specified criteria.\n'), [])
})

test('parseTasklistCsv：畸形行跳过（PID 非纯数字不parseInt 松解析、字段缺失/非正数/非 WXWork 行）', () => {
  // PID 必须整段纯数字：parseInt 式松解析会把 "123oops"→123、"12.5"→12，这里一律跳过
  assert.deepEqual(parseTasklistCsv('"WXWork.exe","123oops"\n"WXWork.exe","12.5"\n'), [])
  assert.deepEqual(parseTasklistCsv('"WXWork.exe"\n"WXWork.exe","abc"\n"other.exe","7"\n'), [])
  assert.deepEqual(parseTasklistCsv('"WXWork.exe","0"\n"WXWork.exe","-3"\n'), [])
  assert.deepEqual(parseTasklistCsv('"WXWork.exe","abc"\n"WXWork.exe","42","Console"\n"chrome.exe","9"'), [42])
})

test('parseTasklistCsv：引号内逗号不切断字段（第 1 列含逗号的非命中行）', () => {
  assert.deepEqual(parseTasklistCsv('"WXWork,exe","7","Console","1","1 K"'), [])
})

test('detectRunning：CSV 命中 → running=true + pids + probe=ok；只读 tasklist 调用契约固定（关键参数存在，不绑定整串顺序）', async () => {
  const calls: Array<{ file: string; args: string[] }> = []
  const info = await detectRunning({
    execFileFn: async (file, args) => {
      calls.push({ file, args })
      return { stdout: '"WXWork.exe","1234","Console","1","12,345 K"' }
    },
  })
  assert.equal(info.running, true)
  assert.deepEqual(info.pids, [1234])
  assert.equal(info.probe, 'ok')
  assert.equal(info.error, undefined)
  assert.equal(calls.length, 1)
  assert.equal(calls[0]!.file, 'tasklist')
  // 过滤条件与输出格式参数存在即可（/FI /FO CSV /NH 顺序与语义无关）
  assert.ok(calls[0]!.args.includes('/FI'))
  assert.ok(calls[0]!.args.includes('IMAGENAME eq WXWork.exe'))
  assert.ok(calls[0]!.args.includes('CSV'))
  assert.ok(calls[0]!.args.includes('/NH'))
})

test('detectRunning：无任务输出 → running=false + probe=ok（确实未运行）', async () => {
  const info = await detectRunning({ execFileFn: async () => ({ stdout: 'INFO: 无运行的任务匹配指定的标准。' }) })
  assert.equal(info.running, false)
  assert.deepEqual(info.pids, [])
  assert.equal(info.probe, 'ok')
  assert.equal(info.error, undefined)
})

test('detectRunning：exec 失败 → probe=failed + error，running=false 不可作为「未运行」结论，不抛出', async () => {
  const info = await detectRunning({
    execFileFn: async () => {
      throw new Error('tasklist missing')
    },
  })
  assert.equal(info.probe, 'failed')
  assert.notEqual(info.probe, 'ok')
  assert.equal(info.running, false)
  assert.deepEqual(info.pids, [])
  assert.match(info.error ?? '', /tasklist missing/)
})
