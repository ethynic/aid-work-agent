import { readdirSync } from 'node:fs'
import path from 'node:path'
import { spawnSync } from 'node:child_process'

const testRoot = path.resolve('dist/tests')
const testFiles = []

function collect(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    const file = path.join(directory, entry.name)
    if (entry.isDirectory()) collect(file)
    else if (entry.isFile() && entry.name.endsWith('.test.js')) testFiles.push(file)
  }
}

collect(testRoot)
if (testFiles.length === 0) throw new Error(`no compiled test files found in ${testRoot}`)

const result = spawnSync(process.execPath, ['--test', ...testFiles], { stdio: 'inherit' })
if (result.error) throw result.error
process.exit(result.status ?? 1)
