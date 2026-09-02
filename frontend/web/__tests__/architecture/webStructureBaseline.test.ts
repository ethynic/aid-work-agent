import { existsSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

// @ts-expect-error 可执行 ESM 门禁脚本没有 TypeScript 声明。
import { findForbiddenWebModules, verifyWebBuild } from '../../../scripts/verify-web-build.mjs'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..')
const baseline = JSON.parse(readFileSync(path.join(frontendRoot, 'baselines/web-structure.json'), 'utf8'))

describe('Web structure baseline', () => {
  it('freezes the Web entry and route/auth source responsibilities', () => {
    const html = readFileSync(path.join(frontendRoot, 'index.html'), 'utf8')
    const bootstrap = readFileSync(path.join(frontendRoot, 'web/app/bootstrap.ts'), 'utf8')
    expect(html).toContain(`src="${baseline.entry}"`)
    for (const suffix of baseline.authGuards.publicSuffixes) expect(bootstrap).toContain(`endsWith('${suffix}')`)
    for (const prefix of baseline.authGuards.tenantPrefixes) expect(bootstrap).toContain(`startsWith('${prefix}')`)
    expect(bootstrap).toContain(baseline.authGuards.tenantComposable)
  })

  it('rejects Desktop-only source modules from the Web artifact manifest', () => {
    expect(findForbiddenWebModules(['web/main.ts', 'desktop/pages/Chat.vue'], baseline.bundleForbiddenModules)).toContain('desktop/')
    expect(findForbiddenWebModules(['web/main.ts', 'web/router/agentRoutes.ts'], baseline.bundleForbiddenModules)).toEqual([])
  })

  it('removes the source module manifest only after artifact verification succeeds', async () => {
    const dist = mkdtempSync(path.join(os.tmpdir(), 'web-build-verifier-'))
    const manifest = path.join(dist, 'web-module-manifest.json')
    try {
      writeFileSync(path.join(dist, 'index.html'), '<script src="/assets/index.js"></script>')
      writeFileSync(manifest, JSON.stringify(['web/main.ts']))
      await expect(verifyWebBuild(dist, path.join(frontendRoot, 'baselines/web-structure.json'))).resolves.toBe(1)
      expect(existsSync(manifest)).toBe(false)

      writeFileSync(manifest, JSON.stringify(['desktop/main.ts']))
      await expect(verifyWebBuild(dist, path.join(frontendRoot, 'baselines/web-structure.json'))).rejects.toThrow('Desktop-only')
      expect(existsSync(manifest)).toBe(true)
    } finally {
      await rm(dist, { recursive: true, force: true })
    }
  })
})
