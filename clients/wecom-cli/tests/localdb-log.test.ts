/**
 * 1.12 结构化日志单测：追加格式、轮转触发与保留数、redaction（32-hex/手机号）、
 * LOCALAPPDATA 缺失静默落空、写入失败不抛（真实 fs 临时目录 + 假 fs 替身混合验证）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import {
  LOCALDB_LOG_FILE_NAME,
  LOCALDB_LOG_ROTATE_BYTES,
  localdbLogDir,
  logLocaldbEvent,
  type LocaldbLogEvent,
  type LocaldbLogFs,
} from '../src/platform/localdb/log.js'

const FIXED_NOW = () => new Date('2026-10-14T08:00:00.000Z')

function tempDir(): string {
  return mkdtempSync(join(tmpdir(), 'wc-localdb-log-'))
}

test('localdbLogDir：默认按 LOCALAPPDATA 定位；缺失 → null；显式注入覆盖', () => {
  assert.equal(localdbLogDir('C:\\la'), join('C:\\la', 'AidWorkAgent', 'wecom-cli', 'logs'))
  assert.equal(localdbLogDir(null), null)
  const saved = process.env.LOCALAPPDATA
  try {
    delete process.env.LOCALAPPDATA
    assert.equal(localdbLogDir(), null)
    process.env.LOCALAPPDATA = 'C:\\la2'
    assert.equal(localdbLogDir(), join('C:\\la2', 'AidWorkAgent', 'wecom-cli', 'logs'))
  } finally {
    if (saved === undefined) delete process.env.LOCALAPPDATA
    else process.env.LOCALAPPDATA = saved
  }
})

test('追加格式：单行 JSONL、ISO UTC 时间戳、可定义字段缺省省略键', () => {
  const dir = tempDir()
  try {
    const event: LocaldbLogEvent = { type: 'db_fallback', run_id: 'r1', stage: 'wal-verify', reason: 'wal-changed' }
    assert.equal(logLocaldbEvent(event, { dir, now: FIXED_NOW }), true)
    const content = readFileSync(join(dir, LOCALDB_LOG_FILE_NAME), 'utf8')
    const lines = content.split('\n')
    assert.equal(lines.length, 2, '应为一行 + 结尾换行')
    const record = JSON.parse(lines[0]!) as Record<string, unknown>
    assert.equal(record['ts'], '2026-10-14T08:00:00.000Z')
    assert.equal(record['type'], 'db_fallback')
    assert.equal(record['run_id'], 'r1')
    assert.equal(record['stage'], 'wal-verify')
    assert.equal(record['reason'], 'wal-changed')
    assert.equal('detail' in record, false, '未定义字段应省略键')

    // 第二条只有 type：仅 ts+type 两键
    assert.equal(logLocaldbEvent({ type: 'keyscan_result' }, { dir, now: FIXED_NOW }), true)
    const second = JSON.parse(readFileSync(join(dir, LOCALDB_LOG_FILE_NAME), 'utf8').trimEnd().split('\n')[1]!)
    assert.deepEqual(Object.keys(second).sort(), ['ts', 'type'])
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('redaction：reason/detail 中的 32-hex 与手机号在盘上被遮蔽', () => {
  const dir = tempDir()
  const secretHex = 'a1b2c3d4e5f60718293a4b5c6d7e8f90'
  try {
    logLocaldbEvent(
      { type: 'db_attempt', reason: `key=${secretHex} phone=13800138000` },
      { dir, now: FIXED_NOW },
    )
    const onDisk = readFileSync(join(dir, LOCALDB_LOG_FILE_NAME), 'utf8')
    assert.ok(onDisk.includes('[REDACTED-HEX]'), '32-hex 应被遮蔽')
    assert.ok(onDisk.includes('1**********'), '手机号应被脱敏')
    assert.ok(!onDisk.includes(secretHex), '盘上不得有密钥 hex')
    assert.ok(!onDisk.includes('13800138000'), '盘上不得有手机号明文')
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('轮转：>5MB 触发改名 .1，旧 .1 被替换（保留 1 个历史），新事件进新文件', () => {
  const dir = tempDir()
  try {
    writeFileSync(join(dir, LOCALDB_LOG_FILE_NAME), 'x'.repeat(LOCALDB_LOG_ROTATE_BYTES + 1), 'utf8')
    writeFileSync(join(dir, LOCALDB_LOG_FILE_NAME + '.1'), 'OLD-HISTORY', 'utf8')
    assert.equal(logLocaldbEvent({ type: 'db_success' }, { dir, now: FIXED_NOW }), true)
    assert.equal(readFileSync(join(dir, LOCALDB_LOG_FILE_NAME + '.1'), 'utf8'), 'x'.repeat(LOCALDB_LOG_ROTATE_BYTES + 1))
    const fresh = readFileSync(join(dir, LOCALDB_LOG_FILE_NAME), 'utf8')
    assert.equal(fresh.trimEnd().split('\n').length, 1, '轮转后新文件只含本次事件')
    assert.ok(fresh.includes('"type":"db_success"'))
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('不轮转：恰好等于阈值（>5MB 才轮转）与文件不存在时保持追加', () => {
  const dir = tempDir()
  try {
    writeFileSync(join(dir, LOCALDB_LOG_FILE_NAME), 'y'.repeat(LOCALDB_LOG_ROTATE_BYTES), 'utf8')
    logLocaldbEvent({ type: 'db_attempt' }, { dir, now: FIXED_NOW })
    assert.ok(!existsSync(join(dir, LOCALDB_LOG_FILE_NAME + '.1')), '等于阈值不应轮转')
    const content = readFileSync(join(dir, LOCALDB_LOG_FILE_NAME), 'utf8')
    assert.ok(content.length > LOCALDB_LOG_ROTATE_BYTES, '应追加到原文件')
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('目录创建：目录不存在时递归创建后写入', () => {
  const dir = join(tempDir(), 'nested', 'logs')
  try {
    assert.equal(logLocaldbEvent({ type: 'mode_decided' }, { dir, now: FIXED_NOW }), true)
    assert.ok(existsSync(join(dir, LOCALDB_LOG_FILE_NAME)))
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('LOCALAPPDATA 缺失（dir=null）静默落空；非法 type 拒写', () => {
  assert.equal(logLocaldbEvent({ type: 'db_attempt' }, { dir: null }), false)
  assert.equal(
    logLocaldbEvent({ type: 'not_a_type' as LocaldbLogEvent['type'] }, { dir: mkdtempSync(join(tmpdir(), 'wc-localdb-log-')) }),
    false,
  )
})

test('写入失败不抛（假 fs append 抛错 → false）；轮转 rename 失败不阻断本次写入', () => {
  const throwingAppend: LocaldbLogFs = {
    mkdirSync: () => {},
    appendFileSync: () => {
      throw new Error('EBUSY: resource busy')
    },
    statSync: () => ({ size: 0 }),
    renameSync: () => {},
    unlinkSync: () => {},
  }
  assert.equal(logLocaldbEvent({ type: 'db_attempt' }, { dir: 'C:\\any', fs: throwingAppend }), false)

  const appendedCalls: Array<{ path: string; data: string }> = []
  const rotateFailsFs: LocaldbLogFs = {
    mkdirSync: () => {},
    appendFileSync: (path, data) => {
      appendedCalls.push({ path, data })
    },
    statSync: () => ({ size: LOCALDB_LOG_ROTATE_BYTES + 100 }),
    renameSync: () => {
      throw new Error('rename locked')
    },
    unlinkSync: () => {},
  }
  assert.equal(logLocaldbEvent({ type: 'db_success' }, { dir: 'C:\\any', fs: rotateFailsFs }), true)
  assert.equal(appendedCalls.length, 1, '轮转失败后仍应追加本次事件')
  assert.ok(appendedCalls[0]!.data.includes('"type":"db_success"'))
})

test('假 fs 全链路：轮转顺序为先删 .1 再改名，随后追加', () => {
  const ops: string[] = []
  const fakeFs: LocaldbLogFs = {
    mkdirSync: (d) => ops.push(`mkdir:${d}`),
    appendFileSync: (p, data) => ops.push(`append:${p}:${data.trim()}`),
    statSync: (p) => {
      ops.push(`stat:${p}`)
      return { size: LOCALDB_LOG_ROTATE_BYTES + 5 }
    },
    renameSync: (from, to) => ops.push(`rename:${from}->${to}`),
    unlinkSync: (p) => ops.push(`unlink:${p}`),
  }
  assert.equal(logLocaldbEvent({ type: 'keyscan_result', reason: 'hit' }, { dir: 'C:\\ld', now: FIXED_NOW, fs: fakeFs }), true)
  const idxStat = ops.findIndex((o) => o.startsWith('stat:'))
  const idxUnlink = ops.findIndex((o) => o.startsWith('unlink:'))
  const idxRename = ops.findIndex((o) => o.startsWith('rename:'))
  const idxAppend = ops.findIndex((o) => o.startsWith('append:'))
  assert.ok(idxStat >= 0 && idxUnlink > idxStat && idxRename > idxUnlink && idxAppend > idxRename, `操作序=${ops.join(' | ')}`)
  assert.ok(ops[idxUnlink]!.endsWith('localdb.log.1'))
  assert.ok(ops[idxRename]!.includes('localdb.log->C:\\ld\\localdb.log.1') || ops[idxRename]!.endsWith('->C:\\ld\\localdb.log.1'))
})

test('默认目录链路：不传 dir 时落 %LOCALAPPDATA% 定位的 logs（真实 fs 冒烟）', () => {
  const base = tempDir()
  const saved = process.env.LOCALAPPDATA
  try {
    process.env.LOCALAPPDATA = base
    assert.equal(logLocaldbEvent({ type: 'mode_decided' }, { now: FIXED_NOW }), true)
    assert.ok(existsSync(join(base, 'AidWorkAgent', 'wecom-cli', 'logs', LOCALDB_LOG_FILE_NAME)))
  } finally {
    if (saved === undefined) delete process.env.LOCALAPPDATA
    else process.env.LOCALAPPDATA = saved
    rmSync(base, { recursive: true, force: true })
  }
})

test('mkdir 失败也不抛（假 fs mkdir 抛错 → false）', () => {
  const mkdirThrows: LocaldbLogFs = {
    mkdirSync: () => {
      throw Object.assign(new Error('EACCES'), { code: 'EACCES' })
    },
    appendFileSync: () => {},
    statSync: () => ({ size: 0 }),
    renameSync: () => {},
    unlinkSync: () => {},
  }
  assert.equal(logLocaldbEvent({ type: 'db_attempt' }, { dir: 'C:\\ro', fs: mkdirThrows }), false)
})
