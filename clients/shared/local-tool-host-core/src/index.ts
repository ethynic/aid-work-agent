export interface LocalToolInvocation {
  readonly invocationId: string
  readonly providerId: string
  readonly toolName: string
  readonly arguments: Readonly<Record<string, unknown>>
}

/** Phase B 的 Host 壳端口；共用 Host Core、Executor 与 Provider 生命周期在 E1～E3 实现。 */
export interface LocalToolHostCore {
  invoke(request: LocalToolInvocation, signal: AbortSignal): Promise<unknown>
  shutdown(): Promise<void>
}

export { RuntimeHost, ManagementError } from './management.js'
export type { HostAdapter, HostStatus, ConnectionStatus, DeviceSummary, PluginSummary, ManagementOperation, PreparedPluginImport } from './management.js'
export { acquireHostLease } from './instanceLease.js'
export type { SelectedPackageInput, SelectedPackageSnapshot, RuntimePlatform, RuntimePlatformRequest, RuntimePlatformResponse } from './platform.js'
export { verifyOfflinePackage, extractVerifiedPackage, safePackagePath, canonicalJson, sha256, PACKAGE_LIMITS, publisherPublicKey } from './plugins/package.js'
export type { PublisherTrust, PackagePlatform, RuntimeManifest, ReleaseEnvelope, VerifiedPackage } from './plugins/package.js'
