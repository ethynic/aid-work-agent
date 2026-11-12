import { existsSync, lstatSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { ManagementError, publisherPublicKey, type PublisherTrust } from '@aid/local-tool-host-core'

/** Reads fixed assets relative to the managed entry, never a renderer/env/argv path. */
export function loadPackagedTrust(entryDirectory: string): PublisherTrust[] | undefined {
  const resources = resolve(entryDirectory, '../..')
  const productFile = join(resources, 'config/runtime-product.json')
  if (!existsSync(productFile)) return undefined // CLI/A1 assets do not gain plugin-install authority.
  try {
    const read = (file: string): unknown => {
      for (let path = file; path !== dirname(path); path = dirname(path)) if (lstatSync(path).isSymbolicLink()) throw new Error()
      const stat = lstatSync(file)
      if (!stat.isFile() || stat.size > 65536) throw new Error()
      return JSON.parse(readFileSync(file, 'utf8'))
    }
    const product = read(productFile) as Record<string, unknown>
    if (Object.keys(product).sort().join(',') !== 'productKind,profile,schemaVersion' || product.schemaVersion !== 1
      || !['desktop', 'runtime'].includes(product.productKind as string) || !['production', 'acceptance'].includes(product.profile as string)) throw new Error()
    const trust = read(join(resources, 'runtime/trust-roots.json')) as Record<string, unknown>
    if (Object.keys(trust).sort().join(',') !== 'profile,roots,schemaVersion' || trust.schemaVersion !== 1 || trust.profile !== product.profile
      || !Array.isArray(trust.roots) || !trust.roots.length) throw new Error()
    // Historical publishers accumulate across releases; the file read already bounds input size.
    const keys = new Set<string>()
    for (const root of trust.roots as PublisherTrust[]) {
      if (!root || Object.keys(root).sort().join(',') !== 'key_id,providers,public_key,test_only' || typeof root.key_id !== 'string' || !root.key_id
        || keys.has(root.key_id) || typeof root.public_key !== 'string' || typeof root.test_only !== 'boolean'
        || !Array.isArray(root.providers) || !root.providers.length
        // 契约 2.20 修订：provider 身份由信任配置数据驱动（第一方 ai.aidwork. 命名空间），
        // 往 trust-roots.json 加新身份即可安装接入，不随源码写死名单扩张
        || root.providers.some(provider => typeof provider !== 'string' || !provider.startsWith('ai.aidwork.') || provider.length === 'ai.aidwork.'.length)
        || publisherPublicKey(root.public_key).asymmetricKeyType !== 'ed25519'
        || (product.profile === 'production' && root.test_only)) throw new Error()
      keys.add(root.key_id)
    }
    return trust.roots as PublisherTrust[]
  } catch { throw new ManagementError(9, 'Runtime产品或发布信任配置无效') }
}

/** 安装白名单（契约 2.20 修订）：产品信任配置 trust roots 覆盖的 provider_id 并集
 *  （去重、保持首现序）。无信任配置的 CLI/A1 资产不传 providers，PluginStore 维持
 *  FIRST_PARTY_PROVIDERS 默认。 */
export function providerIdsOfTrust(trust: readonly PublisherTrust[]): string[] {
  return [...new Set(trust.flatMap(root => root.providers))]
}
