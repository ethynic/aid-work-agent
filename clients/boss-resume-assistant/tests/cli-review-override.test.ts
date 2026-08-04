import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { getClient } from '../db/client.js'
import { reviewOverrideCommand } from '../src/cli/commands/review.js'
import { openCliDb, closeQuietly } from '../src/cli/cliRuntime.js'

/**
 * override 改判链路（真实 SQLite 文件库 + CLI 命令入口）。
 * 覆盖：改判落库（原评估不可变）、评估不存在/理由为空 fail-loud。
 */

function withTempDataDir(fn: (dataDir: string) => void): void {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cli-override-'))
  try {
    fn(dir)
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
}

function seedUncertain(): number {
  const db = getClient()
  db.prepare('INSERT INTO jobs (name) VALUES (?)').run('岗位A')
  db.prepare('INSERT INTO candidates (job_id, fingerprint, list_summary) VALUES (1, ?, ?)').run(
    'fp-1',
    JSON.stringify({ name: '张三' }),
  )
  const info = db
    .prepare("INSERT INTO evaluations (candidate_id, conclusion, reason) VALUES (1, 'UNCERTAIN', '无法判定')")
    .run()
  return Number(info.lastInsertRowid)
}

test('override 改判：写入 review_overrides，原评估行不可变', () => {
  withTempDataDir((dataDir) => {
    openCliDb({ dataDir })
    const evalId = seedUncertain()

    const code = reviewOverrideCommand({ dataDir, evaluationId: evalId, conclusion: 'QUALIFIED', reason: '人工复核合格' })
    assert.equal(code, 0)

    // 命令结束关了连接，重新打开验证落库
    openCliDb({ dataDir })
    const db = getClient()
    const evaluation = db.prepare('SELECT conclusion FROM evaluations WHERE id = ?').get(evalId) as { conclusion: string }
    assert.equal(evaluation.conclusion, 'UNCERTAIN', '原评估行结论不可变')
    const override = db.prepare('SELECT * FROM review_overrides WHERE evaluation_id = ?').get(evalId) as {
      original_conclusion: string
      override_conclusion: string
      override_reason: string
    }
    assert.equal(override.original_conclusion, 'UNCERTAIN')
    assert.equal(override.override_conclusion, 'QUALIFIED')
    assert.equal(override.override_reason, '人工复核合格')
    closeQuietly()
  })
})

test('override 改判 fail-loud：评估不存在 / 理由为空', () => {
  withTempDataDir((dataDir) => {
    openCliDb({ dataDir })
    const evalId = seedUncertain()
    assert.throws(
      () => reviewOverrideCommand({ dataDir, evaluationId: 9999, conclusion: 'QUALIFIED', reason: 'x' }),
      /不存在/,
    )
    openCliDb({ dataDir })
    assert.throws(
      () => reviewOverrideCommand({ dataDir, evaluationId: evalId, conclusion: 'REJECTED', reason: ' ' }),
      /理由不能为空/,
    )
    closeQuietly()
  })
})
