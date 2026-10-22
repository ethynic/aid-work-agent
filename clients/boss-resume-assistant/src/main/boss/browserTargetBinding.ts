/** Local opaque references keep a recovery sequence on its original CDP target. */
import { createHash, randomUUID } from 'node:crypto'
import { mkdir, readFile, writeFile, lstat, realpath, unlink, rename } from 'node:fs/promises'
import { createServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { CodedOperationError } from '../operations/types.js'
import type { FilterSpec } from './FilterSetter.js'

const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i
export interface BrowserPageContext {
  entry_url: string
  account_ref?: string
  job_name?: string
  filters?: { clear: true } | { spec: FilterSpec }
  browser_instance?: string
  managed_profile_dir?: string
}
export interface BrowserTargetBinding { target_id: string; cdp_port: number; page_context?: BrowserPageContext; restore_pending?: boolean }
const root = join(tmpdir(), 'aid-boss-targets')
const targetIndex = (port: number, target: string) => join(root, `${createHash('sha256').update(`${port}:${target}`).digest('hex')}.target`)
function bindingLockName(ref: string): string {
  const identity = `${process.platform === 'win32' ? resolve(root).toLowerCase() : resolve(root)}:${ref.toLowerCase()}`
  const digest = createHash('sha256').update(identity).digest('hex')
  if (process.platform === 'win32') return `\\\\.\\pipe\\AidWorkAgent.BossTarget.${digest}`
  const [major, minor] = process.versions.node.split('.').map(Number)
  if (process.platform === 'linux' && (major! > 20 || major === 20 && minor! >= 8)) return `\0aid-boss-target-${digest}`
  throw new CodedOperationError('INTERNAL_ERROR', '本机页面绑定锁需要 Windows，或使用 Node 20.8+ 的 Linux 开发环境')
}
export function targetBindingStale(): CodedOperationError {
  return new CodedOperationError('UI_CHANGED', '原 BOSS 标签绑定已失效，未重新选择其他标签或投递输入', { recovery_action_error: 'STALE_OBSERVATION', input_status: 'not_started' })
}
function validBinding(value: unknown): value is BrowserTargetBinding {
  if (!value || typeof value !== 'object') return false
  const b = value as BrowserTargetBinding
  return typeof b.target_id === 'string' && !!b.target_id && b.target_id.length <= 256 && !/[\x00-\x1f]/.test(b.target_id)
    && Number.isInteger(b.cdp_port) && b.cdp_port > 0 && b.cdp_port <= 65535
    && (b.restore_pending === undefined || typeof b.restore_pending === 'boolean')
    && (b.page_context === undefined || validPageContext(b.page_context))
}
function validPageContext(value: BrowserPageContext): boolean {
  if (!value || typeof value !== 'object' || value.account_ref !== undefined && !/^[a-f0-9]{64}$/.test(value.account_ref)) return false
  if (!['https://www.zhipin.com/web/chat/recommend', 'https://www.zhipin.com/web/chat/index'].includes(value.entry_url)) return false
  return (value.job_name === undefined || typeof value.job_name === 'string' && !!value.job_name.trim() && value.job_name.length <= 100)
    && (value.browser_instance === undefined || /^[a-f0-9]{64}$/.test(value.browser_instance))
    && (value.managed_profile_dir === undefined || typeof value.managed_profile_dir === 'string' && /^[A-Za-z]:[\\/]/.test(value.managed_profile_dir))
}
async function ordinaryRoot(): Promise<void> {
  await mkdir(root, { recursive: true, mode: 0o700 })
  if ((await lstat(root)).isSymbolicLink() || (await realpath(root)).toLowerCase() !== resolve(root).toLowerCase()) throw targetBindingStale()
}
export async function rememberBrowserTarget(targetId: string, cdpPort: number): Promise<string> {
  const binding = { target_id: targetId, cdp_port: cdpPort }
  if (!validBinding(binding)) throw targetBindingStale()
  await ordinaryRoot()
  const index = targetIndex(cdpPort, targetId)
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

/** Same opaque business binding; a changed target is installed only by its guarded recovery owner. */
export async function updateBrowserTarget(ref: string, port: number, expectedTarget: string,
  update: (binding: BrowserTargetBinding) => BrowserTargetBinding): Promise<void> {
  if (!UUID.test(ref)) throw targetBindingStale()
  await ordinaryRoot()
  // Kernel-owned IPC reservation follows the existing Runtime desktop-lock
  // pattern. A crashed owner leaves no file to reap, so contenders cannot race
  // while deleting a dead owner's lock or steal an old but live lock by TTL.
  const lock = createServer(socket => socket.destroy())
  let lockError: Error | undefined
  lock.on('error', error => { lockError = error })
  try {
    await new Promise<void>((accept, reject) => {
      lock.once('error', reject)
      lock.listen(bindingLockName(ref), () => { lock.off('error', reject); accept() })
    })
  } catch (error) {
    lock.close()
    if ((error as NodeJS.ErrnoException).code === 'EADDRINUSE') throw new CodedOperationError('BUSY', '原 BOSS 页面绑定正在更新，请稍后接续')
    throw error
  }
  const assertHeld = () => {
    if (lockError || !lock.listening) throw new CodedOperationError('INTERNAL_ERROR', '本机页面绑定锁已失效，未提交绑定更新')
  }
  const temporary = join(root, `${ref.toLowerCase()}.${randomUUID()}.tmp`)
  let createdIndex: string | undefined
  let installed = false
  try {
    const current = await resolveBrowserTarget(ref, port)
    if (current.target_id !== expectedTarget) throw targetBindingStale()
    const next = update(current)
    assertHeld()
    if (!validBinding(next) || next.cdp_port !== port) throw targetBindingStale()
    if (next.target_id !== current.target_id) {
      const index = targetIndex(port, next.target_id)
      try { await writeFile(index, ref, { flag: 'wx', mode: 0o600 }); createdIndex = index }
      catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'EEXIST' || (await lstat(index)).isSymbolicLink()
          || await readFile(index, 'utf8') !== ref) throw targetBindingStale()
      }
    }
    await writeFile(temporary, JSON.stringify(next), { flag: 'wx', mode: 0o600 })
    assertHeld()
    await rename(temporary, join(root, `${ref.toLowerCase()}.json`))
    installed = true
    if (next.target_id !== current.target_id) {
      const oldIndex = targetIndex(port, current.target_id)
      if (await readFile(oldIndex, 'utf8').catch(() => undefined) === ref) await unlink(oldIndex)
    }
  } finally {
    if (createdIndex && !installed) await unlink(createdIndex).catch(() => {})
    await unlink(temporary).catch(() => {})
    await new Promise<void>((accept, reject) => lock.close(error => error ? reject(error) : accept()))
  }
}
