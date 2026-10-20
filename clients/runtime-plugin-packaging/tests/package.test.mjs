import assert from 'node:assert/strict'
import { generateKeyPairSync, verify } from 'node:crypto'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync, cpSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import test from 'node:test'
import canonicalize from 'canonicalize'
import { createOfflinePackage, copyProviderScriptAssets, filesUnder, sha256, zipBytes, main } from '../package.mjs'

test('release signature binds inner payload and full canonical manifest, with no self hash', async () => {
  const pair = generateKeyPairSync('ed25519')
  const manifest = { manifest_version: 1, provider_id: 'ai.aidwork.weixin', provider_version: '0.1.0', tools: [] }
  const payload = await zipBytes([['runtime-manifest.json', Buffer.from(canonicalize(manifest))]])
  const { envelope, archive } = await createOfflinePackage(payload, manifest, { release_id: 'dev-test', key_id: 'test', created_at: '2026-10-09T00:00:00Z' }, pair.privateKey.export({ format: 'pem', type: 'pkcs8' }))
  assert.equal(envelope.package_size, payload.length)
  assert.equal(envelope.package_digest, 'sha256:' + sha256(payload))
  assert.notEqual(envelope.package_digest, 'sha256:' + sha256(archive))
  assert.equal(envelope.manifest_digest, 'sha256:' + sha256(Buffer.from(canonicalize(manifest))))
  const { signature, ...unsigned } = envelope
  assert.equal(verify(null, Buffer.from(canonicalize(unsigned)), pair.publicKey, Buffer.from(signature.slice(7), 'base64')), true)
  unsigned.package_digest = 'sha256:' + '0'.repeat(64)
  assert.equal(verify(null, Buffer.from(canonicalize(unsigned)), pair.publicKey, Buffer.from(signature.slice(7), 'base64')), false)
})

test('test packaging refuses known key ids before generating a random signer or touching output', async () => {
  for (const args of [[], ['--test-key-id', 'aid-isolated-boss-ps-20261015-r15']]) {
    await assert.rejects(main(['--test', ...args]), /already registered; use a unique --test-key-id/)
  }
})

test('required files describes actual relocated payload bytes and rejects directory links', () => {
  const home = mkdtempSync(join(tmpdir(), 'aid-package-files-'))
  try {
    mkdirSync(join(home, 'nested'))
    writeFileSync(join(home, 'nested/file.txt'), 'immutable')
    assert.deepEqual(filesUnder(home), [{ path: 'nested/file.txt', size: 9, sha256: sha256(Buffer.from('immutable')) }])
    symlinkSync(join(home, 'nested'), join(home, 'link'), 'junction')
    assert.throws(() => filesUnder(home), /links or special files/)
  } finally { rmSync(home, { recursive: true, force: true }) }
})

test('PowerShell OCR paths relocate, prefer bundled interpreter, and fail closed for installed packages', () => {
  const clients = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
  const home = mkdtempSync(join(tmpdir(), 'aid-ps-ocr-paths-'))
  try {
    for (const [provider, resolver] of [['weixin-cli', 'Resolve-WeixinOcrPython'], ['wecom-cli', 'Resolve-WeComOcrPython']]) {
      const pkg = join(home, provider)
      mkdirSync(join(pkg, 'drivers/ps1'), { recursive: true })
      cpSync(join(clients, provider, 'drivers/ps1/_common.ps1'), join(pkg, 'drivers/ps1/_common.ps1'))
      if (provider === 'weixin-cli') cpSync(join(clients, provider, 'drivers/ps1/win32-lib.ps1'), join(pkg, 'drivers/ps1/win32-lib.ps1'))
      mkdirSync(join(pkg, 'ocr-python'))
      const python = join(pkg, 'ocr-python/python.exe')
      writeFileSync(python, '')
      const script = `. '${join(pkg, 'drivers/ps1/_common.ps1').replaceAll("'", "''")}'; ${resolver}`
      let result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', script], { encoding: 'utf8', windowsHide: true })
      assert.equal(result.status, 0, result.stderr)
      assert.equal(result.stdout.trim(), python)
      rmSync(python)
      writeFileSync(join(pkg, 'runtime-manifest.json'), '{}')
      result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', script], { encoding: 'utf8', windowsHide: true })
      assert.notEqual(result.status, 0)
      assert.match(result.stderr, /CONFIG_MISSING/)
    }
    const history = readFileSync(join(clients, 'weixin-cli/drivers/ps1/history-read.ps1'), 'utf8')
    assert.match(history, /Resolve-WeixinOcrPython/)
    assert.doesNotMatch(history, /experiments|venv\\Scripts/)
  } finally { rmSync(home, { recursive: true, force: true }) }
})


test('BOSS payload staging includes every PowerShell native dependency in the complete required-file manifest', () => {
  const home = mkdtempSync(join(tmpdir(), 'aid-boss-script-assets-'))
  const scripts = resolve(dirname(fileURLToPath(import.meta.url)), '../../boss-resume-assistant/scripts')
  try {
    const source = join(home, 'source'), payload = join(home, 'payload'), target = join(payload, 'scripts')
    mkdirSync(source); mkdirSync(payload)
    cpSync(scripts, source, { recursive: true })
    writeFileSync(join(source, 'UnapprovedNative.cs'), 'must not ship')
    copyProviderScriptAssets('boss', source, target)
    const required = filesUnder(payload)
    for (const native of ['BossPhysicalInput.cs', 'BossWindowExperiment.cs']) {
      const record = required.find(file => file.path === `scripts/${native}`)
      assert.ok(record, `${native} must be signed and hashed as part of the payload`)
      assert.equal(record.sha256, sha256(readFileSync(join(scripts, native))))
    }
    for (const asset of required.filter(file => file.path.endsWith('.ps1'))) {
      const script = readFileSync(join(payload, asset.path), 'utf8')
      for (const [, native] of script.matchAll(/['"]([^'"\r\n]+\.cs)['"]/g)) {
        assert.ok(required.some(file => file.path === `scripts/${native}`), `${asset.path} needs ${native} after relocation`)
      }
    }
    assert.equal(required.some(file => file.path.endsWith('.mjs') || file.path.endsWith('/UnapprovedNative.cs')), false)
  } finally { rmSync(home, { recursive: true, force: true }) }
})

test('missing either BOSS native dependency fails before an incomplete script directory is staged', () => {
  const home = mkdtempSync(join(tmpdir(), 'aid-boss-script-dependency-'))
  try {
    for (const missing of ['BossPhysicalInput.cs', 'BossWindowExperiment.cs']) {
      const source = join(home, missing + '-source'), target = join(home, missing + '-target')
      mkdirSync(source)
      writeFileSync(join(source, 'window-experiment.ps1'), 'do not execute')
      for (const file of ['BossPhysicalInput.cs', 'BossWindowExperiment.cs']) if (file !== missing) writeFileSync(join(source, file), 'native dependency')
      assert.throws(() => copyProviderScriptAssets('boss', source, target), new RegExp(missing.replaceAll('.', '\\.')))
      assert.equal(existsSync(target), false)
    }
  } finally { rmSync(home, { recursive: true, force: true }) }
})

test('non-BOSS script staging retains its PowerShell-only asset policy', () => {
  const home = mkdtempSync(join(tmpdir(), 'aid-other-script-assets-'))
  try {
    const source = join(home, 'source'); mkdirSync(source)
    for (const file of ['driver.ps1', 'BossPhysicalInput.cs', 'BossWindowExperiment.cs', 'probe.mjs']) writeFileSync(join(source, file), file)
    for (const provider of ['weixin', 'wecom']) {
      const target = join(home, provider)
      copyProviderScriptAssets(provider, source, target)
      assert.deepEqual(filesUnder(target).map(file => file.path), ['driver.ps1'])
    }
  } finally { rmSync(home, { recursive: true, force: true }) }
})
