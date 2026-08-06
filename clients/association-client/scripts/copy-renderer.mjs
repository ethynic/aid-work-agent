/**
 * 复制 renderer 静态文件 + preload 到 dist/。
 */
import { cpSync, mkdirSync, existsSync, copyFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const dirname = path.dirname(fileURLToPath(import.meta.url))
const projectRoot = path.resolve(dirname, '..')

// 1. renderer 静态文件
const srcDir = path.resolve(projectRoot, 'src')
const destDir = path.resolve(projectRoot, 'dist', 'renderer')
if (!existsSync(srcDir)) {
  console.error(`renderer 源目录不存在: ${srcDir}`)
  process.exit(1)
}
mkdirSync(destDir, { recursive: true })
cpSync(srcDir, destDir, { recursive: true })
console.log(`renderer 已复制到 ${destDir}`)

// 2. preload.cts → preload.cjs（CommonJS，tsc 不编译 .cts）
const preloadSrc = path.resolve(projectRoot, 'electron', 'preload.cts')
const preloadDest = path.resolve(projectRoot, 'dist', 'electron', 'preload.cjs')
if (existsSync(preloadSrc)) {
  copyFileSync(preloadSrc, preloadDest)
  console.log(`preload 已复制到 ${preloadDest}`)
}
