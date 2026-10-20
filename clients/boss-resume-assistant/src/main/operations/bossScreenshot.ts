/** 实验采图：只采当前 BOSS 页面，不导航、不滚动、不执行恢复动作。 */
import { createHash } from 'node:crypto'
import { writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { defaultSessionFactory, runBossOperation, type BossSessionFactory } from './bossContext.js'
import type { BossOperation } from './types.js'

export function pngMetadata(image: Buffer): { width: number; height: number; sha256: string; space: 'page_image_px' } {
  if (image.length < 24 || !image.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])) ||
      image.readUInt32BE(8) !== 13 || image.toString('ascii', 12, 16) !== 'IHDR') {
    throw new Error('截图必须是有效 PNG（缺少 IHDR）')
  }
  const width = image.readUInt32BE(16)
  const height = image.readUInt32BE(20)
  if (width === 0 || height === 0) throw new Error('PNG 图片尺寸必须大于 0')
  return { width, height, sha256: createHash('sha256').update(image).digest('hex'), space: 'page_image_px' }
}

export function createBossScreenshotOperation(sessionFactory: BossSessionFactory = defaultSessionFactory): BossOperation<{ output: string; snapshot_output?: string }> {
  return {
    name: 'boss_screenshot',
    execute(args, ctx) {
      return runBossOperation({ kind: 'readonly', name: 'boss_screenshot' }, ctx, sessionFactory,
        () => {
          if (typeof args.output !== 'string' || !args.output.trim()) return 'screenshot 需要 --output <PNG路径>'
          if (args.snapshot_output !== undefined && (typeof args.snapshot_output !== 'string' || !args.snapshot_output.trim())) return '--snapshot-output 需要JSON文件路径'
          if (args.snapshot_output && resolve(args.output).toLowerCase() === resolve(args.snapshot_output).toLowerCase()) return '截图与快照不能写入同一文件'
          return null
        },
        async (session) => {
          const image = await session.captureFullpage()
          const metadata = pngMetadata(image)
          const output = resolve(args.output)
          await writeFile(output, image)
          const snapshotOutput = args.snapshot_output ? resolve(args.snapshot_output) : undefined
          if (snapshotOutput) await writeFile(snapshotOutput, JSON.stringify(await session.snapshot()))
          return { message: '已保存当前 BOSS 页面截图（不包含 Chrome 浏览器界面）', data: { output, ...metadata, ...(snapshotOutput ? { snapshot_output: snapshotOutput } : {}) } }
        })
    },
  }
}
