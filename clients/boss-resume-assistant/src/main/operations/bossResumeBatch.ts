/**
 * boss_resume_batch operation：推荐牛人页逐个点开当前视口牛人卡片 → 读取简历 → 自动关闭 → 下一份。
 *
 * 「打开简历→读简历→入库」串联的最后一环：模仿 greet 的逐个模式，每张卡
 * CDP 浏览类点击打开详情（真机 2026-08-17 实证卡片点击 CDP 有效）→ 复用 ResumeReader
 * 读取管线（Win32 滚轮回顶 → 分段截图 → 拼接 → OCR，见 ResumeBatchReader 头注释）→ Escape 关闭。
 *
 * 只读（effect=none），但每份简历的滚动借用真实鼠标约 30 秒，执行期间用户手不能碰鼠标。
 * 输出契约：data = { resumes: [单份契约 payload（buildResumePayload，与 boss_resume_detail 同契约，
 * name_source='dom'）], failures: [{name, error}], attempted }。云端 BossResumeBatchTool 逐份入简历库。
 * 单份失败（打开超时/读取失败/姓名无法确定/姓名交叉校验不过）记 failures 后继续下一份；全部失败仍 success（信息在 data）。
 */
import fsp from 'node:fs/promises'
import { ResumeBatchReader } from '../boss/ResumeBatchReader.js'
import { JobSwitcher, FILTER_BUTTON_PATTERN } from '../boss/JobSwitcher.js'
import { viewportOf } from '../boss/FilterSetter.js'
import { buildResumePayload, wheelAt, sameViewAt, stitchParts, ocrBatch } from './bossResumeDetail.js'
import {
  defaultSessionFactory,
  runBossOperation,
  type BossSessionFactory,
} from './bossContext.js'
import { CodedOperationError, type BossOperation, type OperationResult, type OpContext } from './types.js'

export interface BossResumeBatchArgs {
  /** 读取份数上限（默认 1；CLI 层最大 10，MCP schema 收紧到 3） */
  limit?: number
  /** 可选：拼接长图保存目录（每份存 `<姓名>.png`，目录自动创建） */
  save_dir?: string
}

/** operation 层 limit 上限；MCP schema 用 maximum=3 收紧（每份约 30 秒滚动+OCR，设计 §14 同 greet） */
const LIMIT_MAX = 10

export function createBossResumeBatchOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<BossResumeBatchArgs> {
  return {
    name: 'boss_resume_batch',
    execute(args: BossResumeBatchArgs, ctx: OpContext): Promise<OperationResult> {
      const limit = args?.limit ?? 1
      const saveDir = (args?.save_dir ?? '').trim()
      return runBossOperation(
        'readonly',
        ctx,
        sessionFactory,
        () => {
          if (!Number.isInteger(limit) || limit <= 0 || limit > LIMIT_MAX) {
            return `limit 必须是 1-${LIMIT_MAX} 的整数：${String(args?.limit)}`
          }
          if (args?.save_dir !== undefined && !saveDir) {
            return 'save_dir（拼接图保存目录）不能为空'
          }
          return null
        },
        async (session) => {
          // 前置校验：必须在推荐牛人列表页（无「筛选」按钮即 WRONG_PAGE）。
          // 已知局限（坑 17）：仍用文案判定——沟通页 DOM 内嵌推荐 iframe 时文案同样命中、会误通过
          // （本 op 只读、无外部写副作用）；boss_greet 已于 2026-08-26 改 URL 判定（/web/chat/recommend），
          // 本 op 待跟进同样改造。
          // 已打开的简历详情弹层不算错页：boss_resume_detail 读完不关详情，ResumeBatchReader
          // 入口会先 Escape 关掉残留弹层再点卡片（关不掉 fail-loud，防止把残留简历误记到卡片姓名下）。
          const probe = await session.snapshot()
          if (!probe.strings.some((s) => FILTER_BUTTON_PATTERN.test(s.trim()))) {
            throw new CodedOperationError(
              'WRONG_PAGE',
              '当前页面不是推荐牛人列表页（未找到「筛选」按钮）：请先用 boss_goto 切换到「推荐牛人」页',
            )
          }
          // job_name 一次取足：推荐页职位框当前职位（非推荐页为 null，契约可选）
          const jobBox = new JobSwitcher({ snapshot: session.snapshot, click: session.click }).locateJobBox(probe)
          const jobName = jobBox.name ?? null

          if (saveDir) {
            await fsp.mkdir(saveDir, { recursive: true })
          }
          ctx.progress({
            stage: 'execute',
            current: 0,
            total: limit,
            message: `逐个打开简历并读取（借用真实鼠标滚动，请勿移动）0/${limit}`,
          })
          const reader = new ResumeBatchReader({
            snapshot: session.snapshot,
            clickBrowse: (point) => session.clickBrowse(point),
            pressEscape: session.pressEscape,
            captureFullpage: session.captureFullpage,
            wheel: (rect, viewport, deltaY, notches) => wheelAt(rect, viewport, deltaY, notches),
            sameView: (a, b, rect) => sameViewAt(a, b, rect),
            stitch: stitchParts,
            ocrBatch,
            signal: ctx.signal,
            onProgress: (done) => {
              ctx.progress({
                stage: 'execute',
                current: done,
                total: limit,
                message: `逐个打开简历并读取（借用真实鼠标滚动，请勿移动）${done}/${limit}`,
              })
            },
          })
          const result = await reader.readBatch({ limit, saveDir: saveDir || undefined })
          const names = result.resumes.map((r) => r.name).join('、')
          let message: string
          if (result.resumes.length === 0 && result.failures.length > 0) {
            const first = result.failures[0]!
            message = `完成：未能读取任何简历（尝试 ${result.attempted} 张卡片全部失败，第一个失败：${first.name ?? '未知姓名'}—${first.error}）`
          } else {
            message = `完成：成功读取 ${result.resumes.length} 份简历（${names}）`
            if (result.failures.length > 0) {
              const first = result.failures[0]!
              message += `，失败 ${result.failures.length} 个（第一个：${first.name ?? '未知姓名'}—${first.error}）`
            }
          }
          // P1 接缝质量警告：任一份存在可疑接缝（错位/文本未对上）→ 提示人工核对（元信息随各份 payload 带）
          const seamSuspectCount = result.resumes.filter(
            (r) => r.readResult.suspectSeams.length > 0 || r.readResult.textSeamUnmatched.length > 0,
          ).length
          if (seamSuspectCount > 0) {
            const namesSuspect = result.resumes
              .filter((r) => r.readResult.suspectSeams.length > 0 || r.readResult.textSeamUnmatched.length > 0)
              .map((r) => r.name)
              .join('、')
            message += `；⚠️ ${seamSuspectCount} 份存在可疑拼接接缝（${namesSuspect}）：OCR 文本可能有重复/缺失，请人工核对拼接图`
          }
          return {
            message,
            data: {
              resumes: result.resumes.map((r) => buildResumePayload(r.name, jobName, r.readResult, 'dom')),
              failures: result.failures,
              attempted: result.attempted,
            },
            effect: 'none',
          }
        },
      )
    },
  }
}
