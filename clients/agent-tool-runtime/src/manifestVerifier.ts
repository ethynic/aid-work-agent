/**
 * boss-recruiting manifest 校验兼容层（函数签名与行为不变，内容迁移至 providers.ts 注册表）。
 *
 * - claim 到的 tool_name 必须 ∈ TRUSTED_MANIFEST.tools，否则直接回 TOOL_NOT_ALLOWED。
 * - manifestDigest 随 heartbeat 上报，供云端核对（值与多 Provider 改造前一致）。
 * - 多 Provider 路由下的按 Provider 校验使用 providers.ts 的 getProviderManifest/
 *   isToolAllowedFor/isWriteToolFor；本文件服务 boss 默认链路及既有调用方。
 */
import { getProviderManifest, isToolAllowedFor, isWriteToolFor, manifestDigestFor } from './providers.js'

export const TRUSTED_MANIFEST = getProviderManifest('boss-recruiting')!

export function isToolAllowed(toolName: string): boolean {
  return isToolAllowedFor(TRUSTED_MANIFEST, toolName)
}

export function isWriteTool(toolName: string): boolean {
  return isWriteToolFor(TRUSTED_MANIFEST, toolName)
}

export function manifestDigest(): string {
  return manifestDigestFor('boss-recruiting')
}
