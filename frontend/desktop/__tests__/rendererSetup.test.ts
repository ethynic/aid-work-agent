import { describe, expect, it } from 'vitest'

describe('Desktop renderer test setup', () => {
  it('provides an isolated jsdom document', () => {
    document.body.innerHTML = '<main data-runtime="desktop"></main>'
    expect(document.querySelector('[data-runtime="desktop"]')).not.toBeNull()
  })
})
