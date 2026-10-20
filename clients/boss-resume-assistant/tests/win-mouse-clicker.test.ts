import assert from 'node:assert/strict'
import test from 'node:test'
import { WinMouseClicker, WinClickError } from '../src/main/input/WinMouseClicker.js'

const VP = { width: 1917, height: 1905 }

test('真实拖动同进程传入两端坐标，终点越屏时不启动脚本', async () => {
  const calls: string[][] = []
  const clicker = new WinMouseClicker({ execFileImpl: async (_file, args) => { calls.push(args); return { stdout: '', stderr: '' } } })
  await clicker.drag({ x: 996, y: 392 }, { x: 1208.8, y: 392 }, VP)
  assert.equal(calls.length, 1)
  assert.equal(calls[0]![calls[0]!.indexOf('-DragX') + 1], '1209')
  assert.equal(calls[0]![calls[0]!.indexOf('-DragY') + 1], '392')
  await assert.rejects(clicker.drag({ x: 996, y: 392 }, { x: 9999, y: 392 }, VP), WinClickError)
  assert.equal(calls.length, 1)
})

test('调用 powershell 传参正确：坐标取整 + CssW/CssH 视口尺寸', async () => {
  const calls: Array<{ file: string; args: string[] }> = []
  const clicker = new WinMouseClicker({
    scriptPath: 'C:\\proj\\scripts\\win-click.ps1',
    execFileImpl: async (file, args) => {
      calls.push({ file, args })
      return { stdout: '已点击', stderr: '' }
    },
  })
  await clicker.click({ x: 903.75, y: 640.2 }, VP)
  assert.equal(calls.length, 1)
  assert.equal(calls[0]!.file, 'powershell.exe')
  const args = calls[0]!.args
  assert.deepEqual(args.slice(0, 4), ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File'])
  assert.ok(args.includes('C:\\proj\\scripts\\win-click.ps1'))
  assert.equal(args[args.indexOf('-X') + 1], '904')
  assert.equal(args[args.indexOf('-Y') + 1], '640')
  assert.equal(args[args.indexOf('-CssW') + 1], '1917')
  assert.equal(args[args.indexOf('-CssH') + 1], '1905')
})

test('坐标超出视口 → 拒绝盲点（不调用 ps1）', async () => {
  let called = 0
  const clicker = new WinMouseClicker({
    execFileImpl: async () => {
      called++
      return { stdout: '', stderr: '' }
    },
  })
  await assert.rejects(clicker.click({ x: 2000, y: 100 }, VP), WinClickError)
  await assert.rejects(clicker.click({ x: -1, y: 100 }, VP), WinClickError)
  assert.equal(called, 0)
})

test('ps1 守卫失败（exit 2 遮挡）→ WinClickError 带 exitCode', async () => {
  const clicker = new WinMouseClicker({
    execFileImpl: async () => {
      throw new WinClickError('win-click.ps1 执行失败(exit=2): 落点仍被遮挡', 2)
    },
  })
  await assert.rejects(clicker.click({ x: 100, y: 100 }, VP), (e: unknown) => {
    assert.ok(e instanceof WinClickError)
    assert.equal(e.exitCode, 2)
    return true
  })
})

test('clickAndType：传 -Text 中文原文，timeout 随文本长度放大', async () => {
  const calls: Array<{ args: string[]; timeout: number }> = []
  const clicker = new WinMouseClicker({
    scriptPath: 'C:\\proj\\scripts\\win-click.ps1',
    execFileImpl: async (_file, args, opts) => {
      calls.push({ args, timeout: opts.timeout })
      return { stdout: '已点击', stderr: '' }
    },
  })
  const text = '你好，请查收简历'
  await clicker.clickAndType({ x: 1016, y: 1195 }, VP, text)
  assert.equal(calls.length, 1)
  const { args, timeout } = calls[0]!
  assert.equal(args[args.indexOf('-X') + 1], '1016')
  assert.equal(args[args.indexOf('-Y') + 1], '1195')
  assert.equal(args[args.indexOf('-Text') + 1], text)
  // timeout = 30000 + 字数×500（中文按 code unit 计）
  assert.equal(timeout, 30000 + text.length * 500)
})

test('clickAndType：空文本 / 坐标超视口 → WinClickError，不调 ps1', async () => {
  let called = 0
  const clicker = new WinMouseClicker({
    execFileImpl: async () => {
      called++
      return { stdout: '', stderr: '' }
    },
  })
  await assert.rejects(clicker.clickAndType({ x: 100, y: 100 }, VP, ''), WinClickError)
  await assert.rejects(clicker.clickAndType({ x: 9999, y: 100 }, VP, '你好'), WinClickError)
  assert.equal(called, 0)
})


test('bound physical click, drag and typing activate the exact target before one packaged input and pass its debug port', async () => {
  const events: string[] = []
  const clicker = new WinMouseClicker({ cdpPort: 9333, beforeInput: async () => { events.push('activate-original-target') }, execFileImpl: async (_file, args) => {
    events.push('input'); assert.equal(args[args.indexOf('-CdpPort') + 1], '9333')
    return { stdout: '', stderr: '' }
  } })
  await clicker.click({ x: 10, y: 20 }, VP)
  await clicker.drag({ x: 10, y: 20 }, { x: 20, y: 20 }, VP)
  await clicker.clickAndType({ x: 10, y: 20 }, VP, 'test')
  assert.deepEqual(events, Array(3).fill(['activate-original-target', 'input']).flat())
  await assert.rejects(clicker.click({ x: -1, y: 20 }, VP), WinClickError)
  assert.equal(events.length, 6, 'invalid intent cannot activate or input')
})

test('a failed target activation is a proven zero-input guard failure for every physical action', async () => {
  let inputs = 0
  const clicker = new WinMouseClicker({ cdpPort: 9333, beforeInput: async () => { throw new Error('original tab closed') }, execFileImpl: async () => { inputs++; return { stdout: '', stderr: '' } } })
  for (const action of [() => clicker.click({ x: 10, y: 20 }, VP), () => clicker.drag({ x: 10, y: 20 }, { x: 20, y: 20 }, VP), () => clicker.clickAndType({ x: 10, y: 20 }, VP, 'test')]) {
    await assert.rejects(action(), (error: unknown) => error instanceof WinClickError && error.exitCode === 2 && error.message.includes('STALE_OBSERVATION'))
  }
  assert.equal(inputs, 0)
  assert.throws(() => new WinMouseClicker({ cdpPort: 9333 }), WinClickError)
})
