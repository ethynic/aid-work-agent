import { createHash } from 'node:crypto'
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { extractAll, listPackage } from '@electron/asar'
import { desktopRoot, filesUnder, nodeFacts, normalizeTrust, assertNoSensitiveAssets, validateReleaseInputs, verifyHostDependencyGraph, authenticodeStatus } from './prepare-runtime.mjs'
import { resolveProductConfiguration } from '../dist/electron/productConfiguration.js'

const output = path.resolve(process.argv[2] ?? path.join(desktopRoot, 'build/runtime-release/acceptance'))
const manifest = JSON.parse(readFileSync(path.join(output, 'runtime-release-manifest.json'), 'utf8'))
if (manifest.schemaVersion !== 1 || !['acceptance', 'production'].includes(manifest.profile) || manifest.platform !== 'win32' || manifest.arch !== 'x64'
    || !/^[a-f0-9]{64}$/.test(manifest.buildInputSha256 ?? '') || !/^[a-f0-9]{64}$/.test(manifest.hostPackageSha256 ?? '')
    || typeof manifest.fileName !== 'string' || path.basename(manifest.fileName) !== manifest.fileName) throw new Error('Runtime release manifest invalid')
const recorded = validateReleaseInputs(manifest)
const installer = path.join(output, manifest.fileName)
const bytes = readFileSync(installer)
if (bytes.length !== manifest.size || createHash('sha256').update(bytes).digest('hex') !== manifest.sha256) throw new Error('Runtime installer bytes differ from manifest')
const signatureStatus = authenticodeStatus(installer)
if (signatureStatus !== manifest.signatureStatus || manifest.signatureStatus !== (manifest.profile === 'production' ? 'Valid' : 'NotSigned')) throw new Error('Runtime installer signature policy mismatch')
const resources = path.join(output, 'win-unpacked/resources')
assertNoSensitiveAssets(resources)
const product = resolveProductConfiguration({ resourcesPath: resources, isPackaged: true, appDataPath: path.resolve(output, 'identity-validation') })
if (product.kind !== 'runtime' || product.profile !== manifest.profile || product.appId !== manifest.appId || product.productName !== manifest.productName) throw new Error('Packaged Runtime identity/profile mismatch')
normalizeTrust(JSON.parse(readFileSync(product.trustRootsPath, 'utf8')), product.profile)
const facts = nodeFacts(product.nodeExecutable)
const resourceManifest = JSON.parse(readFileSync(path.join(resources, 'runtime/resource-manifest.json'), 'utf8'))
const runtimeFiles = filesUnder(path.join(resources, 'runtime'), 'runtime/').filter(file => file.path !== 'runtime/resource-manifest.json')
const configFiles = filesUnder(path.join(resources, 'config'), 'config/')
const completeResources = [...filesUnder(path.join(resources, 'runtime'), 'resources/runtime/'), ...filesUnder(path.join(resources, 'config'), 'resources/config/')]
const expectedResources = [...recorded.keys()].filter(file => file.startsWith('resources/'))
if (completeResources.length !== expectedResources.length || completeResources.some(file => recorded.get(file.path) !== file.sha256)) throw new Error('Packaged Runtime resources differ from release input records')
const allFiles = [...runtimeFiles, ...configFiles].sort((left, right) => left.path.localeCompare(right.path, 'en'))
const expectedFiles = [...resourceManifest.files].sort((left, right) => left.path.localeCompare(right.path, 'en'))
if (JSON.stringify(allFiles) !== JSON.stringify(expectedFiles) || resourceManifest.hostPackageSha256 !== manifest.hostPackageSha256 || resourceManifest.profile !== manifest.profile) throw new Error('Packaged Runtime resources incomplete or changed')
const asar = path.join(resources, 'app.asar')
const entries = listPackage(asar).map(file => file.replaceAll('\\', '/'))
for (const required of ['/dist/electron/main.js', '/dist/electron/preload.cjs', '/dist/electron/productConfiguration.js', '/dist/electron/runtimeSupervisor.js', '/dist/electron/runtimeSelection.js', '/dist/renderer/index.html']) {
  if (!entries.includes(required)) throw new Error(`Runtime app.asar missing ${required}`)
}
for (const file of entries) {
  if ((!file.startsWith('/node_modules/') && /\/(tests?|docs?|src)\//i.test(file)) || /\.env(?:\.|$)/i.test(file)) throw new Error('Runtime app.asar contains forbidden development file')
}
const temporary = mkdtempSync(path.join(os.tmpdir(), 'aid-runtime-verify-'))
try {
  extractAll(asar, temporary)
  assertNoSensitiveAssets(temporary)
  const packagedDist = filesUnder(path.join(temporary, 'dist'), 'dist/')
  const expectedDist = [...recorded.keys()].filter(file => file.startsWith('dist/'))
  if (packagedDist.length !== expectedDist.length || packagedDist.some(file => recorded.get(file.path) !== file.sha256)) throw new Error('Packaged Electron/renderer differ from release input records')
  const electron = path.join(temporary, 'dist/electron')
  for (const name of readdirSync(electron).filter(file => /\.(?:js|cjs)$/.test(file))) {
    const source = readFileSync(path.join(electron, name), 'utf8')
    // Check local transitive import/require targets, including baseline platform.js.
    for (const match of source.matchAll(/(?:from\s*|import\s*(?:\(\s*)?|require\s*\(\s*)['"](\.\.?\/[^'"]+)['"]/g)) {
      if (!existsSync(path.resolve(electron, match[1]))) throw new Error(`Runtime Electron dependency missing: ${name} -> ${match[1]}`)
    }
  }
} finally {
  const absolute = path.resolve(temporary)
  if (path.dirname(absolute) !== path.resolve(os.tmpdir()) || !path.basename(absolute).startsWith('aid-runtime-verify-')) throw new Error('Verification cleanup escaped temporary directory')
  rmSync(absolute, { recursive: true, force: true })
}
verifyHostDependencyGraph(product.nodeExecutable, product.hostEntry)
console.log(JSON.stringify({ result: 'RUNTIME_VERIFY_PASS', profile: product.profile, appId: product.appId, node: facts, resourceFiles: allFiles.length, installer }))
