import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'

const execFileAsync = promisify(execFile)

test('paired friend vision sends only the fixed step and never falls back after refusal', {
  skip: process.platform !== 'win32',
  timeout: 20_000,
}, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'weixin-runtime-friend-'))
  const requests: Array<{ url?: string; auth?: string; body: string }> = []
  const server = createServer(async (req, res) => {
    const chunks: Buffer[] = []
    for await (const chunk of req) chunks.push(chunk)
    requests.push({ url: req.url, auth: req.headers.authorization, body: Buffer.concat(chunks).toString('utf8') })
    const status = req.url === '/api/local-tools/runtime/weixin/visual-locate'
      ? [200, 401, 404, 429][requests.length - 1] ?? 500 : 500
    res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' })
    res.end(JSON.stringify(status === 200 ? { content: '{"plus":{"x":12,"y":34}}' } : {}))
  })
  try {
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    const address = server.address()
    assert.ok(address && typeof address === 'object')
    const url = `http://127.0.0.1:${address.port}`
    const common = fileURLToPath(new URL('../../drivers/ps1/_common.ps1', import.meta.url)).replace(/'/g, "''")
    const script = join(dir, 'proxy-test.ps1')
    writeFileSync(script, `\ufeff
$ErrorActionPreference = 'Stop'
. '${common}'
$image = [Text.Encoding]::UTF8.GetBytes('synthetic image bytes')
$calls = @(foreach ($i in 1..5) {
    if ($i -eq 5) { $env:AIDWORK_RUNTIME_DEVICE_TOKEN = '' }
    try {
        Invoke-KimiVision -ImageBytes $image -SystemPrompt 'not transmitted' -UserText 'not transmitted' -ProxyOnly -RuntimeFriendStep 'plus'
    } catch {
        ([string]$_.Exception.Message).Split('|')[1]
    }
})
Write-DriverJson @{ ok = $true; data = @{ calls = $calls; files = @([IO.Directory]::GetFiles($env:TEMP) | ForEach-Object { [IO.Path]::GetFileName($_) }) } }
`)
    const { stdout } = await execFileAsync('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script], {
      windowsHide: true,
      timeout: 15_000,
      env: {
        ...process.env,
        TEMP: dir,
        NO_PROXY: '127.0.0.1',
        AIDWORK_RUNTIME_SERVER: url,
        AIDWORK_RUNTIME_DEVICE_TOKEN: 'runtime-fixture-token',
        AID_WEIXIN_SERVER_URL: url,
        AID_WEIXIN_ACCESS_TOKEN: 'legacy-unused-token',
        AID_WEIXIN_PROXY_ERROR: 'legacy binding unavailable',
      },
    })
    const line = stdout.split(/\r?\n/).find((value) => value.startsWith('DRIVER_JSON: '))
    assert.ok(line)
    const { calls, files } = JSON.parse(line.slice('DRIVER_JSON: '.length)).data
    assert.deepEqual(calls, [{ plus: { x: 12, y: 34 } }, 'CONFIG_MISSING', 'CONFIG_MISSING', 'INTERNAL_ERROR', 'CONFIG_MISSING'])
    assert.deepEqual(files, ['proxy-test.ps1'])
    assert.equal(requests.length, 4)
    for (const request of requests) {
      assert.equal(request.url, '/api/local-tools/runtime/weixin/visual-locate')
      assert.equal(request.auth, 'Bearer runtime-fixture-token')
      assert.deepEqual(JSON.parse(request.body), {
        image_url: `data:image/png;base64,${Buffer.from('synthetic image bytes').toString('base64')}`,
        step: 'plus',
      })
    }
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()))
    rmSync(dir, { recursive: true, force: true })
  }
})

test('Weixin vision uses the billable GLM proxy for friend-add and existing proxy callers', {
  skip: process.platform !== 'win32',
  timeout: 20_000,
}, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'weixin-friend-proxy-'))
  const requests: Array<{ url?: string; auth?: string; body: string }> = []
  const server = createServer(async (req, res) => {
    const chunks: Buffer[] = []
    for await (const chunk of req) chunks.push(chunk)
    requests.push({ url: req.url, auth: req.headers.authorization, body: Buffer.concat(chunks).toString('utf8') })
    const status = req.url === '/api/client/v1/llm/chat' ? [200, 402, 401][requests.length - 1] ?? 200 : 404
    res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' })
    res.end(JSON.stringify(status === 200 ? { content: 'json result: {"x":12,"y":34}' } : { detail: 'test error' }))
  })
  try {
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    const address = server.address()
    assert.ok(address && typeof address === 'object')
    const url = `http://127.0.0.1:${address.port}`
    const common = fileURLToPath(new URL('../../drivers/ps1/_common.ps1', import.meta.url)).replace(/'/g, "''")
    const script = join(dir, 'proxy-test.ps1')
    writeFileSync(script, `\ufeff
$ErrorActionPreference = 'Stop'
. '${common}'
$script:ZhipuApiBase = '${url}/direct-glm'
$script:KimiApiBase = '${url}/direct-kimi'
$image = [Text.Encoding]::UTF8.GetBytes('synthetic image bytes')
$calls = @(foreach ($i in 1..4) {
    if ($i -eq 4) { $env:AID_WEIXIN_ACCESS_TOKEN = '' }
    try {
        Invoke-KimiVision -ImageBytes $image -SystemPrompt 'Find the control.' -UserText 'Return coordinates.' -ArtifactPrefix 'weixin-driver-friend-add-test' -ProxyOnly
    } catch {
        ([string]$_.Exception.Message).Split('|')[1]
    }
})
$friendFiles = @([IO.Directory]::GetFiles($env:TEMP) | ForEach-Object { [IO.Path]::GetFileName($_) })
$env:AID_WEIXIN_ACCESS_TOKEN = 'test-client-binding'
$script:ZhipuApiKey = ''
$script:KimiApiKey = ''
$imagePath = Join-Path $env:TEMP 'synthetic.png'
[IO.File]::WriteAllBytes($imagePath, $image)
$legacyCalls = @(foreach ($provider in @('', 'glm', 'moonshot')) {
    $env:AID_WEIXIN_VISION_PROVIDER = $provider
    Invoke-KimiVision -ImagePath $imagePath -SystemPrompt 'Find the control.' -UserText 'Return coordinates.' -ArtifactPrefix 'weixin-driver-search-test'
})
Write-DriverJson @{ ok = $true; data = @{ calls = $calls; friend_files = $friendFiles; legacy_calls = $legacyCalls } }
`)
    const { stdout } = await execFileAsync('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script], {
      windowsHide: true,
      timeout: 15_000,
      env: {
        ...process.env,
        TEMP: dir,
        NO_PROXY: '127.0.0.1',
        AID_WEIXIN_SERVER_URL: url,
        AID_WEIXIN_ACCESS_TOKEN: 'test-client-binding',
        AID_WEIXIN_PROXY_ERROR: '',
        AID_WEIXIN_VISION_PROVIDER: 'glm',
        AID_WEIXIN_ZHIPU_API_KEY: 'test-glm-key',
        AID_WEIXIN_KIMI_API_KEY: 'test-kimi-key',
      },
    })
    const line = stdout.split(/\r?\n/).find((text) => text.startsWith('DRIVER_JSON: '))
    assert.ok(line)
    const { calls, friend_files, legacy_calls } = JSON.parse(line.slice('DRIVER_JSON: '.length)).data
    assert.deepEqual(calls, [
      { x: 12, y: 34 }, 'INSUFFICIENT_CREDIT', 'CONFIG_MISSING', 'CONFIG_MISSING',
    ])
    assert.deepEqual(friend_files, ['proxy-test.ps1'])
    assert.deepEqual(legacy_calls, [{ x: 12, y: 34 }, { x: 12, y: 34 }, { x: 12, y: 34 }])
    assert.equal(requests.length, 6)
    for (const request of requests) {
      assert.equal(request.url, '/api/client/v1/llm/chat')
      assert.equal(request.auth, 'Bearer test-client-binding')
      const payload = JSON.parse(request.body)
      assert.equal(payload.model, 'GLM-5.3-Flash')
      assert.match(payload.purpose, /^weixin-vision-/)
      assert.equal(payload.messages[1].content[0].image_url.url,
        `data:image/png;base64,${Buffer.from('synthetic image bytes').toString('base64')}`)
    }
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()))
    rmSync(dir, { recursive: true, force: true })
  }
})
