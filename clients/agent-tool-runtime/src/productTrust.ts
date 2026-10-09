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
      || !Array.isArray(trust.roots) || !trust.roots.length || trust.roots.length > 16) throw new Error()
    const keys = new Set<string>()
    for (const root of trust.roots as PublisherTrust[]) {
      if (!root || Object.keys(root).sort().join(',') !== 'key_id,providers,public_key,test_only' || typeof root.key_id !== 'string' || !root.key_id
        || keys.has(root.key_id) || typeof root.public_key !== 'string' || typeof root.test_only !== 'boolean'
        || !Array.isArray(root.providers) || !root.providers.length
        || root.providers.some(provider => !['ai.aidwork.boss-recruiting', 'ai.aidwork.weixin', 'ai.aidwork.wecom'].includes(provider))
        || publisherPublicKey(root.public_key).asymmetricKeyType !== 'ed25519'
        || (product.profile === 'production' && root.test_only)) throw new Error()
      keys.add(root.key_id)
    }
    return trust.roots as PublisherTrust[]
  } catch { throw new ManagementError(9, 'Runtime产品或发布信任配置无效') }
}
