import assert from 'node:assert/strict'
import { generateKeyPairSync, verify } from 'node:crypto'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync, cpSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import test from 'node:test'
import canonicalize from 'canonicalize'
import { createOfflinePackage, filesUnder, sha256, zipBytes } from '../package.mjs'

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
