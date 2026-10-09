import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { defaultPythonPath } from '../src/platform/ocrResident.js'

test('relocated Provider prefers bundled Python without repository venv', () => {
  const home = mkdtempSync(join(tmpdir(), 'ocr-path-test-'))
  try {
    const pkg = join(home, 'relocated-provider')
    mkdirSync(join(pkg, 'ocr-python'), { recursive: true })
    const python = join(pkg, 'ocr-python', 'python.exe')
    writeFileSync(python, '')
    assert.equal(defaultPythonPath(join(pkg, 'dist', 'src', 'platform')), python)
  } finally { rmSync(home, { recursive: true, force: true }) }
})

test('development checkout retains existing venv fallback', () => {
  const home = mkdtempSync(join(tmpdir(), 'ocr-fallback-test-'))
  try {
    const directory = join(home, 'clients', 'weixin-cli', 'dist', 'src', 'platform')
    assert.equal(defaultPythonPath(directory), join(home, 'venv', 'Scripts', 'python.exe'))
  } finally { rmSync(home, { recursive: true, force: true }) }
})

test('installed distribution cannot use a repository venv when bundled OCR is missing', () => {
  const home = mkdtempSync(join(tmpdir(), 'ocr-installed-test-'))
  try {
    const pkg = join(home, 'relocated-provider')
    mkdirSync(pkg)
    writeFileSync(join(pkg, 'runtime-manifest.json'), '{}')
    assert.throws(() => defaultPythonPath(join(pkg, 'dist', 'src', 'platform')), /发行包缺少内部 OCR Python/)
  } finally { rmSync(home, { recursive: true, force: true }) }
})

test('portable pack rejects a venv before copying or executing it', () => {
  const home = mkdtempSync(join(tmpdir(), 'ocr-pack-test-'))
  try {
    const source = join(home, 'venv')
    const destination = join(home, 'out')
    mkdirSync(source)
    mkdirSync(destination)
    writeFileSync(join(source, 'pyvenv.cfg'), 'not portable')
    const root = fileURLToPath(new URL('../../', import.meta.url))
    const script = resolve(root, 'scripts', 'pack-portable.mjs')
    const result = spawnSync(process.execPath, [script, source, destination], { encoding: 'utf8' })
    assert.equal(result.status, 2)
    assert.match(result.stderr, /embedded Python/)
  } finally { rmSync(home, { recursive: true, force: true }) }
})
