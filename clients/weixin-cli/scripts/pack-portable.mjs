/** Package the compiled Weixin Provider for installation with npm. */
import { cpSync, existsSync, lstatSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, isAbsolute, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFileSync } from 'node:child_process'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const [destination, extra] = process.argv.slice(2)
if (!destination || !isAbsolute(destination) || extra !== undefined) {
  console.error('Usage: npm run pack:portable -- <absolute output directory>')
  process.exit(2)
}
if (!existsSync(destination) || !existsSync(join(root, 'dist', 'src', 'cli', 'index.js'))) {
  console.error('Build the Provider first and create the output directory.')
  process.exit(2)
}
const stage = mkdtempSync(join(tmpdir(), 'aid-weixin-portable-'))
try {
  const compiled = join(root, 'dist', 'src')
  cpSync(compiled, join(stage, 'dist', 'src'), { recursive: true, filter: file => {
    if (lstatSync(file).isDirectory()) return true
    return existsSync(join(root, 'src', relative(compiled, file).replace(/\.js$/, '.ts')))
  } })
  cpSync(join(root, 'drivers', 'ps1'), join(stage, 'drivers', 'ps1'), { recursive: true })
  cpSync(join(root, 'provider-manifest.json'), join(stage, 'provider-manifest.json'))
  const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))
  delete pkg.scripts
  delete pkg.devDependencies
  pkg.files = ['dist/src', 'drivers/ps1', 'provider-manifest.json']
  writeFileSync(join(stage, 'package.json'), JSON.stringify(pkg, null, 2) + '\n')
  // npm's CLI is located relative to the npm executable installed beside Node.
  const npmCli = process.env.npm_execpath
  if (!npmCli || !existsSync(npmCli)) throw new Error('Run through npm run pack:portable')
  execFileSync(process.execPath, [npmCli, 'pack', '--ignore-scripts', '--pack-destination', destination],
    { cwd: stage, stdio: 'pipe', timeout: 120000 })
  console.log('Portable Provider package created; clean-user and real-device acceptance still required.')
} catch {
  console.error('Portable packaging failed. Check the build output and npm environment.')
  process.exitCode = 1
} finally {
  // Only remove the exact mkdtemp directory owned by this process.
  if (dirname(stage) === resolve(tmpdir()) && stage.startsWith(join(resolve(tmpdir()), 'aid-weixin-portable-'))) {
    rmSync(stage, { recursive: true, force: true })
  }
}
