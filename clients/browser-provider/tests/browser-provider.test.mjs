import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'
import { listNativeTools, openNative } from '../src/native.js'

const root = fileURLToPath(new URL('../', import.meta.url))
const entry = join(root, 'dist', 'src', 'cli', 'index.js')

async function connect(t, env = {}) {
  const client = new Client({ name: 'aid-browser-provider-tests', version: '1' })
  await client.connect(new StdioClientTransport({ command: process.execPath, args: [entry, 'mcp', '--stdio'], env, stderr: 'ignore' }))
  t.after(() => client.close())
  return client
}

async function call(client, name, args = {}) {
  const result = await client.callTool({ name: 'agent_browser_' + name, arguments: args })
  const receipt = result.structuredContent
  assert.notEqual(result.isError, true)
  assert.deepEqual(JSON.parse(result.content[0].text), receipt)
  assert.match(receipt.run_id, /^[a-f0-9-]{36}$/)
  return receipt
}

test('the signed declaration and public MCP list retain upstream tools and exclude Chat', async t => {
  const nativeCore = await openNative(root, 'core')
  const nativeDebug = await openNative(root, 'core,debug')
  t.after(async () => { await nativeCore.close(); await nativeDebug.close() })
  const upstream = [
    ...await listNativeTools(nativeCore.client),
    ...(await listNativeTools(nativeDebug.client)).filter(({ name }) =>
      ['agent_browser_upload', 'agent_browser_batch'].includes(name)),
  ]
  const client = await connect(t)
  const manifest = JSON.parse(execFileSync(process.execPath, [entry, 'version', '--json'], { encoding: 'utf8' }))
  assert.deepEqual(manifest.tools, upstream)
  assert.equal(manifest.tools.length, 35)
  assert.deepEqual(manifest.tool_declaration, { version: 1, tools: upstream.map(({ name }) => ({ name })) })
  assert.deepEqual(await listNativeTools(client), upstream)
  assert.equal((await call(client, 'chat')).code, 'BROWSER_TOOL_UNAVAILABLE')
  assert.equal((await call(client, 'batch', { commands: [['open'], ['chat', 'blocked']] })).code, 'INVALID_ARGUMENT')
  assert.equal((await call(client, 'batch', { commands: [['get', 'cdp-url']] })).code, 'INVALID_ARGUMENT')
  assert.equal((await call(client, 'open', { extraArgs: ['chat'] })).code, 'INVALID_ARGUMENT')
  assert.equal((await call(client, 'open', { url: 'file:///C:/unrelated.txt' })).code, 'INVALID_ARGUMENT')
  assert.equal((await call(client, 'batch', { commands: [['tab', 'new', '--label', 'local', 'file:///C:/unrelated.txt']] })).code, 'INVALID_ARGUMENT')
  assert.equal((await call(client, 'screenshot', { selector: 'C:/unrelated.png' })).code, 'INVALID_ARGUMENT')
  const env = Object.fromEntries(['SystemRoot', 'WINDIR', 'LOCALAPPDATA', 'TEMP']
    .filter(key => process.env[key]).map(key => [key, process.env[key]]))
  const doctor = JSON.parse(execFileSync(process.execPath, [entry, 'doctor', '--json'], { encoding: 'utf8', env }))
  assert.deepEqual(doctor, { success: true, runtime_readiness: { ready: true } })
})

test('headed Chrome reads and operates on a page, reports batch outcomes, and uploads only staged files', { timeout: 60000 }, async t => {
  const fixture = mkdtempSync(join(tmpdir(), 'aid-browser-test-files-'))
  const uploadDir = join(fixture, 'AID-Work', 'browser-uploads')
  const file = join(uploadDir, '测试 附件.txt')
  const outside = join(fixture, 'outside.txt')
  const fileContents = 'only the selected fixture is uploaded'
  mkdirSync(uploadDir, { recursive: true })
  writeFileSync(file, fileContents)
  writeFileSync(outside, 'outside root')
  symlinkSync(dirname(outside), join(uploadDir, 'outside'), 'junction')
  t.after(() => rmSync(fixture, { recursive: true, force: true }))

  let received
  const site = createServer((req, res) => {
    if (req.url === '/upload') {
      const chunks = []
      req.on('data', chunk => chunks.push(chunk))
      req.on('end', () => {
        received = Buffer.concat(chunks).toString()
        res.end('received')
      })
      return
    }
    if (req.url === '/markdown') {
      res.writeHead(200, { 'content-type': 'text/markdown; charset=utf-8' })
      res.end('# Browser fixture markdown')
      return
    }
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' })
    res.end(`<!DOCTYPE html><title>AID browser fixture</title><h1>Browser fixture</h1>
      <label>First name <input id="first"></label><label>Last field <input id="last"></label>
      <button onclick="document.querySelector('#status').textContent = 'clicked'">Confirm</button>
      <a id="local-link" href="${pathToFileURL(outside).href}">Local file fixture</a>
      <input type="file" id="file"><output id="status"></output>
      <script>
        document.querySelector('#file').onchange = async e => {
          const response = await fetch('/upload', { method: 'POST', body: e.target.files[0] });
          document.querySelector('#status').textContent = await response.text();
        };
      </script>`)
  })
  await new Promise(resolve => site.listen(0, '127.0.0.1', resolve))
  t.after(() => new Promise(resolve => site.close(resolve)))
  const url = `http://127.0.0.1:${site.address().port}`
  const client = await connect(t, { LOCALAPPDATA: fixture })

  assert.equal((await call(client, 'open', { url, headed: true })).success, true)
  const snapshot = await call(client, 'snapshot')
  assert.equal(snapshot.success, true)
  assert.match(JSON.stringify(snapshot.data), /Confirm/)
  assert.equal((await call(client, 'click', { selector: 'button' })).success, true)
  assert.equal((await call(client, 'get_text', { selector: '#status' })).data.text, 'clicked')
  const markdown = await call(client, 'read', { url: url + '/markdown', requireMd: true })
  assert.equal(markdown.success, true)
  assert.match(JSON.stringify(markdown.data), /# Browser fixture markdown/)
  assert.equal((await call(client, 'screenshot')).code, 'OK')
  const newTab = await call(client, 'click', { selector: '#local-link', newTab: true })
  assert.equal(newTab.code, 'INVALID_ARGUMENT')
  assert.equal(newTab.effect, 'none')
  assert.match(newTab.message, /agent_browser_tab_new/)

  const output = join(fixture, 'out-of-scope.png')
  for (const [blocked, reason] of [
    [['screenshot', '#status', output], /agent_browser_screenshot/],
    [['wait', '--download', output, '--timeout', '500'], /等待下载/],
    [['wait', '-d', output, '--timeout', '500'], /等待下载/],
    [['click', '#local-link', '--new-tab'], /agent_browser_tab_new/],
  ]) {
    const rejected = await call(client, 'batch', { commands: [['fill', '#last', 'must not execute'], blocked] })
    assert.equal(rejected.code, 'INVALID_ARGUMENT')
    assert.equal(rejected.effect, 'none')
    assert.match(rejected.message, reason)
    assert.equal(existsSync(output), false)
  }

  const batch = await call(client, 'batch', {
    bail: true, omitCommand: true,
    commands: [['fill', '#first', 'Alice'], ['fill'], ['fill', '#last', 'not applied']],
  })
  assert.equal(batch.code, 'BROWSER_BATCH_INCOMPLETE')
  assert.equal(batch.effect, 'partial')
  assert.deepEqual(batch.data.results.map(result => [result.index, result.success]), [[0, true], [1, false]])
  assert.deepEqual(batch.data.unattempted, [2])
  const fields = await call(client, 'batch', { commands: [['get', 'value', '#first'], ['get', 'value', '#last']] })
  assert.equal(fields.code, 'OK')
  assert.deepEqual(fields.data.results.map(result => result.result.value), ['Alice', ''])
  assert.equal((await call(client, 'batch', { commands: [['wait', '1'], ['wait', '#first', '--state', 'visible'], ['wait', '--text', 'Browser fixture'], ['wait', '--load', 'domcontentloaded']] })).code, 'OK')
  assert.equal((await call(client, 'get_url')).data.url, url + '/')
  assert.equal((await call(client, 'upload', { selector: '#file', files: [outside] })).code, 'INVALID_ARGUMENT')
  assert.equal((await call(client, 'upload', { selector: '#file', files: [join(uploadDir, 'outside', 'outside.txt')] })).code, 'INVALID_ARGUMENT')
  assert.equal((await call(client, 'batch', { commands: [['upload', '#file', outside]] })).code, 'INVALID_ARGUMENT')
  assert.equal((await call(client, 'upload', { selector: '#file', files: [file] })).code, 'OK')
  const seen = await call(client, 'wait_for_text', { text: 'received', waitTimeoutMs: 5000 })
  assert.equal(seen.code, 'OK')
  assert.equal(received, fileContents)
  const js = await call(client, 'eval', { script: 'typeof process' })
  assert.equal(js.code, 'OK')
  assert.equal(js.data.result, 'undefined')
  assert.equal((await call(client, 'chat')).code, 'BROWSER_TOOL_UNAVAILABLE')
  assert.equal((await call(client, 'close', { all: true })).code, 'OK')
})
