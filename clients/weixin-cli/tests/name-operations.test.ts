import test from 'node:test'
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createNameOperations } from '../src/operations/nameOperations.js'

const ctx = { signal: new AbortController().signal, progress() {} }

test('原生精确定位只保存加密身份，并约束后续观察读取同一联系人', async () => {
  const target = '测试联系人'
  const identity = '555:123:1.2.3'
  const stateDir = await mkdtemp(join(tmpdir(), 'aid-weixin-native-resolution-'))
  try {
    let actualIdentity = '555:123:1.2.4'
    let proof: unknown
    const ops = createNameOperations({
      observeCurrentOnly: true,
      resolveEvidence: {
        stateDir,
        dpapiRunFn: async (command, plainText) => {
          assert.match(command, /DataProtectionScope\]::CurrentUser/)
          proof = JSON.parse(plainText)
          return 'fixture-ciphertext'
        },
      },
      nativeDriver: async (opts) => {
        assert.equal(opts.useVisionProxy, false)
        assert.equal(opts.signal, ctx.signal)
        if (opts.script.endsWith('name-resolve-native.ps1')) {
          assert.deepEqual(JSON.parse(opts.stdin!), { target_name: target })
          return { title: target, session_identity: identity }
        }
        assert.match(opts.script, /observe-native\.ps1$/)
        assert.deepEqual(JSON.parse(opts.stdin!), {
          target_name: target, native_title: target, session_identity: identity, include_evidence: false,
        })
        return { title: target, session_identity: actualIdentity, is_group: false, input_empty: true, rows: [] }
      },
    })

    const resolved = await ops.resolve.execute({ target_name: target }, ctx)
    assert.equal(resolved.success, true)
    assert.match(String(resolved.data.evidence_ref), /^dpapi:[a-f0-9-]{36}$/)
    assert.deepEqual(resolved.data, {
      target_name: target, title_exact: true, unique_match: true,
      name_match_mode: 'uia_exact_unique', evidence_ref: resolved.data.evidence_ref,
    })
    assert.deepEqual(proof, { version: 1, title: target, session_identity: identity })
    const files = await readdir(stateDir)
    assert.deepEqual(files, [String(resolved.data.evidence_ref).slice(6) + '.dpapi'])
    assert.equal(await readFile(join(stateDir, files[0]!), 'utf8'), 'fixture-ciphertext')

    const args = { target_name: target, conversation_binding_id: randomUUID(), binding_version: 1, account_identity_version: 0, watermark: null }
    const rejected = await ops.observe.execute(args, ctx)
    assert.equal(rejected.data.coverage, 'gap')
    assert.deepEqual(rejected.data.ordered_messages, [])
    actualIdentity = identity
    const observed = await ops.observe.execute(args, ctx)
    assert.equal(observed.data.coverage, 'complete_window')
    assert.deepEqual(observed.data.ordered_messages, [])
  } finally {
    await rm(stateDir, { recursive: true, force: true })
  }
})

test('定位拒绝非精确原生标题或附带正文的驱动结果', async () => {
  for (const data of [
    { title: 'test', session_identity: '555:123:1' },
    { title: 'Test', session_identity: '555:123:1', rows: [] },
    { title: 'Test', session_identity: '' },
  ]) {
    const { resolve } = createNameOperations({
      nativeDriver: async () => data,
      resolveEvidence: { dpapiRunFn: async () => assert.fail('untrusted proof must not be saved') },
    })
    const result = await resolve.execute({ target_name: 'Test' }, ctx)
    assert.equal(result.code, 'UI_CHANGED')
    assert.deepEqual(result.data, {})
  }
})
