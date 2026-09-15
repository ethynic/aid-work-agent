import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { ResidentOcr, defaultPythonPath } from '../src/platform/ocrResident.js'

async function fixture() {
  const directory = await mkdtemp(join(tmpdir(), 'aid-ocr-cancel-'))
  const scriptPath = join(directory, 'server.py')
  await writeFile(scriptPath, `import sys,json,time
for line in sys.stdin:
 r=json.loads(line)
 if not r.get('image'): print(json.dumps({'id':r['id'],'ok':False,'error':'probe'}),flush=True)
 elif r.get('image') == 'ready': print(json.dumps({'id':r['id'],'ok':True,'boxes':[]}),flush=True)
 else: time.sleep(60)
`)
  return { directory, scriptPath }
}

test('aborted OCR request does not start a process', async () => {
  const ocr = new ResidentOcr({ pythonPath: 'must-not-spawn' })
  await assert.rejects(ocr.recognize('unused', undefined, AbortSignal.abort()), /取消/)
})

test('pending OCR is cancelled without automatic retry', async () => {
  const f = await fixture()
  const ocr = new ResidentOcr({ pythonPath: defaultPythonPath(), scriptPath: f.scriptPath })
  try {
    await ocr.ensureStarted()
    const started = performance.now()
    await assert.rejects(ocr.recognize('unused', undefined, AbortSignal.timeout(100)), /取消/)
    assert.ok(performance.now() - started < 3000)
  } finally { await ocr.shutdown(); await rm(f.directory, { recursive: true, force: true }) }
})

test('unresponsive OCR has a bounded timeout without retry', async () => {
  const f = await fixture()
  const ocr = new ResidentOcr({ pythonPath: defaultPythonPath(), scriptPath: f.scriptPath, requestTimeoutMs: 100 })
  try {
    await ocr.ensureStarted()
    const started = performance.now()
    await assert.rejects(ocr.recognize('unused'), /超时/)
    assert.ok(performance.now() - started < 3000)
  } finally { await ocr.shutdown(); await rm(f.directory, { recursive: true, force: true }) }
})

test('cold startup can be cancelled before the probe responds', async () => {
  const f = await fixture()
  await writeFile(f.scriptPath, 'import time\ntime.sleep(60)\n')
  const ocr = new ResidentOcr({ pythonPath: defaultPythonPath(), scriptPath: f.scriptPath })
  try {
    const started = performance.now()
    await assert.rejects(ocr.recognize('unused', undefined, AbortSignal.timeout(100)), /取消/)
    assert.ok(performance.now() - started < 3000)
  } finally { await ocr.shutdown(); await rm(f.directory, { recursive: true, force: true }) }
})

test('old process shutdown cannot reject the replacement process request', async () => {
  const f = await fixture()
  const ocr = new ResidentOcr({ pythonPath: defaultPythonPath(), scriptPath: f.scriptPath })
  try {
    await ocr.ensureStarted()
    const stopping = ocr.shutdown()
    assert.deepEqual(await ocr.recognize('ready'), [])
    await stopping
    assert.deepEqual(await ocr.recognize('ready'), [])
  } finally { await ocr.shutdown(); await rm(f.directory, { recursive: true, force: true }) }
})
