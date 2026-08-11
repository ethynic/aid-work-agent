/**
 * 跨进程命名互斥（设计 §6.1）：Windows 命名管道实现。
 *
 * 关键语义：EADDRINUSE 即被占用；持有者进程死亡（含崩溃 kill）后名称自动释放。
 * 这正是不能用「锁文件」方案的原因——本文件的「kill 后可占用」用例钉死该行为。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { spawn } from 'node:child_process'
import { NamedMutex, mutexPath } from '../src/platform/namedMutex.js'

test('同进程：占用后同 scope 再占用返回 false，释放后可再占用', async () => {
  const scope = `test-mutex-basic-${process.pid}`
  const a = new NamedMutex(scope)
  assert.equal(await a.acquire(), true)
  assert.equal(a.held, true)

  const b = new NamedMutex(scope)
  assert.equal(await b.acquire(), false)
  assert.equal(b.held, false)

  await a.release()
  assert.equal(a.held, false)
  assert.equal(await b.acquire(), true)
  await b.release()
})

test('非法 scope 直接拒绝（管道名拼接安全）', () => {
  assert.throws(() => new NamedMutex('a/b'))
  assert.throws(() => new NamedMutex('..'))
  assert.throws(() => mutexPath('a b'))
})

test('跨进程：子进程持有管道时占用失败；kill 子进程后名称自动释放', { timeout: 20000 }, async () => {
  const scope = `test-mutex-xproc-${process.pid}`
  const path = mutexPath(scope)
  // 子进程：占用管道后打印一行 ready，然后常驻
  const child = spawn(
    process.execPath,
    [
      '-e',
      `require('node:net').createServer((s)=>s.destroy()).listen(${JSON.stringify(path)},()=>{console.log('ready')});setInterval(()=>{},1000)`,
    ],
    { stdio: ['ignore', 'pipe', 'inherit'] },
  )
  try {
    let buf = ''
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('子进程占用管道超时')), 10000)
      child.stdout.on('data', (d) => {
        buf += d
        if (buf.includes('ready')) {
          clearTimeout(timer)
          resolve()
        }
      })
      child.on('exit', () => {
        clearTimeout(timer)
        reject(new Error('子进程提前退出'))
      })
    })

    // 子进程持有期间：占用失败（EADDRINUSE）
    const contender = new NamedMutex(scope)
    assert.equal(await contender.acquire(), false)

    // kill（SIGKILL 等效，模拟崩溃）：OS 回收管道句柄，名称自动释放
    child.kill('SIGKILL')
    await new Promise<void>((resolve) => child.on('exit', () => resolve()))

    const after = new NamedMutex(scope)
    assert.equal(await after.acquire(), true, '进程死亡后管道名称应自动释放')
    await after.release()
  } finally {
    if (child.exitCode === null && !child.killed) child.kill('SIGKILL')
  }
})
