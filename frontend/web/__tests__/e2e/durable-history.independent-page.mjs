// Task-owned real Vue/Chrome fixture; every business API is blocked.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createServer } from 'node:net'
import { mkdtemp, open, access } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { chromium } from '@playwright/test'

const directory = await mkdtemp(join(tmpdir(),'runner-history-independent-'))
const reservation = createServer()
await new Promise(resolve => reservation.listen(0,'127.0.0.1',resolve))
const port = reservation.address().port
await new Promise(resolve => reservation.close(resolve))
const log = await open(join(directory,'vite.log'),'w')
const vite = spawn(process.execPath,['node_modules/vite/bin/vite.js','--host','127.0.0.1','--port',String(port),'--strictPort'],
  { cwd:process.cwd(),stdio:['ignore',log.fd,log.fd],windowsHide:true })
const origin = `http://127.0.0.1:${port}`
let browser
try {
  let ready = false
  for (let attempt=0;attempt<80;attempt++) {
    try { ready = (await fetch(origin)).ok } catch { /* local fixture server only */ }
    if (ready) break
    await new Promise(resolve => setTimeout(resolve,100))
  }
  assert(ready,'Owned Vite failed to start')
  const executable = process.env.RUNNER_TEST_BROWSER || (process.platform === 'win32'
    ? 'C:/Program Files/Google/Chrome/Application/chrome.exe' : undefined)
  if (executable) await access(executable)
  browser = await chromium.launch({ headless:true,...(executable ? { executablePath:executable } : {}) })
  for (const width of [1280,390]) {
    const page = await browser.newPage({ viewport:{ width,height:860 } })
    const errors = []
    page.on('pageerror',error => errors.push(error.message))
    await page.route(origin+'/api/**',route => route.abort())
    await page.goto(origin+'/web/__tests__/e2e/durable-history.independent.fixture.html')
    const older = page.getByRole('button',{ name:'更早记录',exact:true })
    await older.waitFor()
    await older.focus(); await page.keyboard.press('Enter')
    assert.deepEqual(await page.evaluate(() => window.fixtureActions),['older'])
    await page.getByText('正在查看较早记录',{ exact:true }).waitFor()
    const scroll = page.locator('.overflow-y-auto').first()
    await scroll.evaluate(element => { element.scrollTop = 160 })
    const before = await scroll.evaluate(element => element.scrollTop)
    await page.evaluate(() => window.fixtureAddMessage())
    await page.waitForFunction(() => document.body.textContent.includes('新增实时消息'))
    assert.equal(await scroll.evaluate(element => element.scrollTop),before,'Older history must keep its scroll anchor')
    await scroll.evaluate(element => { element.scrollTop = 0 })
    await page.evaluate(() => window.fixtureUpdateHistory({ loading:true }))
    assert(await older.isDisabled(),'Loading must disable repeated history actions')
    const latest = page.getByRole('button',{ name:'返回最新',exact:true })
    assert(await latest.isDisabled())
    await page.evaluate(() => window.fixtureUpdateHistory({ loading:false,error:'历史记录暂时加载失败，请重试。已保存的内容未丢失。' }))
    await page.getByRole('alert').waitFor()
    await page.evaluate(async () => {
      await Promise.all(document.getAnimations().filter(animation => animation.effect?.getTiming().iterations !== Infinity)
        .map(animation => animation.finished.catch(() => undefined)))
    })
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),'History toolbar overflows viewport')
    await page.screenshot({ path:join(directory,`history-${width}.png`) })
    await latest.focus(); await page.keyboard.press('Enter')
    assert.deepEqual(await page.evaluate(() => window.fixtureActions),['older','latest'])
    await page.waitForFunction(() => !document.body.textContent.includes('正在查看较早记录'))
    await page.waitForFunction(() => {
      const element = document.querySelector('.overflow-y-auto')
      return element && element.scrollTop >= element.scrollHeight-element.clientHeight-2
    })
    assert.deepEqual(errors,[])
    await page.close()
  }
  console.log('PASS: real MessageList/BaseButton, 1280/390px, keyboard paging, loading/error and scroll ownership. Screenshots:',directory)
} finally {
  await browser?.close()
  if (vite.exitCode === null) {
    const stopped = new Promise(resolve => vite.once('exit',resolve))
    vite.kill()
    await stopped
  }
  await log.close()
}
