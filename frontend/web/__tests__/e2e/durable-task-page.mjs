// Task-owned local browser fixture: no auth, backend, model or Runtime calls.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createServer } from 'node:net'
import { mkdtemp, open, access } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { chromium } from '@playwright/test'

const directory = await mkdtemp(join(tmpdir(),'runner-sleep-ui-'))
const socket = createServer()
await new Promise(resolve => socket.listen(0,'127.0.0.1',resolve))
const port = socket.address().port
await new Promise(resolve => socket.close(resolve))
const log = await open(join(directory,'vite.log'),'w')
const vite = spawn(process.execPath,['node_modules/vite/bin/vite.js','--host','127.0.0.1','--port',String(port),'--strictPort'],
  { cwd:process.cwd(), stdio:['ignore',log.fd,log.fd], windowsHide:true })
const origin = `http://127.0.0.1:${port}`
let browser
try {
  let ready = false
  for (let attempt=0;attempt<80;attempt++) {
    try { ready = (await fetch(origin)).ok } catch { /* owned server startup */ }
    if (ready) break
    await new Promise(resolve => setTimeout(resolve,100))
  }
  assert(ready,'Owned local Vite did not start')
  const executable = process.env.RUNNER_TEST_BROWSER || (process.platform === 'win32'
    ? 'C:/Program Files/Google/Chrome/Application/chrome.exe' : undefined)
  if (executable) await access(executable)
  browser = await chromium.launch({ headless:true, ...(executable ? { executablePath:executable } : {}) })
  for (const width of [1280,390]) {
    const page = await browser.newPage({ viewport:{ width,height:860 } })
    const errors = []
    page.on('pageerror',error => { errors.push(error.message); console.error('Fixture page error:',error.message) })
    page.on('console',message => { if (message.type() === 'error') console.error('Fixture console:',message.text()) })
    await page.route(origin+'/api/**',route => route.abort())
    await page.goto(origin+'/web/__tests__/e2e/durable-task.fixture.html')
    await page.getByText('任务休眠中',{ exact:true }).waitFor()
    await page.getByRole('button',{ name:'立即继续',exact:true }).focus()
    await page.keyboard.press('Enter')
    assert.deepEqual(await page.evaluate(() => window.fixtureActions),['resume'])
    assert(await page.getByText('部分用量待结算',{ exact:false }).isVisible())
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),'Task card overflows viewport')
    await page.screenshot({ path:join(directory,`sleep-${width}.png`) })
    await page.evaluate(() => window.fixtureUpdate({ status:'paused' }))
    await page.getByRole('button',{ name:'继续任务',exact:true }).waitFor()
    assert.equal(await page.locator('time').count(),0,'Paused task must not promise an automatic wake')
    await page.evaluate(() => window.fixtureUpdate({ status:'waiting',sleep_blocked:{ error_code:'CREDIT_BLOCKED' } }))
    await page.getByText('自动继续已暂停',{ exact:true }).waitFor()
    assert.equal(await page.locator('time').count(),0)
    await page.evaluate(() => window.fixtureUpdate({ status:'cancelled',cancel_requested:true,sleep_blocked:undefined }))
    await page.getByText('任务已停止',{ exact:true }).waitFor()
    assert.equal(await page.getByRole('button').count(),0)
    assert.deepEqual(errors,[])
    await page.close()
  }
  console.log('PASS: 1280/390px, keyboard resume, credits/pending, pause/blocked/stop states. Screenshots:',directory)
} finally {
  await browser?.close()
  vite.kill()
  await log.close()
}
