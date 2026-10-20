import { fork } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

// A fresh Node Host handshake, without pairing, claiming or executing work.
const [entry, metadata] = process.argv.slice(2)
if (!entry || !metadata || !path.isAbsolute(entry) || !path.isAbsolute(metadata)) throw new Error('INVALID_CHECK_PATH')
mkdirSync(metadata, { recursive: true })
const home = mkdtempSync(path.join(metadata, 'installation-probe-'))
let child
try {
  writeFileSync(path.join(home, 'config-write-check'), 'installation check')
  const { dpapiProtect, dpapiUnprotect } = await import(pathToFileURL(path.join(path.dirname(entry), 'dpapi.js')).href)
  const nonce = randomUUID()
  if (await dpapiUnprotect(await dpapiProtect(nonce)) !== nonce) throw new Error('CREDENTIAL_PROTECTION_CHECK_FAILED')
  child = fork(entry, [], { execPath: process.execPath, windowsHide: true, stdio: ['ignore', 'ignore', 'ignore', 'ipc'], env: { ...process.env, AIDWORK_RUNTIME_HOME: home, AIDWORK_RUNTIME_CLEAN_GENERATION: '1', NODE_OPTIONS: '', NODE_PATH: '' } })
  const exit = new Promise(resolve => child.once('exit', resolve))
  const request = method => new Promise((resolve, reject) => {
    const requestId = randomUUID()
    const timer = setTimeout(() => reject(new Error('FRESH_HOST_CHECK_TIMEOUT')), 20000)
    child.once('error', reject)
    child.once('exit', () => { clearTimeout(timer); reject(new Error('FRESH_HOST_EXITED')) })
    child.on('message', frame => {
      if (frame?.request_id !== requestId) return
      clearTimeout(timer)
      if (frame.code !== 0 || !frame.result) reject(new Error('FRESH_HOST_HANDSHAKE_FAILED'))
      else resolve(frame.result)
    })
    child.send({ request_id: requestId, method, params: {} })
  })
  const description = await request('describe')
  if (description.api_major !== 1 || !Array.isArray(description.features) || !description.features.includes('first_party_plugins')) throw new Error('FRESH_HOST_PLUGIN_MANAGEMENT_UNAVAILABLE')
  const state = await request('getState')
  if (state.state !== 'stopped' || state.connection !== 'unpaired' || state.device || state.blocked_reason) throw new Error('FRESH_HOST_NOT_USABLE')
  child.disconnect()
  await Promise.race([exit, new Promise((_, reject) => setTimeout(() => reject(new Error('FRESH_HOST_DISPOSE_TIMEOUT')), 5000).unref())])
  // Verify the installed page bytes, not only that app.asar exists. Actual
  // Electron page loading is separately tested with the final EXE.
  const archive = readFileSync(path.resolve(path.dirname(entry), '../../app.asar'))
  if (archive.length < 16) throw new Error('INSTALLED_PAGE_ARCHIVE_INVALID')
  const headerSize = archive.readUInt32LE(4), jsonLength = archive.readUInt32LE(12)
  if (headerSize > 16 * 1024 ** 2 || jsonLength < 2 || jsonLength > headerSize - 8 || 8 + headerSize > archive.length) throw new Error('INSTALLED_PAGE_ARCHIVE_INVALID')
  const header = JSON.parse(archive.subarray(16, 16 + jsonLength).toString('utf8'))
  const page = header.files?.dist?.files?.renderer?.files?.['index.html']
  if (!page || page.link || page.unpacked || !Number.isSafeInteger(page.size) || page.size < 1 || !/^\d+$/.test(page.offset)) throw new Error('INSTALLED_PAGE_MISSING')
  const start = 8 + headerSize + Number(page.offset)
  if (!Number.isSafeInteger(start) || start + page.size > archive.length || !/<html\b/i.test(archive.subarray(start, start + page.size).toString('utf8'))) throw new Error('INSTALLED_PAGE_INVALID')
  console.log('INSTALLATION_CHECK_PASS')
} finally {
  if (child?.exitCode === null) child.kill()
  const absolute = path.resolve(home)
  if (path.dirname(absolute) !== path.resolve(metadata) || !path.basename(absolute).startsWith('installation-probe-')) throw new Error('CHECK_CLEANUP_RANGE_INVALID')
  rmSync(absolute, { recursive: true, force: true })
}
