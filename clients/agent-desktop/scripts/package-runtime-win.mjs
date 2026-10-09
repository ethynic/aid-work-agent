import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { desktopRoot, resourcesRoot, filesUnder, nodeFacts, normalizeTrust, assertNoSensitiveAssets, verifyHostDependencyGraph, runtimeNodeSignExclusion, authenticodeStatus } from './prepare-runtime.mjs'
import { load as loadYaml } from 'js-yaml'
import { resolveProductConfiguration } from '../dist/electron/productConfiguration.js'
import { assertReleaseSigning } from '../dist/electron/releasePolicy.js'

const profile = process.argv[2]
if (!['acceptance', 'production'].includes(profile) || process.argv.length !== 3) throw new Error('Use package-runtime-win.mjs acceptance|production; run prepare-runtime first')
process.chdir(desktopRoot)
const source = JSON.parse(readFileSync(path.join(resourcesRoot, 'config/runtime-product.json'), 'utf8'))
if (source.productKind !== 'runtime' || source.profile !== profile) throw new Error('Prepared Runtime product/profile does not match packaging invocation')
const product = resolveProductConfiguration({ resourcesPath: resourcesRoot, isPackaged: true, appDataPath: path.resolve('build/identity-validation') })
normalizeTrust(JSON.parse(readFileSync(product.trustRootsPath, 'utf8')), profile)
assertNoSensitiveAssets(resourcesRoot)
nodeFacts(product.nodeExecutable)
assertReleaseSigning(profile === 'production' ? 'release' : 'dev', process.env)
const sourceManifest = JSON.parse(readFileSync(path.join(resourcesRoot, 'runtime/resource-manifest.json'), 'utf8'))
const actual = filesUnder(resourcesRoot).filter(file => file.path !== 'runtime/resource-manifest.json')
if (JSON.stringify(actual) !== JSON.stringify(sourceManifest.files) || sourceManifest.profile !== profile) throw new Error('Prepared Runtime resources changed; rerun preparation with approved inputs')
verifyHostDependencyGraph(product.nodeExecutable, product.hostEntry)
const pkg = JSON.parse(readFileSync('package.json', 'utf8'))
const output = path.join(desktopRoot, 'build/runtime-release', profile)
if (!output.startsWith(path.join(desktopRoot, 'build') + path.sep)) throw new Error('Runtime package output must stay under Desktop build')
rmSync(output, { recursive: true, force: true })
mkdirSync(output, { recursive: true })
const buildConfiguration = path.join(output, 'builder-config.json')
const artifactName = `AID-Work-Runtime-${pkg.version}-win-x64-${profile}${profile === 'acceptance' ? '-unsigned' : ''}.exe`
writeFileSync(buildConfiguration, JSON.stringify({
  extends: path.join(desktopRoot, 'electron-builder.yml'), appId: product.appId, productName: product.productName,
  directories: { output }, protocols: [], publish: null,
  extraResources: [
    { from: path.join(resourcesRoot, 'config'), to: 'config' },
    { from: path.join(resourcesRoot, 'runtime'), to: 'runtime' },
  ],
  win: { artifactName, signExts: [runtimeNodeSignExclusion] }, forceCodeSigning: profile === 'production',
}, null, 2) + '\n')
const inputs = [...filesUnder(resourcesRoot).map(file => ({ path: `resources/${file.path}`, sha256: file.sha256 })),
  ...filesUnder(path.join(desktopRoot, 'dist')).filter(file => !file.path.startsWith('tests/')).map(file => ({ path: `dist/${file.path}`, sha256: file.sha256 })),
  ...['package.json', 'package-lock.json', 'electron-builder.yml', 'scripts/prepare-runtime.mjs', 'scripts/runtime-node-resources.json', 'scripts/package-runtime-win.mjs', 'scripts/verify-runtime-package.mjs'].map(file => ({ path: file, sha256: createHash('sha256').update(readFileSync(file)).digest('hex') })),
]
const buildInputSha256 = createHash('sha256').update(JSON.stringify(inputs)).digest('hex')
const whitelist = loadYaml(readFileSync('electron-builder.yml', 'utf8')).files
if (!Array.isArray(whitelist) || !whitelist.includes('dist/renderer/**/*')) throw new Error('Runtime app file whitelist missing renderer')
const packagedInputs = inputs.filter(item => item.path.startsWith('resources/') || item.path.startsWith('dist/renderer/') || (item.path.startsWith('dist/') && whitelist.includes(item.path)))
const builder = spawnSync(process.execPath, [path.join(desktopRoot, 'node_modules/electron-builder/out/cli/cli.js'), '--win', 'nsis', '--x64', '--config', buildConfiguration], {
  stdio: 'inherit', windowsHide: true, env: { ...process.env, CSC_IDENTITY_AUTO_DISCOVERY: profile === 'production' ? 'true' : 'false' },
})
if (builder.error) throw builder.error
if (builder.status !== 0) throw new Error('Runtime electron-builder failed')
for (const item of inputs) {
  const file = item.path.startsWith('resources/') ? path.join(resourcesRoot, item.path.slice(10)) : path.join(desktopRoot, item.path)
  if (createHash('sha256').update(readFileSync(file)).digest('hex') !== item.sha256) throw new Error('Runtime packaging inputs changed during build')
}
const installer = path.join(output, artifactName)
if (!existsSync(installer)) throw new Error('Runtime installer missing')
const bytes = readFileSync(installer)
const signatureStatus = authenticodeStatus(installer)
if (signatureStatus !== (profile === 'production' ? 'Valid' : 'NotSigned')) throw new Error('Runtime installer signature/profile mismatch')
writeFileSync(path.join(output, 'runtime-release-manifest.json'), JSON.stringify({
  schemaVersion: 1, profile, appId: product.appId, productName: product.productName, version: pkg.version,
  platform: 'win32', arch: 'x64', fileName: artifactName, size: bytes.length,
  sha256: createHash('sha256').update(bytes).digest('hex'), signatureStatus, buildInputSha256,
  hostPackageSha256: sourceManifest.hostPackageSha256, inputs, packagedInputs,
}, null, 2) + '\n')
const verification = spawnSync(process.execPath, ['scripts/verify-runtime-package.mjs', output], { stdio: 'inherit', windowsHide: true })
if (verification.error) throw verification.error
if (verification.status !== 0) throw new Error('Runtime package verification failed')
console.log(`RUNTIME_PACKAGE_PASS:${installer}`)
