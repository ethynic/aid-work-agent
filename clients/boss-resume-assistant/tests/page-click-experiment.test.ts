import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtemp, rm, utimes, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createPageClickExperiment } from '../src/main/operations/bossPageClickExperiment.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import { parseArgs } from '../src/cli/args.js'
import { validateCommandArgs } from '../src/cli/validate.js'

const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9gAAAABJRU5ErkJggg==', 'base64')
const ctx = () => ({ signal: new AbortController().signal, progress: () => {} })

test('experimental page click uses exact page coordinates once and disconnects; input ACK is not success proof', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'boss-page-click-'))
  try {
    const image = join(dir, 'page.png'); await writeFile(image, png)
    let clicks = 0, closed = 0
    const operation = createPageClickExperiment(async () => ({ captureFullpage: async () => png,
      clickBrowse: async (point: { x: number; y: number }) => { assert.deepEqual(point, { x: 0, y: 0 }); clicks++ }, close: async () => { closed++ },
    }) as unknown as BossSession)
    const result = await operation.execute({ image, x: 0, y: 0 }, ctx())
    assert.equal(result.success, true)
    assert.equal(result.data?.input_status, 'attempted')
    assert.equal(result.data?.space, 'page_image_px')
    assert.equal(result.data?.dismissed, undefined)
    assert.equal(clicks, 1); assert.equal(closed, 1)
  } finally { await rm(dir, { recursive: true, force: true }) }
})

test('stale, changed, malformed or outside page image cannot cause an input', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'boss-page-refuse-'))
  try {
    const image = join(dir, 'page.png')
    let clicks = 0, closed = 0
    let current = png
    const operation = createPageClickExperiment(async () => ({ captureFullpage: async () => current,
      clickBrowse: async () => { clicks++ }, close: async () => { closed++ },
    }) as unknown as BossSession)
    await writeFile(image, png)
    assert.equal((await operation.execute({ image, x: 1, y: 0 }, ctx())).success, false)
    await utimes(image, new Date(0), new Date(0))
    assert.equal((await operation.execute({ image, x: 0, y: 0 }, ctx())).success, false)
    await writeFile(image, png)
    current = Buffer.from(png); current[current.length - 1] = current[current.length - 1]! ^ 1
    assert.equal((await operation.execute({ image, x: 0, y: 0 }, ctx())).success, false)
    await writeFile(image, 'not png')
    assert.equal((await operation.execute({ image, x: 0, y: 0 }, ctx())).success, false)
    assert.equal(clicks, 0); assert.equal(closed, 4)
  } finally { await rm(dir, { recursive: true, force: true }) }
})

test('direct page input error never retries and still releases session', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'boss-page-error-'))
  try {
    const image = join(dir, 'page.png'); await writeFile(image, png)
    let clicks = 0, closed = 0
    const operation = createPageClickExperiment(async () => ({ captureFullpage: async () => png,
      clickBrowse: async () => { clicks++; throw new Error('input response lost') }, close: async () => { closed++ },
    }) as unknown as BossSession)
    assert.equal((await operation.execute({ image, x: 0, y: 0 }, ctx())).success, false)
    assert.equal(clicks, 1); assert.equal(closed, 1)
  } finally { await rm(dir, { recursive: true, force: true }) }
})

test('page-click CLI rejects missing intent and unknown flags; help never inputs', () => {
  const valid = (argv: string[]) => validateCommandArgs(argv[0], parseArgs(argv)).ok
  const base = ['page-click', '--image', 'a.png', '--x', '0', '--y', '0']
  assert.equal(valid(base), true)
  for (const argv of [['page-click'], [...base, '--space', 'window_image_px'], [...base, '--json', 'false'],
    [...base, '--max-age-seconds', '0'], [...base, '--cdp-port'], [...base, 'extra'], ['page-click', '--image', 'a.png', '--x', '-1', '--y', '0']]) {
    assert.equal(valid(argv), false)
  }
  assert.equal(valid(['page-click', '--help']), true)
})

test('cancellation during fresh capture is CANCELLED and never emits page input', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'boss-page-cancel-'))
  try {
    const image = join(dir, 'page.png'); await writeFile(image, png)
    const ac = new AbortController()
    let clicks = 0, closed = 0
    const operation = createPageClickExperiment(async () => ({
      captureFullpage: async () => { ac.abort(); return png },
      clickBrowse: async () => { clicks++ }, close: async () => { closed++ },
    }) as unknown as BossSession)
    const result = await operation.execute({ image, x: 0, y: 0 }, { signal: ac.signal, progress: () => {} })
    assert.equal(result.code, 'CANCELLED')
    assert.equal(clicks, 0); assert.equal(closed, 1)
  } finally { await rm(dir, { recursive: true, force: true }) }
})
