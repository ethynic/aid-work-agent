/**
 * Windows 打包脚本。
 *
 * 前置条件：
 *   1. CLI exe 已构建：cd ../association-client-cli && pyinstaller build.spec
 *   2. npm install 已执行
 *
 * 流程：
 *   1. 验证 CLI exe 存在
 *   2. electron-builder 打包（CLI exe 作为 extraResources）
 *   3. 输出安装包路径
 */
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { execSync } from 'node:child_process'

const dirname = path.dirname(fileURLToPath(import.meta.url))
const projectRoot = path.resolve(dirname, '..')
const cliExe = path.resolve(projectRoot, '..', 'association-client-cli', 'dist', 'association-cli.exe')

console.log('=== 协会信息收集助手 Windows 打包 ===\n')

// 1. 验证 CLI exe
if (!existsSync(cliExe)) {
  console.error(`[错误] CLI exe 不存在: ${cliExe}`)
  console.error('请先构建 CLI: cd ../association-client-cli && pyinstaller build.spec')
  process.exit(1)
}
console.log(`[OK] CLI exe: ${cliExe}`)

// 2. electron-builder（extraResources 已在 electron-builder.yml 静态配置，
//    不再动态注入——无论走 package-win.mjs 还是直接 npx electron-builder，
//    cli.exe 都会打入安装包，防漏打包）
console.log('\n[打包中] electron-builder...')
execSync('npx electron-builder --win', { cwd: projectRoot, stdio: 'inherit' })

console.log('\n=== 打包完成 ===')
console.log(`安装包在: ${path.resolve(projectRoot, 'release')}`)
