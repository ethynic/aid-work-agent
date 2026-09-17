/**
 * B1.0 特征测试：AssignmentMeta v1 回放语义（BOSS 端侧会话设计 §9.1 B1.0 / 计划 §3 第 9 项）。
 *
 * 现状锁定（B1.2 前，零回归基线）：
 * - meta.json schema_version=1，写入字段不含 scenario_key（AssignmentMeta v1 无该字段）；
 * - readMeta 对缺少 scenario_key 的 meta.json 正常回放（当前唯一形态）；
 * - readMeta 对含未知字段（未来 v2 增加的 scenario_key 等）的 meta.json 容忍——
 *   回放路径不因未知字段失败（readMeta 只校验已知字段，忽略其余）；
 * - readMetaTaskId 不解密 spec 也能提取 task_id（meta 无 scenario_key 时的归属定位）。
 *
 * 与既有测试的分工：meta 写入失败/损坏/解密失败/缺 task_id 的阻断语义已由
 * session-engine.test.ts（meta.json 写入失败、旧 assignment 元数据解密失败、
 * meta 缺失/损坏不可归属）与 session-store.test.ts 覆盖；本文件只锁定
 * "scenario_key 字段缺席/未知字段容忍"这一 B1.2 envelope 演进点。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  SessionStore,
  sessionTaskDir,
  type SessionCrypto,
} from '../src/sessionTasks/sessionStore.js'

function fakeCrypto(): SessionCrypto {
  return {
    async protect(plain: string) {
      return Buffer.from(plain, 'utf-8').toString('base64')
    },
    async unprotect(cipher: string) {
      return Buffer.from(cipher, 'base64').toString('utf-8')
    },
  }
}

const ASSIGNMENT_ID = 'a-meta1111'

function newStore(): { store: SessionStore; home: string } {
  const home = mkdtempSync(join(tmpdir(), 'st-meta-char-'))
  return { store: new SessionStore({ runtimeHome: home, assignmentId: ASSIGNMENT_ID, crypto: fakeCrypto() }), home }
}

const V1_META = {
  input_version_base: 3,
  fresh_baseline: false,
  task_id: 'task-1',
  conversation_binding_id: 'conv-1',
  binding_version: 1,
  account_identity_version: 0,
  spec_revision: 1,
  spec: { goal: '特征测试目标', completion_rule: { mode: 'judged', criteria: ['c1'] } },
  fence: 2,
  control_epoch: 1,
}

test('writeMeta 现状：schema_version=1 且不含 scenario_key 字段（v1 形态锁定）', async (t) => {
  const { store, home } = newStore()
  t.after(() => rmSync(home, { recursive: true, force: true }))
  await store.writeMeta({ ...V1_META })
  const raw = JSON.parse(readFileSync(join(sessionTaskDir(home, ASSIGNMENT_ID), 'meta.json'), 'utf-8')) as Record<string, unknown>
  assert.equal(raw['schema_version'], 1)
  assert.equal('scenario_key' in raw, false, '现状 meta.json 不含 scenario_key；B1.2 加字段时本断言显式变红')
  // 加密 spec 不落明文
  assert.equal(typeof raw['encrypted_spec'], 'string')
  assert.equal('spec' in raw, false)
})

test('readMeta 现状：无 scenario_key 的 v1 meta.json 正常回放（微信按唯一场景处理）', async (t) => {
  const { store, home } = newStore()
  t.after(() => rmSync(home, { recursive: true, force: true }))
  await store.writeMeta({ ...V1_META })
  const result = await store.readMeta()
  assert.equal(result.status, 'ok')
  if (result.status !== 'ok') return
  assert.equal(result.meta.task_id, 'task-1')
  assert.equal(result.meta.fence, 2)
  assert.equal(result.meta.control_epoch, 1)
  assert.equal(result.meta.binding_version, 1)
  assert.equal(result.meta.account_identity_version, 0)
  assert.equal(result.meta.input_version_base, 3)
  assert.equal(result.meta.fresh_baseline, false)
  assert.equal((result.meta.spec as { goal: string }).goal, '特征测试目标')
})

test('readMeta 容忍未知字段：手工写入带 scenario_key 的 meta.json 回放不失败（B1.2 演进点）', async (t) => {
  const { store, home } = newStore()
  t.after(() => rmSync(home, { recursive: true, force: true }))
  await store.writeMeta({ ...V1_META })
  const file = join(sessionTaskDir(home, ASSIGNMENT_ID), 'meta.json')
  const doc = JSON.parse(readFileSync(file, 'utf-8')) as Record<string, unknown>
  doc['scenario_key'] = 'weixin.conversation.v1' // 模拟 v2 版本新增长字段
  writeFileSync(file, JSON.stringify(doc))
  const result = await store.readMeta()
  assert.equal(result.status, 'ok', '现状 readMeta 只校验已知字段，未知字段不阻断回放')
  // readMetaTaskId 同样不受未知字段影响
  assert.deepEqual(store.readMetaTaskId(), { status: 'ok', taskId: 'task-1' })
})

test('readMetaTaskId 现状：meta.json 无 scenario_key 仍可归属任务定位（不解密 spec）', async (t) => {
  const { store, home } = newStore()
  t.after(() => rmSync(home, { recursive: true, force: true }))
  await store.writeMeta({ ...V1_META })
  assert.deepEqual(store.readMetaTaskId(), { status: 'ok', taskId: 'task-1' })
})
