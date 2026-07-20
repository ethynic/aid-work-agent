import { createHash } from 'node:crypto'
import { existsSync, mkdtempSync, readFileSync, readdirSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { assertPackageInvocation, assertReleaseSigning, expectedSignature, isValidReleaseVersion } from '../dist/electron/releasePolicy.js'
import { normalizeApiBaseUrl } from '../dist/electron/security.js'
import { normalizeUpdateBaseUrl } from '../dist/electron/updateConfiguration.js'

const mode = process.argv[2]
assertPackageInvocation(mode, process.argv.slice(3))
assertReleaseSigning(mode, process.env)
const packageJson = JSON.parse(readFileSync('package.json', 'utf8'))
if (!isValidReleaseVersion(packageJson.version)) throw new Error('package version is not release-compatible semver')
const packagedApiBaseUrl = normalizeApiBaseUrl(process.env.AID_AGENT_PACKAGE_API_BASE_URL ?? '')
const isRelease = mode === 'release'
const packagedUpdateBaseUrl = isRelease ? normalizeUpdateBaseUrl(process.env.AID_AGENT_UPDATE_BASE_URL ?? '') : null

const temporaryPackagingRoot = mkdtempSync(path.join(os.tmpdir(), 'aidagent-package-'))
const packagedConfig = path.join(temporaryPackagingRoot, 'desktop-config.json')
const packagedUpdateConfig = path.join(temporaryPackagingRoot, 'update-config.json')
const builderConfig = path.join(temporaryPackagingRoot, 'electron-builder.json')
try {
  writeFileSync(packagedConfig, `${JSON.stringify({ schemaVersion: 1, apiBaseUrl: packagedApiBaseUrl }, null, 2)}\n`, { flag: 'wx', mode: 0o600 })
  writeFileSync(packagedUpdateConfig, `${JSON.stringify({
    schemaVersion: 1,
    channel: isRelease ? 'release' : 'development-unsigned',
    updateBaseUrl: packagedUpdateBaseUrl,
  }, null, 2)}\n`, { flag: 'wx', mode: 0o600 })
  writeFileSync(builderConfig, `${JSON.stringify({
    extends: path.resolve('electron-builder.yml'),
    extraResources: [
      { from: packagedConfig, to: 'config/desktop-config.json' },
      { from: packagedUpdateConfig, to: 'config/update-config.json' },
    ],
    ...(isRelease ? { publish: [{ provider: 'generic', url: packagedUpdateBaseUrl }] } : {}),
  }, null, 2)}\n`, { flag: 'wx', mode: 0o600 })
} catch (error) {
  rmSync(temporaryPackagingRoot, { recursive: true, force: true })
  throw error
}

const artifactName = isRelease
  ? `AID-Work-Agent-${packageJson.version}-win-x64.${'${ext}'}`
  : `AID-Work-Agent-${packageJson.version}-win-x64-dev-unsigned.${'${ext}'}`
const builderArguments = [
  path.resolve('node_modules/electron-builder/out/cli/cli.js'), '--win', 'nsis', '--x64', '--config', builderConfig,
  `--config.win.artifactName=${artifactName}`,
]
if (isRelease) builderArguments.push('--config.forceCodeSigning=true')

const releaseDirectory = path.resolve('release')
rmSync(releaseDirectory, { recursive: true, force: true })

function listFiles(root) {
  const result = []
  const visit = (directory) => {
    for (const entry of readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const file = path.join(directory, entry.name)
      if (entry.isDirectory()) visit(file)
      else if (entry.isFile()) result.push(file)
    }
  }
  visit(root)
  return result
}

function buildInputDigest() {
  const roots = [
    'electron-builder.yml',
    'package.json',
    'package-lock.json',
    'scripts/package-win.mjs',
    'scripts/verify-package.mjs',
    'dist/electron',
    'dist/renderer',
  ]
  const files = roots.flatMap((entry) => statSync(entry).isDirectory() ? listFiles(entry) : [path.resolve(entry)])
  const hash = createHash('sha256')
  hash.update(readFileSync(packagedConfig))
  hash.update('\0')
  hash.update(readFileSync(packagedUpdateConfig))
  hash.update('\0')
  for (const file of files) {
    hash.update(path.relative(process.cwd(), file).replaceAll('\\', '/'))
    hash.update('\0')
    hash.update(readFileSync(file))
    hash.update('\0')
  }
  return hash.digest('hex')
}

function writeJsonAtomic(file, value) {
  const temporary = `${file}.${process.pid}.tmp`
  try {
    writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, { flag: 'wx' })
    renameSync(temporary, file)
  } finally {
    rmSync(temporary, { force: true })
  }
}

function parseCommandJson(label, result, allowedStatuses = [0]) {
  if (result.error) throw result.error
  let value
  try {
    value = JSON.parse(result.stdout)
  } catch {
    throw new Error(`${label} did not return valid JSON (exit ${result.status ?? 'unknown'})`)
  }
  if (!allowedStatuses.includes(result.status) || value?.error) {
    throw new Error(`${label} failed (exit ${result.status ?? 'unknown'})`)
  }
  return value
}

const inputSha256 = buildInputDigest()
let builder
try {
  builder = spawnSync(process.execPath, builderArguments, {
    stdio: 'inherit',
    env: { ...process.env, CSC_IDENTITY_AUTO_DISCOVERY: isRelease ? 'true' : 'false' },
  })
  if (builder.error) throw builder.error
  if (builder.status !== 0) throw new Error(`electron-builder failed with exit ${builder.status ?? 'unknown'}`)
  if (buildInputDigest() !== inputSha256) throw new Error('packaging inputs changed while electron-builder was running')
} finally {
  rmSync(temporaryPackagingRoot, { recursive: true, force: true })
}

const installerName = artifactName.replace('${ext}', 'exe')
const installer = path.join(releaseDirectory, installerName)
if (!existsSync(installer) || !statSync(installer).isFile()) throw new Error(`expected NSIS installer was not produced: ${installerName}`)
const unpackedAsar = path.join(releaseDirectory, 'win-unpacked', 'resources', 'app.asar')
if (!existsSync(unpackedAsar) || !statSync(unpackedAsar).isFile()) throw new Error('win-unpacked app.asar was not produced')
const unpackedConfig = path.join(releaseDirectory, 'win-unpacked', 'resources', 'config', 'desktop-config.json')
if (!existsSync(unpackedConfig) || !statSync(unpackedConfig).isFile()) throw new Error('packaged desktop config was not produced')
const verifiedPackagedConfig = JSON.parse(readFileSync(unpackedConfig, 'utf8'))
if (verifiedPackagedConfig.schemaVersion !== 1 || verifiedPackagedConfig.apiBaseUrl !== packagedApiBaseUrl) throw new Error('packaged desktop config verification failed')
const unpackedUpdateConfig = path.join(releaseDirectory, 'win-unpacked', 'resources', 'config', 'update-config.json')
if (!existsSync(unpackedUpdateConfig) || !statSync(unpackedUpdateConfig).isFile()) throw new Error('packaged update config was not produced')
const verifiedUpdateConfig = JSON.parse(readFileSync(unpackedUpdateConfig, 'utf8'))
if (verifiedUpdateConfig.schemaVersion !== 1 || verifiedUpdateConfig.channel !== (isRelease ? 'release' : 'development-unsigned') || verifiedUpdateConfig.updateBaseUrl !== packagedUpdateBaseUrl) {
  throw new Error('packaged update config verification failed')
}

const signatureCommand = `(Get-AuthenticodeSignature -LiteralPath '${installer.replaceAll("'", "''")}').Status.ToString()`
const signature = spawnSync('powershell.exe', ['-NoProfile', '-Command', signatureCommand], { encoding: 'utf8' })
const signatureStatus = signature.stdout.trim()
if (signatureStatus !== expectedSignature(mode)) {
  throw new Error(`${mode} signature status mismatch: ${signatureStatus || '<missing>'}`)
}

const git = (...args) => spawnSync('git', args, { cwd: '../..', encoding: 'utf8' }).stdout.trim()
const bytes = readFileSync(installer)
const manifest = {
  schemaVersion: 2,
  channel: isRelease ? 'release' : 'development-unsigned',
  version: packageJson.version,
  platform: 'win32',
  arch: 'x64',
  fileName: path.basename(installer),
  size: bytes.byteLength,
  sha256: createHash('sha256').update(bytes).digest('hex'),
  commit: git('rev-parse', 'HEAD'),
  dirty: Boolean(git('status', '--porcelain')),
  signatureStatus,
  buildInputSha256: inputSha256,
  apiBaseUrl: packagedApiBaseUrl,
  updateBaseUrl: packagedUpdateBaseUrl,
  updateMetadata: isRelease ? {
    latestYml: 'latest.yml',
    blockmap: `${path.basename(installer)}.blockmap`,
  } : null,
}
writeJsonAtomic(path.join(releaseDirectory, 'release-manifest.json'), manifest)

const npmCliCandidates = [
  process.env.npm_execpath,
  path.resolve(path.dirname(process.execPath), 'node_modules/npm/bin/npm-cli.js'),
  process.env.APPDATA ? path.resolve(process.env.APPDATA, 'npm/node_modules/npm/bin/npm-cli.js') : undefined,
].filter(Boolean)
const npmCli = npmCliCandidates.find((candidate) => existsSync(candidate) && statSync(candidate).isFile())
if (!npmCli) throw new Error('npm CLI path is unavailable')
const sbom = spawnSync(process.execPath, [npmCli, 'sbom', '--sbom-format', 'cyclonedx'], { encoding: 'utf8' })
const sbomReport = parseCommandJson('npm sbom', sbom)
writeJsonAtomic(path.join(releaseDirectory, 'sbom.cdx.json'), sbomReport)
// Installation may use a dependency mirror that does not implement npm's security API.
// Audit against npm's canonical advisory endpoint so a mirror 404 is never reported as zero vulnerabilities.
const audit = spawnSync(process.execPath, [npmCli, 'audit', '--json', '--registry=https://registry.npmjs.org'], { encoding: 'utf8' })
const auditReport = parseCommandJson('npm audit', audit, [0, 1])
if (!auditReport?.metadata?.vulnerabilities) throw new Error('npm audit report is missing vulnerability metadata')
writeJsonAtomic(path.join(releaseDirectory, 'npm-audit.json'), auditReport)
const lock = JSON.parse(readFileSync('package-lock.json', 'utf8'))
const licenses = Object.entries(lock.packages ?? {}).filter(([key]) => key).map(([key, value]) => ({
  package: key.replace(/^node_modules\//, ''), version: value.version, license: value.license ?? 'UNKNOWN'
}))
writeJsonAtomic(path.join(releaseDirectory, 'licenses.json'), licenses)

const verify = spawnSync(process.execPath, ['scripts/verify-package.mjs', releaseDirectory, mode], { stdio: 'inherit' })
if (verify.status !== 0) process.exit(verify.status ?? 1)
console.log(`PACKAGE_WIN_${mode.toUpperCase()}_PASS:${installer}`)
