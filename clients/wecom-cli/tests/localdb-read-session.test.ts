/** P1b 合成整链：真实三库、独立加密和 Python 驱动；只替换活进程/DPAPI 边界。 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, unlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createWecomReadSessionOperation } from '../src/operations/readSession.js'
import { CodedOperationError, type OpContext } from '../src/operations/types.js'
import { readLocaldbSession, type LocaldbReadDeps } from '../src/platform/localdb/readSession.js'
import { locateDataDir } from '../src/platform/localdb/datadir.js'
import type { CurrentAccount } from '../src/platform/localdb/identity.js'
import { decodeCursor } from '../src/platform/localdb/messages.js'
import { readLocalState, writeLocalState } from '../src/platform/localdb/matrix.js'
import { runLocaldbPy } from '../src/platform/localdb/driver.js'
import { createLocaldbWorkDir } from '../src/platform/localdb/cleanup.js'

const root = fileURLToPath(new URL('../../', import.meta.url))
const python = join(root, '..', '..', 'venv', 'Scripts', 'python.exe')
const helper = join(root, 'tests', 'py', 'read_session_fixture.py')
const SELF = '9007199254740997'
const KEY = '000102030405060708090a0b0c0d0e0f' // 合成密钥，不接触真实 keystore。
const VERSION = '5.0.11.6029'
const ctx = (): OpContext => ({ signal: new AbortController().signal, progress() {} })
const windows = { skip: process.platform !== 'win32' }

async function fixture(count: number) {
  const base = mkdtempSync(join(tmpdir(), 'wecom-p1b-'))
  const command = (mode: string, n = 0) => execFileSync(python, [helper, base, mode, String(n)], { windowsHide: true })
  command('setup', count)
  const data = await locateDataDir({ cefLogDir: null, candidateRoots: [],
    activeAccount: { pid: 4242, message_db: join(base, SELF, 'Data', 'message.db') } })
  const current: CurrentAccount = { account: SELF, windowPid: 4242, dataDir: data }
  const stateDir = join(base, 'state')
  writeLocalState({ mode: 'db', version: VERSION, checked_at: 'fixture-epoch' }, stateDir)
  const deps: LocaldbReadDeps = {
    probeAccountFn: async () => current,
    detectInstallFn: async opts => ({ installed: true, pid: opts?.processPid ?? 4242,
      exe_path: 'C:\\synthetic\\WXWork.exe', install_dir: 'C:\\synthetic', version: VERSION,
      version_source: 'process', process_started_at: null, upgrade_signals: [] }),
    loadMatrixFn: () => ({ entries: { [VERSION]: { status: 'validated', algorithm: 'wxaes128cbc-nonlegacy',
      page_size: 4096, db_magic: readFileSync(data.db_paths!.message!).subarray(0, 8).toString('hex') } } }),
    loadKeyFn: async () => ({ keyHex: KEY, savedAt: 'fixture' }),
    stateDir, keyDir: stateDir, workBaseDir: join(base, 'work'), logFn: () => true,
    now: () => 1700100000000, snapshotRetryMs: 1,
  }
  return { base, current, deps, stateDir, command,
    close() { rmSync(base, { recursive: true, force: true }) } }
}

test('真实合成整链：最近100、字符串64位身份、确认元数据补齐和显式增量水位', windows, async () => {
  const f = await fixture(105)
  try {
    const first = await readLocaldbSession({ name: 'Peer' }, ctx(), f.current, f.deps)
    const messages = first.data!.messages as Array<{ msg_id: number; side: string }>
    assert.equal(messages.length, 100)
    assert.equal(messages[0]!.msg_id, 6)
    assert.ok(messages.some(m => m.side === 'self'))
    assert.ok(messages.some(m => m.side === 'peer'))
    f.command('ack') // server_id/sequence补齐不是数据库重建，旧游标必须继续有效。
    f.command('append', 5)
    const next = await readLocaldbSession({ name: 'Peer', cursor: first.data!.cursor as string, limit: 2 }, ctx(), f.current, f.deps)
    assert.deepEqual((next.data!.messages as Array<{ msg_id: number }>).map(m => m.msg_id), [106, 107])
    assert.equal(decodeCursor(next.data!.cursor as string).ok, true)
    assert.deepEqual(readdirSync(f.deps.workBaseDir!), [])
  } finally { f.close() }
})

test('空会话0游标后同步超过limit：必须从最小新ID分批返回', windows, async () => {
  const f = await fixture(0)
  try {
    const empty = await readLocaldbSession({ name: 'Empty Group' }, ctx(), f.current, f.deps)
    const zero = decodeCursor(empty.data!.cursor as string)
    assert.equal(zero.ok && zero.lastMessageId, 0)
    const contact = { name: 'Peer', type: 'contact', subtitle: '', account: SELF }
    const emptyContact = await readLocaldbSession({ name: 'Peer', target: contact }, ctx(), f.current, f.deps)
    assert.equal(decodeCursor(emptyContact.data!.cursor as string).ok, true)
    f.command('append-empty', 5)
    const next = await readLocaldbSession({ name: 'Empty Group', limit: 2, cursor: empty.data!.cursor as string }, ctx(), f.current, f.deps)
    assert.deepEqual((next.data!.messages as Array<{ msg_id: number }>).map(m => m.msg_id), [1, 2])
    const last = decodeCursor(next.data!.cursor as string)
    assert.equal(last.ok && last.lastMessageId, 2)
    f.command('append', 5)
    const contactNext = await readLocaldbSession({ name: 'Peer', target: contact, limit: 2, cursor: emptyContact.data!.cursor as string }, ctx(), f.current, f.deps)
    assert.deepEqual((contactNext.data!.messages as Array<{ msg_id: number }>).map(m => m.msg_id), [6, 7])
  } finally { f.close() }
})

test('真实文件替换或同文件水位身份复用拒绝旧cursor，普通metadata更新不拒绝', windows, async () => {
  for (const change of ['replace', 'reuse']) {
    const f = await fixture(3)
    try {
      const first = await readLocaldbSession({ name: 'Peer' }, ctx(), f.current, f.deps)
      f.command(change)
      await assert.rejects(readLocaldbSession({ name: 'Peer', cursor: first.data!.cursor as string }, ctx(), f.current, f.deps),
        (err: unknown) => err instanceof CodedOperationError && err.code === 'CURSOR_UNSUPPORTED')
      assert.deepEqual(readdirSync(f.deps.workBaseDir!), [])
    } finally { f.close() }
  }
})

test('本人映射缺失、同名无历史联系人、必要来源缺失分别报告，不伪装查无目标', windows, async () => {
  for (const [change, code] of [['missing-self', 'IDENTITY_UNCONFIRMED'], ['duplicate-name', 'TARGET_AMBIGUOUS'], ['missing-session', 'LOCALDB_UNAVAILABLE']]) {
    const f = await fixture(1)
    try {
      if (change === 'missing-session') { unlinkSync(f.current.dataDir.db_paths!.session!); f.current.dataDir.db_paths!.session = null }
      else f.command(change!)
      await assert.rejects(readLocaldbSession({ name: 'Peer' }, ctx(), f.current, f.deps),
        (err: unknown) => err instanceof CodedOperationError && err.code === code)
    } finally { f.close() }
  }
})

test('session的旧S标题不能替代user精确命名；改名后仅新联系人名可定位', windows, async () => {
  const f = await fixture(1)
  try {
    f.command('rename-peer') // session仍缓存Peer，联系人名称/别名/备注已全部不匹配。
    for (const target of [undefined, { name: 'Peer', type: 'contact', subtitle: '', account: SELF }]) {
      await assert.rejects(readLocaldbSession({ name: 'Peer', target }, ctx(), f.current, f.deps),
        (err: unknown) => err instanceof CodedOperationError && err.code === 'TARGET_NOT_FOUND')
    }
    const renamed = await readLocaldbSession({ name: 'Renamed' }, ctx(), f.current, f.deps)
    assert.equal((renamed.data!.messages as unknown[]).length, 1)
  } finally { f.close() }
})

test('实际scan失败在state持久化，第二调用不重扫；doctor时刻变化后允许重试', windows, async () => {
  const f = await fixture(1)
  try {
    let scanned = 0
    f.deps.loadKeyFn = async () => null
    f.deps.detectRunningFn = async () => ({ running: true, pids: [4242], probe: 'ok' })
    f.deps.runPyFn = async (args, opts) => {
      if (args[0] === 'keyscan') { scanned++; return { ok: true, data: { ok: false, error: 'KEY_NOT_FOUND' }, raw_last_line: null } }
      return runLocaldbPy(args, opts)
    }
    for (let i = 0; i < 2; i++) {
      await assert.rejects(readLocaldbSession({ name: 'Peer' }, ctx(), f.current, { ...f.deps }),
        (err: unknown) => err instanceof CodedOperationError && err.code === 'KEY_SCAN_FAILED')
    }
    assert.equal(scanned, 1)
    assert.ok(readLocalState(f.stateDir)!.scan_failure)
    writeLocalState({ ...readLocalState(f.stateDir)!, checked_at: 'new-doctor-epoch' }, f.stateDir)
    await assert.rejects(readLocaldbSession({ name: 'Peer' }, ctx(), f.current, f.deps))
    assert.equal(scanned, 2)
    unlinkSync(join(f.stateDir, 'localdb-state.json'))
    scanned = 0
    for (let i = 0; i < 2; i++) {
      await assert.rejects(readLocaldbSession({ name: 'Peer', forced: true }, ctx(), f.current, f.deps))
    }
    assert.equal(scanned, 1)
    assert.equal(readLocalState(f.stateDir)!.mode, 'unset', '辅助负缓存不能替代doctor启用数据库')
    assert.ok(readLocalState(f.stateDir)!.scan_failure)
    const events: string[] = []
    f.deps.now = () => 1700101000000
    f.deps.writeStateFn = () => false
    f.deps.logFn = event => { events.push(`${event.stage}:${event.reason}`); return true }
    await assert.rejects(readLocaldbSession({ name: 'Peer', forced: true }, ctx(), f.current, f.deps),
      (err: unknown) => err instanceof CodedOperationError && err.data?.scan_cache_saved === false)
    assert.ok(events.includes('scan-cache:state_write_failed'))
  } finally { f.close() }
})

test('contact已有实际S时不依赖可选session；空S无可用session明确报告能力不足', windows, async () => {
  for (const count of [0, 1]) {
    for (const issue of ['missing', 'schema', 'cipher']) {
      const f = await fixture(count)
      try {
        if (issue === 'missing') {
          unlinkSync(f.current.dataDir.db_paths!.session!)
          f.current.dataDir.db_paths!.session = null
        } else if (issue === 'schema') f.command('bad-session-schema')
        else writeFileSync(f.current.dataDir.db_paths!.session!, Buffer.alloc(4096))
        const attempt = readLocaldbSession({ name: 'Peer', target: { name: 'Peer', type: 'contact', subtitle: '', account: SELF } }, ctx(), f.current, f.deps)
        if (count === 1) assert.equal((await attempt).data!.channel, 'local-db')
        else await assert.rejects(attempt, (err: unknown) => err instanceof CodedOperationError && ['LOCALDB_UNAVAILABLE', 'VERSION_UNSUPPORTED'].includes(err.code))
      } finally { f.close() }
    }
  }
})

test('ref账号错误优先于版本不可用；legacy仅UI兼容，cursor/强制DB不降级', windows, async () => {
  const f = await fixture(1)
  try {
    let uiCalls = 0, dbCalls = 0
    const op = createWecomReadSessionOperation({
      localdb: f.deps, artifactDirFn: () => f.base, env: {},
      verifyRefFn: ref => ({ name: 'Peer', type: 'contact', subtitle: '', ...(ref === 'legacy' ? {} : { account: 'another-account' }) }),
      runDriverFn: async () => { uiCalls++; return { messages: [{ side: 'peer', text: 'synthetic-ui' }] } },
      readDbFn: async () => { dbCalls++; throw new CodedOperationError('VERSION_UNSUPPORTED', 'synthetic-version') },
    })
    assert.equal((await op.execute({ target_ref: 'bound' }, ctx())).code, 'TARGET_REF_STALE')
    assert.equal((await op.execute({ target_ref: 'bound', channel: 'ui' }, ctx())).code, 'TARGET_REF_STALE')
    assert.equal((await op.execute({ target_ref: 'legacy' }, ctx())).code, 'TARGET_REF_STALE')
    const legacy = await op.execute({ target_ref: 'legacy', channel: 'ui' }, ctx())
    assert.equal(legacy.success, true)
    assert.equal(legacy.data.target_ref_legacy, true)
    const first = await readLocaldbSession({ name: 'Peer' }, ctx(), f.current, f.deps)
    const incremental = await op.execute({ target_name: 'Peer', cursor: first.data!.cursor as string }, ctx())
    assert.equal(incremental.code, 'CURSOR_UNSUPPORTED')
    const forced = await op.execute({ target_name: 'Peer', channel: 'local-db' }, ctx())
    assert.equal(forced.code, 'VERSION_UNSUPPORTED')
    assert.equal(uiCalls, 1)
    assert.equal(dbCalls, 2)
  } finally { f.close() }
})

test('unset/compat保持UI，transient成功提升；强制本地库不改compat落盘模式', windows, async () => {
  const f = await fixture(1)
  try {
    const op = createWecomReadSessionOperation({ localdb: f.deps, artifactDirFn: () => f.base, env: {},
      verifyRefFn: () => ({ name: 'Peer', type: 'contact', subtitle: '', account: SELF }),
      runDriverFn: async () => ({ messages: [{ side: 'peer', text: 'synthetic-ui' }] }),
    })
    for (const mode of ['unset', 'compat', 'transient']) {
      writeLocalState(mode === 'unset' ? { mode: 'unset' } : { mode: 'ui', kind: mode as 'compat' | 'transient', version: VERSION }, f.stateDir)
      const result = await op.execute({ target_ref: 'bound' }, ctx())
      assert.equal(result.success, true)
      assert.equal(result.data.channel, mode === 'transient' ? 'local-db' : 'ocr')
      if (mode === 'transient') assert.equal(readLocalState(f.stateDir)!.mode, 'db')
    }
    writeLocalState({ mode: 'ui', kind: 'compat', version: VERSION }, f.stateDir)
    assert.equal((await op.execute({ target_ref: 'bound', channel: 'local-db' }, ctx())).data.channel, 'local-db')
    assert.equal(readLocalState(f.stateDir)!.mode, 'ui')
  } finally { f.close() }
})

test('bound ref UI长读取后账号变化不返回结果，legacy UI不新增身份依赖', windows, async () => {
  const f = await fixture(1)
  try {
    let probes = 0
    f.deps.probeAccountFn = async () => ++probes === 1 ? f.current : { ...f.current, account: 'changed' }
    const op = createWecomReadSessionOperation({ localdb: f.deps, artifactDirFn: () => f.base, env: {},
      verifyRefFn: () => ({ name: 'Peer', type: 'contact', subtitle: '', account: SELF }),
      runDriverFn: async () => ({ messages: [{ side: 'peer', text: 'synthetic-ui' }] }),
    })
    assert.equal((await op.execute({ target_ref: 'bound', channel: 'ui' }, ctx())).code, 'TARGET_REF_STALE')
    assert.equal(probes, 2)
  } finally { f.close() }
})

test('DB失败且清理失败后UI也失败，保留UI错误与cleanup_failed', windows, async () => {
  const f = await fixture(1)
  try {
    f.deps.captureFn = async () => ({ ok: false, reason: 'copy-failed', detail: 'synthetic' })
    f.deps.workDirFn = () => {
      const work = createLocaldbWorkDir('read-session', f.deps.workBaseDir)!
      return { ...work, cleanup: () => false }
    }
    const op = createWecomReadSessionOperation({ localdb: f.deps, artifactDirFn: () => f.base, env: {},
      verifyRefFn: () => ({ name: 'Peer', type: 'contact', subtitle: '', account: SELF }),
      runDriverFn: async () => { throw new CodedOperationError('UI_CHANGED', 'synthetic-ui-failed') },
    })
    const result = await op.execute({ target_ref: 'bound' }, ctx())
    assert.equal(result.code, 'UI_CHANGED')
    assert.equal(result.message, 'synthetic-ui-failed')
    assert.equal(result.effect, 'none')
    assert.equal((result.data.cleanup_failed as unknown[]).length, 1)
  } finally { f.close() }
})

test('带cursor取消等待真实Python close后才删工作目录，不改成游标失败或启动UI', windows, async () => {
  const f = await fixture(1)
  const ac = new AbortController()
  let poll: ReturnType<typeof setInterval> | undefined
  let deadline: ReturnType<typeof setTimeout> | undefined
  try {
    const first = await readLocaldbSession({ name: 'Peer' }, ctx(), f.current, f.deps)
    const marker = join(f.base, 'child-started')
    const slow = join(f.base, 'slow.py')
    writeFileSync(slow, "import pathlib,sys,time\npathlib.Path(sys.argv[1]).write_text('started')\ntime.sleep(60)\n")
    const events: string[] = []
    f.deps.workDirFn = () => {
      const work = createLocaldbWorkDir('read-session', f.deps.workBaseDir)!
      return { ...work, cleanup: () => { events.push('cleanup'); return work.cleanup() } }
    }
    f.deps.runPyFn = async (_args, opts) => {
      const result = await runLocaldbPy([marker], opts, { driverPath: slow })
      events.push('close')
      return result
    }
    poll = setInterval(() => { if (existsSync(marker)) ac.abort() }, 10)
    deadline = setTimeout(() => ac.abort(), 5000)
    const op = createWecomReadSessionOperation({ localdb: f.deps, env: {}, runDriverFn: async () => { throw new Error('UI must not run') } })
    const result = await op.execute({ target_name: 'Peer', cursor: first.data!.cursor as string }, { signal: ac.signal, progress() {} })
    assert.equal(result.code, 'CANCELLED')
    assert.ok(existsSync(marker), '必须真实启动过子进程，不能仅覆盖spawn前取消')
    assert.deepEqual(events, ['close', 'cleanup'])
    assert.deepEqual(readdirSync(f.deps.workBaseDir!), [])
  } finally {
    if (poll) clearInterval(poll)
    if (deadline) clearTimeout(deadline)
    f.close()
  }
})
