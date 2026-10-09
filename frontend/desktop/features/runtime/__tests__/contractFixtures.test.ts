import { describe, expect, it } from 'vitest'
import fixtures from '../../../../../contracts/runtime-host/v1/fixtures/management.json'
import invalidFixtures from '../../../../../contracts/runtime-host/v1/fixtures/invalid.json'
import { decodeResponse, isHostEvent, isHostState, type ManagementMethod } from '../contracts'

describe('H1 shared fixtures against production renderer decoder', () => {
  for (const fixture of [...fixtures, ...invalidFixtures]) {
    if (!['management-response.schema.json', 'host-state.schema.json', 'host-event.schema.json'].includes(fixture.schema)) continue
    it(fixture.name, () => {
      const value: unknown = fixture.value
      if (fixture.schema === 'host-state.schema.json') { expect(isHostState(value)).toBe(fixture.valid); return }
      if (fixture.schema === 'host-event.schema.json') { expect(isHostEvent(value)).toBe(fixture.valid); return }
      const response = value as { request_id: string; method: ManagementMethod }
      const decode = () => decodeResponse(value, { request_id: response.request_id, method: response.method, params: {} })
      if (fixture.valid) expect(decode).not.toThrow(); else expect(decode).toThrow()
    })
  }
})
