/** 真实加密三库+Python解析+operation投影，防止提及/引用被最长字符串替代。 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createWecomGroupReadOperation } from '../src/operations/groups.js'
import { locateDataDir } from '../src/platform/localdb/datadir.js'
import type { CurrentAccount } from '../src/platform/localdb/identity.js'
import type { LocaldbReadDeps } from '../src/platform/localdb/readSession.js'

const root = fileURLToPath(new URL('../../', import.meta.url))
const python = join(root, '..', '..', 'venv', 'Scripts', 'python.exe')
const SELF = '9007199254740997'
const PEER = '9007199254740999'
const VERSION = '5.0.11.6029'
interface Mention { kind: string; user_id: string; name: string | null; name_source: string }
interface Message {
  msg_id: number; text: string; sender_id: string; sender_name: string | null; sender_name_source: string
  mentions: Mention[]; mentions_complete: boolean; parse_trusted: boolean
}

test('群历史按有序片段恢复正文/@/引用，按精确字符串ID补姓名且保留未知状态',
  { skip: process.platform !== 'win32' }, async () => {
    const base = mkdtempSync(join(tmpdir(), 'wecom-mentions-'))
    try {
      execFileSync(python, [join(root, 'tests', 'py', 'group_mentions_fixture.py'), base], { windowsHide: true })
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
      const result = await createWecomGroupReadOperation(deps).execute({ target_name: '项目群' },
        { signal: new AbortController().signal, progress() {} })
      assert.equal(result.success, true, result.message)
      assert.equal(result.data.naming_incomplete, true)
      const messages = result.data.messages as Message[]
      assert.deepEqual(messages.map(m => m.msg_id), [1, 2, 3, 4, 5, 6, 7, 8, 9])
      assert.equal(messages[0]!.sender_id, PEER)
      assert.equal(messages[0]!.sender_name, '张三')
      assert.equal(messages[0]!.sender_name_source, 'user_table')
      assert.equal(messages[1]!.text, '"王五：\n旧消息@某人"\n------\n是的@张三 ')
      assert.deepEqual(messages[1]!.mentions.map(m => m.name), ['张三']) // 引用内字面@不是结构提及。
      assert.equal(messages[1]!.parse_trusted, true)
      assert.equal(messages[2]!.text, '@张三 @李四旧名 请确认🙂')
      assert.deepEqual(messages[2]!.mentions.map(m => m.user_id), [PEER, '9007199254740995'])
      assert.equal(messages[2]!.mentions[1]!.name_source, 'message')
      assert.equal(messages[3]!.text, '@所有人')
      assert.equal(messages[3]!.mentions[0]!.kind, 'all')
      assert.equal(messages[3]!.mentions[0]!.user_id, '0')
      assert.equal(messages[4]!.sender_name, null)
      assert.equal(messages[4]!.sender_name_source, 'unresolved')
      assert.equal(messages[4]!.mentions[0]!.user_id, '18446744073709551615')
      assert.equal(messages[4]!.mentions[0]!.name, null)
      assert.ok(messages[4]!.text.includes('18446744073709551615'))
      assert.equal(messages[5]!.parse_trusted, false)
      assert.equal(messages[5]!.mentions_complete, false)
      assert.ok(messages[5]!.text.startsWith('前') && messages[5]!.text.endsWith('后'))
      assert.equal(messages[6]!.text, '@本人')
      assert.equal(messages[6]!.mentions[0]!.name_source, 'user_table')
      assert.equal(messages[7]!.text, '旧类型片段@张三')
      assert.equal(messages[7]!.parse_trusted, true)
      assert.equal(messages[7]!.mentions[0]!.user_id, PEER)
      assert.equal(messages[8]!.text, '旧格式纯文字')
      assert.equal(messages[8]!.parse_trusted, true)
      assert.deepEqual(messages[8]!.mentions, [])
      assert.deepEqual(readdirSync(deps.workBaseDir!), [])
    } finally { rmSync(base, { recursive: true, force: true }) }
  })
