/**
 * Windows 打包脚本（精简版）。
 * - dev 模式：不签名（CSC_IDENTITY_AUTO_DISCOVERY=false），artifactName 带 -dev-unsigned
 * - release 模式：必须签名（forceCodeSigning），artifactName 无后缀（首版未接入签名证书，留扩展点）
 * - 产物：NSIS 安装包 + release-manifest.json
 * - 输入摘要防打包中被改；签名状态用 PowerShell 校验
 *
 * 用法：node scripts/package-win.mjs dev|release
 */
import { createHash } from 'node:crypto'
import { existsSync, mkdtempSync, readFileSync, readdirSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'

const mode = process.argv[2]
if (mode !== 'dev' && mode !== 'release') {
  console.error('usage: node scripts/package-win.mjs dev|release')
  process.exit(1)
}
if (mode === 'release' && !process.env.CSC_LINK && !process.env.WIN_CSC_LINK) {
  console.error('release mode requires CSC_LINK / WIN_CSC_LINK (signing certificate)')
  process.exit(1)
}

const packageJson = JSON.parse(readFileSync('package.json', 'utf8'))
const isRelease = mode === 'release'

const temporaryPackagingRoot = mkdtempSync(path.join(os.tmpdir(), 'boss-resume-package-'))
const builderConfig = path.join(temporaryPackagingRoot, 'electron-builder.json')
writeFileSync(
  builderConfig,
  `${JSON.stringify({ extends: path.resolve('electron-builder.yml') }, null, 2)}\n`,
  { flag: 'wx', mode: 0o600 },
)

const artifactName = isRelease
  ? `BOSS-Resume-Assistant-${packageJson.version}-win-x64.${'${ext}'}`
  : `BOSS-Resume-Assistant-${packageJson.version}-win-x64-dev-unsigned.${'${ext}'}`

const builderArguments = [
  path.resolve('node_modules/electron-builder/out/cli/cli.js'),
  '--win', 'nsis', '--x64',
  '--config', builderConfig,
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
    'dist/src/main',
    'dist/src/shared',
    'dist/db',
    'dist/renderer',
  ].filter((r) => existsSync(r))
  const files = roots.flatMap((entry) => (statSync(entry).isDirectory() ? listFiles(entry) : [path.resolve(entry)]))
  const hash = createHash('sha256')
  for (const file of files) {
    hash.update(path.relative(process.cwd(), file).replaceAll('\\', '/'))
    hash.update('\0')
    hash.update(readFileSync(file))
    hash.update('\0')
  }
  return hash.digest('hex')
}

// better-sqlite3 native 必须已为 electron ABI 编译
const nativeNode = path.resolve('node_modules/better-sqlite3/build/Release/better_sqlite3.node')
if (!existsSync(nativeNode)) {
  console.error('better-sqlite3 native binary missing — run "npm run rebuild" first')
  rmSync(temporaryPackagingRoot, { recursive: true, force: true })
  process.exit(1)
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
if (!existsSync(installer) || !statSync(installer).isFile()) {
  throw new Error(`expected NSIS installer was not produced: ${installerName}`)
}
const unpackedAsar = path.join(releaseDirectory, 'win-unpacked', 'resources', 'app.asar')
if (!existsSync(unpackedAsar) || !statSync(unpackedAsar).isFile()) {
  throw new Error('win-unpacked app.asar was not produced')
}

const expectedSignature = isRelease ? 'Valid' : 'NotSigned'
const signatureCommand = `(Get-AuthenticodeSignature -LiteralPath '${installer.replaceAll("'", "''")}').Status.ToString()`
const signature = spawnSync('powershell.exe', ['-NoProfile', '-Command', signatureCommand], { encoding: 'utf8' })
const signatureStatus = signature.stdout.trim()
if (signatureStatus !== expectedSignature) {
  throw new Error(`${mode} signature status mismatch: ${signatureStatus || '<missing>'} (expected ${expectedSignature})`)
}

const git = (...args) => {
  const r = spawnSync('git', args, { cwd: path.resolve('../..'), encoding: 'utf8' })
  return r.stdout.trim()
}
const bytes = readFileSync(installer)
const manifest = {
  schemaVersion: 1,
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
}

function writeJsonAtomic(file, value) {
  const temporary = `${file}.${process.pid}.tmp`
  writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, { flag: 'wx' })
  renameSync(temporary, file)
}
writeJsonAtomic(path.join(releaseDirectory, 'release-manifest.json'), manifest)

console.log(`PACKAGE_WIN_${mode.toUpperCase()}_PASS:${installer}`)
