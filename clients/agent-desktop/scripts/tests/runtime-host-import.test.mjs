import assert from 'node:assert/strict'
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { desktopRoot, verifyHostDependencyGraph, assertNoSensitiveAssets } from '../prepare-runtime.mjs'

test('固定Node导入门核对搬迁后的传递依赖，并保持受管入口不启动Host', () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'aid-host-import-test-'))
  try {
    const node = path.join(desktopRoot, 'build/runtime-cache/node-v22.23.3-win-x64/node.exe')
    const entry = path.join(root, 'managed-entry.js')
    const marker = path.join(root, 'unexpected-start.txt')
    writeFileSync(path.join(root, 'package.json'), JSON.stringify({ type: 'module' }))
    writeFileSync(entry, `import { writeFileSync } from 'node:fs'; import './dependency.js'; if (process.argv[1]?.endsWith('managed-entry.js')) writeFileSync(${JSON.stringify(marker)}, 'unexpected');`)
    writeFileSync(path.join(root, 'dependency.js'), 'export const value = 1;')
    assert.doesNotThrow(() => verifyHostDependencyGraph(node, entry))
    assert.equal(existsSync(marker), false)
    rmSync(path.join(root, 'dependency.js'))
    assert.throws(() => verifyHostDependencyGraph(node, entry))
    assert.equal(existsSync(marker), false)
    // Complete resources scanning must allow bundled Node/OpenSSL marker strings.
    mkdirSync(path.join(root, 'resources'))
    assert.doesNotThrow(() => assertNoSensitiveAssets(path.join(desktopRoot, 'build/runtime-cache/node-v22.23.3-win-x64')))
  } finally {
    if (path.dirname(path.resolve(root)) !== path.resolve(os.tmpdir()) || !path.basename(root).startsWith('aid-host-import-test-')) throw new Error('Unsafe test cleanup target')
    rmSync(root, { recursive: true, force: true })
  }
})
