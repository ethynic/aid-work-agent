import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..')
const tsconfig = JSON.parse(readFileSync(path.join(frontendRoot, 'tsconfig.json'), 'utf8'))
const webTsconfig = JSON.parse(readFileSync(path.join(frontendRoot, 'tsconfig.web.json'), 'utf8'))
const viteAliases = readFileSync(path.join(frontendRoot, 'config/aliases.ts'), 'utf8')

describe('frontend aliases', () => {
  it.each([
    ['@', 'web'],
    ['@web', 'web'],
    ['@desktop', 'desktop'],
    ['@shared', 'shared'],
  ])('maps %s to the intended source root', (alias, directory) => {
    const tsAlias = alias === '@' ? '@/*' : `${alias}/*`
    expect(tsconfig.compilerOptions.paths[tsAlias]).toEqual([`${directory}/*`])
    expect(viteAliases).toContain(`'${alias}': path.resolve(frontendRoot, '${directory}')`)
  })

  it('keeps the production Web typecheck rooted at Web while imported Shared types remain discoverable', () => {
    expect(webTsconfig.extends).toBe('./tsconfig.json')
    expect(webTsconfig.include).toEqual(['web/**/*.ts', 'web/**/*.tsx', 'web/**/*.vue'])
  })
})
