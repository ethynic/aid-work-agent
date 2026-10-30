import { createHash, createPrivateKey, createPublicKey, generateKeyPairSync, sign } from 'node:crypto'
import { cpSync, existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { basename, dirname, join, resolve } from 'node:path'
import { tmpdir } from 'node:os'
import { fileURLToPath } from 'node:url'
import canonicalize from 'canonicalize'
import yazl from 'yazl'
import { assertUnusedAcceptanceTestKeyId } from '../agent-desktop/scripts/prepare-runtime.mjs'

const root = dirname(fileURLToPath(import.meta.url))
const clients = resolve(root, '..')
export const definitions = {
  boss: { directory: 'boss-resume-assistant', display_name: 'BOSS 招聘', assets: ['scripts'], ocr: false },
  weixin: { directory: 'weixin-cli', display_name: '微信', assets: ['drivers'], ocr: true },
  wecom: { directory: 'wecom-cli', display_name: '企业微信', assets: ['drivers'], ocr: true },
}
export const sha256 = bytes => createHash('sha256').update(bytes).digest('hex')

export function filesUnder(directory, prefix = '') {
  const files = []
  for (const name of readdirSync(directory).sort()) {
    const path = join(directory, name)
    const stat = lstatSync(path)
    if (stat.isSymbolicLink() || (!stat.isDirectory() && !stat.isFile())) throw new Error('Distribution contains links or special files')
    const relative = prefix + name
    if (stat.isDirectory()) files.push(...filesUnder(path, relative + '/'))
    else files.push({ path: relative, size: stat.size, sha256: sha256(readFileSync(path)) })
  }
  return files
}

export function zipBytes(entries) {
  return new Promise((accept, reject) => {
    const zip = new yazl.ZipFile()
    const chunks = []
    zip.outputStream.on('data', data => chunks.push(data))
    zip.outputStream.on('error', reject)
    zip.outputStream.on('end', () => accept(Buffer.concat(chunks)))
    for (const [name, bytes] of entries) zip.addBuffer(bytes, name, { mtime: new Date('2026-01-01T00:00:00Z'), mode: 0o100644 })
    zip.end()
  })
}

export async function createOfflinePackage(payload, manifest, metadata, privateKey) {
  const key = createPrivateKey(privateKey)
  if (key.asymmetricKeyType !== 'ed25519') throw new Error('Release signer must use Ed25519')
  const unsigned = {
    envelope_version: 1,
    provider_release_id: metadata.release_id,
    provider_id: manifest.provider_id,
    provider_version: manifest.provider_version,
    platform: 'win32', arch: 'x64', package_format: 'zip', package_size: payload.length,
    package_digest: 'sha256:' + sha256(payload),
    manifest_digest: 'sha256:' + sha256(Buffer.from(canonicalize(manifest))),
    min_runtime_version: '0.2.14', max_runtime_version: null,
    publisher_key_id: metadata.key_id, signature_algorithm: 'Ed25519', created_at: metadata.created_at,
  }
  const envelope = { ...unsigned, signature: 'base64:' + sign(null, Buffer.from(canonicalize(unsigned)), key).toString('base64') }
  const archive = await zipBytes([['envelope.json', Buffer.from(JSON.stringify(envelope))], ['payload.zip', payload]])
  return { envelope, archive }
}

/** Copy approved script assets and require the BOSS native dependencies before staging any files. */
export function copyProviderScriptAssets(name, source, target) {
  const dependencies = name === 'boss' ? ['BossPhysicalInput.cs', 'BossWindowExperiment.cs'] : []
  for (const file of dependencies) if (!existsSync(join(source, file))) throw new Error(`BOSS input dependency missing: ${file}`)
  mkdirSync(target)
  for (const file of readdirSync(source).filter(file => file.endsWith('.ps1') || dependencies.includes(file))) cpSync(join(source, file), join(target, file))
}

/** Local Python test runs must not change the signed driver payload. */
export function copyProviderDriverAssets(source, target) {
  cpSync(source, target, { recursive: true,
    filter: file => basename(file) !== '__pycache__' && !/\.(?:pyc|pyo)$/i.test(file) })
}

const NATIVE_INPUT_SPEC = 'file:../shared/win32-native-input'

/**
 * 处理 Provider 对第一方原生输入共享包的依赖（`file:../shared/win32-native-input`）。
 * 临时 stage 不在仓库目录，相对路径无法解析；npm 对 file: 目录依赖创建的链接
 * （绝对路径）也无法随包分发。改为：npm pack 出 tgz 放进 stage，把 package.json
 * 的 spec 重写为 `file:./<tgz>`（不再生成链接，node_modules 是真实拷贝）。
 * 返回 null（未声明依赖）；否则返回 tgz 相对名（安装完成后需删除，不入 payload）。
 * 需要先构建共享包（dist/），因为 --ignore-scripts 不会执行 prepack。
 */
export function stageNativeInputDependency(source, stage, options = {}) {
  const pkgPath = join(stage, 'package.json')
  const pkg = JSON.parse(readFileSync(pkgPath, 'utf8'))
  const spec = pkg.dependencies?.['@aid/win32-native-input']
  if (!spec) return null
  if (spec !== NATIVE_INPUT_SPEC) throw new Error(`Unsupported @aid/win32-native-input dependency spec: ${spec}`)
  // source = clients/<provider>（与 package.json 的 file:../shared/win32-native-input 同级解析）
  const origin = resolve(source, '../shared/win32-native-input')
  if (!existsSync(join(origin, 'dist/index.js'))) throw new Error('Run npm --prefix clients/shared/win32-native-input build before packaging (dist/index.js missing)')
  const { name: pkgName, version } = JSON.parse(readFileSync(join(origin, 'package.json'), 'utf8'))
  const tarball = `${pkgName.replace(/^@/, '').replace('/', '-')}-${version}.tgz`
  // 每次从当前构建产物重新 pack 到临时目录（评审 P2）：同版本源码更新后，源码树残留的
  // 旧 tgz 不得混入发布——签名与摘要只能证明内容完整，证明不了内容是最新的。
  const npm = options.npm_cli && existsSync(options.npm_cli) ? options.npm_cli : 'npm'
  const node = options.node && existsSync(options.node) ? options.node : process.execPath
  const packDir = mkdtempSync(join(tmpdir(), 'aid-native-input-pack-'))
  try {
    execFileSync(node, [npm, 'pack', '--ignore-scripts', '--no-audit', '--no-fund', `--pack-destination=${packDir}`], { cwd: origin, timeout: 60000, stdio: 'pipe' })
    const produced = join(packDir, tarball)
    if (!existsSync(produced)) throw new Error(`npm pack did not produce ${tarball}`)
    cpSync(produced, join(stage, tarball))
  } finally { rmSync(packDir, { recursive: true, force: true }) }
  pkg.dependencies['@aid/win32-native-input'] = `file:./${tarball}`
  writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + '\n')
  return tarball
}

async function buildProvider(name, options, signer) {
  const definition = definitions[name]
  if (!definition) throw new Error('Unknown first-party Provider')
  const source = join(clients, definition.directory)
  if (!existsSync(join(source, 'dist/src/cli/index.js'))) throw new Error('Build Provider before packaging')
  const stage = mkdtempSync(join(tmpdir(), 'aid-provider-package-'))
  try {
    cpSync(join(source, 'dist/src'), join(stage, 'dist/src'), { recursive: true })
    for (const assets of definition.assets) {
      // BOSS needs approved PowerShell drivers, not developer live probes.
      if (assets === 'scripts') {
        copyProviderScriptAssets(name, join(source, assets), join(stage, assets))
      } else copyProviderDriverAssets(join(source, assets), join(stage, assets))
    }
    if (definition.ocr) {
      const ocr = resolve(options.ocr ?? join(root, 'assets/ocr-python-final'))
      if (!existsSync(join(ocr, 'python.exe')) || existsSync(join(ocr, 'pyvenv.cfg')) || !existsSync(join(ocr, 'asset-provenance.json')) || !existsSync(join(ocr, 'msvcp140.dll')) || !existsSync(join(ocr, 'msvcp140_1.dll'))) throw new Error('Verified embedded OCR input with app-local CRT required')
      filesUnder(ocr) // reject links before copying or executing
      cpSync(ocr, join(stage, 'ocr-python'), { recursive: true })
      const env = { ...process.env }; delete env.PYTHONHOME; delete env.PYTHONPATH
      execFileSync(join(stage, 'ocr-python/python.exe'), ['-I', '-B', '-c', 'from rapidocr_onnxruntime import RapidOCR; from PIL import Image; RapidOCR(); print("OCR_BUNDLE_OK")'], { cwd: stage, env, timeout: 90000 })
      if (name === 'wecom') {
        // Native DB decryption imports Crypto lazily; plain SQLite/OCR checks
        // cannot prove a reused embedded distribution can decrypt a snapshot.
        execFileSync(join(stage, 'ocr-python/python.exe'), ['-I', '-B', '-c',
          'from Crypto.Cipher import AES; import sqlite3; encrypted=AES.new(bytes(16),AES.MODE_CBC,bytes(16)).encrypt(bytes(16)); assert encrypted.hex()=="66e94bd4ef8a2c3b884cfa59ca342b2e"; assert AES.new(bytes(16),AES.MODE_CBC,bytes(16)).decrypt(encrypted)==bytes(16); print("WECOM_SQLITE_AES_BUNDLE_OK")'],
        { cwd: stage, env, timeout: 30000 })
      }
    }
    cpSync(join(source, 'package.json'), join(stage, 'package.json'))
    cpSync(join(source, 'package-lock.json'), join(stage, 'package-lock.json'))
    const npm = options.npm_cli
    if (!npm || !existsSync(npm)) throw new Error('Pass --npm-cli with the build machine npm CLI path')
    // 首发原生输入共享包依赖：file: 相对路径在临时 stage 无法解析，重写为
    // 仓内 tgz（见 stageNativeInputDependency）；此时锁文件需要 npm 重新生成，
    // 用 install 而不是 ci（其余依赖仍按既有锁定版本解析）
    const nativeInputTarball = stageNativeInputDependency(source, stage, options)
    execFileSync(options.node, [npm, nativeInputTarball ? 'install' : 'ci', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund'], { cwd: stage, timeout: 120000, stdio: 'pipe' })
    if (nativeInputTarball) {
      rmSync(join(stage, nativeInputTarball), { force: true })
      // 重写后的锁文件不回源；manifest 按实际 staged 内容生成
    }
    // npm creates executable wrapper links; these are unnecessary in a node-entry distribution.
    rmSync(join(stage, 'node_modules/.bin'), { recursive: true, force: true })
    const pkg = JSON.parse(readFileSync(join(stage, 'package.json'), 'utf8'))
    delete pkg.scripts; delete pkg.devDependencies; delete pkg.bin
    pkg.engines = { node: '22.23.3' }
    writeFileSync(join(stage, 'package.json'), JSON.stringify(pkg, null, 2) + '\n')
    if (existsSync(join(source, 'provider-manifest.json'))) {
      const identity = JSON.parse(readFileSync(join(source, 'provider-manifest.json'), 'utf8'))
      identity.entrypoint = ['dist/src/cli/index.js', 'mcp', '--stdio']
      writeFileSync(join(stage, 'provider-manifest.json'), JSON.stringify(identity, null, 2) + '\n')
    }
    const reported = JSON.parse(execFileSync(options.node, [join(stage, 'dist/src/cli/index.js'), 'version', '--json'], { cwd: stage, timeout: 30000, encoding: 'utf8' }))
    const manifest = {
      ...reported, manifest_version: 1, display_name: definition.display_name, description: pkg.description,
      distribution: { platform: 'win32', arch: 'x64', node: { version: '22.23.3', modules_abi: '127' }, required_files: filesUnder(stage) },
    }
    writeFileSync(join(stage, 'runtime-manifest.json'), canonicalize(manifest) + '\n')
    const payload = await zipBytes(filesUnder(stage).map(file => [file.path, readFileSync(join(stage, file.path))]))
    const { envelope, archive } = await createOfflinePackage(payload, manifest, {
      release_id: options.release_id ? options.release_id + '-' + name : `dev-${name}-${pkg.version}-${sha256(payload).slice(0, 16)}`,
      key_id: signer.key_id, created_at: options.created_at ?? new Date().toISOString(),
    }, signer.private_key)
    const output = resolve(options.output ?? join(root, 'release'))
    mkdirSync(output, { recursive: true })
    const filename = join(output, `${name}-${pkg.version}${options.test ? '-dev' : ''}.aidplugin.zip`)
    if (existsSync(filename)) throw new Error('Release output already exists; do not overwrite immutable packages')
    writeFileSync(filename, archive, { flag: 'wx' })
    writeFileSync(filename + '.manifest.json', JSON.stringify(manifest, null, 2) + '\n', { flag: 'wx' })
    console.log(JSON.stringify({ provider: name, path: filename, size: archive.length, sha256: sha256(archive), tools: reported.tools.length, envelope }))
  } finally {
    if (dirname(stage) === resolve(tmpdir()) && stage.startsWith(join(resolve(tmpdir()), 'aid-provider-package-'))) rmSync(stage, { recursive: true, force: true })
  }
}

export async function main(argv) {
  const options = {}
  for (let index = 0; index < argv.length; index++) {
    const key = argv[index]
    if (key === '--test') { options.test = true; continue }
    const allowed = { '--provider': 'provider', '--node': 'node', '--npm-cli': 'npm_cli', '--ocr': 'ocr', '--output': 'output', '--signer': 'signer', '--release-id': 'release_id', '--created-at': 'created_at', '--test-key-id': 'test_key_id' }
    if (!Object.hasOwn(allowed, key) || !argv[index + 1]) throw new Error('Invalid packaging arguments')
    options[allowed[key]] = argv[++index]
  }
  let testKeyId
  if (options.test && !options.signer) {
    if (options.test_key_id && !/^aid-isolated-[a-z0-9-]{1,80}$/.test(options.test_key_id)) throw new Error('Invalid isolated test key id')
    testKeyId = assertUnusedAcceptanceTestKeyId(options.test_key_id ?? 'aid-isolated-dev-only')
  }
  if (!options.node || !existsSync(options.node)) throw new Error('Pass the approved fixed Node executable with --node')
  const nodeFacts = JSON.parse(execFileSync(options.node, ['-p', 'JSON.stringify({version:process.versions.node,abi:process.versions.modules,platform:process.platform,arch:process.arch})'], { encoding: 'utf8' }))
  if (nodeFacts.version !== '22.23.3' || nodeFacts.abi !== '127' || nodeFacts.platform !== 'win32' || nodeFacts.arch !== 'x64') throw new Error('Packaging requires approved Node 22.23.3 win32-x64 ABI127')
  let signer
  if (options.test && !options.signer) {
    const pair = generateKeyPairSync('ed25519')
    signer = { key_id: testKeyId, private_key: pair.privateKey.export({ format: 'pem', type: 'pkcs8' }) }
    const output = resolve(options.output ?? join(root, 'release'))
    mkdirSync(output, { recursive: true })
    writeFileSync(join(output, 'ISOLATED-TEST-TRUST-ROOT.json'), JSON.stringify({ schemaVersion: 1, profile: 'acceptance', roots: [{ test_only: true, key_id: signer.key_id, public_key: pair.publicKey.export({ format: 'pem', type: 'spki' }), providers: ['ai.aidwork.boss-recruiting', 'ai.aidwork.weixin', 'ai.aidwork.wecom'] }] }, null, 2) + '\n', { flag: 'wx' })
    writeFileSync(join(output, 'DEVELOPMENT-ONLY.txt'), 'Isolated development packages. Production trust and release acceptance are not provided. CPython 3.12.10 needs release security review.\n', { flag: 'wx' })
  } else {
    if (options.test || !options.signer || !options.release_id) throw new Error('Production signing requires external signer metadata and immutable release-id')
    const metadata = JSON.parse(readFileSync(options.signer, 'utf8'))
    if (!metadata.key_id || !metadata.private_key_path || metadata.key_id.startsWith('aid-isolated-')) throw new Error('Invalid external signer metadata')
    signer = { key_id: metadata.key_id, private_key: readFileSync(resolve(dirname(options.signer), metadata.private_key_path)) }
    createPublicKey(createPrivateKey(signer.private_key)) // validate without writing/logging the private key
  }
  for (const name of options.provider ? [options.provider] : Object.keys(definitions)) await buildProvider(name, options, signer)
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main(process.argv.slice(2)).catch(error => { console.error('Package build failed:', error.message); process.exitCode = 1 })
