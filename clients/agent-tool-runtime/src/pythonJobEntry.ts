/** Private fixed entry. The native supervisor assigns this process to a Job before resume. */
import { spawn } from 'node:child_process'
import { isAbsolute } from 'node:path'

async function run(): Promise<number> {
  const chunks: Buffer[] = []
  let bytes = 0
  for await (const chunk of process.stdin) {
    bytes += chunk.length
    if (bytes > 512 * 1024) return 1
    chunks.push(chunk)
  }
  const { python, argv, cwd } = JSON.parse(Buffer.concat(chunks).toString('utf8'))
  if (typeof python !== 'string' || !isAbsolute(python) || typeof cwd !== 'string' || !isAbsolute(cwd)
    || !Array.isArray(argv) || !argv.every(arg => typeof arg === 'string')) return 1
  return new Promise(resolve => {
    const child = spawn(python, argv, { cwd, shell: false, windowsHide: true, stdio: ['ignore', 'inherit', 'inherit'] })
    child.once('error', () => resolve(1))
    // The supervisor stops descendants after this fixed wrapper exits.
    child.once('exit', code => resolve(code ?? 1))
  })
}

run().then(code => { process.exitCode = code }, () => { process.exitCode = 1 })
