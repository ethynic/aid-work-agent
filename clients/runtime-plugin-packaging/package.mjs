import { createHash, createPrivateKey, createPublicKey, generateKeyPairSync, sign } from 'node:crypto'
import { cpSync, existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { dirname, join, resolve } from 'node:path'
import { tmpdir } from 'node:os'
import { fileURLToPath } from 'node:url'
import canonicalize from 'canonicalize'
import yazl from 'yazl'

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
        mkdirSync(join(stage, assets))
        for (const file of readdirSync(join(source, assets)).filter(file => file.endsWith('.ps1'))) cpSync(join(source, assets, file), join(stage, assets, file))
      } else cpSync(join(source, assets), join(stage, assets), { recursive: true })
    }
    if (definition.ocr) {
      const ocr = resolve(options.ocr ?? join(root, 'assets/ocr-python-final'))
      if (!existsSync(join(ocr, 'python.exe')) || existsSync(join(ocr, 'pyvenv.cfg')) || !existsSync(join(ocr, 'asset-provenance.json')) || !existsSync(join(ocr, 'msvcp140.dll')) || !existsSync(join(ocr, 'msvcp140_1.dll'))) throw new Error('Verified embedded OCR input with app-local CRT required')
      filesUnder(ocr) // reject links before copying or executing
      cpSync(ocr, join(stage, 'ocr-python'), { recursive: true })
      const env = { ...process.env }; delete env.PYTHONHOME; delete env.PYTHONPATH
      execFileSync(join(stage, 'ocr-python/python.exe'), ['-I', '-B', '-c', 'from rapidocr_onnxruntime import RapidOCR; from PIL import Image; RapidOCR(); print("OCR_BUNDLE_OK")'], { cwd: stage, env, timeout: 90000 })
    }
    cpSync(join(source, 'package.json'), join(stage, 'package.json'))
    cpSync(join(source, 'package-lock.json'), join(stage, 'package-lock.json'))
    const npm = options.npm_cli
    if (!npm || !existsSync(npm)) throw new Error('Pass --npm-cli with the build machine npm CLI path')
    execFileSync(options.node, [npm, 'ci', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund'], { cwd: stage, timeout: 120000, stdio: 'pipe' })
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
    const allowed = { '--provider': 'provider', '--node': 'node', '--npm-cli': 'npm_cli', '--ocr': 'ocr', '--output': 'output', '--signer': 'signer', '--release-id': 'release_id', '--created-at': 'created_at' }
    if (!Object.hasOwn(allowed, key) || !argv[index + 1]) throw new Error('Invalid packaging arguments')
    options[allowed[key]] = argv[++index]
  }
  if (!options.node || !existsSync(options.node)) throw new Error('Pass the approved fixed Node executable with --node')
  const nodeFacts = JSON.parse(execFileSync(options.node, ['-p', 'JSON.stringify({version:process.versions.node,abi:process.versions.modules,platform:process.platform,arch:process.arch})'], { encoding: 'utf8' }))
  if (nodeFacts.version !== '22.23.3' || nodeFacts.abi !== '127' || nodeFacts.platform !== 'win32' || nodeFacts.arch !== 'x64') throw new Error('Packaging requires approved Node 22.23.3 win32-x64 ABI127')
  let signer
  if (options.test && !options.signer) {
    const pair = generateKeyPairSync('ed25519')
    signer = { key_id: 'aid-isolated-dev-only', private_key: pair.privateKey.export({ format: 'pem', type: 'pkcs8' }) }
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
