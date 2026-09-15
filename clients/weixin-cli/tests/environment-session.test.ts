import assert from 'node:assert/strict'
import test from 'node:test'
import { probeEnvironment } from '../src/platform/environment.js'

test('missing inherited SESSIONNAME queries current PID rather than another process', async () => {
  const previous = process.env.SESSIONNAME
  delete process.env.SESSIONNAME
  try {
    const result = await probeEnvironment({ platform: 'win32', execFileFn: async (_file, args) => ({
      stdout: args.includes(`PID eq ${process.pid}`) ? `"node.exe","${process.pid}","RDP-Tcp#0","2","1 K"` : '',
    }) })
    assert.equal(result.interactive_session, true)
    assert.equal(result.session_name, 'RDP-Tcp#0')
    const other = await probeEnvironment({ platform: 'win32', execFileFn: async () => ({ stdout: '"Weixin.exe","999999","Console","2","1 K"' }) })
    assert.equal(other.interactive_session, false)
  } finally {
    if (previous === undefined) delete process.env.SESSIONNAME
    else process.env.SESSIONNAME = previous
  }
})

test('explicit unknown or Services does not grant an interactive session', async () => {
  for (const sessionName of [undefined, 'Services']) {
    const result = await probeEnvironment({ platform: 'win32', sessionName, execFileFn: async (_file, args) => {
      assert.ok(!args.some(value => value.startsWith('PID eq')))
      return { stdout: '' }
    } })
    assert.equal(result.interactive_session, false)
  }
})
