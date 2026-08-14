import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

test('local tool host core skeleton has no Coordinator, Electron or UI dependency', async () => {
  const source = await readFile(new URL('../src/index.ts', import.meta.url), 'utf8')
  assert.doesNotMatch(source, /(?:agent-coordinator-core|electron|frontend\/|@desktop|@web)/)
  assert.match(source, /LocalToolHostCore/)
})
