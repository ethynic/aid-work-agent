/**
 * Playwright 全局 setup：登录真实后端并生成 storageState 认证快照。
 * 账号环境变量缺失时跳过（此时依赖已存在的 .auth/*.json 快照）。
 */
import { refreshStorageStates } from './scripts/refresh-auth.mjs'

export default async function globalSetup() {
  await refreshStorageStates()
}
