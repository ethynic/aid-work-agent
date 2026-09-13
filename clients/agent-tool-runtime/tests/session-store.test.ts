/**
 * SessionStore 契约测试（设计 §8）：
 * 追加+回放往返、尾行容错、中间损坏/序号缺口/身份不符 → Corrupt、解密失败 →
 * blocked、重复 event_id 拒绝、ACK 游标不删除未确认记录。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtempSync, readFileSync, rmSync, appendFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  SessionStore,
  SessionStoreCorruptError,
  SessionStoreWriteError,
  sessionTaskDir,
  type SessionCrypto,
} from '../src/sessionTasks/sessionStore.js'

function fakeCrypto(): SessionCrypto & { failUnprotect: boolean } {
  return {
    async protect(plain: string) {
      return Buffer.from(plain, 'utf-8').toString('base64')
    },
    async unprotect(cipher: string) {
      if ((this as { failUnprotect: boolean }).failUnprotect) throw new Error('密文无效')
      return Buffer.from(cipher, 'base64').toString('utf-8')
    },
    failUnprotect: false,
  }
}

function newStore(assignmentId = 'a-1111', crypto = fakeCrypto()): { store: SessionStore; home: string; crypto: SessionCrypto & { failUnprotect: boolean } } {
  const home = mkdtempSync(join(tmpdir(), 'st-store-'))
  return { store: new SessionStore({ runtimeHome: home, assignmentId, crypto }), home, crypto: crypto as never }
}

test('追加/回放往返：连续序号、payload 解密、event_id 去重集合', async () => {
  const { store } = newStore()
  await store.appendEncrypted('e-1', 'observation', { coverage: 'complete_window' }, 1)
  await store.appendEncrypted('e-2', 'batch', { batch_id: 'b1' }, 1)
  const replayed = await store.replay()
  assert.equal(replayed.localSeq, 2)
  assert.equal(replayed.events.length, 2)
  assert.deepEqual((replayed.events[0] as { payload: { coverage: string } }).payload, {
    coverage: 'complete_window',
  })
  assert.equal((replayed.events[1] as { payload: { batch_id: string } }).payload.batch_id, 'b1')
  assert.ok(store.hasEvent('e-1'))
})

test('重放幂等：同一 store 二次 replay 结果一致', async () => {
  const { store } = newStore()
  await store.appendEncrypted('e-1', 'phase', { to: 'waiting_peer' }, 1)
  const a = await store.replay()
  const b = await store.replay()
  assert.equal(a.localSeq, b.localSeq)
  // 新 store 实例（模拟重启）回放一致
  const { store: fresh } = { store: new SessionStore({ runtimeHome: store['opts'].runtimeHome, assignmentId: 'a-1111', crypto: fakeCrypto() }) }
  const c = await fresh.replay()
  assert.equal(c.localSeq, 2 - 1 + 1 - 1) // = 1
  assert.equal(c.events.length, 1)
})

test('尾行不完整可丢弃（留诊断）；中间行损坏 → MID_FILE_CORRUPT', async () => {
  const { store, home } = newStore()
  await store.appendEncrypted('e-1', 'observation', { n: 1 }, 1)
  await store.appendEncrypted('e-2', 'observation', { n: 2 }, 1)
  const file = join(sessionTaskDir(home, 'a-1111'), 'events.jsonl')
  appendFileSync(file, '{"schema_version":1,"assignment_id":"a-1111"') // 未完整尾行
  const replayed = await store.replay()
  assert.equal(replayed.localSeq, 2)
  assert.match(replayed.droppedTailDiagnostic ?? '', /tail-incomplete/)

  // 中间损坏：把第 1 行截断后追加完整行 → 损坏在非尾位置
  writeFileSync(file, '{"broken":true}\n' + readFileSync(file, 'utf-8'))
  const { store: fresh2 } = { store: new SessionStore({ runtimeHome: home, assignmentId: 'a-1111', crypto: fakeCrypto() }) }
  await assert.rejects(() => fresh2.replay(), (err: unknown) => {
    assert.ok(err instanceof SessionStoreCorruptError)
    assert.equal(err.code, 'MID_FILE_CORRUPT')
    return true
  })
})

test('序号缺口 → SEQ_GAP（禁止跳过继续）', async () => {
  const { store, home } = newStore()
  await store.appendEncrypted('e-1', 'observation', {}, 1)
  await store.appendEncrypted('e-2', 'observation', {}, 1)
  // 手工删掉第 2 行制造缺口（重启后 replay 期望 seq2 却见 3）
  const file = join(sessionTaskDir(home, 'a-1111'), 'events.jsonl')
  const lines = readFileSync(file, 'utf-8').split('\n').filter((l) => l.trim())
  const tampered = JSON.parse(lines[1] as string) as { local_seq: number }
  tampered.local_seq = 5
  lines[1] = JSON.stringify(tampered)
  writeFileSync(file, lines.join('\n') + '\n')
  const fresh = new SessionStore({ runtimeHome: home, assignmentId: 'a-1111', crypto: fakeCrypto() })
  await assert.rejects(() => fresh.replay(), (err: unknown) => {
    assert.ok(err instanceof SessionStoreCorruptError)
    assert.equal(err.code, 'SEQ_GAP')
    return true
  })
})

test('assignment_id 不符 → IDENTITY_MISMATCH', async () => {
  const { store, home } = newStore('a-1111')
  await store.appendEncrypted('e-1', 'observation', {}, 1)
  const foreign = new SessionStore({ runtimeHome: home, assignmentId: 'a-2222', crypto: fakeCrypto() })
  foreign['opts'] // 构造完成
  // foreign 回放读同一文件（同 dir？不同 assignment 不同目录）——改为直接改写行内 id
  const file = join(sessionTaskDir(home, 'a-1111'), 'events.jsonl')
  const lines = readFileSync(file, 'utf-8').split('\n').filter((l) => l.trim())
  const tampered = JSON.parse(lines[0] as string) as { assignment_id: string }
  tampered.assignment_id = 'a-other'
  writeFileSync(file, JSON.stringify(tampered) + '\n')
  await assert.rejects(() => store.replay(), (err: unknown) => {
    assert.ok(err instanceof SessionStoreCorruptError)
    assert.equal(err.code, 'IDENTITY_MISMATCH')
    return true
  })
})

test('解密失败 → DECRYPT_FAILED（blocked 语义，禁止空状态启动）', async () => {
  const { store, crypto } = newStore()
  await store.appendEncrypted('e-1', 'observation', { a: 1 }, 1)
  crypto.failUnprotect = true
  await assert.rejects(() => store.replay(), (err: unknown) => {
    assert.ok(err instanceof SessionStoreCorruptError)
    assert.equal(err.code, 'DECRYPT_FAILED')
    return true
  })
})

test('重复 event_id 追加被拒绝（幂等重放保护）', async () => {
  const { store } = newStore()
  await store.appendEncrypted('e-1', 'observation', {}, 1)
  await assert.rejects(
    () => store.appendEncrypted('e-1', 'observation', {}, 1),
    (err: unknown) => err instanceof SessionStoreWriteError,
  )
  assert.equal(store.lastLocalSeq, 1)
})

test('ACK 游标只前进；未 ACK 记录保留在日志（同步 outbox 语义）', async () => {
  const { store } = newStore()
  await store.appendEncrypted('e-1', 'observation', {}, 1)
  await store.appendEncrypted('e-2', 'observation', {}, 1)
  await store.appendEncrypted('e-3', 'observation', {}, 1)
  store.ackUpTo(2)
  assert.equal(store.ackedLocalSeq, 2)
  assert.equal(store.pendingSyncCount, 1)
  store.ackUpTo(1) // 不回退
  assert.equal(store.ackedLocalSeq, 2)
})

test('写失败抛 SessionStoreWriteError 且序号不推进', async (t) => {
  const home = mkdtempSync(join(tmpdir(), 'st-store-ro-'))
  t.after(() => rmSync(home, { recursive: true, force: true }))
  const store = new SessionStore({ runtimeHome: home, assignmentId: 'a-3333', crypto: fakeCrypto() })
  // 把目录换成文件使 mkdir 失败
  const dir = sessionTaskDir(home, 'a-3333')
  const { mkdirSync: mk, writeFileSync: wf } = await import('node:fs')
  mk(join(home, 'session-tasks'), { recursive: true })
  wf(dir, 'not-a-dir')
  await assert.rejects(
    () => store.appendEncrypted('e-1', 'observation', {}, 1),
    (err: unknown) => err instanceof SessionStoreWriteError,
  )
  assert.equal(store.lastLocalSeq, 0)
})
