import { spawn } from 'node:child_process'
import electron from 'electron'
import { createServer } from 'node:http'
import { mkdtemp, rm } from 'node:fs/promises'
import os from 'node:os'
import process from 'node:process'

const origin = 'aidagent://app'
const expectedOriginPaths = new Set(['/health', '/api/complaints/stats', '/stream', '/upload', '/download'])
const observedOriginPaths = new Set()
const originViolations = []
const server = createServer((request, response) => {
  const requestOrigin = request.headers.origin
  if (requestOrigin !== origin) {
    originViolations.push(`${request.method} ${request.url}: ${requestOrigin ?? '<missing>'}`)
    response.writeHead(403).end()
    return
  }
  const requestPath = request.url ? new URL(request.url, 'http://fixture').pathname : ''
  if (expectedOriginPaths.has(requestPath)) observedOriginPaths.add(requestPath)
  response.setHeader('Access-Control-Allow-Origin', requestOrigin)
  response.setHeader('Vary', 'Origin')
  if (request.method === 'OPTIONS') {
    response.setHeader('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
    response.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-Tenant-Id, Authorization')
    response.writeHead(204).end()
    return
  }
  if (request.url === '/health') {
    response.writeHead(200, { 'Content-Type': 'application/json' }).end('{"status":"ok"}')
    return
  }
  if (requestPath === '/api/complaints/stats') {
    response.writeHead(200, { 'Content-Type': 'application/json' }).end('{"success":true,"data":{"total":0}}')
    return
  }
  if (request.url === '/stream') {
    response.writeHead(200, { 'Content-Type': 'text/event-stream' })
    response.write('data: chunk-1\n\n')
    setTimeout(() => response.write('data: chunk-2\n\n'), 100)
    setTimeout(() => response.end('data: chunk-3\n\n'), 200)
    return
  }
  if (request.url === '/upload' && request.method === 'POST') {
    const chunks = []
    request.on('data', (chunk) => chunks.push(chunk))
    request.on('end', () => {
      const contentType = request.headers['content-type'] ?? ''
      const received = contentType.startsWith('multipart/form-data; boundary=')
        && Buffer.concat(chunks).includes(Buffer.from('desktop-upload-marker'))
      response.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify({ received }))
    })
    return
  }
  if (request.url === '/download') {
    response.writeHead(200, { 'Content-Type': 'application/octet-stream' }).end(Buffer.alloc(65536, 7))
    return
  }
  response.writeHead(404).end()
})

await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
const address = server.address()
if (!address || typeof address === 'string') throw new Error('无法启动 Spike fixture server')

const executable = process.env.AID_AGENT_DESKTOP_EXECUTABLE || electron
const executableArguments = process.env.AID_AGENT_DESKTOP_EXECUTABLE ? [] : ['.']
const smokeUserData = await mkdtemp(`${os.tmpdir()}${process.platform === 'win32' ? '\\' : '/'}aidagent-smoke-`)
const child = spawn(executable, executableArguments, {
  cwd: process.cwd(),
  env: {
    ...process.env,
    AID_AGENT_API_BASE_URL: `http://127.0.0.1:${address.port}`,
    AID_AGENT_DESKTOP_SMOKE: '1',
    AID_AGENT_DESKTOP_SMOKE_USER_DATA: smokeUserData,
  },
  stdio: 'inherit',
})
const timeout = setTimeout(() => {
  console.error('AGENT_DESKTOP_SMOKE_PARENT_TIMEOUT')
  child.kill()
}, 30_000)
let exitCode
try {
  exitCode = await new Promise((resolve, reject) => {
    child.once('error', reject)
    child.once('exit', resolve)
  })
} finally {
  clearTimeout(timeout)
  if (child.exitCode === null) child.kill()
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
  await rm(smokeUserData, { recursive: true, force: true })
}
const missingOriginPaths = [...expectedOriginPaths].filter((url) => !observedOriginPaths.has(url))
if (originViolations.length > 0) console.error(`CORS_ORIGIN_VIOLATIONS: ${originViolations.join('; ')}`)
if (missingOriginPaths.length > 0) console.error(`CORS_ORIGIN_MISSING: ${missingOriginPaths.join(', ')}`)
process.exitCode = typeof exitCode === 'number' && exitCode === 0 && originViolations.length === 0 && missingOriginPaths.length === 0 ? 0 : 1
