/** W26 media target_name: directory uniqueness before UI, then one DB-backed dispatch. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createWecomSendImageOperation } from '../src/operations/sendImage.js'
import { createWecomSendFileOperation } from '../src/operations/sendFile.js'
import { createWecomMessageSendOperation, type WecomMessageSendArgs } from '../src/operations/messageSend.js'
import type { OpContext } from '../src/operations/types.js'
import type { PowerShellScriptOptions, RunPowerShellDriverFn } from '../src/platform/powershell.js'
import type { LocaldbReadDeps } from '../src/platform/localdb/readSession.js'
import { locateDataDir } from '../src/platform/localdb/datadir.js'
import type { CurrentAccount } from '../src/platform/localdb/identity.js'
import { writeLocalState } from '../src/platform/localdb/matrix.js'
import { runLocaldbPy } from '../src/platform/localdb/driver.js'

const root = fileURLToPath(new URL('../../', import.meta.url))
const python = join(root, '..', '..', 'venv', 'Scripts', 'python.exe')
const helper = join(root, 'tests', 'py', 'read_session_fixture.py')
const SELF = '9007199254740997'
const VERSION = '5.0.11.6029'
const ctx = (): OpContext => ({ signal: new AbortController().signal, progress: () => {} })
const crypto = { protect: async (s: string) => Buffer.from(s).toString('base64'),
  unprotect: async (s: string) => Buffer.from(s, 'base64').toString('utf8') }

async function fixture() {
  const base = mkdtempSync(join(tmpdir(), 'wecom-target-name-w26-'))
  execFileSync(python, [helper, base, 'unread-setup'], { windowsHide: true })
  const data = await locateDataDir({ cefLogDir: null, candidateRoots: [],
    activeAccount: { pid: 4242, message_db: join(base, SELF, 'Data', 'message.db') } })
  const current: CurrentAccount = { account: SELF, windowPid: 4242, dataDir: data }
  const stateDir = join(base, 'state')
  writeLocalState({ mode: 'db', version: VERSION, checked_at: 'fixture-epoch' }, stateDir)
  const localdb: LocaldbReadDeps = {
    probeAccountFn: async () => current,
    detectInstallFn: async opts => ({ installed: true, pid: opts?.processPid ?? 4242,
      exe_path: 'C:\\synthetic\\WXWork.exe', install_dir: 'C:\\synthetic', version: VERSION,
      version_source: 'process', process_started_at: null, upgrade_signals: [] }),
    loadMatrixFn: () => ({ entries: { [VERSION]: { status: 'validated', algorithm: 'wxaes128cbc-nonlegacy',
      page_size: 4096, db_magic: readFileSync(data.db_paths!.message!).subarray(0, 8).toString('hex') } } }),
    loadKeyFn: async () => ({ keyHex: '000102030405060708090a0b0c0d0e0f', savedAt: 'fixture' }),
    stateDir, keyDir: stateDir, workBaseDir: join(base, 'work'), logFn: () => true,
    now: () => 1700000014000, snapshotRetryMs: 1,
    runPyFn: async (params, opts) => {
      if (params[params.indexOf('--task') + 1] !== 'send-messages') return runLocaldbPy(params, opts)
      const conv = params[params.indexOf('--conv') + 1]
      return { ok: true, raw_last_line: '', data: { ok: true, conversation_id: conv, messages: [{
        message_id: 13, conversation_id: conv, sender_id: SELF, send_time: 1700000014,
        content_type: 101, trusted: false, attachment: { kind: 'image', status: 'not_cached' },
      }] } }
    },
  }
  const image = join(base, 'tiny.png')
  const file = join(base, 'a')
  writeFileSync(image, Buffer.from('synthetic image'))
  writeFileSync(file, Buffer.from('synthetic file'))
  return { base, image, file, localdb, close: () => rmSync(base, { recursive: true, force: true }) }
}

type Kind = 'image' | 'file'
type DirectoryMode = 'unique' | 'none' | 'ambiguous' | 'prefix'
async function run(kind: Kind, mode: DirectoryMode, args: Record<string, unknown>) {
  const f = await fixture()
  const calls: PowerShellScriptOptions[] = []
  const catalog: string[] = []
  const localdb: LocaldbReadDeps = { ...f.localdb, runPyFn: async (params, opts) => {
    if (params[params.indexOf('--task') + 1] !== 'send-messages') return f.localdb.runPyFn!(params, opts)
    const conv = params[params.indexOf('--conv') + 1]
    return { ok: true, raw_last_line: '', data: { ok: true, conversation_id: conv, messages: [{
      message_id: 13, conversation_id: conv, sender_id: SELF, send_time: 1700000014,
      content_type: kind === 'image' ? 101 : 102, trusted: false,
      attachment: kind === 'image' ? { kind: 'image' } : { kind: 'file', name: 'a' },
    }] } }
  } }
  const deps = {
    localdb, artifactDirFn: () => join(f.base, 'artifacts'), receiptCrypto: crypto,
    localdbVerifyPoll: { attempts: 1, delayMs: 0 },
    runDriverFn: (async (opts: PowerShellScriptOptions) => {
      calls.push(opts)
      return { sent: true, title: 'Peer', input_point: { x: 760, y: 1245, source: 'cache' } }
    }) as RunPowerShellDriverFn,
    createRefFn: (name: string, _type: string, _subtitle: string | undefined, _coords: unknown, account?: string) => `ref:${name}:${account ?? ''}`,
    verifyRefFn: (ref: string) => ({ name: ref.split(':')[1]!, type: 'contact' as const, subtitle: '', account: ref.split(':')[2]! }),
    readCatalogFn: async (query: { query?: string }) => {
      catalog.push(query.query ?? '')
      const candidates = mode === 'none' ? [] : mode === 'ambiguous'
        ? [{ name: 'Peer', section: '联系人' }, { name: 'PeerTwo', section: '联系人' }]
        : [{ name: mode === 'prefix' ? 'PeerTwo' : 'Peer', section: '联系人' }]
      return { message: '', data: { self_user_id: SELF, match_count: candidates.length, candidates } }
    },
  }
  const op = kind === 'image' ? createWecomSendImageOperation(deps) : createWecomSendFileOperation(deps)
  const result = await op.execute({ ...args, [kind === 'image' ? 'image_path' : 'file_path']:
    kind === 'image' ? f.image : f.file }, ctx())
  return { f, calls, catalog, result }
}

for (const kind of ['image', 'file'] as const) {
  test(`${kind} target_name 唯一完整名称：只读目录后单次媒体驱动并本地库确认`, async () => {
    const x = await run(kind, 'unique', { target_name: 'Peer' })
    try {
      assert.equal(x.result.success, true, JSON.stringify(x.result))
      assert.equal(x.result.data.verification, 'local-record')
      assert.deepEqual(x.catalog, ['Peer'])
      assert.equal(x.calls.length, 1)
      assert.ok(x.calls[0]!.script.endsWith(kind === 'image' ? 'send-image.ps1' : 'send-file.ps1'))
      assert.deepEqual(x.result.data.resolved_target, { name: 'Peer', subtitle: '', section: '联系人' })
    } finally { x.f.close() }
  })
  for (const [mode, code] of [['none', 'TARGET_NOT_FOUND'], ['ambiguous', 'TARGET_AMBIGUOUS'],
    ['prefix', 'TARGET_NOT_FOUND']] as const) {
    test(`${kind} target_name ${mode}：不触达 UI`, async () => {
      const x = await run(kind, mode, { target_name: 'Peer' })
      try { assert.equal(x.result.code, code); assert.equal(x.result.effect, 'none'); assert.equal(x.calls.length, 0) }
      finally { x.f.close() }
    })
  }
  test(`${kind} target_ref 与 target_name 互斥且至少一项，零 UI`, async () => {
    const both = await run(kind, 'unique', { target_ref: `ref:Peer:${SELF}`, target_name: 'Peer' })
    const neither = await run(kind, 'unique', {})
    try {
      assert.equal(both.result.code, 'INVALID_ARGUMENT')
      assert.equal(neither.result.code, 'INVALID_ARGUMENT')
      assert.equal(both.calls.length + neither.calls.length, 0)
    } finally { both.f.close(); neither.f.close() }
  })
  test(`${kind} 无坐标账号绑定 ref 仍走一次本地库发送`, async () => {
    const x = await run(kind, 'unique', { target_ref: `ref:Peer:${SELF}` })
    try { assert.equal(x.result.success, true, JSON.stringify(x.result)); assert.equal(x.catalog.length, 0); assert.equal(x.calls.length, 1) }
    finally { x.f.close() }
  })
}

test('文本 target_name 空白/超长、subtitle 类型错及空 ref 在发送驱动前拒绝', async () => {
  let calls = 0
  const op = createWecomMessageSendOperation({ runDriverFn: async () => { calls++; throw new Error('UI must not run') } })
  const cases: WecomMessageSendArgs[] = [
    { target_name: '   ', text: 'hi' },
    { target_name: 'x'.repeat(101), text: 'hi' },
    { target_name: 'Peer', subtitle: 123, text: 'hi' } as unknown as WecomMessageSendArgs,
    { target_ref: '', text: 'hi' },
  ]
  for (const args of cases) {
    const r = await op.execute(args, ctx())
    assert.equal(r.code, 'INVALID_ARGUMENT')
    assert.equal(r.effect, 'none')
  }
  assert.equal(calls, 0)
})
