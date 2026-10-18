import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtemp, rm, stat, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { visualDiagnose } from '../src/main/boss/visualDiagnoseClient.js'
import { pngMetadata } from '../src/main/operations/bossScreenshot.js'
import { parseArgs } from '../src/cli/args.js'
import { validateCommandArgs } from '../src/cli/validate.js'

const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9gAAAABJRU5ErkJggg==', 'base64')
const requestId = '0c5a306b-8659-4000-8000-123456789012'
const env = { AID_BOSS_SERVER_URL: 'http://127.0.0.1:9999', AID_BOSS_SERVER_TOKEN: 'private-test-token' }
const context = { purpose: 'verify', operation: { name: 'open-detail', goal: '打开王欣详情', target_label: '王欣', expected: '简历区域及右侧经历概览和打招呼可见' },
  execution: { stage: 'postcheck', input_status: 'attempted', attempts_used: 2, ordinary_retries_used: 1 }, error: null,
  verification: { status: 'inconclusive', evidence: ['姓名在canvas，不能作为DOM靶点'] }, available_actions: ['press_escape'] }

test('v2 forwards operation facts to backend once while deriving image/space/time from actual observation', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'boss-visual-v2-'))
  try {
    const image = join(dir, 'page.png'), contextFile = join(dir, 'context.json'), observation = join(dir, 'window.json')
    await writeFile(image, png)
    await writeFile(contextFile, JSON.stringify(context))
    const capturedAt = new Date().toISOString()
    await writeFile(observation, JSON.stringify({ ...pngMetadata(png), output: image, space: 'window_image_px', captured_at: capturedAt,
      cdp_port: 9222, window: { hwnd: '123456', pid: 7, left: -10, top: 40, width: 1, height: 1 }, owned_dialogs: [] }))
    for (const isWindow of [false, true]) {
      let calls = 0
      const receipt = { api_version: 2, prompt_version: 'backend-version', receipt: { usage_factor: 50 } }
      const result = await visualDiagnose({ requestId, image, contextFile, ...(isWindow ? { observation } : {}) }, { env, fetch: (async (_url, init) => {
        calls++
        assert.deepEqual(JSON.parse(String(init?.body)), { api_version: 2, request_id: requestId, context: { mode: 'standalone' }, ...context,
          observation: { image_base64: png.toString('base64'), image_sha256: pngMetadata(png).sha256, width: 1, height: 1,
            space: isWindow ? 'window_image_px' : 'page_image_px', captured_at: isWindow ? capturedAt : (await stat(image)).mtime.toISOString(), after_attempt: 2 } })
        for (const field of ['hwnd', 'cdp_port', 'owned_dialogs', 'metadata_path']) assert.equal(String(init?.body).includes(`"${field}"`), false)
        return Response.json(receipt)
      }) as typeof fetch })
      assert.equal(calls, 1)
      assert.deepEqual(result.body, receipt)
    }
  } finally { await rm(dir, { recursive: true, force: true }) }
})

test('v2 retrieval remains a single receipt query with no picture/context or model retry', async () => {
  let calls = 0
  const result = await visualDiagnose({ requestId, apiVersion: 2, retrieveOnly: true }, { env, fetch: (async (_url, init) => {
    calls++
    assert.deepEqual(JSON.parse(String(init?.body)), { api_version: 2, request_id: requestId, retrieve_only: true })
    return Response.json({ state: 'unknown' }, { status: 202 })
  }) as typeof fetch })
  assert.equal(calls, 1)
  assert.equal(result.status, 202)
})

test('context JSON cannot inject request identity, image, authorization or model configuration', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'boss-context-reject-'))
  try {
    const image = join(dir, 'page.png'), contextFile = join(dir, 'context.json')
    await writeFile(image, png)
    for (const invalid of [[], null, { ...context, model: 'direct-vendor' }, { ...context, request_id: 'other' },
      { ...context, observation: {} }, { ...context, context: { mode: 'managed' } }, { ...context, execution: { attempts_used: '2' } }]) {
      await writeFile(contextFile, JSON.stringify(invalid))
      let calls = 0
      await assert.rejects(visualDiagnose({ requestId, image, contextFile }, { env, fetch: (async () => { calls++; return Response.json({}) }) as typeof fetch }))
      assert.equal(calls, 0)
    }
    await writeFile(contextFile, JSON.stringify(context))
    for (const options of [{ apiVersion: 1 as const }, { goal: 'other' }, { retrieveOnly: true }]) {
      await assert.rejects(visualDiagnose({ requestId, image, contextFile, ...options }, { env }))
    }
  } finally { await rm(dir, { recursive: true, force: true }) }
})

test('CLI rejects incomplete v2 and arbitrary keyboard commands without side effects', () => {
  const validate = (argv: string[]) => validateCommandArgs(argv[0], parseArgs(argv)).ok
  const base = ['visual-diagnose', '--image', 'a.png', '--request-id', requestId]
  for (const tail of [['--api-version', '2'], ['--api-version'], ['--api-version', '3'], ['--context-file'],
    ['--context-file', 'c.json', '--goal', 'other'], ['--context-file', 'c.json', '--api-version', '1']]) {
    assert.equal(validate([...base, ...tail]), false)
  }
  assert.equal(validate([...base, '--context-file', 'c.json']), true)
  assert.equal(validate(['visual-diagnose', '--request-id', requestId, '--retrieve-only', '--api-version', '2']), true)
  assert.equal(validate(['window-escape', '--observation', 'a.png.json']), true)
  for (const tail of [[], ['--observation'], ['--observation', 'a.json', '--key', 'Enter'], ['--observation', 'a.json', '--max-age-seconds', '0'], ['--observation', 'a.json', '--json', 'false']]) {
    assert.equal(validate(['window-escape', ...tail]), false)
  }
})
