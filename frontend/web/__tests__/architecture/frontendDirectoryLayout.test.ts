import { existsSync, readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..')

describe('frontend directory layout', () => {
  it('uses web as the application source root and does not restore legacy src', () => {
    expect(existsSync(path.join(frontendRoot, 'web/main.ts'))).toBe(true)
    expect(existsSync(path.join(frontendRoot, 'src'))).toBe(false)
  })

  it.each([
    ['index.html', '/web/main.ts'],
    ['desktop.html', '/web/main.desktop.ts'],
  ])('%s points to the web entry', (fileName, expectedEntry) => {
    const html = readFileSync(path.join(frontendRoot, fileName), 'utf8')
    expect(html).toContain(expectedEntry)
    expect(html).not.toMatch(/(?:src|href)=["']\/src\//)
  })
})
