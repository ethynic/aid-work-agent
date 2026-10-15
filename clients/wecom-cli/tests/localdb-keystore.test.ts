import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import {
  clearKey,
  KEY_FILE_NAME,
  loadKey,
  saveKey,
  type KeyBinding,
  type KeystoreDeps,
} from '../src/platform/localdb/keystore.js'

const BINDING: KeyBinding = {
  user_id: '1234567890',
  data_dir: 'C:\\Users\\t\\Documents\\WXWork\\1234567890',
  algorithm: 'wxaes128cbc-nonlegacy',
}
const OTHER_BINDING: KeyBinding = { ...BINDING, user_id: '9876543210' }
const KEY_HEX = '00112233445566778899aabbccddeeff' // 测试专用合成密钥

interface RunnerCall {
  command: string
  stdinText: string
}

/**
 * mock dpapi runner（零 PowerShell）：protect → base64(stdin)、unprotect → base64 解码，
 * 与真实执行器的数据面语义一致；捕获每次调用的 (command, stdinText) 供密钥外泄断言。
 */
function makeMockRunner(calls: RunnerCall[]): KeystoreDeps['runFn'] {
  return async (command: string, stdinText: string): Promise<string> => {
    calls.push({ command, stdinText })
    if (command.includes('::Protect(')) return Buffer.from(stdinText, 'utf8').toString('base64')
    if (command.includes('::Unprotect(')) return Buffer.from(stdinText, 'base64').toString('utf8')
    throw new Error('unexpected dpapi command')
  }
}

function tempDir(): string {
  return mkdtempSync(join(tmpdir(), 'wc-localdb-keystore-'))
}

test('save/load roundtrip（mock runner）：文件落盘、binding 齐备、blob 为 DPAPI 形态、密钥只在 protect 入参出现', async () => {
  const dir = tempDir()
  const calls: RunnerCall[] = []
  try {
    assert.equal(await saveKey({ keyHex: KEY_HEX, binding: BINDING, dir }, { runFn: makeMockRunner(calls) }), true)

    const file = join(dir, KEY_FILE_NAME)
    assert.ok(existsSync(file), 'key.bin 应落盘')
    const onDisk = readFileSync(file, 'utf8')
    assert.ok(!onDisk.includes(KEY_HEX), '盘上文件不得含密钥明文（只有 DPAPI blob）')
    const record = JSON.parse(onDisk)
    assert.deepEqual(record.binding, BINDING)
    assert.equal(typeof record.saved_at, 'string')
    assert.ok(record.saved_at.length > 0)
    assert.equal(
      Buffer.from(record.key_blob, 'base64').toString('utf8'),
      KEY_HEX,
      'key_blob 应为 DPAPI 形态（mock 下 base64）',
    )

    const stored = await loadKey({ binding: BINDING, dir }, { runFn: makeMockRunner(calls) })
    assert.deepEqual(stored, { keyHex: KEY_HEX, savedAt: record.saved_at })

    // 密钥外泄面：捕获参数中含密钥明文的调用有且仅有 protect 一次；unprotect 只拿 blob
    const protectCalls = calls.filter((c) => c.command.includes('::Protect('))
    const unprotectCalls = calls.filter((c) => c.command.includes('::Unprotect('))
    assert.equal(protectCalls.length, 1)
    assert.equal(unprotectCalls.length, 1)
    assert.equal(protectCalls[0]!.stdinText, KEY_HEX)
    assert.ok(!unprotectCalls[0]!.stdinText.includes(KEY_HEX), 'unprotect 入参只应是密文 blob')
    for (const c of calls) {
      assert.ok(!c.command.includes(KEY_HEX), '密钥不得进命令串')
    }
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('binding 三元组任一变化 → null 且文件被清（旧账号残留不留盘）', async () => {
  const dir = tempDir()
  const calls: RunnerCall[] = []
  try {
    assert.equal(await saveKey({ keyHex: KEY_HEX, binding: BINDING, dir }, { runFn: makeMockRunner(calls) }), true)
    const file = join(dir, KEY_FILE_NAME)
    assert.ok(existsSync(file))

    assert.equal(await loadKey({ binding: OTHER_BINDING, dir }, { runFn: makeMockRunner(calls) }), null)
    assert.ok(!existsSync(file), 'binding 不匹配时顺带 clearKey')

    // data_dir / algorithm 维度同样触发失效
    assert.equal(await saveKey({ keyHex: KEY_HEX, binding: BINDING, dir }, { runFn: makeMockRunner(calls) }), true)
    assert.equal(
      await loadKey({ binding: { ...BINDING, data_dir: 'C:\\elsewhere' }, dir }, { runFn: makeMockRunner(calls) }),
      null,
    )
    assert.ok(!existsSync(file))
    assert.equal(await saveKey({ keyHex: KEY_HEX, binding: BINDING, dir }, { runFn: makeMockRunner(calls) }), true)
    assert.equal(
      await loadKey({ binding: { ...BINDING, algorithm: 'other' }, dir }, { runFn: makeMockRunner(calls) }),
      null,
    )
    assert.ok(!existsSync(file))
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('文件缺失 / JSON 损坏 / shape 不合法 → null（损坏现场保留，不臆测清除）', async () => {
  const { mkdirSync, writeFileSync } = await import('node:fs')
  const dir = tempDir()
  const runFn = makeMockRunner([])
  try {
    // 文件缺失
    assert.equal(await loadKey({ binding: BINDING, dir }, { runFn }), null)
    // JSON 损坏
    mkdirSync(dir, { recursive: true })
    const file = join(dir, KEY_FILE_NAME)
    writeFileSync(file, '{not-json', 'utf8')
    assert.equal(await loadKey({ binding: BINDING, dir }, { runFn }), null)
    assert.ok(existsSync(file), '损坏现场保留（清除仅限 binding 不匹配）')
    // shape 不合法：缺 key_blob / saved_at 非字符串 / binding 缺字段
    writeFileSync(file, JSON.stringify({ binding: BINDING, saved_at: 't' }), 'utf8')
    assert.equal(await loadKey({ binding: BINDING, dir }, { runFn }), null)
    writeFileSync(file, JSON.stringify({ binding: BINDING, key_blob: 'AAAA', saved_at: 42 }), 'utf8')
    assert.equal(await loadKey({ binding: BINDING, dir }, { runFn }), null)
    writeFileSync(
      file,
      JSON.stringify({ binding: { user_id: BINDING.user_id, data_dir: BINDING.data_dir }, key_blob: 'AAAA', saved_at: 't' }),
      'utf8',
    )
    assert.equal(await loadKey({ binding: BINDING, dir }, { runFn }), null)
    // 解密产物不是合法主密钥 hex（blob 被 mock 侧换成垃圾）→ null
    assert.equal(await saveKey({ keyHex: KEY_HEX, binding: BINDING, dir }, { runFn: makeMockRunner([]) }), true)
    const tampered = JSON.parse(readFileSync(file, 'utf8'))
    tampered.key_blob = Buffer.from('short-not-a-key', 'utf8').toString('base64')
    writeFileSync(file, JSON.stringify(tampered), 'utf8')
    assert.equal(await loadKey({ binding: BINDING, dir }, { runFn: makeMockRunner([]) }), null)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('dpapi 失败（runner reject）→ saveKey false / loadKey null；异常消息不外泄、密钥不入异常', async () => {
  const dir = tempDir()
  try {
    const boom = (what: string) => async (): Promise<string> => {
      throw new Error(`boom ${what} ${KEY_HEX}`) // 故意带密钥的消息：验证不被 keystore 抛出
    }
    assert.equal(await saveKey({ keyHex: KEY_HEX, binding: BINDING, dir }, { runFn: boom('protect') }), false)
    assert.ok(!existsSync(join(dir, KEY_FILE_NAME)))

    // 落一个合法文件后让 unprotect 失败 → null（视同失效，调用方走重扫），且不抛出
    const okRunner = makeMockRunner([])
    assert.equal(await saveKey({ keyHex: KEY_HEX, binding: BINDING, dir }, { runFn: okRunner }), true)
    let result: unknown = 'sentinel'
    await assert.doesNotReject(async () => {
      result = await loadKey({ binding: BINDING, dir }, { runFn: boom('unprotect') })
    })
    assert.equal(result, null)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('非法输入：keyHex 非 32-hex / binding 缺字段 → false 且不触碰 runner、不落盘；load 非法 binding → null', async () => {
  const dir = tempDir()
  const calls: RunnerCall[] = []
  try {
    for (const bad of ['', '00112233445566778899aabbccddeef', '00112233445566778899aabbccddeefff', 'zz112233445566778899aabbccddeeff']) {
      assert.equal(await saveKey({ keyHex: bad, binding: BINDING, dir }, { runFn: makeMockRunner(calls) }), false, bad)
    }
    assert.equal(calls.length, 0, '非法输入不得调用 dpapi')
    assert.ok(!existsSync(join(dir, KEY_FILE_NAME)))
    assert.equal(
      await saveKey({ keyHex: KEY_HEX, binding: { ...BINDING, user_id: '' }, dir }, { runFn: makeMockRunner(calls) }),
      false,
    )
    assert.equal(await loadKey({ binding: { ...BINDING, algorithm: '' }, dir }, { runFn: makeMockRunner(calls) }), null)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('LOCALAPPDATA 缺失（显式 null dir）→ 明确落空不抛：save false / load null / clear true', async () => {
  const calls: RunnerCall[] = []
  const runFn = makeMockRunner(calls)
  await assert.doesNotReject(async () => {
    assert.equal(await saveKey({ keyHex: KEY_HEX, binding: BINDING, dir: null }, { runFn }), false)
    assert.equal(await loadKey({ binding: BINDING, dir: null }, { runFn }), null)
    assert.equal(clearKey(null), true)
  })
  assert.equal(calls.length, 0, '目录不可定位时不应发起 dpapi 调用')
})

test('clearKey：文件在 → 删除返回 true；文件不在 → 幂等 true', async () => {
  const dir = tempDir()
  try {
    assert.equal(await saveKey({ keyHex: KEY_HEX, binding: BINDING, dir }, { runFn: makeMockRunner([]) }), true)
    assert.ok(existsSync(join(dir, KEY_FILE_NAME)))
    assert.equal(clearKey(dir), true)
    assert.ok(!existsSync(join(dir, KEY_FILE_NAME)))
    assert.equal(clearKey(dir), true) // 幂等
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})
