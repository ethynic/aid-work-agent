import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createHash } from 'node:crypto'
import { createBossScreenshotOperation, pngMetadata } from '../src/main/operations/bossScreenshot.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import { visualDiagnose, VisualDiagnoseError } from '../src/main/boss/visualDiagnoseClient.js'
import { parseArgs } from '../src/cli/args.js'
import { validateCommandArgs } from '../src/cli/validate.js'

const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9gAAAABJRU5ErkJggg==', 'base64')
const requestId = '0c5a306b-8659-4000-8000-123456789012'
const env = { AID_BOSS_SERVER_URL: 'http://127.0.0.1:9999', AID_BOSS_SERVER_TOKEN: 'private-test-token' }

test('page screenshot preserves exact PNG pixels/hash and closes session without navigation or input', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'boss-screenshot-'))
  let closed = 0
  let captures = 0
  try {
    const op = createBossScreenshotOperation(async () => ({
      captureFullpage: async () => { captures++; return png }, close: async () => { closed++ },
    }) as BossSession)
    const output = join(dir, 'page.png')
    const result = await op.execute({ output }, { signal: new AbortController().signal, progress: () => {} })
    assert.equal(result.success, true)
    assert.deepEqual(await readFile(output), png)
    assert.deepEqual(result.data, { output, width: 1, height: 1, sha256: createHash('sha256').update(png).digest('hex'), space: 'page_image_px' })
    assert.equal(captures, 1)
    assert.equal(closed, 1)
    assert.equal(JSON.stringify(result).includes(png.toString('base64')), false)
  } finally { await rm(dir, { recursive: true, force: true }) }
})

test('invalid screenshot path is rejected before attaching browser; failed write still disconnects', async () => {
  let connected = 0
  let closed = 0
  const op = createBossScreenshotOperation(async () => {
    connected++
    return { captureFullpage: async () => png, close: async () => { closed++ } } as BossSession
  })
  const ctx = { signal: new AbortController().signal, progress: () => {} }
  assert.equal((await op.execute({ output: '' }, ctx)).code, 'INVALID_ARGUMENT')
  assert.equal(connected, 0)
  const dir = await mkdtemp(join(tmpdir(), 'boss-write-'))
  try { assert.equal((await op.execute({ output: dir }, ctx)).success, false) }
  finally { await rm(dir, { recursive: true, force: true }) }
  assert.equal(closed, 1)
})

test('PNG metadata rejects non-PNG and impossible empty dimensions', () => {
  assert.throws(() => pngMetadata(Buffer.from('not a png')))
  const empty = Buffer.from(png)
  empty.writeUInt32BE(0, 16)
  assert.throws(() => pngMetadata(empty))
})

test('diagnosis posts exact original image once to Agent API and returns receipt unchanged', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'boss-diagnose-'))
  const image = join(dir, 'original.png')
  await writeFile(image, png)
  let calls = 0
  const receipt = { api_version: 1, request_id: requestId, state: 'completed', receipt: { usage_factor: 50 } }
  try {
    const result = await visualDiagnose({ image, requestId }, { env, fetch: (async (url, init) => {
      calls++
      assert.equal(String(url), 'http://127.0.0.1:9999/api/client/v1/boss/visual-diagnose')
      assert.equal(init?.redirect, 'error')
      assert.equal((init?.headers as Record<string, string>).Authorization, 'Bearer private-test-token')
      const body = JSON.parse(String(init?.body))
      assert.deepEqual(body, { api_version: 1, request_id: requestId, context: { mode: 'standalone' },
        operation: { kind: 'overlay', goal: '关闭阻挡弹窗，恢复页面操作' },
        observation: { image_base64: png.toString('base64'), image_sha256: pngMetadata(png).sha256, width: 1, height: 1, space: 'page_image_px' } })
      assert.equal(JSON.stringify(body).includes('model'), false)
      return Response.json(receipt)
    }) as typeof fetch })
    assert.equal(calls, 1)
    assert.deepEqual(result, { status: 200, body: receipt })
  } finally { await rm(dir, { recursive: true, force: true }) }
})

test('retrieve-only sends no image and preserves pending HTTP status without retry or new request ID', async () => {
  let calls = 0
  const result = await visualDiagnose({ requestId, retrieveOnly: true }, { env, fetch: (async (_url, init) => {
    calls++
    assert.deepEqual(JSON.parse(String(init?.body)), { api_version: 1, request_id: requestId, retrieve_only: true })
    return Response.json({ state: 'dispatched' }, { status: 202 })
  }) as typeof fetch })
  assert.equal(calls, 1)
  assert.equal(result.status, 202)
})

test('network timeout/redirect errors never leak token or infer rejection, and never repeat charged inference', async () => {
  let calls = 0
  await assert.rejects(visualDiagnose({ requestId, retrieveOnly: true }, { env, fetch: (async () => {
    calls++
    throw new Error('Bearer private-test-token should never surface')
  }) as typeof fetch }), (err: unknown) => {
    assert.ok(err instanceof VisualDiagnoseError)
    assert.equal(err.requestId, requestId)
    assert.match(err.message, /是否受理未知/)
    assert.match(err.message, /retrieve-only/)
    assert.equal(err.message.includes('private-test-token'), false)
    return true
  })
  assert.equal(calls, 1)
})

test('remote plaintext/URL credentials are refused before any request; local HTTP remains available', async () => {
  for (const url of ['http://example.com', 'https://name:password@example.com', 'https://example.com?token=secret', 'file:///tmp/backend']) {
    await assert.rejects(visualDiagnose({ requestId, retrieveOnly: true }, {
      env: { ...env, AID_BOSS_SERVER_URL: url }, fetch: (async () => { throw new Error('must not fetch') }) as typeof fetch,
    }), /后台 URL/)
  }
})

test('HTTP errors retain status and backend JSON so insufficient credits/conflicts are visible', async () => {
  for (const status of [401, 402, 409]) {
    const body = { error: `status_${status}` }
    assert.deepEqual(await visualDiagnose({ requestId, retrieveOnly: true }, { env,
      fetch: (async () => Response.json(body, { status })) as typeof fetch }), { status, body })
  }
})

test('non-JSON proxy error preserves HTTP status without leaking its body or retrying', async () => {
  await assert.rejects(visualDiagnose({ requestId, retrieveOnly: true }, { env,
    fetch: (async () => new Response('private-test-token from untrusted proxy', { status: 502 })) as typeof fetch }), (err: unknown) => {
      assert.ok(err instanceof VisualDiagnoseError)
      assert.equal(err.status, 502)
      assert.match(err.message, /HTTP 502/)
      assert.equal(err.message.includes('private-test-token'), false)
      return true
    })
})

test('experiment CLI rejects malformed intent, unknown flags, and extra positionals before side effects', () => {
  const validate = (argv: string[]) => validateCommandArgs(argv[0], parseArgs(argv)).ok
  for (const args of [
    ['screenshot'], ['screenshot', '--output'], ['screenshot', '--output', 'a.png', 'extra'],
    ['screenshot', '--output', 'a.png', '--json', 'false'], ['screenshot', '--output', 'a.png', '--cdp-port'],
    ['visual-diagnose', '--image', 'a.png'], ['visual-diagnose', '--request-id', requestId],
    ['visual-diagnose', '--request-id', requestId, '--retrieve-only', '--image', 'a.png'],
    ['visual-diagnose', '--request-id', requestId, '--retrieve-only', '--model', 'vendor'],
    ['visual-diagnose', '--request-id', requestId, '--image', 'a.png', '--goal'],
  ]) assert.equal(validate(args), false, args.join(' '))
  assert.equal(validate(['screenshot', '--output', 'a.png', '--json']), true)
  assert.equal(validate(['visual-diagnose', '--request-id', requestId, '--image', 'a.png', '--goal', '关闭弹窗']), true)
  assert.equal(validate(['visual-diagnose', '--request-id', requestId, '--retrieve-only']), true)
})


test('explicit snapshot export writes current session facts only to requested local file',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'boss-snapshot-'));let captures=0,closed=0
 const snapshot={strings:['private page value'],documents:[]}
 try{
  const op=createBossScreenshotOperation(async()=>({captureFullpage:async()=>png,snapshot:async()=>{captures++;return snapshot},close:async()=>{closed++}}) as unknown as BossSession)
  const output=join(dir,'page.png'),snapshot_output=join(dir,'snapshot.json'),ctx={signal:new AbortController().signal,progress:()=>{}}
  const result=await op.execute({output,snapshot_output},ctx)
  assert.equal(result.success,true);assert.deepEqual(JSON.parse(await readFile(snapshot_output,'utf8')),snapshot)
  assert.equal(JSON.stringify(result).includes('private page value'),false);assert.equal(captures,1);assert.equal(closed,1)
  assert.equal((await op.execute({output,snapshot_output:output},ctx)).code,'INVALID_ARGUMENT')
  assert.equal(captures,1)
  assert.equal(validateCommandArgs('screenshot',parseArgs(['screenshot','--output',output,'--snapshot-output',snapshot_output])).ok,true)
  assert.equal(validateCommandArgs('screenshot',parseArgs(['screenshot','--output',output,'--snapshot-output'])).ok,false)
  assert.equal(validateCommandArgs('screenshot',parseArgs(['screenshot','--output',output,'--scope','window','--snapshot-output',snapshot_output])).ok,false)
 }finally{await rm(dir,{recursive:true,force:true})}
})
