/**
 * CLI 子命令 version：版本与 manifest digest（标准 §4 强制命令面）。
 *
 * 用法：
 *   node dist/src/cli/index.js version           # 人类可读
 *   node dist/src/cli/index.js version --json    # 机器可读 manifest（含 schema_digest）
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
  console.log(`boss-resume-assistant ${manifest.provider_version}`)
  console.log(`provider_id: ${manifest.provider_id}`)
  console.log(`schema_digest: ${manifest.schema_digest}`)
  console.log(`tools: ${manifest.tools.map((t) => t.name).join(', ')}`)
  return 0
}
