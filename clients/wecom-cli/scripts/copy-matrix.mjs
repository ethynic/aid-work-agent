/**
 * 构建资产复制：把 src/platform/localdb/version-matrix.json 复制到 dist/src/platform/localdb/，
 * 与编译产物 matrix.js 同目录（loadMatrix 默认路径首选该发行位置）。
 * 打包器只整拷 dist/src + 登记 drivers 资产、不复制源码 src（runtime-plugin-packaging/package.mjs），
 * 因此矩阵必须进 dist 才能随包发布；仓库内源码态由 loadMatrix 的 src 相对路径回退覆盖。
 */
import { copyFileSync, mkdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const source = path.join(root, 'src', 'platform', 'localdb', 'version-matrix.json')
const destDir = path.join(root, 'dist', 'src', 'platform', 'localdb')
mkdirSync(destDir, { recursive: true })
copyFileSync(source, path.join(destDir, 'version-matrix.json'))
console.log('copied src/platform/localdb/version-matrix.json -> dist/src/platform/localdb/')
