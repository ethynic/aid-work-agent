import { createHash } from 'node:crypto'
import { mkdirSync, realpathSync } from 'node:fs'
import { createServer } from 'node:net'
import { tmpdir, userInfo } from 'node:os'
import { join } from 'node:path'

export interface InstanceLease { home: string; release(): Promise<void> }

/** An OS-owned lease; it carries no management messages or device credentials. */
export async function acquireHostLease(home: string): Promise<InstanceLease | null> {
  mkdirSync(home, { recursive: true })
  const resolved = realpathSync(home)
  const identity = process.platform === 'win32' ? resolved.toLowerCase() : resolved
  const scope = createHash('sha256').update(`${userInfo().username}|${identity}`).digest('hex').slice(0, 32)
  const address = process.platform === 'win32'
    ? `\\\\.\\pipe\\AidWorkAgent.RuntimeHost.${scope}`
    : join(tmpdir(), `aidwork-host-${scope}.sock`)
  const server = createServer(socket => socket.destroy())
  const acquired = await new Promise<boolean>((resolve, reject) => {
    server.once('error', error => {
      if (['EADDRINUSE', 'EACCES'].includes((error as NodeJS.ErrnoException).code ?? '')) resolve(false)
      else reject(error)
    })
    server.listen(address, () => resolve(true))
  })
  if (!acquired) return null
  server.on('error', () => { /* The lease is released only by safe Host disposal. */ })
  let released = false
  return { home: resolved, async release() {
    if (released) return
    released = true
    await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
  } }
}
