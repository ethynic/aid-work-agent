import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtemp, writeFile, readFile, rm, open } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { RuntimeSelections, SelectionError } from '../electron/runtimeSelection.js'

test('selected bytes stay local and same-key replay returns the frozen outer ZIP after expiry', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'h3-selection-'))
  let now = 0
  const selections = new RuntimeSelections(root, () => now)
  try {
    const file = path.join(root, 'chosen.zip'); await writeFile(file, 'test outer zip bytes')
    const chosen = await selections.choose(file, 7, 'instance', 'import-key')
    assert.deepEqual(Object.keys(chosen).sort(), ['label', 'selection_ref'])
    const input = { selection_ref: chosen.selection_ref, request_key: 'import-key', instance_id: 'instance' }
    for (const [owner, params] of [[8, input], [7, { ...input, request_key: 'other' }], [7, { ...input, instance_id: 'old' }]] as const) await assert.rejects(selections.take(params, owner), /失效/)
    const staged = await selections.take(input, 7)
    assert.equal(staged.sha256, createHash('sha256').update(await readFile(file)).digest('hex'))
    assert.equal(staged.size, (await readFile(file)).length)
    now = 700_000
    assert.deepEqual(await selections.take(input, 7), staged)
    await selections.invalidate(7)
    assert.equal((await readFile(staged.staged_path)).toString(), 'test outer zip bytes', 'Main must preserve the transferred snapshot for Host recovery')
    await assert.rejects(selections.take(input, 7), /失效/)
  } finally { await selections.invalidate(); await rm(root, { recursive: true, force: true }) }
})

test('expired or changed selections cannot import bytes the user did not select', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'h3-selection-'))
  let now = 0; const selections = new RuntimeSelections(root, () => now)
  try {
    const file = path.join(root, 'chosen.zip'); await writeFile(file, 'first bytes')
    const expired = await selections.choose(file, 1, 'instance', 'expired-key')
    now = 700_000
    await assert.rejects(selections.take({ selection_ref: expired.selection_ref, request_key: 'expired-key', instance_id: 'instance' }, 1), /过期/)
    const changed = await selections.choose(file, 1, 'instance', 'changed-key')
    await writeFile(file, 'replacement different bytes')
    await assert.rejects(selections.take({ selection_ref: changed.selection_ref, request_key: 'changed-key', instance_id: 'instance' }, 1), /替换|改变/)
  } finally { await selections.invalidate(); await rm(root, { recursive: true, force: true }) }
})

test('filesystem failures from the native picker are safe to display without disclosing paths', async () => {
  const selections = new RuntimeSelections(os.tmpdir())
  const missing = path.join(os.tmpdir(), `missing-private-selection-${Date.now()}`, 'private-package.zip')
  await assert.rejects(selections.choose(missing, 1, 'instance', 'key'), error => {
    assert.ok(error instanceof SelectionError)
    assert.equal(error.code, 7)
    assert.equal(error.message.includes(missing), false)
    assert.equal(error.message.includes('private-package.zip'), false)
    return true
  })
})

test('picker rejects oversized outer archives before spending time hashing bytes the Host cannot accept', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'h3-selection-'))
  const selections = new RuntimeSelections(root)
  try {
    const source = path.join(root, 'oversized.zip')
    const file = await open(source, 'wx')
    try { await file.truncate(512 * 1024 * 1024 + 1) } finally { await file.close() }
    await assert.rejects(selections.choose(source, 1, 'instance', 'key'), /大小不符合/)
  } finally { await selections.invalidate(); await rm(root, { recursive: true, force: true }) }
})
