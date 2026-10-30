import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { locateDataDir } from '../src/platform/localdb/datadir.js'
import { readLocaldbSession, type LocaldbReadDeps } from '../src/platform/localdb/readSession.js'
import { readLocaldbUnread } from '../src/platform/localdb/unread.js'
import { writeLocalState } from '../src/platform/localdb/matrix.js'
import { createTargetRef, verifyTargetRef } from '../src/platform/targetRef.js'
import { createWecomMessageSendOperation } from '../src/operations/messageSend.js'
import { createWecomReadSessionOperation } from '../src/operations/readSession.js'
import { CodedOperationError, type OpContext } from '../src/operations/types.js'
import type { CurrentAccount } from '../src/platform/localdb/identity.js'

const root = fileURLToPath(new URL('../../', import.meta.url))
const python = join(root, '..', '..', 'venv', 'Scripts', 'python.exe')
const helper = join(root, 'tests', 'py', 'read_session_fixture.py')
const SELF = '9007199254740997'
const VERSION = '5.0.11.6029'
const ctx = (): OpContext => ({ signal: new AbortController().signal, progress() {} })
const windows = { skip: process.platform !== 'win32' }

async function fixture() {
  const base = mkdtempSync(join(tmpdir(), 'wecom-external-'))
  const command = (mode: string, count = 0) => execFileSync(python, [helper, base, mode, String(count)], { windowsHide: true })
  command('external-setup')
  const dataDir = await locateDataDir({ cefLogDir: null, candidateRoots: [],
    activeAccount: { pid: 4242, message_db: join(base, SELF, 'Data', 'message.db') } })
  const current: CurrentAccount = { account: SELF, windowPid: 4242, dataDir }
  const stateDir = join(base, 'state')
  writeLocalState({ mode: 'db', version: VERSION, checked_at: 'fixture' }, stateDir)
  const deps: LocaldbReadDeps = {
    probeAccountFn: async () => current,
    detectInstallFn: async opts => ({ installed: true, pid: opts?.processPid ?? 4242,
      exe_path: 'C:\\synthetic\\WXWork.exe', install_dir: 'C:\\synthetic', version: VERSION,
      version_source: 'process', process_started_at: null, upgrade_signals: [] }),
    loadMatrixFn: () => ({ entries: { [VERSION]: { status: 'validated', algorithm: 'wxaes128cbc-nonlegacy',
      page_size: 4096, db_magic: readFileSync(dataDir.db_paths!.message!).subarray(0, 8).toString('hex') } } }),
    loadKeyFn: async () => ({ keyHex: '000102030405060708090a0b0c0d0e0f', savedAt: 'fixture' }),
    stateDir, keyDir: stateDir, workBaseDir: join(base, 'work'), logFn: () => true,
    now: () => 1700000014000, snapshotRetryMs: 1,
  }
  return { base, command, current, deps, close: () => rmSync(base, { recursive: true, force: true }) }
}

test('显式微信外部约束排除内部同名人，默认查询仍歧义，游标可继续读取', windows, async () => {
  const f = await fixture()
  try {
    await assert.rejects(readLocaldbSession({ name: 'Peer' }, ctx(), f.current, f.deps),
      (e: unknown) => e instanceof CodedOperationError && e.code === 'TARGET_AMBIGUOUS')
    const first = await readLocaldbSession({ name: 'Peer@微信' }, ctx(), f.current, f.deps)
    assert.equal(first.data!.channel, 'local-db')
    assert.equal(first.data!.navigated, false)
    const next = await readLocaldbSession({ name: 'Peer@微信', cursor: first.data!.cursor as string }, ctx(), f.current, f.deps)
    assert.deepEqual(next.data!.messages, [])
    const unread = await readLocaldbUnread({ focus: ['Peer@微信', 'Peer'] }, ctx(), f.current, f.deps)
    assert.deepEqual(unread.data!.failed, [{ focus: 'Peer', reason: 'TARGET_AMBIGUOUS' }])
  } finally { f.close() }
})

test('多个外部同名或外部关系未知时，不能借约束选出一个目标', windows, async () => {
  for (const [mode, code] of [['external-duplicate', 'TARGET_AMBIGUOUS'], ['external-unknown', 'VERSION_UNSUPPORTED']]) {
    const f = await fixture()
    try {
      f.command(mode!)
      await assert.rejects(readLocaldbSession({ name: 'Peer@微信' }, ctx(), f.current, f.deps),
        (e: unknown) => e instanceof CodedOperationError && e.code === code)
    } finally { f.close() }
  }
})

test('外部目标按DB唯一定位直接发送，不搜索；真实新出站记录对账确认一次', windows, async () => {
  const f = await fixture()
  try {
    let sends = 0
    const keyDir = join(f.base, 'refs')
    const op = createWecomMessageSendOperation({
      artifactDirFn: () => join(f.base, 'artifacts'), localdb: f.deps,
      createRefFn: (name, type, subtitle, coords, account) => createTargetRef(name, type, subtitle, { keyDir, coords, account }),
      verifyRefFn: ref => verifyTargetRef(ref, { keyDir }),
      localdbVerifyPoll: { attempts: 1, delayMs: 1 },
      runDriverFn: async opts => {
        assert.ok(opts.script.endsWith('message-send.ps1'), '可信DB定位不能再搜索或选择联系人')
        assert.ok(opts.args?.includes('-SkipVerify'))
        sends++
        f.command('append', 2)
        return { navigate_required: false, title: 'Peer @微信', sent_verification: { method: 'skipped', result: 'unverified' } }
      },
    })
    const result = await op.execute({ target_name: 'Peer@微信', text: 'synthetic-14' }, ctx())
    assert.equal(result.success, true, result.message)
    assert.equal(result.effect, 'applied')
    assert.equal(result.data.verification, 'local-record')
    assert.equal(sends, 1)
  } finally { f.close() }
})

test('外部歧义发送在任何UI调用之前拒绝，不退回搜索或宽松发送', windows, async () => {
  const f = await fixture()
  try {
    f.command('external-duplicate')
    let uiCalls = 0
    const op = createWecomMessageSendOperation({ artifactDirFn: () => join(f.base, 'artifacts'), localdb: f.deps,
      runDriverFn: async () => { uiCalls++; throw new Error('unexpected UI') },
    })
    const result = await op.execute({ target_name: 'Peer@微信', text: 'must not send' }, ctx())
    assert.equal(result.code, 'TARGET_AMBIGUOUS')
    assert.equal(result.effect, 'none')
    assert.equal(uiCalls, 0)
  } finally { f.close() }
})

test('operation默认通道外部身份不完整时拒绝，不把DB拒绝降级成同名UI读取', windows, async () => {
  const f = await fixture()
  try {
    f.command('external-unknown')
    let uiCalls = 0
    const keyDir = join(f.base, 'refs')
    const op = createWecomReadSessionOperation({ artifactDirFn: () => join(f.base, 'artifacts'), localdb: f.deps,
      verifyRefFn: ref => verifyTargetRef(ref, { keyDir }),
      runDriverFn: async () => { uiCalls++; throw new Error('unexpected UI') },
    })
    const ref = createTargetRef('Peer@微信', 'contact', '微信联系人', { keyDir, account: SELF })
    for (const selector of [{ target_name: 'Peer@微信' }, { target_ref: ref }]) {
      const result = await op.execute(selector, ctx())
      assert.equal(result.code, 'VERSION_UNSUPPORTED')
      assert.equal(result.effect, 'none')
      assert.equal(uiCalls, 0, '外部身份拒绝不能变成宽松UI读取')
    }
  } finally { f.close() }
})
