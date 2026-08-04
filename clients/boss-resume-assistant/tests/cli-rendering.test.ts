import assert from 'node:assert/strict'
import test from 'node:test'
import { renderEventLine, renderStatsLine } from '../src/cli/progress.js'
import { parseStdinCommand } from '../src/cli/stdinCommands.js'
import { renderQueueLines } from '../src/cli/commands/review.js'
import { renderActionLine, summarizeCdpAudit } from '../src/cli/commands/audit.js'
import { parseArgs } from '../src/cli/args.js'
import type { SessionEvent } from '../src/main/workflow/ScreeningSession.js'
import type { ReviewItem } from '../src/main/storage/reviewStore.js'

// ===== 事件渲染 =====

test('进度行：状态切换事件', () => {
  const e: SessionEvent = { type: 'state', state: 'READING_LIST', message: '读取列表', at: '2026-08-04T10:20:30.000Z' }
  assert.equal(renderEventLine(e), '[10:20:30] 状态 → READING_LIST — 读取列表')
})

test('进度行：候选人结论事件', () => {
  const e: SessionEvent = {
    type: 'candidate',
    candidateName: '张三',
    conclusion: 'QUALIFIED',
    message: '张三 → QUALIFIED（全部通过）',
    at: '2026-08-04T10:20:31.000Z',
  }
  assert.equal(renderEventLine(e), '[10:20:31] 候选人 张三 → QUALIFIED')
})

test('进度行：日志事件（PAUSED 原因等）', () => {
  const e: SessionEvent = { type: 'log', level: 'warn', message: 'Chrome 断开连接: EOF', at: '2026-08-04T10:20:32.000Z' }
  assert.equal(renderEventLine(e), '[10:20:32] warn: Chrome 断开连接: EOF')
})

test('统计行：三态计数 + 动作数', () => {
  const line = renderStatsLine({ viewed: 10, qualified: 3, rejected: 5, uncertain: 2, actionsAttempted: 4 })
  assert.equal(line, '[统计] 已看 10｜合格 3｜不合格 5｜待复核 2｜动作 4')
})

// ===== stdin 命令解析 =====

test('stdin 命令：p/r/s/q 映射，大小写与空白不敏感', () => {
  assert.equal(parseStdinCommand('p'), 'pause')
  assert.equal(parseStdinCommand(' R '), 'resume')
  assert.equal(parseStdinCommand('S'), 'stop')
  assert.equal(parseStdinCommand('\tq\n'), 'quit')
})

test('stdin 命令：无法识别返回 null', () => {
  assert.equal(parseStdinCommand('x'), null)
  assert.equal(parseStdinCommand(''), null)
  assert.equal(parseStdinCommand('pause'), null, '只接受单字符命令')
})

// ===== 复核队列终端行 =====

test('复核队列行：编号/姓名/结论/理由；空队列提示', () => {
  assert.deepEqual(renderQueueLines([]), ['复核队列为空：没有待处理的 UNCERTAIN 项。'])
  const item: ReviewItem = {
    evaluationId: 7,
    candidateId: 3,
    fingerprint: 'fp',
    candidateName: '张三',
    conclusion: 'UNCERTAIN',
    reason: 'LLM 无法引用简历证据',
    evidence: null,
    model: 'deepseek',
    createdAt: '2026-08-04 10:00:00',
    override: null,
  }
  const lines = renderQueueLines([item])
  assert.equal(lines.length, 1)
  assert.ok(lines[0]!.startsWith('#7\t'))
  assert.ok(lines[0]!.includes('张三'))
  assert.ok(lines[0]!.includes('UNCERTAIN'))
})

// ===== 审计渲染 =====

test('审计：动作行渲染', () => {
  const line = renderActionLine({
    id: 1,
    candidateId: 2,
    fingerprint: 'abcdef123456',
    candidateName: '张三',
    jobId: 1,
    action: 'GREET',
    reason: 'QUALIFIED',
    status: 'CONFIRMED',
    error: null,
    createdAt: '2026-08-04 10:00:00',
    sentAt: null,
    confirmedAt: null,
  })
  assert.ok(line.includes('张三'))
  assert.ok(line.includes('GREET'))
  assert.ok(line.includes('CONFIRMED'))
})

test('审计：CDP 按方法×状态计数', () => {
  const summary = summarizeCdpAudit([
    { id: 1, sessionIdHash: 'h', method: 'Page.captureScreenshot', paramsSummary: '{}', durationMs: 10, status: 'OK', at: 't' },
    { id: 2, sessionIdHash: 'h', method: 'Page.captureScreenshot', paramsSummary: '{}', durationMs: 10, status: 'OK', at: 't' },
    { id: 3, sessionIdHash: 'h', method: 'Input.dispatchMouseEvent', paramsSummary: '{}', durationMs: 5, status: 'ERROR', at: 't' },
  ])
  assert.deepEqual(summary, [
    { method: 'Page.captureScreenshot', status: 'OK', count: 2 },
    { method: 'Input.dispatchMouseEvent', status: 'ERROR', count: 1 },
  ])
})

// ===== argv 解析 =====

test('argv 解析：位置参数 + --key value + --flag', () => {
  const args = parseArgs(['run', '--job', 'a.yaml', '--open'])
  assert.deepEqual(args.positional, ['run'])
  assert.equal(args.flags.get('job'), 'a.yaml')
  assert.equal(args.flags.get('open'), true)
})
