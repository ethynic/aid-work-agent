import { createHash } from 'node:crypto'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, readFileSync, readdirSync, rmSync, statSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { extractAll, listPackage } from '@electron/asar'
import { load as loadYaml } from 'js-yaml'
import { expectedSignature } from '../dist/electron/releasePolicy.js'
import { normalizeApiBaseUrl } from '../dist/electron/security.js'
import { normalizeUpdateBaseUrl } from '../dist/electron/updateConfiguration.js'

const releaseDirectory = path.resolve(process.argv[2] ?? 'release')
const mode = process.argv[3] ?? 'dev'
const asarPath = path.join(releaseDirectory, 'win-unpacked', 'resources', 'app.asar')
const files = listPackage(asarPath).map((file) => file.replaceAll('\\', '/'))
for (const required of ['/dist/electron/main.js', '/dist/electron/preload.cjs', '/dist/electron/apiConfiguration.js', '/dist/electron/desktopUpdater.js', '/dist/electron/updateConfiguration.js', '/dist/renderer/index.html']) {
  if (!files.includes(required)) throw new Error(`package missing required runtime file: ${required}`)
}
for (const file of files) {
  if ((!file.startsWith('/node_modules/') && /\/(tests?|docs?|src)\//i.test(file)) || /\.env/i.test(file)) {
    throw new Error(`package contains forbidden file: ${file}`)
  }
}

const temporary = mkdtempSync(path.join(os.tmpdir(), 'aidagent-package-'))
try {
  extractAll(asarPath, temporary)
  const renderer = path.join(temporary, 'dist', 'renderer')
  const stack = [renderer]
  const forbidden = /portal_token|portalRoutes|TenantDashboard|AgentDefinitionManager|RpaBindingPanel|BEGIN (RSA |EC )?PRIVATE KEY/i
  while (stack.length) {
    const current = stack.pop()
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      const file = path.join(current, entry.name)
      if (entry.isDirectory()) stack.push(file)
      else if (/\.(js|html|json)$/i.test(entry.name) && forbidden.test(readFileSync(file, 'utf8'))) {
        throw new Error(`package renderer contains forbidden Portal/credential marker: ${entry.name}`)
      }
    }
  }
} finally {
  rmSync(temporary, { recursive: true, force: true })
}

const manifest = JSON.parse(readFileSync(path.join(releaseDirectory, 'release-manifest.json'), 'utf8'))
if (manifest.schemaVersion !== 2 || manifest.platform !== 'win32' || manifest.arch !== 'x64') throw new Error('release manifest schema/platform mismatch')
if (!/^[a-f0-9]{64}$/.test(manifest.buildInputSha256 ?? '')) throw new Error('release manifest build input fingerprint missing')
const installer = path.join(releaseDirectory, manifest.fileName)
const digest = createHash('sha256').update(readFileSync(installer)).digest('hex')
if (digest !== manifest.sha256 || statSync(installer).size !== manifest.size) throw new Error('release manifest hash/size mismatch')
const signatureCommand = `(Get-AuthenticodeSignature -LiteralPath '${installer.replaceAll("'", "''")}').Status.ToString()`
const signature = spawnSync('powershell.exe', ['-NoProfile', '-Command', signatureCommand], { encoding: 'utf8' })
if (signature.error || signature.status !== 0) throw new Error('installer signature verification command failed')
const signatureStatus = signature.stdout.trim()
if (signatureStatus !== expectedSignature(mode) || manifest.signatureStatus !== signatureStatus) {
  throw new Error('installer signature and release manifest policy mismatch')
}
const packagedConfigPath = path.join(releaseDirectory, 'win-unpacked', 'resources', 'config', 'desktop-config.json')
const packagedConfig = JSON.parse(readFileSync(packagedConfigPath, 'utf8'))
const packagedConfigKeys = Object.keys(packagedConfig).sort()
if (packagedConfigKeys.length !== 2 || packagedConfigKeys[0] !== 'apiBaseUrl' || packagedConfigKeys[1] !== 'schemaVersion') {
  throw new Error('packaged desktop config schema mismatch')
}
if (packagedConfig.schemaVersion !== 1 || normalizeApiBaseUrl(packagedConfig.apiBaseUrl) !== packagedConfig.apiBaseUrl) {
  throw new Error('packaged desktop config value is invalid')
}
if (manifest.apiBaseUrl !== packagedConfig.apiBaseUrl) throw new Error('release manifest API base URL mismatch')
const packagedUpdateConfig = JSON.parse(readFileSync(path.join(releaseDirectory, 'win-unpacked', 'resources', 'config', 'update-config.json'), 'utf8'))
const updateConfigKeys = Object.keys(packagedUpdateConfig).sort()
if (updateConfigKeys.join(',') !== 'channel,schemaVersion,updateBaseUrl' || packagedUpdateConfig.schemaVersion !== 1) {
  throw new Error('packaged update config schema mismatch')
}
if (mode === 'release') {
  const normalizedUpdateBaseUrl = normalizeUpdateBaseUrl(packagedUpdateConfig.updateBaseUrl)
  if (manifest.updateBaseUrl !== normalizedUpdateBaseUrl) throw new Error('release manifest update URL mismatch')
  const appUpdatePath = path.join(releaseDirectory, 'win-unpacked', 'resources', 'app-update.yml')
  const appUpdate = loadYaml(readFileSync(appUpdatePath, 'utf8'))
  if (!appUpdate || typeof appUpdate !== 'object' || Array.isArray(appUpdate)) throw new Error('app-update.yml schema mismatch')
  const appUpdateRecord = appUpdate
  const publisherNames = Array.isArray(appUpdateRecord.publisherName)
    ? appUpdateRecord.publisherName
    : [appUpdateRecord.publisherName]
  if (appUpdateRecord.provider !== 'generic' || normalizeUpdateBaseUrl(appUpdateRecord.url) !== normalizedUpdateBaseUrl) {
    throw new Error('app-update.yml provider or URL mismatch')
  }
  if (publisherNames.length === 0 || publisherNames.some((name) => typeof name !== 'string' || !name.trim())) {
    throw new Error('app-update.yml must pin the Authenticode publisher name')
  }
  const latestPath = path.join(releaseDirectory, 'latest.yml')
  const blockmapPath = path.join(releaseDirectory, `${manifest.fileName}.blockmap`)
  if (!statSync(latestPath).isFile()) throw new Error('release update metadata missing: latest.yml')
  if (!statSync(blockmapPath).isFile() || statSync(blockmapPath).size === 0) throw new Error(`release update metadata missing: ${manifest.fileName}.blockmap`)
  if (manifest.updateMetadata?.latestYml !== 'latest.yml' || manifest.updateMetadata?.blockmap !== `${manifest.fileName}.blockmap`) {
    throw new Error('release update metadata manifest mismatch')
  }
  const latest = loadYaml(readFileSync(latestPath, 'utf8'))
  if (!latest || typeof latest !== 'object' || Array.isArray(latest)) throw new Error('latest.yml schema mismatch')
  const latestRecord = latest
  const expectedSha512 = createHash('sha512').update(readFileSync(installer)).digest('base64')
  const matchingFile = Array.isArray(latestRecord.files)
    ? latestRecord.files.find((file) => file?.url === manifest.fileName)
    : undefined
  if (latestRecord.version !== manifest.version || latestRecord.path !== manifest.fileName || latestRecord.sha512 !== expectedSha512) {
    throw new Error('latest.yml top-level installer metadata mismatch')
  }
  if (!matchingFile || matchingFile.sha512 !== expectedSha512 || matchingFile.size !== manifest.size) {
    throw new Error('latest.yml file metadata mismatch')
  }
} else if (packagedUpdateConfig.channel !== 'development-unsigned' || packagedUpdateConfig.updateBaseUrl !== null || manifest.updateBaseUrl !== null || manifest.updateMetadata !== null) {
  throw new Error('development-unsigned package must not configure an update source')
}
const packageJson = JSON.parse(readFileSync('package.json', 'utf8'))
if (manifest.version !== packageJson.version) throw new Error('release manifest version mismatch')
const expectedChannel = mode === 'release' ? 'release' : 'development-unsigned'
if (manifest.channel !== expectedChannel) throw new Error('release manifest channel mismatch')
if (typeof manifest.commit !== 'string' || !/^[a-f0-9]{40}$/.test(manifest.commit) || typeof manifest.dirty !== 'boolean') {
  throw new Error('release manifest source identity is invalid')
}
const protocols = packageJson.build?.protocols ?? []
const config = readFileSync('electron-builder.yml', 'utf8')
if (!config.includes('cn.aidingyi.agent.desktop') || !config.includes('aidagent')) throw new Error('appId/deep-link packaging configuration missing')
if (!protocols.length && !config.includes('protocols:')) throw new Error('deep-link protocol registration missing')
console.log(`PACKAGE_VERIFY_PASS:${files.length} asar files`)
