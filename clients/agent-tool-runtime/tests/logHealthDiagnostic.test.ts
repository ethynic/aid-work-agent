/**
 * Phase 7 CR 修复（问题4，二轮）桌面消费端验证：日志目录不可写时健康诊断不丢失。
 *
 * 缺口背景：log-health.json 原与 runtime.log 同在 logs/ 目录，logs 被同名文件占位/
 * 无法创建时健康文件一起写失败且被吞——桌面 supervisor 不保存 stderr，诊断返回
 * {health:null,error:null}，把已发生的日志失败呈现为「无记录、无错误」。
 *
 * 修复语义：
 * - 写端：主位置 <home>/logs/log-health.json 写失败 → 降级写 <home>/log-health.json
 *   （独立于 logs 目录），记录 degraded=true + 原始错误；两处都失败才放弃（内存计数保留）。
 * - 读端：readLogFileHealth 探测日志目录异常（missing/not-directory/linked/unwritable），
 *   两位置按序查找并报告命中位置（source）；目录异常且无记录时返回明确 error，绝不 {null,null}。
 * - 诊断导出（runtime-diagnostic-summary.ps1）与 legacy/diagnostics.readLogFileHealth 同口径。
 */
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, rmSync, utimesSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { test } from 'node:test'
import { readLogFileHealth } from '../src/diagnostics.js'

const savedHome = process.env.AIDWORK_RUNTIME_HOME

/** logs 可写但 runtime.log 本身不可写（目录占位）的故障现场：日志通道失败、健康文件主位置可用 */
function makeFailingLogHome(): string {
  const home = mkdtempSync(path.join(os.tmpdir(), 'aidwork-log-health-'))
  mkdirSync(path.join(home, 'logs', 'runtime.log'), { recursive: true })
  return home
}

/** logs 目录本身不可写（被同名文件占位）：runtime.log 与健康文件主位置一起失效 → 触发降级 */
function makeBlockedLogsHome(): string {
  const home = mkdtempSync(path.join(os.tmpdir(), 'aidwork-log-health-blk-'))
  writeFileSync(path.join(home, 'logs'), 'not-a-directory', 'utf8')
  return home
}

/** home 根也不可用（路径本身是文件）：主位置与降级位置全部失败 → 健康快照无法落盘 */
function makeUnusableHome(): string {
  const home = mkdtempSync(path.join(os.tmpdir(), 'aidwork-log-health-dead-'))
  writeFileSync(path.join(home, 'occupied'), 'home-root-blocked', 'utf8')
  return path.join(home, 'occupied') // home 路径指向文件：其下任何子路径都不可写
}

test.after(() => {
  process.env.AIDWORK_RUNTIME_HOME = savedHome
})

test('日志文件自身失败、目录可写：健康快照落主位置（source=logs，未降级）', async () => {
  const home = makeFailingLogHome()
  try {
    process.env.AIDWORK_RUNTIME_HOME = home
    const { logInfo, logError, flushLogFileHealth, logFileHealthSnapshot } = await import('../src/log.js')
    const before = logFileHealthSnapshot().fileWriteFailures

    logInfo('fail-a')
    logError('fail-b')
    flushLogFileHealth()

    const memory = logFileHealthSnapshot()
    const reading = readLogFileHealth(home)
    assert.equal(reading.error, null)
    assert.ok(reading.health)
    assert.equal(reading.source, 'logs', '主位置可写时健康文件在 logs/ 下')
    assert.equal(reading.health.fileWriteFailures, before + 2)
    assert.equal(reading.health.fileWriteFailures, memory.fileWriteFailures, '文件与内存计数一致')
    assert.equal(reading.health.degraded, false, '目录可写不应降级')
    assert.ok(reading.health.lastFileWriteError)
    assert.ok(reading.health.updatedAt)
    assert.equal(reading.logDirError, null, 'logs 目录本身正常')
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('场景a：logs 被同名文件占位 → 健康快照降级落 home 根，诊断读到计数与降级说明', async () => {
  const home = makeBlockedLogsHome()
  try {
    process.env.AIDWORK_RUNTIME_HOME = home
    const { logInfo, flushLogFileHealth, logFileHealthSnapshot } = await import('../src/log.js')
    const before = logFileHealthSnapshot().fileWriteFailures

    logInfo('blocked-logs-failure')
    flushLogFileHealth()

    const memory = logFileHealthSnapshot()
    assert.equal(memory.fileWriteFailures, before + 1, '内存计数不受目录故障影响')
    assert.equal(memory.degraded, true, '写端应标记降级')
    assert.ok(memory.degradedReason, '应保留主位置失败的原始错误')

    const reading = readLogFileHealth(home)
    assert.equal(reading.error, null, '命中降级记录时 error 为空（降级说明在 record 与 logDirError）')
    assert.ok(reading.health, '降级位置的健康记录必须可读')
    assert.equal(reading.source, 'home-root', '两位置按序查找应命中降级位置')
    assert.equal(reading.health.fileWriteFailures, before + 1, '失败计数经降级位置进入诊断')
    assert.equal(reading.health.degraded, true)
    assert.ok(reading.health.degradedReason, '降级原因应进入诊断导出')
    assert.equal(reading.logDirError, 'log-dir-not-directory', '目录被同名文件占位应显式上报')
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('降级自动恢复：目录恢复可写后新失败回到主位置，降级标记清除', async () => {
  const home = makeFailingLogHome() // logs 正常、仅 runtime.log 不可写
  try {
    process.env.AIDWORK_RUNTIME_HOME = home
    const { logInfo, flushLogFileHealth, logFileHealthSnapshot } = await import('../src/log.js')
    const before = logFileHealthSnapshot().fileWriteFailures
    // 前一用例已把模块标记为降级；本用例 home 的 logs 目录可写 → 首次落盘即回主位置
    logInfo('recover-primary')
    flushLogFileHealth()

    const memory = logFileHealthSnapshot()
    assert.equal(memory.degraded, false, '主位置恢复可写后应清除降级标记')
    const reading = readLogFileHealth(home)
    assert.ok(reading.health)
    assert.equal(reading.source, 'logs')
    assert.equal(reading.health.fileWriteFailures, before + 1)
    assert.notEqual(reading.health.degraded, true)
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('场景b：home 根也不可用 → 健康快照无法落盘，但诊断返回明确 error（绝不 {null,null}）', async () => {
  const home = makeUnusableHome()
  try {
    process.env.AIDWORK_RUNTIME_HOME = home
    const { logInfo, flushLogFileHealth, logFileHealthSnapshot } = await import('../src/log.js')
    const before = logFileHealthSnapshot().fileWriteFailures

    logInfo('total-loss-failure')
    flushLogFileHealth()

    const memory = logFileHealthSnapshot()
    assert.equal(memory.fileWriteFailures, before + 1, '两处都写失败时内存计数仍累计')
    assert.equal(memory.degraded, true, '无法落盘也保留降级标记（说明健康通道受损）')

    const reading = readLogFileHealth(home)
    assert.equal(reading.health, null, '两处均无文件')
    assert.ok(reading.error, '必须返回明确 error，不得呈现为无记录')
    assert.notEqual(reading.error, null)
    assert.ok(reading.logDirError, '目录异常应一并上报')
  } finally {
    rmSync(path.dirname(home), { recursive: true, force: true })
  }
})

test('诊断读取端鲁棒性：正常目录无记录=无失败；损坏/形状非法降级为 error，不抛出', () => {
  const home = mkdtempSync(path.join(os.tmpdir(), 'aidwork-log-health-read-'))
  try {
    // 空_HOME（无 logs 目录）：目录异常必须显式上报，不得 {health:null,error:null}
    const empty = readLogFileHealth(home)
    assert.equal(empty.health, null)
    assert.equal(empty.error, 'log-dir-missing')
    assert.equal(empty.logDirError, 'log-dir-missing')

    mkdirSync(path.join(home, 'logs'), { recursive: true })
    // logs 目录正常且无健康文件：真实「无失败记录」，{null,null} 是事实而非掩盖
    const clean = readLogFileHealth(home)
    assert.equal(clean.health, null)
    assert.equal(clean.error, null)
    assert.equal(clean.source, null)
    assert.equal(clean.logDirError, null)

    const file = path.join(home, 'logs', 'log-health.json')
    writeFileSync(file, '{not-json', 'utf8')
    assert.equal(readLogFileHealth(home).error, 'log-health-corrupt-json')

    writeFileSync(file, JSON.stringify({ fileWriteFailures: 'three' }), 'utf8')
    assert.equal(readLogFileHealth(home).error, 'log-health-invalid-shape')

    writeFileSync(file, JSON.stringify({ fileWriteFailures: -1, lastFileWriteErrorAt: null, lastFileWriteError: null }), 'utf8')
    assert.equal(readLogFileHealth(home).error, 'log-health-invalid-shape')

    writeFileSync(file, JSON.stringify({ fileWriteFailures: 0, lastFileWriteErrorAt: null, lastFileWriteError: null }), 'utf8')
    const zero = readLogFileHealth(home)
    assert.equal(zero.error, null)
    assert.ok(zero.health)
    assert.equal(zero.health.fileWriteFailures, 0)
    assert.equal(zero.source, 'logs')

    // 主位置损坏、降级位置可读：健康记录命中降级位置并如实携带主位置损坏错误
    writeFileSync(file, '{corrupt', 'utf8')
    writeFileSync(
      path.join(home, 'log-health.json'),
      JSON.stringify({ fileWriteFailures: 2, lastFileWriteErrorAt: null, lastFileWriteError: null, degraded: true, degradedReason: 'boom' }),
      'utf8',
    )
    const mixed = readLogFileHealth(home)
    assert.ok(mixed.health)
    assert.equal(mixed.source, 'home-root')
    assert.equal(mixed.health.fileWriteFailures, 2)
    assert.equal(mixed.error, 'log-health-corrupt-json', '主位置损坏应如实上报')
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('健康文件写节流：突发失败不逐条同步写盘，flushLogFileHealth 补齐最终计数（防写放大）', async () => {
  const home = makeFailingLogHome()
  try {
    process.env.AIDWORK_RUNTIME_HOME = home
    const { logInfo, flushLogFileHealth, logFileHealthSnapshot } = await import('../src/log.js')
    const before = logFileHealthSnapshot().fileWriteFailures

    logInfo('burst-1')
    logInfo('burst-2')
    logInfo('burst-3') // 三次失败在同一节流窗口内：至多落盘一份快照
    const mid = readLogFileHealth(home).health
    assert.ok(
      !mid || mid.fileWriteFailures < before + 3,
      '节流窗口内不得逐失败同步写盘（至多即时一份 + 待写标记）',
    )

    flushLogFileHealth()
    const final = readLogFileHealth(home).health!
    assert.equal(final.fileWriteFailures, before + 3, '尾写补齐最终计数（无失败丢失）')
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

/** 伪造一个最小合法健康记录 JSON */
function healthJson(fields: Record<string, unknown>): string {
  return JSON.stringify({ lastFileWriteErrorAt: null, lastFileWriteError: null, ...fields })
}

test('三轮新鲜度选择：主位置旧记录不得遮蔽更新的降级记录（评审复现 1 次 vs 2 次）', () => {
  const home = mkdtempSync(path.join(os.tmpdir(), 'aidwork-log-health-fresh-'))
  try {
    mkdirSync(path.join(home, 'logs'), { recursive: true })
    const primary = path.join(home, 'logs', 'log-health.json')
    const fallback = path.join(home, 'log-health.json')

    // 评审复现现场：主位置旧（1 次，T1，未降级）+ 根位置新（2 次，T2 > T1，降级中）
    writeFileSync(
      primary,
      healthJson({ fileWriteFailures: 1, lastFileWriteError: 'old failure', degraded: false, degradedReason: null, updatedAt: '2026-10-21T01:00:00.000Z' }),
      'utf8',
    )
    writeFileSync(
      fallback,
      healthJson({ fileWriteFailures: 2, lastFileWriteError: 'EPERM', degraded: true, degradedReason: 'EEXIST: mkdir logs', updatedAt: '2026-10-21T02:00:00.000Z' }),
      'utf8',
    )
    const shadowed = readLogFileHealth(home)
    assert.ok(shadowed.health, '应有健康记录')
    assert.equal(shadowed.health.fileWriteFailures, 2, '必须取最新（根位置）计数，不得被旧主位置遮蔽为 1')
    assert.equal(shadowed.source, 'home-root', '最新记录在降级位置')
    assert.equal(shadowed.health.degraded, true, '降级状态随最新记录呈现')
    assert.equal(shadowed.error, null)
    assert.equal(shadowed.logDirError, null, 'logs 目录本身正常（残留旧文件≠目录异常）')

    // 反向：目录恢复（自愈）后主位置写新记录，根位置残留旧降级记录 → 选主位置
    writeFileSync(
      primary,
      healthJson({ fileWriteFailures: 3, lastFileWriteError: 'recovered-era failure', degraded: false, degradedReason: null, updatedAt: '2026-10-21T03:00:00.000Z' }),
      'utf8',
    )
    const healed = readLogFileHealth(home)
    assert.ok(healed.health, '自愈后主位置记录应可读')
    assert.equal(healed.source, 'logs', '主位置记录更新（自愈后）应胜出')
    assert.equal(healed.health.fileWriteFailures, 3)
    assert.notEqual(healed.health.degraded, true)
    assert.equal(healed.error, null)
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('旧格式（无 updatedAt）视为更旧；双方均无 updatedAt 时以文件 mtime 兜底决胜', () => {
  const home = mkdtempSync(path.join(os.tmpdir(), 'aidwork-log-health-old-'))
  try {
    mkdirSync(path.join(home, 'logs'), { recursive: true })
    const primary = path.join(home, 'logs', 'log-health.json')
    const fallback = path.join(home, 'log-health.json')
    const tOld = new Date('2026-10-20T00:00:00Z')
    const tNew = new Date('2026-10-21T00:00:00Z')

    // 一方有 updatedAt（旧格式视为更旧）：主位置旧格式 + 根位置新格式 → 根位置胜
    writeFileSync(primary, healthJson({ fileWriteFailures: 5 }), 'utf8')
    utimesSync(primary, tNew, tNew) // 即便主位置 mtime 更新，无 updatedAt 仍视为更旧
    writeFileSync(fallback, healthJson({ fileWriteFailures: 2, updatedAt: '2026-10-21T01:00:00.000Z' }), 'utf8')
    utimesSync(fallback, tOld, tOld)
    const mixed = readLogFileHealth(home)
    assert.ok(mixed.health)
    assert.equal(mixed.source, 'home-root', '有 updatedAt 的一律更新（旧格式视为更旧），不受 mtime 影响')
    assert.equal(mixed.health.fileWriteFailures, 2)

    // 双方均无 updatedAt：mtime 新者胜（utimesSync 设定确定时间戳）
    writeFileSync(primary, healthJson({ fileWriteFailures: 7 }), 'utf8')
    utimesSync(primary, tNew, tNew)
    writeFileSync(fallback, healthJson({ fileWriteFailures: 9 }), 'utf8')
    utimesSync(fallback, tOld, tOld)
    const byMtime = readLogFileHealth(home)
    assert.ok(byMtime.health)
    assert.equal(byMtime.source, 'logs', '双方均旧格式时按 mtime 决胜：主位置 mtime 新')
    assert.equal(byMtime.health.fileWriteFailures, 7)

    // mtime 反向 → 根位置胜（规则对称）
    utimesSync(primary, tOld, tOld)
    utimesSync(fallback, tNew, tNew)
    const reversed = readLogFileHealth(home)
    assert.ok(reversed.health)
    assert.equal(reversed.source, 'home-root')
    assert.equal(reversed.health.fileWriteFailures, 9)
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})
