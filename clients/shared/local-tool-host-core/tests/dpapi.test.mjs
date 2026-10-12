import assert from 'node:assert/strict'
import childProcess from 'node:child_process'
import { EventEmitter } from 'node:events'
import { syncBuiltinESMExports } from 'node:module'
import { win32 } from 'node:path'
import { PassThrough } from 'node:stream'
import test from 'node:test'

// The override permits source-only isolated compilation without rewriting a
// concurrently packaged dist tree. Normal package tests consume dist.
const { dpapiProtect, dpapiUnprotect, trustedSystemPowerShell } = await import(process.env.AID_DPAPI_TEST_MODULE
  ?? new URL('../dist/legacy/dpapi.js', import.meta.url).href)
const windows = { skip: process.platform !== 'win32' }

function fakeChild() {
  const child = new EventEmitter()
  child.stdin = new PassThrough()
  child.kill = () => true
  return child
}
async function mockedExec(t, implementation, action) {
  t.mock.method(childProcess, 'execFile', implementation)
  syncBuiltinESMExports()
  try { await action() } finally { t.mock.restoreAll(); syncBuiltinESMExports() }
}

test('DPAPI real WinPS 5.1 roundtrip preserves Unicode, whitespace and empty plaintext', windows, async () => {
  const executable = win32.join(process.env.SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
  assert.equal(childProcess.execFileSync(executable, ['-NoProfile', '-NonInteractive', '-Command', '[Console]::Out.Write($PSVersionTable.PSVersion.Major)'], { windowsHide: true }).toString(), '5')
  for (const plaintext of ['fixture: 中文 😀\n\t\uFEFF', '\uFEFFleading-bom-fixture', '']) {
    const cipher = await dpapiProtect(plaintext)
    assert.match(cipher, /^[A-Za-z0-9+/]+=*$/)
    assert.notEqual(cipher, plaintext)
    assert.equal(await dpapiUnprotect(cipher), plaintext)
  }
})

test('DPAPI works with poisoned inherited PATH and PowerShell 7 module paths', windows, async () => {
  const before = { ...process.env }
  try {
    for (const key of Object.keys(process.env)) if (/^(?:path|psmodulepath)$/i.test(key)) delete process.env[key]
    process.env.PATH = 'Z:\\nonexistent-fixture-executables'
    process.env.PSModulePath = 'C:\\Program Files\\PowerShell\\7\\Modules'
    const cipher = await dpapiProtect('pollution-fixture')
    assert.equal(await dpapiUnprotect(cipher), 'pollution-fixture')
    assert.equal(process.env.PATH, 'Z:\\nonexistent-fixture-executables')
    assert.equal(process.env.PSModulePath, 'C:\\Program Files\\PowerShell\\7\\Modules')
  } finally {
    for (const key of Object.keys(process.env)) delete process.env[key]
    Object.assign(process.env, before)
  }
})

test('DPAPI retains the existing CurrentUser/no-entropy ciphertext format', windows, async () => {
  const plaintext = 'legacy-format-fixture'
  const executable = win32.join(process.env.SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
  const oldScript = "Add-Type -AssemblyName System.Security; $bytes=[Convert]::FromBase64String([Console]::In.ReadToEnd().Trim()); $enc=[Security.Cryptography.ProtectedData]::Protect($bytes,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser); [Console]::Out.Write([Convert]::ToBase64String($enc))"
  const cipher = childProcess.execFileSync(executable, ['-NoProfile', '-NonInteractive', '-Command', oldScript], {
    input: Buffer.from(plaintext).toString('base64'), windowsHide: true,
  }).toString()
  assert.equal(await dpapiUnprotect(cipher), plaintext)
  const newCipher = await dpapiProtect(plaintext)
  const oldDecode = "Add-Type -AssemblyName System.Security; $bytes=[Convert]::FromBase64String([Console]::In.ReadToEnd().Trim()); $dec=[Security.Cryptography.ProtectedData]::Unprotect($bytes,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser); [Console]::Out.Write([Convert]::ToBase64String($dec))"
  const decoded = childProcess.execFileSync(executable, ['-NoProfile', '-NonInteractive', '-Command', oldDecode], {
    input: newCipher, windowsHide: true,
  }).toString()
  assert.equal(Buffer.from(decoded, 'base64').toString('utf8'), plaintext)
})

test('DPAPI rejects a syntactically valid corrupt blob with a fixed redacted error', windows, async () => {
  await assert.rejects(dpapiUnprotect('AAAA'), { message: 'DPAPI_CALL_FAILED' })
})

test('DPAPI accepts legacy ASCII whitespace and folded ciphertext without accepting other characters', windows, async () => {
  const plaintext = 'legacy-folded-cipher-fixture'
  const cipher = await dpapiProtect(plaintext)
  assert.equal(await dpapiUnprotect(cipher + '\r\n'), plaintext)
  const folded = '\t ' + cipher.match(/.{1,40}/g).join('\r\n \t') + '\n'
  assert.equal(await dpapiUnprotect(folded), plaintext)
  await assert.rejects(dpapiUnprotect(cipher + '\u00a0'), { message: 'DPAPI_INPUT_INVALID' })
})

test('DPAPI rejects noncanonical and oversized inputs before spawning', async t => {
  await mockedExec(t, () => { assert.fail('invalid input must not spawn') }, async () => {
    for (const blob of ['', 'AAAA\u00a0', 'A===', 'AB==', 'not a fixture blob', 'A'.repeat(4 * 1024 * 1024 + 4)]) {
      await assert.rejects(dpapiUnprotect(blob), { message: 'DPAPI_INPUT_INVALID' })
    }
    await assert.rejects(dpapiProtect('x'.repeat(3 * 1024 * 1024 + 1)), { message: 'DPAPI_INPUT_INVALID' })
  })
})

test('DPAPI rejects an invalid system root without disclosing its value', windows, async () => {
  const before = process.env.SystemRoot
  try {
    process.env.SystemRoot = 'relative-sensitive-fixture-root'
    await assert.rejects(dpapiProtect('fixture'), { message: 'DPAPI_POWERSHELL_UNAVAILABLE' })
  } finally { process.env.SystemRoot = before }
})

test('DPAPI launcher uses absolute system executable, bounded calls and process-local environment', windows, async t => {
  const launch = trustedSystemPowerShell()
  assert.equal(launch.executable, win32.join(process.env.SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'))
  assert.equal(launch.cwd, win32.dirname(launch.executable))
  assert.equal(launch.env.PSModulePath, win32.join(launch.cwd, 'Modules'))
  await mockedExec(t, (executable, args, options, callback) => {
    assert.equal(executable, win32.join(process.env.SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'))
    assert.deepEqual(args.slice(0, 3), ['-NoProfile', '-NonInteractive', '-Command'])
    assert.match(args[3], /\$ErrorActionPreference='Stop'/)
    assert.match(args[3], /catch.*exit 1/)
    assert.doesNotMatch(args.join(' '), /stdin-only-fixture/)
    assert.equal(options.windowsHide, true)
    assert.equal(options.cwd, win32.dirname(executable))
    assert.equal(options.timeout, 30000)
    assert.equal(options.maxBuffer, 4 * 1024 * 1024)
    assert.equal(options.env.PSModulePath, win32.join(process.env.SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'Modules'))
    assert.equal(Object.keys(options.env).filter(key => /^path$/i.test(key)).length, 1)
    assert.equal(Object.keys(options.env).filter(key => /^psmodulepath$/i.test(key)).length, 1)
    queueMicrotask(() => callback(null, 'QUJD', ''))
    return fakeChild()
  }, async () => assert.equal(await dpapiProtect('stdin-only-fixture'), 'QUJD'))
})

test('DPAPI rejects stderr, bad output, startup exceptions and pipe errors without echoing details', windows, async t => {
  for (const [error, stdout, stderr, code] of [
    [new Error('sensitive-fixture-argv'), '', 'sensitive-fixture-stderr', 'DPAPI_CALL_FAILED'],
    [null, 'QUJD', 'sensitive-fixture-warning', 'DPAPI_CALL_FAILED'],
    [null, '', '', 'DPAPI_OUTPUT_INVALID'],
    [null, 'QUJD\n', '', 'DPAPI_OUTPUT_INVALID'],
    [null, 'AB==', '', 'DPAPI_OUTPUT_INVALID'],
    [null, 'A'.repeat(4 * 1024 * 1024 + 4), '', 'DPAPI_OUTPUT_INVALID'],
  ]) {
    await mockedExec(t, (_executable, _args, _options, callback) => {
      queueMicrotask(() => callback(error, stdout, stderr))
      return fakeChild()
    }, async () => assert.rejects(dpapiProtect('fixture'), { message: code }))
  }
  await mockedExec(t, () => { throw new Error('sensitive-fixture-startup') }, async () => {
    await assert.rejects(dpapiProtect('fixture'), { message: 'DPAPI_CALL_FAILED' })
  })
  await mockedExec(t, () => {
    const child = fakeChild()
    child.stdin.end = () => child.stdin.emit('error', new Error('sensitive-fixture-pipe'))
    return child
  }, async () => assert.rejects(dpapiProtect('fixture'), { message: 'DPAPI_CALL_FAILED' }))
})

test('DPAPI refuses invalid UTF-8 returned from a protected payload', windows, async t => {
  await mockedExec(t, (_executable, _args, _options, callback) => {
    queueMicrotask(() => callback(null, '//8=', ''))
    return fakeChild()
  }, async () => assert.rejects(dpapiUnprotect('AAAA'), { message: 'DPAPI_OUTPUT_INVALID' }))
})
