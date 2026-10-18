/** Experimental page-image click; reuse the existing page browsing input only. */
import { readFile, stat } from 'node:fs/promises'
import { defaultSessionFactory, runBossOperation, type BossSessionFactory } from './bossContext.js'
import { pngMetadata } from './bossScreenshot.js'
import { CancelledError, type BossOperation } from './types.js'

export interface PageClickExperimentArgs { image: string; x: number; y: number; maxAgeSeconds?: number }

export function createPageClickExperiment(sessionFactory: BossSessionFactory = defaultSessionFactory): BossOperation<PageClickExperimentArgs> {
  return {
    name: 'boss_page_click_experiment',
    execute(args, ctx) {
      const maxAge = args.maxAgeSeconds ?? 120
      return runBossOperation({ kind: 'readonly', name: 'boss_page_click_experiment' }, ctx, sessionFactory,
        () => !args.image?.trim() || ![args.x, args.y].every(Number.isInteger) || args.x < 0 || args.y < 0 ||
          !Number.isInteger(maxAge) || maxAge < 1 || maxAge > 600 ? 'page-click 需要网页PNG、非负整数坐标和1-600秒有效期' : null,
        async session => {
          const imageStat = await stat(args.image)
          if (imageStat.size > 10 * 1024 * 1024) throw new Error('page-click PNG不能超过10 MiB')
          const original = pngMetadata(await readFile(args.image))
          if (args.x >= original.width || args.y >= original.height) throw new Error('page-click 坐标超出网页原图')
          const current = pngMetadata(await session.captureFullpage())
          const age = Date.now() - imageStat.mtimeMs
          if (age < 0 || age > maxAge * 1000) throw new Error('STALE_OBSERVATION: 网页观察已过期，请重新截图')
          // Conservative experiment guard, not the production target-region verification policy.
          if (current.sha256 !== original.sha256 || current.width !== original.width || current.height !== original.height) {
            throw new Error('STALE_OBSERVATION: 网页图已变化，未执行点击')
          }
          if (ctx.signal.aborted) throw new CancelledError()
          await session.clickBrowse({ x: args.x, y: args.y })
          return { message: '已投递一次网页点击，需另行验证页面结果', effect: 'none', data: {
            input_status: 'attempted', x: args.x, y: args.y, space: 'page_image_px',
            width: original.width, height: original.height, image_sha256: original.sha256,
          } }
        })
    },
  }
}
