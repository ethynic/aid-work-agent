/** Only synthetic encrypted DBs and injected UI drivers; safe to use in a real stdio MCP process. */
import { execFileSync } from 'node:child_process'
import { mkdirSync, readFileSync, appendFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { locateDataDir } from '../../src/platform/localdb/datadir.js'
import type { CurrentAccount } from '../../src/platform/localdb/identity.js'
import type { LocaldbReadDeps } from '../../src/platform/localdb/readSession.js'
import type { RunPowerShellDriverFn } from '../../src/platform/powershell.js'
import { createWecomMessageSendOperation } from '../../src/operations/messageSend.js'
import { createTargetRef, verifyTargetRef } from '../../src/platform/targetRef.js'

const cliRoot = fileURLToPath(new URL('../../../', import.meta.url))
export const fixturePython = join(cliRoot, '..', '..', 'venv', 'Scripts', 'python.exe')
const helper = join(cliRoot, 'tests', 'py', 'group_monitor_fixture.py')
export function changeGroupFixture(base: string, action: string, data?: Record<string, unknown>): void {
  execFileSync(fixturePython, [helper, base, action, ...(data ? [JSON.stringify(data)] : [])], { windowsHide: true })
}

export async function groupMonitorFixture(base: string, setup = true, searchSection = '群聊') {
  if (setup) changeGroupFixture(base, 'setup')
  const dataDir = await locateDataDir({ cefLogDir: null, candidateRoots: [],
    activeAccount: { pid: 4242, message_db: join(base, '9007199254740997', 'Data', 'message.db') } })
  const current: CurrentAccount = { account: '9007199254740997', windowPid: 4242, dataDir }
  const deps: LocaldbReadDeps = { probeAccountFn: async () => current,
    readStateFn: () => ({ mode: 'db', version: '5.0.11.6029', checked_at: 'fixture' }), writeStateFn: () => true,
    detectInstallFn: async () => ({ installed: true, pid: 4242, exe_path: 'C:\\synthetic\\WXWork.exe',
      install_dir: 'C:\\synthetic', version: '5.0.11.6029', version_source: 'process', process_started_at: null, upgrade_signals: [] }),
    loadMatrixFn: () => ({ entries: { '5.0.11.6029': { status: 'validated', algorithm: 'wxaes128cbc-nonlegacy',
      page_size: 4096, db_magic: readFileSync(dataDir.db_paths!.message!).subarray(0, 8).toString('hex') } } }),
    loadKeyFn: async () => ({ keyHex: '000102030405060708090a0b0c0d0e0f', savedAt: 'fixture' }),
    stateDir: join(base, 'state'), keyDir: join(base, 'state'), workBaseDir: join(base, 'work'), logFn: () => true,
  }
  const calls: string[] = []
  const driver: RunPowerShellDriverFn = async options => {
    const script = options.script.replace(/^.*[\\/]/, '')
    calls.push(script)
    const value = (key: string) => options.args?.[(options.args?.indexOf(key) ?? -2) + 1] as string
    if (script === 'chat-search.ps1') {
      const name = value('-Query')
      return { query: name, items: [{ name, subtitle: '', section: searchSection, x: 100, y: 100 }], best_index: 0,
        overlay: { x: 0, y: 0, w: 400, h: 500 }, jev: { used: false }, timing_ms: {} }
    }
    if (script !== 'message-send.ps1' || options.args?.includes('-VerifyOnly')) throw new Error('unexpected fixture driver')
    const text = value('-Text')
    const name = value('-TargetName')
    changeGroupFixture(base, 'append', { text, self: true, conversation_id: name === '合成群乙' ? 'R:fixture-b' : 'R:fixture-a' })
    appendFileSync(join(base, 'sent.jsonl'), JSON.stringify({ target_name: name, text }) + '\n')
    return { navigate_required: false, title: name, sent_verification: { method: 'skipped', result: 'unverified' }, timing_ms: {} }
  }
  const artifacts = join(base, 'artifacts')
  mkdirSync(artifacts, { recursive: true })
  const keyDir = join(base, 'refs')
  const refDeps = { createRefFn: (name: string, type: string, subtitle = '', coords?: { x: number; y: number }, account?: string) => createTargetRef(name, type, subtitle, { keyDir, coords, account }),
    verifyRefFn: (value: string) => verifyTargetRef(value, { keyDir }) }
  const send = createWecomMessageSendOperation({ ...refDeps, localdb: deps, artifactDirFn: () => artifacts,
    runDriverFn: driver, localdbVerifyPoll: { attempts: 1, delayMs: 0 },
    receiptCrypto: { protect: async plain => Buffer.from(plain).toString('base64'),
      unprotect: async cipher => Buffer.from(cipher, 'base64').toString() } })
  return { deps, current, send, calls, artifacts, refDeps }
}
