import { createHash } from 'node:crypto'
import { mkdtempSync, readFileSync, readdirSync, rmSync, statSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { extractAll, listPackage } from '@electron/asar'
import { expectedSignature } from '../dist/electron/releasePolicy.js'

const releaseDirectory = path.resolve(process.argv[2] ?? 'release')
const mode = process.argv[3] ?? 'dev'
const asarPath = path.join(releaseDirectory, 'win-unpacked', 'resources', 'app.asar')
const files = listPackage(asarPath).map((file) => file.replaceAll('\\', '/'))
for (const required of ['/dist/electron/main.js', '/dist/electron/preload.cjs', '/dist/renderer/index.html']) {
  if (!files.includes(required)) throw new Error(`package missing required runtime file: ${required}`)
}
for (const file of files) {
  if (/\/(tests?|docs?|src)\//i.test(file) || /\.env/i.test(file)) throw new Error(`package contains forbidden file: ${file}`)
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
if (manifest.schemaVersion !== 1 || manifest.platform !== 'win32' || manifest.arch !== 'x64') throw new Error('release manifest schema/platform mismatch')
if (!/^[a-f0-9]{64}$/.test(manifest.buildInputSha256 ?? '')) throw new Error('release manifest build input fingerprint missing')
const installer = path.join(releaseDirectory, manifest.fileName)
const digest = createHash('sha256').update(readFileSync(installer)).digest('hex')
if (digest !== manifest.sha256 || statSync(installer).size !== manifest.size) throw new Error('release manifest hash/size mismatch')
if (manifest.signatureStatus !== expectedSignature(mode)) throw new Error('release manifest signature policy mismatch')
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
