import { createPublicKey } from 'node:crypto'
import { existsSync, readFileSync, statSync } from 'node:fs'
import path from 'node:path'

export type ProductKind = 'desktop' | 'runtime'
export type ProductProfile = 'production' | 'acceptance'
export interface ProductConfiguration {
  kind: ProductKind
  profile: ProductProfile
  appId: string
  productName: string
  userDataPath: string | null
  runtimeEnabled: boolean
  runtimeHome: string | null
  nodeExecutable: string | null
  hostEntry: string | null
  trustRootsPath: string | null
}

export const RUNTIME_IDENTITIES = {
  production: { appId: 'cn.aidingyi.agent.runtime', productName: 'AID Work Runtime', userData: 'aidwork-runtime-app', home: 'aidwork-tool-runtime' },
  acceptance: { appId: 'cn.aidingyi.agent.runtime.acceptance', productName: 'AID Work Runtime 验收版', userData: 'aidwork-runtime-app-acceptance', home: 'aidwork-tool-runtime-acceptance' },
} as const

function isPublicKeyPem(value: unknown): value is string {
  // createPublicKey also accepts private keys; reject them before invoking it.
  return typeof value === 'string' && /^-----BEGIN PUBLIC KEY-----\r?\n(?:[A-Za-z0-9+/]{1,64}={0,2}\r?\n)+-----END PUBLIC KEY-----\r?\n?$/.test(value)
}

export function resolveProductConfiguration(input: { resourcesPath: string; isPackaged: boolean; appDataPath: string }): ProductConfiguration {
  const file = path.join(input.resourcesPath, 'config/runtime-product.json')
  if (!existsSync(file)) return {
    kind: 'desktop', profile: 'production', appId: 'cn.aidingyi.agent.desktop', productName: 'AID Work Agent',
    userDataPath: null, runtimeEnabled: false, runtimeHome: null, nodeExecutable: null, hostEntry: null, trustRootsPath: null,
  }
  const record = JSON.parse(readFileSync(file, 'utf8')) as Record<string, unknown>
  if (Object.keys(record).sort().join(',') !== 'productKind,profile,schemaVersion'
      || record.schemaVersion !== 1 || (record.productKind !== 'runtime' && record.productKind !== 'desktop')
      || (record.profile !== 'acceptance' && record.profile !== 'production')) throw new Error('Runtime product configuration invalid')
  const profile = record.profile
  const identity = record.productKind === 'runtime' ? RUNTIME_IDENTITIES[profile] : profile === 'production'
    ? { appId: 'cn.aidingyi.agent.desktop', productName: 'AID Work Agent', userData: null, home: 'aidwork-tool-runtime' }
    : { appId: 'cn.aidingyi.agent.desktop.acceptance', productName: 'AID Work Agent 验收版', userData: 'aidwork-desktop-app-acceptance', home: 'aidwork-tool-runtime-acceptance' }
  if (!path.isAbsolute(input.appDataPath) || !path.isAbsolute(input.resourcesPath)) throw new Error('Runtime product paths must be absolute')
  const nodeExecutable = path.join(input.resourcesPath, 'runtime/node/win-x64/node.exe')
  const hostEntry = path.join(input.resourcesPath, 'runtime/host/managed-entry.js')
  const trustRootsPath = path.join(input.resourcesPath, 'runtime/trust-roots.json')
  for (const asset of [nodeExecutable, hostEntry, trustRootsPath]) {
    if (!existsSync(asset) || !statSync(asset).isFile()) throw new Error('Runtime product asset missing')
  }
  const trustBytes = readFileSync(trustRootsPath)
  const trust = JSON.parse(trustBytes.toString('utf8'))
  if (trust.schemaVersion !== 1 || trust.profile !== profile || !Array.isArray(trust.roots) || trust.roots.length === 0
      || Object.keys(trust).sort().join(',') !== 'profile,roots,schemaVersion') throw new Error('Runtime trust configuration invalid')
  const keys = new Set<string>()
  for (const root of trust.roots) {
    if (!root || Object.keys(root).sort().join(',') !== 'key_id,providers,public_key,test_only' || typeof root.key_id !== 'string' || !root.key_id.trim()
        || typeof root.test_only !== 'boolean' || keys.has(root.key_id)
        || !isPublicKeyPem(root.public_key) || !Array.isArray(root.providers) || !root.providers.length
        || root.providers.some((provider: unknown) => typeof provider !== 'string' || !provider.trim())
        || createPublicKey(root.public_key).asymmetricKeyType !== 'ed25519') throw new Error('Runtime publisher trust invalid')
    if (profile === 'production' && root.test_only) throw new Error('Production Runtime cannot contain test publisher trust')
    keys.add(root.key_id)
  }
  return {
    kind: record.productKind, profile, ...{ appId: identity.appId, productName: identity.productName },
    userDataPath: identity.userData ? path.join(input.appDataPath, identity.userData) : null, runtimeEnabled: true,
    runtimeHome: path.join(input.appDataPath, identity.home), nodeExecutable, hostEntry, trustRootsPath,
  }
}
