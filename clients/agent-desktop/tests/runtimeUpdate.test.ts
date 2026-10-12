import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, writeFileSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { RuntimeUpdateIntent, runtimeUpdateExit, shouldResumeRuntime } from '../electron/runtimeUpdate.js'

test('installer rendezvous only applies to installed Windows Runtime', () => {
  const input = { kind: 'runtime', packaged: true, platform: 'win32', smoke: false, argv: ['--aidwork-exit-for-update'] }
  assert.equal(runtimeUpdateExit(input), true)
  for (const change of [{ kind: 'desktop' }, { packaged: false }, { platform: 'darwin' }, { smoke: true }, { argv: [] }]) assert.equal(runtimeUpdateExit({ ...input, ...change }), false)
  assert.equal(runtimeUpdateExit({ ...input, argv: ['--aidwork-exit-for-uninstall'] }), true)
})
test('explicit stop wins over recovery and login while a running update resumes once', () => {
  for (const recovered of [false, true]) for (const login of [false, true]) assert.equal(shouldResumeRuntime({ intent: false, recovered, login }), false)
  assert.equal(shouldResumeRuntime({ intent: true, recovered: false, login: false }), true)
  assert.equal(shouldResumeRuntime({ intent: undefined, recovered: true, login: false }), true)
  assert.equal(shouldResumeRuntime({ intent: undefined, recovered: false, login: false }), false)
})
test('protected update intent preserves explicit stop and rejects another product/home', async () => {
  const directory = mkdtempSync(path.join(tmpdir(), 'aid-update-intent-'))
  const protection = { protect: async (value: string) => Buffer.from(value).toString('base64'), unprotect: async (value: string) => Buffer.from(value, 'base64').toString() }
  const intent = new RuntimeUpdateIntent(directory, 'product', 'home', protection)
  try {
    assert.equal(await intent.read(), undefined)
    await intent.save(false); assert.equal(await intent.read(), false)
    await assert.rejects(() => new RuntimeUpdateIntent(directory, 'other-product', 'home', protection).read())
    await assert.rejects(() => new RuntimeUpdateIntent(directory, 'product', 'other-home', protection).read())
    await intent.save(true); assert.equal(await intent.read(), true)
    await intent.clear(); assert.equal(await intent.read(), undefined)
  } finally { rmSync(directory, { recursive: true, force: true }) }
})
test('a delayed expired read cannot archive a new saved intent or race another read', async () => {
  const directory = mkdtempSync(path.join(tmpdir(), 'aid-update-decrypt-race-'))
  let entered!: () => void, release!: () => void
  const started = new Promise<void>(resolve => { entered = resolve })
  const gate = new Promise<void>(resolve => { release = resolve })
  let first = true
  const protection = {
    protect: async (value: string) => Buffer.from(value).toString('base64'),
    unprotect: async (value: string) => {
      if (first) { first = false; entered(); await gate }
      return Buffer.from(value, 'base64').toString()
    },
  }
  const intent = new RuntimeUpdateIntent(directory, 'product', 'home', protection)
  try {
    writeFileSync(path.join(directory, 'runtime-update-resume.bin'), Buffer.from(JSON.stringify({ schemaVersion: 1, appId: 'product', home: 'home', resume: false, createdAt: Date.now() - 8 * 86400000 })).toString('base64'))
    const oldRead = intent.read(); await started
    let saved = false
    const save = intent.save(true).then(() => { saved = true })
    const nextRead = intent.read()
    await Promise.resolve(); assert.equal(saved, false)
    release(); assert.equal(await oldRead, undefined)
    await save; assert.equal(await nextRead, true)
    assert.equal(readdirSync(directory).filter(name => name.includes('.expired-')).length, 1)
    await intent.clear(); assert.equal(await intent.read(), undefined)
  } finally { release(); rmSync(directory, { recursive: true, force: true }) }
})
