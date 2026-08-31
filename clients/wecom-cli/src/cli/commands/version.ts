/**
 * CLI 子命令 version：版本与 manifest digest。
 *
 * 用法：
 *   aid-wecom version           # 人类可读
 *   aid-wecom version --json    # 机器可读 manifest（含 schema_digest）
 */
import { buildManifest } from '../../mcp/manifest.js'

export interface VersionCommandOptions {
  json?: boolean
}

export async function versionCommand(opts: VersionCommandOptions): Promise<number> {
  const manifest = buildManifest()
  if (opts.json) {
    console.log(JSON.stringify(manifest, null, 2))
    return 0
  }
  console.log(`aid-wecom ${manifest.provider_version}`)
  console.log(`provider_id: ${manifest.provider_id}`)
  console.log(`schema_digest: ${manifest.schema_digest}`)
  console.log(`tools: ${manifest.tools.map((t) => t.name).join(', ')}`)
  return 0
}
