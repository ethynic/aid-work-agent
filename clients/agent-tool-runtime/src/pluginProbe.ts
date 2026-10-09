import { execFile } from 'node:child_process'
import { join } from 'node:path'
import { ManagementError, canonicalJson, type RuntimeManifest } from '@aid/local-tool-host-core'

function readOnlyCommand(node: string, root: string, entry: string, args: string[], env: Record<string, string>): Promise<{ code: number; value: Record<string, unknown> }> {
  return new Promise((resolve, reject) => execFile(node, [join(root, entry), ...args],
    { cwd: root, env, windowsHide: true, shell: false, timeout: 5000, maxBuffer: 2 * 1024 ** 2, encoding: 'utf8' },
    (error, stdout) => {
      if (error && (typeof error.code !== 'number' || error.killed)) { reject(new ManagementError(9, '插件只读诊断未完成，请检查本机运行条件')); return }
      try {
        const value = JSON.parse(stdout) as Record<string, unknown>
        if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error()
        resolve({ code: error ? error.code as number : 0, value })
      } catch { reject(new ManagementError(8, '插件只读版本或诊断报告无效')) }
    }))
}

/** Runs signed, already verified entrypoints only; never invokes MCP business tools. */
export async function probePlugin(node: string, root: string, manifest: RuntimeManifest, env: Record<string, string>): Promise<{ ready: boolean; reason?: string }> {
  const version = await readOnlyCommand(node, root, manifest.entrypoint[0]!, ['version', '--json'], env)
  for (const key of ['provider_id', 'provider_version', 'protocol', 'transport', 'platforms', 'entrypoint', 'tools', 'schema_digest', 'execution_target']) {
    if (version.code !== 0 || version.value[key] === undefined || canonicalJson(version.value[key]) !== canonicalJson(manifest[key])) {
      throw new ManagementError(8, '插件实际版本与完整manifest不一致')
    }
  }
  let doctor: Awaited<ReturnType<typeof readOnlyCommand>>
  try { doctor = await readOnlyCommand(node, root, manifest.entrypoint[0]!, ['doctor', '--json'], env) }
  catch (error) {
    if (error instanceof ManagementError && error.code === 9) return { ready: false, reason: error.message }
    throw error
  }
  const readiness = doctor.value.runtime_readiness as { ready?: unknown; reason?: unknown } | undefined
  if (!readiness || typeof readiness.ready !== 'boolean') throw new ManagementError(8, '插件缺少机器可读就绪诊断')
  const ready = doctor.code === 0 && doctor.value.success === true && readiness.ready
  return ready ? { ready: true } : { ready: false, reason: typeof readiness.reason === 'string' && readiness.reason ? readiness.reason.slice(0, 200) : '请检查桌面环境并启动、登录业务应用' }
}
