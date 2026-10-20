/** Local opaque references keep a recovery sequence on its original CDP target. */
import { createHash, randomUUID } from 'node:crypto'
import { mkdir, readFile, writeFile, lstat, realpath, unlink } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { CodedOperationError } from '../operations/types.js'

const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i
export interface BrowserTargetBinding { target_id: string; cdp_port: number }
const root = join(tmpdir(), 'aid-boss-targets')
export function targetBindingStale(): CodedOperationError {
  return new CodedOperationError('UI_CHANGED', '原 BOSS 标签绑定已失效，未重新选择其他标签或投递输入', { recovery_action_error: 'STALE_OBSERVATION', input_status: 'not_started' })
}
function validBinding(value: unknown): value is BrowserTargetBinding {
  if (!value || typeof value !== 'object') return false
  const b = value as BrowserTargetBinding
  return typeof b.target_id === 'string' && !!b.target_id && b.target_id.length <= 256 && !/[\x00-\x1f]/.test(b.target_id)
    && Number.isInteger(b.cdp_port) && b.cdp_port > 0 && b.cdp_port <= 65535
}
async function ordinaryRoot(): Promise<void> {
  await mkdir(root, { recursive: true, mode: 0o700 })
  if ((await lstat(root)).isSymbolicLink() || (await realpath(root)).toLowerCase() !== resolve(root).toLowerCase()) throw targetBindingStale()
}
export async function rememberBrowserTarget(targetId: string, cdpPort: number): Promise<string> {
  const binding = { target_id: targetId, cdp_port: cdpPort }
  if (!validBinding(binding)) throw targetBindingStale()
  await ordinaryRoot()
  const index = join(root, `${createHash('sha256').update(`${cdpPort}:${targetId}`).digest('hex')}.target`)
  const existing = async (): Promise<string> => {
    if ((await lstat(index)).isSymbolicLink()) throw targetBindingStale()
    const ref = await readFile(index, 'utf8')
    const value = await resolveBrowserTarget(ref, cdpPort)
    if (value.target_id !== targetId) throw targetBindingStale()
    return ref
  }
  try { return await existing() }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error }
  const ref = randomUUID(), file = join(root, `${ref}.json`)
  await writeFile(file, JSON.stringify(binding), { flag: 'wx', mode: 0o600 })
  try { await writeFile(index, ref, { flag: 'wx', mode: 0o600 }); return ref }
  catch (error) {
    await unlink(file)
    if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error
    return existing()
  }
}
export async function resolveBrowserTarget(ref: string, cdpPort: number): Promise<BrowserTargetBinding> {
  if (!UUID.test(ref)) throw targetBindingStale()
  try {
    await ordinaryRoot()
    const file = join(root, `${ref.toLowerCase()}.json`)
    if ((await lstat(file)).isSymbolicLink()) throw targetBindingStale()
    const binding: unknown = JSON.parse(await readFile(file, 'utf8'))
    if (!validBinding(binding) || binding.cdp_port !== cdpPort) throw targetBindingStale()
    return binding
  } catch { throw targetBindingStale() }
}
