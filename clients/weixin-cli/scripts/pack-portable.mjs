/** C5: package an already built embedded OCR runtime, without touching BOSS. */
import { cpSync, existsSync, lstatSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, isAbsolute, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFileSync } from 'node:child_process'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const [source, destination] = process.argv.slice(2)
if (!source || !destination || !isAbsolute(source) || !isAbsolute(destination)) {
  console.error('Usage: node scripts/pack-portable.mjs <absolute embedded OCR directory> <absolute output directory>')
  process.exit(2)
}
if (!existsSync(destination) || !existsSync(join(root, 'dist', 'src', 'cli', 'index.js'))) {
  console.error('Build the Provider first and create the output directory.')
  process.exit(2)
}
// A venv is not relocatable. Require the embedded distribution layout.
if (!existsSync(source) || lstatSync(source).isSymbolicLink() || existsSync(join(source, 'pyvenv.cfg')) || !existsSync(join(source, 'python.exe')) ||
    !readdirSync(source).some(name => /^python\d+\._pth$/.test(name))) {
  console.error('Source must be a prepared embedded Python distribution, not a venv.')
  process.exit(2)
}
const stage = mkdtempSync(join(tmpdir(), 'aid-weixin-portable-'))
try {
  function validateTree(directory) {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      if (entry.isSymbolicLink()) throw new Error('Embedded runtime must contain files, not links')
      if (entry.isDirectory()) validateTree(join(directory, entry.name))
    }
  }
  validateTree(source)
  for (const name of readdirSync(source).filter(name => /^python\d+\._pth$/.test(name))) {
    for (const raw of readFileSync(join(source, name), 'utf8').split(/\r?\n/)) {
      const line = raw.trim()
      if (!line || line.startsWith('#') || line === 'import site') continue
      const rel = relative(resolve(source), resolve(source, line))
      if (line.startsWith('import ') || isAbsolute(line) || rel === '..' || rel.startsWith('..\\') || rel.startsWith('../') || isAbsolute(rel)) {
        throw new Error('Embedded search path escapes package')
      }
    }
  }
  for (const directory of ['dist', 'drivers']) cpSync(join(root, directory), join(stage, directory), { recursive: true })
  cpSync(source, join(stage, 'ocr-python'), { recursive: true })
  // Validate the COPIED interpreter with repository and PYTHONPATH excluded.
  // RapidOCR construction proves that packaged model assets can be located.
  const env = { ...process.env }
  delete env.PYTHONPATH
  delete env.PYTHONHOME
  execFileSync(join(stage, 'ocr-python', 'python.exe'), ['-I', '-c',
    'from rapidocr_onnxruntime import RapidOCR; from PIL import Image; import onnxruntime; RapidOCR(); print("OCR_BUNDLE_OK")'],
  { cwd: stage, env, stdio: 'pipe', timeout: 60000 })
  const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))
  delete pkg.scripts
  delete pkg.devDependencies
  pkg.files = ['dist/src', 'drivers', 'ocr-python']
  writeFileSync(join(stage, 'package.json'), JSON.stringify(pkg, null, 2) + '\n')
  // npm's CLI is located relative to the npm executable installed beside Node.
  const npmCli = process.env.npm_execpath
  if (!npmCli || !existsSync(npmCli)) throw new Error('Run through npm run pack:portable')
  execFileSync(process.execPath, [npmCli, 'pack', '--ignore-scripts', '--pack-destination', destination],
    { cwd: stage, env, stdio: 'pipe', timeout: 120000 })
  console.log('Portable Provider package created; clean-user and real-device acceptance still required.')
} catch {
  console.error('Portable package validation failed; no rollout approval. Check embedded dependencies and npm environment.')
  process.exitCode = 1
} finally {
  // Only remove the exact mkdtemp directory owned by this process.
  if (dirname(stage) === resolve(tmpdir()) && stage.startsWith(join(resolve(tmpdir()), 'aid-weixin-portable-'))) {
    rmSync(stage, { recursive: true, force: true })
  }
}
