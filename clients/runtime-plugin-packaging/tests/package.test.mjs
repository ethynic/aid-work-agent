import assert from 'node:assert/strict'
import { generateKeyPairSync, verify } from 'node:crypto'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync, cpSync, existsSync, realpathSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import test from 'node:test'
import canonicalize from 'canonicalize'
import { createOfflinePackage, copyProviderScriptAssets, copyProviderDriverAssets, filesUnder, sha256, zipBytes, main, stageNativeInputDependency } from '../package.mjs'

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

test('offline verification accepts a stable authorized publisher and keeps production separate from test trust', async () => {
  const home = mkdtempSync(join(tmpdir(), 'aid-stable-publisher-verification-'))
  try {
    const pair = generateKeyPairSync('ed25519')
    const manifest = { manifest_version: 1, provider_id: 'ai.aidwork.boss-recruiting', provider_version: '1.2.3', tools: [] }
    const payload = await zipBytes([['runtime-manifest.json', Buffer.from(canonicalize(manifest))]])
    const built = await createOfflinePackage(payload, manifest, { release_id: 'boss-1.2.3', key_id: 'stable-fixture', created_at: '2026-10-30T00:00:00Z' }, pair.privateKey.export({ format: 'pem', type: 'pkcs8' }))
    const paths = ['envelope.json', 'payload.zip', 'manifest.json', 'trust.json'].map(file => join(home, file))
    writeFileSync(paths[0], JSON.stringify(built.envelope)); writeFileSync(paths[1], payload); writeFileSync(paths[2], JSON.stringify(manifest))
    const root = { key_id: 'stable-fixture', public_key: pair.publicKey.export({ format: 'pem', type: 'spki' }), providers: ['ai.aidwork.boss-recruiting'], test_only: false }
    const verifier = resolve(dirname(fileURLToPath(import.meta.url)), '../verify-signature.mjs')
    for (const profile of ['acceptance', 'production']) {
      writeFileSync(paths[3], JSON.stringify({ schemaVersion: 1, profile, roots: [root] }))
      const result = spawnSync(process.execPath, [verifier, ...paths], { encoding: 'utf8' })
      assert.equal(result.status, 0, result.stderr); assert.match(result.stdout, /SIGNATURE_OK/)
    }
    for (const [profile, changed] of [['production', { test_only: true }], ['acceptance', { providers: ['ai.aidwork.wecom'] }]]) {
      writeFileSync(paths[3], JSON.stringify({ schemaVersion: 1, profile, roots: [{ ...root, ...changed }] }))
      const result = spawnSync(process.execPath, [verifier, ...paths], { encoding: 'utf8' })
      assert.notEqual(result.status, 0, 'A valid signature cannot broaden its Provider scope or admit test roots into production')
    }
  } finally { rmSync(home, { recursive: true, force: true }) }
})

test('Python offline smoke uses explicit stable public roots and retains the isolated-test default', async () => {
  const home = mkdtempSync(join(tmpdir(), 'aid-stable-offline-smoke-'))
  try {
    const pair = generateKeyPairSync('ed25519')
    const reported = { provider_id: 'ai.aidwork.boss-recruiting', provider_version: '1.2.3',
      entrypoint: ['dist/src/cli/index.js', 'mcp', '--stdio'], tools: [], schema_digest: 'fixture', protocol: 'mcp', transport: 'stdio' }
    const cli = Buffer.from(`console.log(JSON.stringify(process.argv.includes('doctor') ? {success:false,runtime_readiness:{ready:false,reason:'fixture not logged in'}} : ${JSON.stringify(reported)})); if(process.argv.includes('doctor')) process.exitCode=1`)
    const manifest = { ...reported, manifest_version: 1, distribution: { required_files: [{ path: 'dist/src/cli/index.js', size: cli.length, sha256: sha256(cli) }] } }
    const payload = await zipBytes([['runtime-manifest.json', Buffer.from(JSON.stringify(manifest))], ['dist/src/cli/index.js', cli]])
    const built = await createOfflinePackage(payload, manifest, { release_id: 'boss-1.2.3', key_id: 'stable-fixture', created_at: '2026-10-30T00:00:00Z' }, pair.privateKey.export({ format: 'pem', type: 'pkcs8' }))
    const output = join(home, 'plugins'); mkdirSync(output)
    writeFileSync(join(output, 'boss-fixture.aidplugin.zip'), built.archive)
    const root = { key_id: 'stable-fixture', public_key: pair.publicKey.export({ format: 'pem', type: 'spki' }), providers: [reported.provider_id], test_only: false }
    const stable = join(home, 'public.json')
    writeFileSync(stable, JSON.stringify({ schemaVersion: 1, profile: 'production', roots: [root] }))
    const verifier = resolve(dirname(fileURLToPath(import.meta.url)), '../verify-offline.py')
    for (const explicit of [true, false]) {
      if (!explicit) writeFileSync(join(output, 'ISOLATED-TEST-TRUST-ROOT.json'), JSON.stringify({ schemaVersion: 1, profile: 'acceptance', roots: [{ ...root, test_only: true }] }))
      const result = spawnSync('python', [verifier, '--directory', output, '--node', process.execPath, ...(explicit ? ['--trust-roots', stable] : [])], { encoding: 'utf8', timeout: 30_000, windowsHide: true })
      assert.equal(result.status, 0, result.stderr)
      const summaries = JSON.parse(result.stdout)
      assert.equal(summaries[0].payload_unchanged, true, 'A publisher check must never mutate signed payload files')
      assert.equal(summaries[0].runtime_readiness.ready, false, 'Not logged in remains a successful package smoke with truthful readiness')
    }
  } finally { rmSync(home, { recursive: true, force: true }) }
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
      assert.equal(realpathSync.native(result.stdout.trim()), realpathSync.native(python))
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

test('signed drivers keep source bytes while local Python bytecode never enters the release', () => {
  const home = mkdtempSync(join(tmpdir(), 'aid-driver-source-assets-'))
  try {
    const source = join(home, 'source'), payload = join(home, 'payload')
    mkdirSync(join(source, 'py/__pycache__'), { recursive: true })
    mkdirSync(join(source, 'ps1'))
    writeFileSync(join(source, 'py/session.py'), 'current source')
    writeFileSync(join(source, 'ps1/driver.ps1'), '\ufeff# current driver')
    writeFileSync(join(source, 'schema.json'), '{}')
    writeFileSync(join(source, 'py/__pycache__/session.cpython-312.pyc'), 'stale developer bytecode')
    writeFileSync(join(source, 'py/legacy.pyo'), 'stale optimized bytecode')
    writeFileSync(join(source, 'py/legacy.PYC'), 'stale bytecode')
    copyProviderDriverAssets(source, payload)
    assert.deepEqual(filesUnder(payload).map(file => file.path), ['ps1/driver.ps1', 'py/session.py', 'schema.json'])
    for (const file of filesUnder(payload)) {
      assert.equal(file.sha256, sha256(readFileSync(join(source, file.path))))
    }
  } finally { rmSync(home, { recursive: true, force: true }) }
})

test('native input dependency: no declaration stays untouched; declared one is restaged as an in-stage tarball with dist check', async () => {
  const home = mkdtempSync(join(tmpdir(), 'aid-native-input-staging-'))
  try {
    // source: <home>/clients/<provider>; shared: <home>/clients/shared/win32-native-input
    // （与真实仓库一致：package.json 的 file:../shared/win32-native-input 按 provider 目录解析）
    const provider = join(home, 'clients', 'some-provider')
    const shared = join(home, 'clients', 'shared', 'win32-native-input')
    mkdirSync(provider, { recursive: true })
    const stage = (deps) => {
      const s = mkdtempSync(join(home, 'stage-'))
      writeFileSync(join(s, 'package.json'), JSON.stringify({ name: 'p', version: '1.0.0', dependencies: deps }, null, 2))
      return s
    }
    const NATIVE_DEP = { '@aid/win32-native-input': 'file:../shared/win32-native-input' }

    // 未声明依赖：返回 null，不改 package.json
    const untouched = stage(undefined)
    assert.equal(stageNativeInputDependency(provider, untouched, {}), null)
    assert.equal(JSON.parse(readFileSync(join(untouched, 'package.json'), 'utf8')).dependencies, undefined)

    // 未构建（缺 dist）直接失败
    mkdirSync(shared, { recursive: true })
    writeFileSync(join(shared, 'package.json'), JSON.stringify({ name: '@aid/win32-native-input', version: '0.1.0' }))
    const missingDist = stage(NATIVE_DEP)
    assert.throws(() => stageNativeInputDependency(provider, missingDist, {}), /build before packaging/)
    assert.equal(existsSync(join(missingDist, 'win32-native-input.tgz')), false)

    // 已构建：dist 存在；注入 fake npm cli（单测不跑真实 npm pack）
    mkdirSync(join(shared, 'dist'), { recursive: true })
    writeFileSync(join(shared, 'dist/index.js'), 'export {}')
    const tarName = 'aid-win32-native-input-0.1.0.tgz'
    const fakeNpm = join(home, 'fake-npm.cjs')
    writeFileSync(fakeNpm, "const {writeFileSync}=require('node:fs');const {join}=require('node:path');"
      + "const dest=process.argv.slice(2).map(a=>a.startsWith('--pack-destination=')?a.slice('--pack-destination='.length):null).filter(Boolean)[0];"
      + "writeFileSync(join(dest,'aid-win32-native-input-0.1.0.tgz'),'tarball-bytes')")
    // 评审 P2：源码树残留的同名旧 tgz 不得被复用——每次必须从当前构建产物重新 pack
    writeFileSync(join(shared, tarName), 'stale-tarball-bytes')
    const ready = stage(NATIVE_DEP)
    assert.equal(stageNativeInputDependency(provider, ready, { node: process.execPath, npm_cli: fakeNpm }), tarName)
    assert.deepEqual(readFileSync(join(ready, tarName)), Buffer.from('tarball-bytes'))
    assert.deepEqual(readFileSync(join(shared, tarName)), Buffer.from('stale-tarball-bytes'))
    const rewritten = JSON.parse(readFileSync(join(ready, 'package.json'), 'utf8'))
    assert.equal(rewritten.dependencies['@aid/win32-native-input'], `file:./${tarName}`)
    // 不再包含相对路径（staged npm 无法解析）
    assert.ok(!readFileSync(join(ready, 'package.json'), 'utf8').includes('../shared'))
  } finally { rmSync(home, { recursive: true, force: true }) }
})
