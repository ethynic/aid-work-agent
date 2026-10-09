import test from 'node:test'
import assert from 'node:assert/strict'
import { doctorCommand } from '../src/cli/commands/doctor.js'

test('doctor JSON reports blocked readiness without connecting to or creating a business browser', async () => {
  const output: string[] = []
  const original = console.log
  console.log = (value: unknown) => { output.push(String(value)) }
  let closed = false
  try {
    const status = await doctorCommand({ json: true, gateway: {
      connect: async () => { throw new Error('No debug browser') },
      attachToRecommendPage: async () => { throw new Error('Must not attach') },
      captureDomSnapshot: async () => { throw new Error('Must not capture') },
      close: async () => { closed = true },
    } })
    assert.equal(status, 1)
    assert.equal(output.length, 1)
    const report = JSON.parse(output[0]!)
    assert.equal(report.success, false)
    assert.equal(report.runtime_readiness.ready, false)
    assert.equal(closed, true)
  } finally { console.log = original }
})
