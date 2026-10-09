import { createHash, createPublicKey } from 'node:crypto'
import { cpSync, existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { execFileSync, spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

export const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
export const resourcesRoot = path.join(desktopRoot, 'build/runtime-resources')
export const nodeSpecification = JSON.parse(readFileSync(path.join(desktopRoot, 'scripts/runtime-node-resources.json'), 'utf8'))
export const digest = bytes => createHash('sha256').update(bytes).digest('hex')
// app-builder-lib uses endsWith, not glob matching. Exclude this exact Windows
// resource suffix while retaining its default application/installer signing.
export const runtimeNodeSignExclusion = '!\\runtime\\node\\win-x64\\node.exe'

export function authenticodeStatus(file, environment = process.env) {
  // PowerShell 7 module paths cannot be used by Windows PowerShell 5.1.
  const env = Object.fromEntries(Object.entries(environment).filter(([key]) => key.toLowerCase() !== 'psmodulepath'))
  const command = `$ErrorActionPreference='Stop'; Import-Module (Join-Path $PSHOME 'Modules\\Microsoft.PowerShell.Security\\Microsoft.PowerShell.Security.psd1') -Force; (Microsoft.PowerShell.Security\\Get-AuthenticodeSignature -LiteralPath '${file.replaceAll("'", "''")}').Status.ToString()`
  const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', command], { encoding: 'utf8', windowsHide: true, env })
  if (result.error || result.status !== 0 || !result.stdout.trim()) throw new Error('Windows Authenticode verification command failed')
  return result.stdout.trim()
}

export function validateReleaseInputs(manifest) {
  const seen = new Map()
  if (!Array.isArray(manifest.inputs) || !manifest.inputs.length || !Array.isArray(manifest.packagedInputs) || !manifest.packagedInputs.length) throw new Error('Runtime release input records missing')
  for (const item of manifest.inputs) {
    if (!item || Object.keys(item).sort().join(',') !== 'path,sha256' || typeof item.path !== 'string' || item.path.includes('\\') || item.path.includes(':')
        || item.path.split('/').some(part => !part || part === '.' || part === '..') || item.path.startsWith('/')
        || !/^[a-f0-9]{64}$/.test(item.sha256 ?? '') || seen.has(item.path)) throw new Error('Runtime release input record invalid or duplicate')
    seen.set(item.path, item.sha256)
  }
  if (digest(Buffer.from(JSON.stringify(manifest.inputs))) !== manifest.buildInputSha256) throw new Error('Runtime release build input fingerprint mismatch')
  const packaged = new Map()
  for (const item of manifest.packagedInputs) {
    if (!item || Object.keys(item).sort().join(',') !== 'path,sha256' || typeof item.path !== 'string' || !/^(?:resources\/(?:config|runtime)\/|dist\/)/.test(item.path)
        || !seen.has(item.path) || !/^[a-f0-9]{64}$/.test(item.sha256 ?? '') || seen.get(item.path) !== item.sha256 || packaged.has(item.path)) throw new Error('Runtime packaged input record invalid or duplicate')
    packaged.set(item.path, item.sha256)
  }
  for (const [file, sha] of seen) {
    if (file.startsWith('resources/') && packaged.get(file) !== sha) throw new Error('Runtime resource input omitted from packaged records')
  }
  return packaged
}

export function filesUnder(root, prefix = '') {
  return readdirSync(root).sort().flatMap(name => {
    const file = path.join(root, name)
    const stat = lstatSync(file)
    if (stat.isSymbolicLink() || (!stat.isFile() && !stat.isDirectory())) throw new Error('Runtime resources contain link or special file')
    return stat.isDirectory() ? filesUnder(file, prefix + name + '/') : [{ path: prefix + name, size: stat.size, sha256: digest(readFileSync(file)) }]
  })
}

export function normalizeTrust(input, profile) {
  const trust = input
  if (!trust || Object.keys(trust).sort().join(',') !== 'profile,roots,schemaVersion' || trust.schemaVersion !== 1 || trust.profile !== profile
      || !Array.isArray(trust.roots) || !trust.roots.length) throw new Error('Matching explicit publisher trust required; no fallback')
  const keys = new Set()
  for (const root of trust.roots) {
    if (!root || Object.keys(root).sort().join(',') !== 'key_id,providers,public_key,test_only' || typeof root.key_id !== 'string' || !root.key_id.trim()
        || typeof root.test_only !== 'boolean' || keys.has(root.key_id)
        || typeof root.public_key !== 'string' || !/^-----BEGIN PUBLIC KEY-----\r?\n(?:[A-Za-z0-9+/]{1,64}={0,2}\r?\n)+-----END PUBLIC KEY-----\r?\n?$/.test(root.public_key)
        || !Array.isArray(root.providers) || !root.providers.length
        || root.providers.some(provider => typeof provider !== 'string' || !provider.trim())
        || createPublicKey(root.public_key).asymmetricKeyType !== 'ed25519') throw new Error('Invalid publisher trust')
    if (profile === 'production' && root.test_only) throw new Error('Production build cannot contain test publisher trust')
    keys.add(root.key_id)
  }
  return trust
}

function safeRemoveBuild(file) {
  const build = path.join(desktopRoot, 'build')
  const target = path.resolve(file)
  if (!target.startsWith(build + path.sep)) throw new Error('Refusing removal outside Desktop build directory')
  rmSync(target, { recursive: true, force: true })
}

export function nodeFacts(node) {
  if (digest(readFileSync(node)) !== nodeSpecification.executableSha256) throw new Error('Bundled Node executable SHA-256 differs from verified official archive')
  const facts = JSON.parse(execFileSync(node, ['-p', 'JSON.stringify({version:process.versions.node,platform:process.platform,arch:process.arch,abi:process.versions.modules})'], { encoding: 'utf8', windowsHide: true }))
  if (facts.version !== nodeSpecification.version || facts.platform !== nodeSpecification.platform || facts.arch !== nodeSpecification.arch || facts.abi !== nodeSpecification.modulesAbi) throw new Error('Bundled Node facts do not match locked resource specification')
  return facts
}

export function assertNoSensitiveAssets(root) {
  const containsPrivateKey = text => /-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----[\t ]*\r?\n[A-Za-z0-9+/=\r\n]+-----END (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----/.test(text)
  const checkValue = value => {
    if (typeof value === 'string') return containsPrivateKey(value)
    if (Array.isArray(value)) return value.some(checkValue)
    return value !== null && typeof value === 'object' && Object.values(value).some(checkValue)
  }
  for (const file of filesUnder(root)) {
    if (/(?:^|\/)(?:\.env(?:\..*)?|credentials\.bin|management-secret\.bin|id_rsa|id_ed25519)$/i.test(file.path)) throw new Error('Runtime assets contain credential file')
    const text = readFileSync(path.join(root, file.path)).toString('utf8')
    if (containsPrivateKey(text)) throw new Error('Runtime assets contain private key material')
    if (file.path.endsWith('.json')) {
      let value
      try { value = JSON.parse(text) } catch { continue }
      if (checkValue(value)) throw new Error('Runtime assets contain private key material')
    }
  }
}

export function verifyHostDependencyGraph(node, entry) {
  nodeFacts(node)
  // argv[1] is absent for --eval; managed-entry's CLI guard cannot start a Host.
  execFileSync(node, ['--input-type=module', '--eval', `await import(${JSON.stringify(pathToFileURL(entry).href)})`], {
    windowsHide: true, encoding: 'utf8', stdio: 'pipe', timeout: 30000,
    env: { ...process.env, NODE_OPTIONS: '', NODE_PATH: '' },
  })
}

async function prepareNode(options) {
  if (process.platform !== 'win32' || process.arch !== 'x64') throw new Error('Runtime packaging requires Windows x64')
  const cache = path.join(desktopRoot, 'build/runtime-cache')
  mkdirSync(cache, { recursive: true })
  const archive = options['node-archive'] ? path.resolve(options['node-archive']) : path.join(cache, nodeSpecification.archive)
  if (!existsSync(archive)) {
    if (options['node-archive']) throw new Error('Explicit Node archive missing')
    const response = await fetch(nodeSpecification.url)
    if (!response.ok) throw new Error('Official fixed Node download failed')
    const bytes = Buffer.from(await response.arrayBuffer())
    if (digest(bytes) !== nodeSpecification.sha256) throw new Error('Downloaded Node SHA-256 mismatch')
    writeFileSync(archive, bytes, { flag: 'wx' })
  }
  if (digest(readFileSync(archive)) !== nodeSpecification.sha256) throw new Error('Node archive SHA-256 mismatch')
  const extracted = path.join(cache, `node-v${nodeSpecification.version}-win-x64`)
  if (existsSync(extracted)) safeRemoveBuild(extracted)
  const literal = value => "'" + value.replaceAll("'", "''") + "'"
  execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', `Expand-Archive -LiteralPath ${literal(archive)} -DestinationPath ${literal(cache)}`], { windowsHide: true })
  const node = path.join(extracted, 'node.exe')
  nodeFacts(node)
  return { node, license: path.join(extracted, 'LICENSE'), archive }
}

function extractHost(archive, stage) {
  const listing = execFileSync('tar', ['-tzf', archive], { encoding: 'utf8', windowsHide: true, maxBuffer: 8 * 1024 * 1024 }).trim().split(/\r?\n/)
  if (listing.some(entry => !entry.startsWith('package/') || entry.includes('\\') || entry.split('/').includes('..') || entry.includes(':'))) throw new Error('Host archive contains invalid path')
  const details = execFileSync('tar', ['-tvzf', archive], { encoding: 'utf8', windowsHide: true, maxBuffer: 8 * 1024 * 1024 }).trim().split(/\r?\n/)
  if (details.some(entry => !/^[-d]/.test(entry))) throw new Error('Host archive contains links or special files')
  execFileSync('tar', ['-xzf', archive, '-C', stage], { windowsHide: true })
  const source = path.join(stage, 'package')
  filesUnder(source)
  const host = path.join(stage, 'resources/runtime/host')
  cpSync(path.join(source, 'dist/src'), host, { recursive: true })
  cpSync(path.join(source, 'node_modules'), path.join(host, 'node_modules'), { recursive: true })
  cpSync(path.join(source, 'package.json'), path.join(host, 'package.json'))
  assertNoSensitiveAssets(host)
  const pkg = JSON.parse(readFileSync(path.join(host, 'package.json'), 'utf8'))
  if (pkg.type !== 'module' || !existsSync(path.join(host, 'managed-entry.js'))) throw new Error('Host package missing ESM metadata or managed entry')
  return pkg.version
}

export async function main(argv) {
  const options = {}
  for (let index = 0; index < argv.length; index++) {
    const key = argv[index]
    if (key === '--node-only') { options['node-only'] = true; continue }
    if (!['--profile', '--host-package', '--host-sha256', '--trust-roots', '--node-archive'].includes(key) || !argv[index + 1] || Object.hasOwn(options, key.slice(2))) throw new Error('Invalid prepare-runtime arguments')
    options[key.slice(2)] = argv[++index]
  }
  let trustBytes, hostBytes
  if (!options['node-only']) {
    if (!['acceptance', 'production'].includes(options.profile) || !options['host-package'] || !/^[a-f0-9]{64}$/.test(options['host-sha256'] ?? '') || !options['trust-roots']) throw new Error('Explicit profile, Host package/digest and publisher trust required')
    hostBytes = readFileSync(path.resolve(options['host-package']))
    if (digest(hostBytes) !== options['host-sha256']) throw new Error('Host package SHA-256 mismatch')
    trustBytes = readFileSync(path.resolve(options['trust-roots']))
    normalizeTrust(JSON.parse(trustBytes.toString('utf8')), options.profile)
  }
  const node = await prepareNode(options)
  if (options['node-only']) { console.log(JSON.stringify({ node: node.node, facts: nodeFacts(node.node), archiveSha256: nodeSpecification.sha256 })); return }
  const stage = mkdtempSync(path.join(desktopRoot, 'build/runtime-stage-'))
  try {
    // Extract the exact verified bytes, even if another build replaces its source.
    const snapshot = path.join(stage, 'host.tgz')
    writeFileSync(snapshot, hostBytes, { flag: 'wx' })
    const version = extractHost(snapshot, stage)
    const resources = path.join(stage, 'resources')
    const nodeFolder = path.join(resources, 'runtime/node/win-x64')
    mkdirSync(nodeFolder, { recursive: true })
    cpSync(node.node, path.join(nodeFolder, 'node.exe'))
    cpSync(node.license, path.join(nodeFolder, 'LICENSE'))
    writeFileSync(path.join(resources, 'runtime/trust-roots.json'), trustBytes, { flag: 'wx' })
    mkdirSync(path.join(resources, 'config'), { recursive: true })
    writeFileSync(path.join(resources, 'config/runtime-product.json'), JSON.stringify({ schemaVersion: 1, productKind: 'runtime', profile: options.profile }, null, 2) + '\n')
    assertNoSensitiveAssets(resources)
    verifyHostDependencyGraph(path.join(nodeFolder, 'node.exe'), path.join(resources, 'runtime/host/managed-entry.js'))
    const manifest = {
      schemaVersion: 1, profile: options.profile, hostVersion: version, hostPackageSha256: options['host-sha256'], node: nodeSpecification,
      files: filesUnder(resources),
    }
    writeFileSync(path.join(resources, 'runtime/resource-manifest.json'), JSON.stringify(manifest, null, 2) + '\n')
    if (existsSync(resourcesRoot)) safeRemoveBuild(resourcesRoot)
    renameSync(resources, resourcesRoot)
    console.log(JSON.stringify({ resourcesPath: resourcesRoot, profile: options.profile, hostVersion: version, files: manifest.files.length }))
  } finally { safeRemoveBuild(stage) }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main(process.argv.slice(2)).catch(error => { console.error('Runtime resources preparation failed:', error.message); process.exitCode = 1 })
