import test, { type TestContext } from 'node:test'
import assert from 'node:assert/strict'
import { spawn, type ChildProcess } from 'node:child_process'
import { createHash, randomUUID } from 'node:crypto'
import { rm, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { rememberBrowserTarget, resolveBrowserTarget } from '../src/main/boss/browserTargetBinding.js'

// Children import the actual compiled repository module. The held callback blocks
// its own event loop, so an application timer cannot accidentally release the lock.
const workerSource = `
const [moduleUrl, ref, rawPort, target, mode, rawIterations] = process.argv.slice(1);
const { updateBrowserTarget } = await import(moduleUrl);
const port = Number(rawPort);
process.send({ type: 'ready' });
await new Promise(resolve => process.once('message', resolve));
let successes = 0;
try {
  const iterations = Number(rawIterations);
  for (let index = 0; index < iterations; index++) {
    let finished = false;
    for (let retry = 0; !finished && retry < 300; retry++) {
      try {
        await updateBrowserTarget(ref, port, target, current => {
          process.send({ type: mode === 'hold' ? 'held' : 'entered' });
          if (mode === 'hold') Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0);
          if (mode === 'throw') throw new Error('fixture callback failed');
          if (mode === 'compete') Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 15);
          const count = Number(current.page_context?.job_name ?? '0');
          if (!Number.isSafeInteger(count)) throw new Error('fixture counter corrupted');
          return { ...current, page_context: {
            entry_url: 'https://www.zhipin.com/web/chat/recommend', job_name: String(count + 1)
          } };
        });
        successes++; finished = true;
      } catch (error) {
        if (mode !== 'compete') throw error;
        await new Promise(resolve => setTimeout(resolve, 5));
      }
    }
    if (!finished) throw new Error('fixture contention never converged');
  }
  process.send({ type: 'done', successes });
} catch (error) {
  process.send({ type: 'rejected', code: error.code, message: error.message });
}
process.disconnect();
`

interface Message { type: string; successes?: number; code?: string; message?: string }
interface ChildRun {
  child: ChildProcess
  messages: Message[]
  closed: Promise<void>
  wait(type: string): Promise<Message>
  start(): void
  stop(): Promise<void>
}

function childRun(t: TestContext, ref: string, port: number, target: string, mode: string, iterations = 1): ChildRun {
  const child = spawn(process.execPath, ['--input-type=module', '--eval', workerSource,
    new URL('../src/main/boss/browserTargetBinding.js', import.meta.url).href,
    ref, String(port), target, mode, String(iterations)], {
    stdio: ['ignore', 'ignore', 'pipe', 'ipc'], windowsHide: true,
    env: { ...process.env, AID_BOSS_AUTO_CHROME: '0' },
  })
  const messages: Message[] = []
  let stderr = '', exited = false
  child.stderr!.on('data', data => { stderr = (stderr + String(data)).slice(-2000) })
  child.on('message', value => { messages.push(value as Message) })
  const closed = new Promise<void>(resolve => {
    child.once('exit', () => { exited = true; resolve() })
    child.once('error', error => { stderr += error.message; exited = true; resolve() })
  })
  const watchdog = setTimeout(() => { if (!exited) child.kill('SIGKILL') }, 15000)
  const stop = async () => { if (!exited) child.kill('SIGKILL'); await closed; clearTimeout(watchdog) }
  t.after(stop)
  return { child, messages, closed, stop,
    start() { child.send({ type: 'start' }) },
    wait(type: string) {
      const existing = messages.find(value => value.type === type)
      if (existing) return Promise.resolve(existing)
      if (exited) return Promise.reject(new Error('Child exited before ' + type + ': ' + stderr))
      return new Promise((resolve, reject) => {
        const cleanup = () => { clearTimeout(timer); child.off('message', onMessage); child.off('exit', onExit) }
        const onMessage = (raw: unknown) => {
          const value = raw as Message
          if (value.type === type) { cleanup(); resolve(value) }
        }
        const onExit = () => { cleanup(); reject(new Error('Child exited before ' + type + ': ' + stderr)) }
        const timer = setTimeout(() => { cleanup(); reject(new Error('Timed out waiting for child ' + type + ': ' + stderr)) }, 10000)
        child.on('message', onMessage); child.once('exit', onExit)
      })
    },
  }
}

async function fixture(t: TestContext) {
  const target = 'lock-test-' + randomUUID(), port = 56281
  const ref = await rememberBrowserTarget(target, port)
  const root = join(tmpdir(), 'aid-boss-targets')
  t.after(async () => {
    // Exact UUID/index paths owned by this test; never enumerate or unlink locks
    // belonging to another reference or touch any real Chrome/Runtime process.
    await rm(join(root, `${ref}.json`), { force: true })
    await rm(join(root, `${ref}.lock`), { force: true })
    await rm(join(root, createHash('sha256').update(`${port}:${target}`).digest('hex') + '.target'), { force: true })
  })
  const run = (mode: string, iterations = 1) => childRun(t, ref, port, target, mode, iterations)
  return { ref, port, target, root, run }
}

test('a live blocked owner excludes contenders and its process death releases the same reference', { timeout: 30000 }, async t => {
  const f = await fixture(t)
  const owner = f.run('hold')
  await owner.wait('ready'); owner.start(); await owner.wait('held')
  for (let attempt = 0; attempt < 3; attempt++) {
    const contender = f.run('once')
    await contender.wait('ready'); contender.start()
    const rejected = await contender.wait('rejected')
    assert.equal(rejected.code, 'BUSY')
    await contender.closed
    assert.equal(contender.messages.some(value => value.type === 'entered'), false,
      'rejection must never enter the update callback or remove the live owner lock')
  }
  await owner.stop()
  const resumed = f.run('once')
  await resumed.wait('ready'); resumed.start()
  assert.equal((await resumed.wait('done')).successes, 1)
  await resumed.closed
  assert.equal((await resolveBrowserTarget(f.ref, f.port)).page_context?.job_name, '1')
})

test('independent processes cannot lose same-reference counter updates under contention', { timeout: 30000 }, async t => {
  const f = await fixture(t)
  const contenders = Array.from({ length: 6 }, () => f.run('compete', 4))
  await Promise.all(contenders.map(child => child.wait('ready')))
  contenders.forEach(child => child.start())
  const results = await Promise.all(contenders.map(child => child.wait('done')))
  await Promise.all(contenders.map(child => child.closed))
  assert.equal(results.reduce((sum, item) => sum + item.successes!, 0), 24)
  assert.equal((await resolveBrowserTarget(f.ref, f.port)).page_context?.job_name, '24',
    'every callback must read the last committed binding; double ownership loses increments')
})

test('a throwing update callback releases ownership without committing partial state', { timeout: 20000 }, async t => {
  const f = await fixture(t)
  const failed = f.run('throw')
  await failed.wait('ready'); failed.start()
  assert.equal((await failed.wait('rejected')).message, 'fixture callback failed')
  await failed.closed
  assert.equal((await resolveBrowserTarget(f.ref, f.port)).page_context, undefined)
  const next = f.run('once')
  await next.wait('ready'); next.start(); await next.wait('done'); await next.closed
  assert.equal((await resolveBrowserTarget(f.ref, f.port)).page_context?.job_name, '1')
})

test('an empty lock file from an unpublished older version cannot permanently block the new lock', { timeout: 20000 }, async t => {
  const f = await fixture(t)
  await writeFile(join(f.root, `${f.ref}.lock`), '', { flag: 'wx' })
  const next = f.run('once')
  await next.wait('ready'); next.start(); await next.wait('done'); await next.closed
  assert.equal((await resolveBrowserTarget(f.ref, f.port)).page_context?.job_name, '1')
})

test('uppercase UUID aliases contend for the same lock and recover the same binding after owner exit', { timeout: 20000 }, async t => {
  const f = await fixture(t)
  const owner = f.run('hold')
  await owner.wait('ready'); owner.start(); await owner.wait('held')
  const alias = childRun(t, f.ref.toUpperCase(), f.port, f.target, 'once')
  await alias.wait('ready'); alias.start()
  assert.equal((await alias.wait('rejected')).code, 'BUSY')
  await alias.closed
  assert.equal(alias.messages.some(value => value.type === 'entered'), false)
  await owner.stop()
  const resumed = childRun(t, f.ref.toUpperCase(), f.port, f.target, 'once')
  await resumed.wait('ready'); resumed.start(); await resumed.wait('done'); await resumed.closed
  assert.equal((await resolveBrowserTarget(f.ref, f.port)).page_context?.job_name, '1')
})

test('an unrelated reference remains writable while another reference owner is blocked', { timeout: 20000 }, async t => {
  const held = await fixture(t), independent = await fixture(t)
  const owner = held.run('hold')
  await owner.wait('ready'); owner.start(); await owner.wait('held')
  const other = independent.run('once')
  await other.wait('ready'); other.start(); await other.wait('done'); await other.closed
  assert.equal((await resolveBrowserTarget(independent.ref, independent.port)).page_context?.job_name, '1')
  assert.equal((await resolveBrowserTarget(held.ref, held.port)).page_context, undefined)
  const same = held.run('once')
  await same.wait('ready'); same.start()
  assert.equal((await same.wait('rejected')).code, 'BUSY')
  await same.closed
  await owner.stop()
})
