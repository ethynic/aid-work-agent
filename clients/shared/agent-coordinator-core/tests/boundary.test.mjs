import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

test('coordinator core skeleton has no Electron or renderer dependency', async () => {
  const source = await readFile(new URL('../src/index.ts', import.meta.url), 'utf8')
  assert.doesNotMatch(source, /(?:electron|frontend\/|@desktop|@web)/)
  assert.match(source, /AgentCoordinatorDependencies/)
})
