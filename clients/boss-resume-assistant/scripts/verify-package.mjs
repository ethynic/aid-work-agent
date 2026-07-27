/**
 * 打包产物自检。验证安装包存在、asar 完整、manifest 哈希一致。
 * 用法：node scripts/verify-package.mjs dev|release [release-dir]
 */
import { existsSync, readFileSync, statSync } from 'node:fs'
import { createHash } from 'node:crypto'
import path from 'node:path'

const mode = process.argv[2]
if (mode !== 'dev' && mode !== 'release') {
  console.error('usage: node scripts/verify-package.mjs dev|release [release-dir]')
  process.exit(1)
}
const releaseDir = process.argv[3] ? path.resolve(process.argv[3]) : path.resolve('release')
const isRelease = mode === 'release'

const packageJson = JSON.parse(readFileSync(path.resolve('package.json'), 'utf8'))
const manifestPath = path.join(releaseDir, 'release-manifest.json')
if (!existsSync(manifestPath)) throw new Error(`release-manifest.json missing in ${releaseDir}`)

const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
const installer = path.join(releaseDir, manifest.fileName)
if (!existsSync(installer) || !statSync(installer).isFile()) {
  throw new Error(`installer missing: ${manifest.fileName}`)
}

const bytes = readFileSync(installer)
const sha256 = createHash('sha256').update(bytes).digest('hex')
if (sha256 !== manifest.sha256) {
  throw new Error(`installer sha256 mismatch: ${sha256} !== ${manifest.sha256}`)
}
if (manifest.version !== packageJson.version) {
  throw new Error(`manifest version drift: ${manifest.version} !== ${packageJson.version}`)
}
if (manifest.channel !== (isRelease ? 'release' : 'development-unsigned')) {
  throw new Error(`manifest channel mismatch: ${manifest.channel}`)
}

const asar = path.join(releaseDir, 'win-unpacked', 'resources', 'app.asar')
if (!existsSync(asar)) throw new Error('win-unpacked app.asar missing')

console.log(`VERIFY_PACKAGE_${mode.toUpperCase()}_PASS:${manifest.fileName}`)
