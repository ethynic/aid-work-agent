/** Media send intent and reconciliation with an encrypted local DB fixture; no real UI or attachment send. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createWecomSendImageOperation } from '../src/operations/sendImage.js'
import { createWecomSendFileOperation } from '../src/operations/sendFile.js'
import { createWecomSendStatusOperation } from '../src/operations/sendStatus.js'
import { CodedOperationError, type OpContext } from '../src/operations/types.js'
import type { LocaldbReadDeps } from '../src/platform/localdb/readSession.js'
import { locateDataDir } from '../src/platform/localdb/datadir.js'
import type { CurrentAccount } from '../src/platform/localdb/identity.js'
import { writeLocalState } from '../src/platform/localdb/matrix.js'
import { runLocaldbPy } from '../src/platform/localdb/driver.js'
import type { RunPowerShellDriverFn, PowerShellScriptOptions } from '../src/platform/powershell.js'
import { readSessionDirectory } from '../src/operations/sessionDirectory.js'
import { createTargetRef, verifyTargetRef, type CreateTargetRefFn } from '../src/platform/targetRef.js'

const root = fileURLToPath(new URL('../../', import.meta.url))
const python = join(root, '..', '..', 'venv', 'Scripts', 'python.exe')
const helper = join(root, 'tests', 'py', 'read_session_fixture.py')
const SELF = '9007199254740997'
const VERSION = '5.0.11.6029'
const ctx = (): OpContext => ({ signal: new AbortController().signal, progress: () => {} })
const crypto = { protect: async (s: string) => Buffer.from(s).toString('base64'),
  unprotect: async (s: string) => Buffer.from(s, 'base64').toString('utf8') }

async function fixture(setupMode = 'unread-setup') {
  const base = mkdtempSync(join(tmpdir(), 'wecom-media-send-'))
  execFileSync(python, [helper, base, setupMode], { windowsHide: true })
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
    loadKeyFn: async () => ({ keyHex: '000102030405060708090a0b0c0d0e0f', savedAt: 'fixture' }),
    stateDir, keyDir: stateDir, workBaseDir: join(base, 'work'), logFn: () => true,
    now: () => 1700000014000, snapshotRetryMs: 1,
  }
  const image = join(base, 'tiny.png')
  const file = join(base, 'a')
  writeFileSync(image, Buffer.from('synthetic image'))
  writeFileSync(file, Buffer.from('synthetic file'))
  return { base, deps, current, image, file, artifacts: join(base, 'artifacts'), close: () => rmSync(base, { recursive: true, force: true }) }
}

function mediaRow(kind: 'image' | 'file', name = 'a', id = 13) {
  return { message_id: id, conversation_id: `S:${SELF}_PEER`, sender_id: SELF,
    content_type: kind === 'image' ? 101 : 102, send_time: 1700000014,
    trusted: false, attachment: kind === 'image' ? { kind: 'image', status: 'not_cached' } : { kind: 'file', name, status: 'not_cached' } }
}

function withRows(deps: LocaldbReadDeps, rows: Record<string, unknown>[] | 'error', onRead?: () => void): LocaldbReadDeps {
  return { ...deps, runPyFn: async (params, opts) => {
    if (params[params.indexOf('--task') + 1] !== 'send-messages') return runLocaldbPy(params, opts)
    onRead?.()
    if (rows === 'error') return { ok: true, raw_last_line: '', data: { ok: false, error: 'SCHEMA_UNSUPPORTED' } }
    const conv = params[params.indexOf('--conv') + 1]
    return { ok: true, raw_last_line: '', data: { ok: true, conversation_id: conv,
      messages: rows.map(row => ({ ...row, conversation_id: conv })) } }
  } }
}

async function execute(kind: 'image' | 'file', rows: Record<string, unknown>[] | 'error',
  driverError?: Error, switchAccount = false, abortDuringPoll = false) {
  const f = await fixture()
  let probes = 0
  const controller = new AbortController()
  let reads = 0
  const local = withRows({ ...f.deps, probeAccountFn: async () => {
    probes++
    return switchAccount && probes >= 2 ? { ...f.current, account: 'other' } : f.current
  } }, rows, abortDuringPoll ? () => {
    if (++reads === 1) setTimeout(() => controller.abort(), 50)
  } : undefined)
  const calls: PowerShellScriptOptions[] = []
  const driver = (async (opts: PowerShellScriptOptions) => {
    calls.push(opts)
    if (driverError) throw driverError
    return { sent: true, verified: false, input_point: { x: 760, y: 1245, source: 'cache' } }
  }) as RunPowerShellDriverFn
  const common = { localdb: local, runDriverFn: driver, artifactDirFn: () => f.artifacts,
    verifyRefFn: () => ({ name: 'Peer', type: 'contact' as const, subtitle: '', account: SELF }),
    receiptCrypto: crypto, localdbVerifyPoll: { attempts: abortDuringPoll ? 2 : 1, delayMs: abortDuringPoll ? 1000 : 0 } }
  const op = kind === 'image' ? createWecomSendImageOperation(common) : createWecomSendFileOperation(common)
  const result = await op.execute({ target_ref: 'synthetic-ref', [kind === 'image' ? 'image_path' : 'file_path']:
    kind === 'image' ? f.image : f.file }, { signal: controller.signal, progress: () => {} })
  return { f, calls, result, local }
}

test('图片唯一 101 出站记录才确认客户端记录，不声称原图片字节一致', async () => {
  const x = await execute('image', [mediaRow('image')])
  try {
    assert.equal(x.result.success, true, JSON.stringify(x.result))
    assert.equal(x.result.data.verification, 'local-record')
    assert.equal(Object.hasOwn(x.result.data, 'title'), false)
    assert.match(x.result.message, /不证明与原图片字节一致/)
    assert.equal(x.calls.length, 1)
    assert.match(x.calls[0]!.script, /send-image\.ps1$/)
    assert.ok(x.calls[0]!.args!.includes('-SelfName'))
    assert.ok(!x.calls[0]!.args!.includes('-ImageHash'))
    const status = createWecomSendStatusOperation({ artifactDirFn: () => x.f.artifacts, localdb: x.local, receiptCrypto: crypto })
    const checked = await status.execute({ receipt_id: x.result.data.receipt_id as string }, ctx())
    assert.equal(checked.data.send_status, 'confirmed')
    assert.match(checked.message, /不证明与原图片字节一致/)
    assert.equal(x.calls.length, 1)
  } finally { x.f.close() }
})

test('文件名全等的唯一 102 出站记录确认；短文件名不再因 OCR 限制拒发', async () => {
  const x = await execute('file', [mediaRow('file')])
  try {
    assert.equal(x.result.success, true, JSON.stringify(x.result))
    const status = createWecomSendStatusOperation({ artifactDirFn: () => x.f.artifacts, localdb: x.local, receiptCrypto: crypto })
    assert.equal((await status.execute({ receipt_id: x.result.data.receipt_id as string }, ctx())).data.send_status, 'confirmed')
    assert.equal(x.calls.length, 1)
  }
  finally { x.f.close() }
})

for (const kind of ['image', 'file'] as const) {
  test(`${kind} 按完整 @微信 群名直达：真实本地目录与发送预检同名`, async () => {
    const f = await fixture('suffix-group')
    try {
      const calls: PowerShellScriptOptions[] = []
      const local = withRows(f.deps, [mediaRow(kind)])
      const keyDir = join(f.base, 'refs')
      const createRefFn: CreateTargetRefFn = (name, type, subtitle, coords, account) =>
        createTargetRef(name, type, subtitle, { keyDir, coords, account })
      const deps = { localdb: local, artifactDirFn: () => f.artifacts, receiptCrypto: crypto,
        localdbVerifyPoll: { attempts: 1, delayMs: 0 },
        readCatalogFn: (args: Parameters<typeof readSessionDirectory>[0], opCtx: OpContext) =>
          readSessionDirectory(args, opCtx, local),
        createRefFn,
        verifyRefFn: (ref: string) => verifyTargetRef(ref, { keyDir }),
        runDriverFn: (async (opts: PowerShellScriptOptions) => {
          calls.push(opts)
          return { sent: true, input_point: { x: 760, y: 1245, source: 'cache' } }
        }) as RunPowerShellDriverFn }
      const op = kind === 'image' ? createWecomSendImageOperation(deps) : createWecomSendFileOperation(deps)
      const result = await op.execute({ target_name: '项目群@微信',
        [kind === 'image' ? 'image_path' : 'file_path']: kind === 'image' ? f.image : f.file }, ctx())
      assert.equal(result.success, true, JSON.stringify(result))
      assert.equal(result.data.verification, 'local-record')
      assert.equal(calls.length, 1)
      const args = calls[0]!.args!
      assert.equal(args[args.indexOf('-TargetName') + 1], '项目群@微信')
      assert.equal(args[args.indexOf('-Section') + 1], 'group')
    } finally { f.close() }
  })

  for (const [mode, groupName] of [
    ['suffix-collision', 'Peer@微信'],
    ['suffix-space-collision', 'Peer @微信'],
    ['suffix-no-history-collision', 'Peer@微信'],
  ] as const) test(`${kind} ${mode} 群与外部联系人显示同名：零 UI 拒发`, async () => {
    const f = await fixture(mode)
    try {
      const calls: PowerShellScriptOptions[] = []
      const keyDir = join(f.base, 'refs')
      const createRefFn: CreateTargetRefFn = (name, type, subtitle, coords, account) =>
        createTargetRef(name, type, subtitle, { keyDir, coords, account })
      const deps = { localdb: f.deps, artifactDirFn: () => f.artifacts, receiptCrypto: crypto,
        readCatalogFn: (args: Parameters<typeof readSessionDirectory>[0], opCtx: OpContext) =>
          readSessionDirectory(args, opCtx, f.deps),
        createRefFn, verifyRefFn: (ref: string) => verifyTargetRef(ref, { keyDir }),
        runDriverFn: (async (opts: PowerShellScriptOptions) => {
          calls.push(opts)
          throw new Error('ambiguous target must not reach UI')
        }) as RunPowerShellDriverFn }
      const op = kind === 'image' ? createWecomSendImageOperation(deps) : createWecomSendFileOperation(deps)
      const pathArg = { [kind === 'image' ? 'image_path' : 'file_path']: kind === 'image' ? f.image : f.file }
      const refs = [createRefFn(groupName, 'group', '', undefined, SELF)]
      if (mode !== 'suffix-no-history-collision') refs.push(createRefFn('Peer', 'contact', '微信联系人', undefined, SELF))
      for (const selector of [{ target_name: groupName }, ...refs.map(target_ref => ({ target_ref }))]) {
        const result = await op.execute({ ...selector, ...pathArg }, ctx())
        assert.equal(result.code, 'TARGET_AMBIGUOUS', JSON.stringify(result))
        assert.equal(result.effect, 'none')
      }
      assert.equal(calls.length, 0)
    } finally { f.close() }
  })
}

for (const [kind, rows] of [
  ['image', []], ['image', [mediaRow('image'), mediaRow('image', 'a', 14)]],
  ['file', [mediaRow('file', 'wrong')]], ['file', [mediaRow('file'), mediaRow('file', 'a', 14)]],
  ['image', 'error'],
] as const) {
  test(`${kind} 无唯一合格记录保持 unknown 且 send-status 只读`, async () => {
    const x = await execute(kind, rows as Record<string, unknown>[] | 'error')
    try {
      assert.equal(x.result.code, 'EXECUTION_UNKNOWN')
      assert.equal(x.result.effect, 'unknown')
      assert.equal(x.calls.length, 1)
      assert.equal(typeof x.result.data.receipt_id, 'string')
      const status = createWecomSendStatusOperation({ artifactDirFn: () => x.f.artifacts, localdb: x.local, receiptCrypto: crypto })
      const checked = await status.execute({ receipt_id: x.result.data.receipt_id as string }, ctx())
      assert.equal(checked.data.send_status, 'unknown')
      assert.equal(x.calls.length, 1, 'read-only recovery cannot dispatch a second send')
    } finally { x.f.close() }
  })
}

test('Enter 阶段超时留下 unknown receipt，不自动重发', async () => {
  const x = await execute('image', [], new CodedOperationError('RESULT_TIMEOUT', 'synthetic timeout'))
  try {
    assert.equal(x.result.code, 'EXECUTION_UNKNOWN')
    assert.equal(x.result.effect, 'unknown')
    assert.equal(typeof x.result.data.receipt_id, 'string')
    assert.equal(x.calls.length, 1)
  } finally { x.f.close() }
})

test('Enter 已投递后第二轮等待期间取消仍是 unknown 且保留 receipt，只读恢复不重发', async () => {
  const x = await execute('image', [], undefined, false, true)
  try {
    assert.equal(x.result.code, 'EXECUTION_UNKNOWN')
    assert.equal(x.result.effect, 'unknown')
    assert.equal(typeof x.result.data.receipt_id, 'string')
    assert.equal(x.calls.length, 1)
    const status = createWecomSendStatusOperation({ artifactDirFn: () => x.f.artifacts, localdb: x.local, receiptCrypto: crypto })
    assert.equal((await status.execute({ receipt_id: x.result.data.receipt_id as string }, ctx())).data.send_status, 'unknown')
    assert.equal(x.calls.length, 1)
  } finally { x.f.close() }
})

test('账号在发送驱动前切换，零 UI 派发且不保留可确认凭据', async () => {
  const x = await execute('file', [], undefined, true)
  try { assert.equal(x.result.code, 'TARGET_REF_STALE'); assert.equal(x.result.effect, 'none'); assert.equal(x.calls.length, 0) }
  finally { x.f.close() }
})

test('图片类型与大小约束在任何 UI 前拒绝', async () => {
  const f = await fixture()
  try {
    const op = createWecomSendImageOperation({ artifactDirFn: () => f.artifacts,
      runDriverFn: async () => { throw new Error('UI must not run') } })
    const r = await op.execute({ target_ref: 'x', image_path: join(f.base, 'wrong.txt') }, ctx())
    assert.equal(r.code, 'INVALID_ARGUMENT')
  } finally { f.close() }
})
