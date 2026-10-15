/**
 * 真机 DPAPI 冒烟（手动运行，不在 npm test 默认链）：protect → unprotect 往返一致性。
 *
 * 运行：npm run build:main && node dist/../scripts/smoke-dpapi.mjs（即先构建后
 *   node scripts/smoke-dpapi.mjs；导入的是 dist/src 编译产物）
 * 前置：Windows + PowerShell（CurrentUser ProtectedData 无需管理员）。
 * 通过标准：退出码 0（往返一致）；非 0 = DPAPI 环境不可用/往返不一致。
 * 冒烟数据为一次性合成串，不落盘、不回显密文。
 */
import { protectText, unprotectText } from '../dist/src/security/dpapi.js'

const probe = `wecom-cli-dpapi-smoke:${process.pid}:${Date.now()}`

const blob = await protectText(probe).catch((err) => {
  console.error('SMOKE_FAIL protect:', err instanceof Error ? err.message : err)
  process.exit(1)
})
const roundtrip = await unprotectText(blob).catch((err) => {
  console.error('SMOKE_FAIL unprotect:', err instanceof Error ? err.message : err)
  process.exit(1)
})
if (roundtrip !== probe) {
  console.error('SMOKE_FAIL: 往返不一致')
  process.exit(1)
}
console.log('SMOKE_OK: DPAPI protect/unprotect 往返一致（CurrentUser）')
