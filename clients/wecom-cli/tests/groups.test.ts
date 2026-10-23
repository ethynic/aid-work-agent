/** 独立加密三库+真实Python查询，覆盖最近N选择与时间线/身份安全意图。 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'
import { createWecomGroupListOperation, createWecomGroupReadOperation, parseGroupTime } from '../src/operations/groups.js'
import { queryLocaldbGroups } from '../src/platform/localdb/groups.js'
import { locateDataDir } from '../src/platform/localdb/datadir.js'
import { CodedOperationError, CancelledError, type OpContext } from '../src/operations/types.js'
import type { CurrentAccount } from '../src/platform/localdb/identity.js'
import type { LocaldbReadDeps } from '../src/platform/localdb/readSession.js'
import { runLocaldbPy } from '../src/platform/localdb/driver.js'
import { createLocaldbWorkDir } from '../src/platform/localdb/cleanup.js'

const root = fileURLToPath(new URL('../../', import.meta.url))
const python = join(root, '..', '..', 'venv', 'Scripts', 'python.exe')
const SELF = '9007199254740997'
const VERSION = '5.0.11.6029'
const ctx = (): OpContext => ({ signal: new AbortController().signal, progress() {} })
const windows = { skip: process.platform !== 'win32' }

async function fixture() {
  const base = mkdtempSync(join(tmpdir(), 'wecom-group-'))
  execFileSync(python, [join(root, 'tests', 'py', 'group_fixture.py'), base], { windowsHide: true })
  const dataDir = await locateDataDir({ cefLogDir: null, candidateRoots: [],
    activeAccount: { pid: 4242, message_db: join(base, SELF, 'Data', 'message.db') } })
  const current: CurrentAccount = { account: SELF, windowPid: 4242, dataDir }
  const deps: LocaldbReadDeps = {
    probeAccountFn: async () => current,
    readStateFn: () => ({ mode: 'db', version: VERSION, checked_at: 'fixture' }), writeStateFn: () => true,
    detectInstallFn: async () => ({ installed: true, pid: 4242, exe_path: 'C:\\synthetic\\WXWork.exe',
      install_dir: 'C:\\synthetic', version: VERSION, version_source: 'process', process_started_at: null, upgrade_signals: [] }),
    loadMatrixFn: () => ({ entries: { [VERSION]: { status: 'validated', algorithm: 'wxaes128cbc-nonlegacy',
      page_size: 4096, db_magic: readFileSync(dataDir.db_paths!.message!).subarray(0, 8).toString('hex') } } }),
    loadKeyFn: async () => ({ keyHex: '000102030405060708090a0b0c0d0e0f', savedAt: 'fixture' }),
    stateDir: join(base, 'state'), keyDir: join(base, 'state'), workBaseDir: join(base, 'work'), logFn: () => true,
  }
  return { base, current, deps, close() { rmSync(base, { recursive: true, force: true }) } }
}

test('时间解析固定北京时间，拒绝日历溢出/缺时分秒/反常格式', () => {
  assert.equal(parseGroupTime('2026-10-22 08:00:00'), parseGroupTime('2026-10-22T00:00:00Z'))
  assert.equal(parseGroupTime('2024-02-29T10:00:00+08:00'), 1709172000)
  for (const value of ['2026-02-29 00:00:00', '2026-10-22', '2026-10-22 24:00:00', '2026-10-22T00:00:00+24:00', '2026-10-22T00:00:00.123Z']) {
    assert.throws(() => parseGroupTime(value), value)
  }
})

test('非法输入在访问账号和数据库前失败，避免不必要的敏感数据读取', async () => {
  let probes = 0
  const deps = { probeAccountFn: async () => { probes++; throw new Error('must not probe') } }
  const list = createWecomGroupListOperation(deps)
  const read = createWecomGroupReadOperation(deps)
  for (const args of [{ limit: 0 }, { limit: 1.5 }, { query: ' ' }]) assert.equal((await list.execute(args, ctx())).code, 'INVALID_ARGUMENT')
  for (const args of [ { target_name: '' }, { target_name: '群', from_time: 'bad' },
    { target_name: '群', from_time: '2026-10-23 00:00:00', to_time: '2026-10-22 00:00:00' }]) {
    assert.equal((await read.execute(args, ctx())).code, 'INVALID_ARGUMENT')
  }
  assert.equal(probes, 0)
})

test('真实加密整链：群子串/limit/字面符号/空群，和联系人同名不干扰群定位', windows, async () => {
  const f = await fixture()
  try {
    const list = createWecomGroupListOperation(f.deps)
    const all = await list.execute({ limit: 2 }, ctx())
    assert.equal(all.success, true, all.message)
    assert.equal(all.data.count, 2)
    assert.equal(all.data.has_more, true)
    const literal = await list.execute({ query: '%_' }, ctx())
    assert.deepEqual((literal.data.groups as Array<{ name: string }>).map(g => g.name), ['100%_群'])
    const empty = await createWecomGroupReadOperation(f.deps).execute({ target_name: '空群' }, ctx())
    assert.equal(empty.success, true, empty.message)
    assert.deepEqual(empty.data.messages, [])
    assert.equal(empty.data.has_more, false)
    assert.deepEqual(readdirSync(f.deps.workBaseDir!), [])
  } finally { f.close() }
})

test('最近100依据发送时间而非入库ID，闭区间选取后从早到晚且明示截断', windows, async () => {
  const f = await fixture()
  try {
    const read = createWecomGroupReadOperation(f.deps)
    const all = await read.execute({ target_name: '项目群' }, ctx())
    assert.equal(all.success, true, all.message)
    const rows = all.data.messages as Array<{ send_time: number; sender_id: string; msg_id: number }>
    assert.deepEqual(rows.map(m => m.send_time), Array.from({ length: 100 }, (_, i) => 1700000005 + i))
    assert.equal(all.data.has_more, true)
    assert.ok(rows.every(m => m.msg_id !== 106)) // 最后入库的旧消息不可挤掉较新发送的消息。
    assert.ok(rows.some(m => m.sender_id === SELF))
    const from = new Date(1700000010 * 1000).toISOString().replace('.000', '')
    const to = new Date(1700000015 * 1000).toISOString().replace('.000', '')
    const range = await read.execute({ target_name: '项目群', from_time: from, to_time: to, limit: 3 }, ctx())
    assert.equal(range.success, true, range.message)
    assert.deepEqual((range.data.messages as Array<{ send_time: number }>).map(m => m.send_time), [1700000013, 1700000014, 1700000015])
    assert.equal(range.data.has_more, true)
    assert.deepEqual(readdirSync(f.deps.workBaseDir!), [])
  } finally { f.close() }
})

test('账号变化和源库代际变化不能返回查询结果；异常仍清理解密产物', windows, async () => {
  for (const change of ['account', 'source']) {
    const f = await fixture()
    try {
      let after = false
      f.deps.probeAccountFn = async () => { after = true; return change === 'account' ? { ...f.current, account: 'other' } : f.current }
      f.deps.fileIdentityFn = () => after ? 'replaced' : 'original'
      await assert.rejects(queryLocaldbGroups({ limit: 2 }, ctx(), f.current, f.deps),
        (err: unknown) => err instanceof CodedOperationError && err.code === (change === 'account' ? 'IDENTITY_UNCONFIRMED' : 'SNAPSHOT_UNSTABLE'))
      assert.deepEqual(readdirSync(f.deps.workBaseDir!), [])
    } finally { f.close() }
  }
})

test('查询取消后等待驱动退出并清理，清理失败显式附带信息', windows, async () => {
  const f = await fixture()
  try {
    const ac = new AbortController()
    f.deps.runPyFn = async (params, opts) => {
      const result = await runLocaldbPy(params, opts)
      if (params.includes('groups')) ac.abort()
      return result
    }
    await assert.rejects(queryLocaldbGroups({ limit: 2 }, { signal: ac.signal, progress() {} }, f.current, f.deps), CancelledError)
    assert.deepEqual(readdirSync(f.deps.workBaseDir!), [])
    f.deps.runPyFn = undefined
    f.deps.workDirFn = () => {
      const work = createLocaldbWorkDir('groups', f.deps.workBaseDir)!
      return { ...work, cleanup: () => { work.cleanup(); return false } }
    }
    const result = await queryLocaldbGroups({ limit: 2 }, ctx(), f.current, f.deps)
    assert.ok(result.data!.cleanup_failed)
  } finally { f.close() }
})

test('真实MCP stdio生产消费：发现新工具，参数校验失败不访问企微', async () => {
  const transport = new StdioClientTransport({ command: process.execPath,
    args: [join(root, 'dist', 'src', 'cli', 'index.js'), 'mcp', '--stdio'], stderr: 'pipe' })
  const client = new Client({ name: 'group-contract-test', version: '1' })
  try {
    await client.connect(transport)
    const tools = (await client.listTools()).tools
    assert.ok(tools.some(t => t.name === 'wecom_group_list'))
    assert.ok(tools.some(t => t.name === 'wecom_group_read'))
    const result = await client.callTool({ name: 'wecom_group_read', arguments: { target_name: '群', from_time: 'bad' } })
    const content = result.structuredContent as { success: boolean; code: string; effect: string }
    assert.equal(content.success, false)
    assert.equal(content.code, 'INVALID_ARGUMENT')
    assert.equal(content.effect, 'none')
  } finally { await client.close() }
})
