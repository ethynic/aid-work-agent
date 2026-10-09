import { createHash, randomUUID } from 'node:crypto'
import { open, stat, mkdir, mkdtemp, realpath, rename, rm } from 'node:fs/promises'
import type { FileHandle } from 'node:fs/promises'
import path from 'node:path'
import type { SelectedPackageInput, SelectedPackageSnapshot } from '@aid/local-tool-host-core'

export class SelectionError extends Error { readonly code = 7 }
// Match the first-party Host's outer archive budget; expanded payload has a separate limit.
const MAX_PACKAGE_BYTES = 512 * 1024 * 1024
interface Selection {
  owner: number; instance: string; key: string; file: FileHandle; source: string
  identity: string; digest: string; size: number; expires: number; valid: boolean
  snapshot?: SelectedPackageSnapshot; taking?: Promise<SelectedPackageSnapshot>
}
const identity = (value: Awaited<ReturnType<FileHandle['stat']>>) => `${value.dev}:${value.ino}:${value.size}:${value.mtimeMs}:${value.ctimeMs}`

/** Native dialog paths never cross the renderer boundary. */
export class RuntimeSelections {
  private readonly selections = new Map<string, Selection>()
  constructor(private readonly home: string, private readonly now = () => Date.now()) {}

  async choose(source: string, owner: number, instance: string, key: string): Promise<{ selection_ref: string; label: string }> {
    let file: FileHandle | undefined
    try {
      file = await open(source, 'r')
      const before = await file.stat()
      if (!before.isFile() || before.size <= 0 || before.size > MAX_PACKAGE_BYTES) throw new SelectionError('离线包大小不符合要求')
      const hash = createHash('sha256'); let size = 0
      for await (const chunk of file.createReadStream({ start: 0, autoClose: false })) {
        size += chunk.length
        if (size > MAX_PACKAGE_BYTES) throw new SelectionError('离线包超过大小限制')
        hash.update(chunk)
      }
      const after = await file.stat()
      if (identity(before) !== identity(after) || size !== before.size || identity(await stat(source)) !== identity(before)) throw new SelectionError('离线包已改变，请重新选择')
      const ref = randomUUID()
      this.selections.set(ref, { owner, instance, key, file, source, identity: identity(before), digest: hash.digest('hex'), size, expires: this.now() + 600_000, valid: true })
      return { selection_ref: ref, label: path.basename(source) }
    } catch (error) {
      await file?.close().catch(() => {})
      throw error instanceof SelectionError ? error : new SelectionError('离线包不可读取或已改变，请重新选择')
    }
  }

  async take(input: SelectedPackageInput, owner: number): Promise<SelectedPackageSnapshot> {
    const selection = this.selections.get(input.selection_ref)
    if (!selection || !selection.valid || selection.owner !== owner || selection.instance !== input.instance_id || selection.key !== input.request_key) throw new SelectionError('选包引用已失效，请重新选择')
    if (selection.snapshot) return { ...selection.snapshot }
    if (selection.taking) return selection.taking
    if (selection.expires < this.now()) throw new SelectionError('选包引用已过期，请重新选择')
    selection.taking = this.stage(selection)
    try { return await selection.taking }
    catch (error) { selection.valid = false; throw error }
    finally { await selection.file.close(); selection.taking = undefined }
  }

  private async stage(selection: Selection): Promise<SelectedPackageSnapshot> {
    const home = await realpath(this.home)
    const parent = path.join(home, 'staging')
    await mkdir(parent, { recursive: true, mode: 0o700 })
    const resolved = await realpath(parent)
    if (path.relative(home, resolved) !== 'staging') throw new SelectionError('暂存目录不可用')
    if (identity(await selection.file.stat()) !== selection.identity || identity(await stat(selection.source)) !== selection.identity) throw new SelectionError('离线包已替换，请重新选择')
    const directory = await mkdtemp(path.join(resolved, 'desktop-selection-'))
    const temporary = path.join(directory, 'package.tmp')
    const staged = path.join(directory, 'package.aidplugin.zip')
    const output = await open(temporary, 'wx', 0o600)
    let complete = false
    try {
      const hash = createHash('sha256'); let size = 0
      for await (const chunk of selection.file.createReadStream({ start: 0, autoClose: false })) {
        size += chunk.length
        if (!selection.valid || size > MAX_PACKAGE_BYTES) throw new SelectionError('选包引用已失效')
        hash.update(chunk); await output.writeFile(chunk)
      }
      const digest = hash.digest('hex')
      if (!selection.valid || size !== selection.size || digest !== selection.digest || identity(await selection.file.stat()) !== selection.identity || identity(await stat(selection.source)) !== selection.identity) throw new SelectionError('离线包已改变，请重新选择')
      await output.sync(); await output.close(); await rename(temporary, staged)
      selection.snapshot = { staged_path: staged, size, sha256: digest }
      complete = true
      return { ...selection.snapshot }
    } finally {
      if (!complete) { await output.close().catch(() => {}); await rm(temporary, { force: true }) }
      // Accepted snapshots belong to Host recovery; Main never removes them.
    }
  }

  async invalidate(owner?: number): Promise<void> {
    for (const [ref, selection] of this.selections) {
      if (owner !== undefined && selection.owner !== owner) continue
      selection.valid = false; this.selections.delete(ref)
      if (selection.taking) await selection.taking.catch(() => {})
      await selection.file.close().catch(() => {})
    }
  }
}
