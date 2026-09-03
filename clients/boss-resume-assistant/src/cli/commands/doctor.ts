/**
 * CLI 子命令 doctor：只读环境检查（标准 §4 强制命令面）。
 *
 * 用法：
 *   node dist/src/cli/index.js doctor [--cdp-port 9222]
 *
 * 检查 ① win-click.ps1 存在 ② CDP 端点可连 ③ 能 attach zhipin.com 页面
 * ④ 页面含登录态特征（「筛选」按钮或侧边菜单文案）
 * ⑤ 姓名配对自检（2026-09-01 新增）：推荐页视口内「打招呼」按钮与卡片姓名配对——
 *   配对率低说明页面布局与锚定规则失配，定向打招呼会「滚遍列表也找不到人」。
 * ⑥ OCR 引擎（P2 2026-09-02 新增）：简历读取将使用的引擎（RapidOCR 主 + WinRT 兜底），
 *   报告 python 路径 / 兜底原因；WinRT 兜底是合法配置不算失败，强制 rapid 而不可用才失败。
 *   任一失败退出码 1。只读：绝不点击、不产生任何业务写动作。
 */
import { CdpGateway } from '../../main/cdp/CdpGateway.js'
import { DEFAULT_CDP_PORT } from '../../main/chrome/ChromeAttacher.js'
import { WinMouseClicker } from '../../main/input/WinMouseClicker.js'
import { benchRapidOcr, resolveOcrEngine } from '../../main/operations/bossResumeDetail.js'
import type { DomSnapshot } from '../../main/boss/domSnapshot.js'
import { viewportOf } from '../../main/boss/FilterSetter.js'
import { GREET_TEXT, findButtonsByExactText, pairCardName } from '../../main/boss/cardName.js'

export interface DoctorCommandOptions {
  cdpPort?: number
}

/** 登录态特征：推荐页「筛选」按钮或左侧菜单文案 */
const LOGIN_MARKERS = [/^筛选(·\d+)?$/, /^推荐牛人$/, /^沟通$/]

export async function doctorCommand(opts: DoctorCommandOptions): Promise<number> {
  const port = opts.cdpPort ?? DEFAULT_CDP_PORT
  const endpoint = `http://127.0.0.1:${port}`
  let failed = false
  const check = (ok: boolean, label: string, detail = '') => {
    console.log(`${ok ? '✅' : '❌'} ${label}${detail ? `：${detail}` : ''}`)
    if (!ok) failed = true
    return ok
  }

  // ① win-click.ps1 存在（WinMouseClicker 构造时探测路径，缺失即抛）
  try {
    new WinMouseClicker()
    check(true, 'win-click.ps1 点击脚本存在')
  } catch (e) {
    check(false, 'win-click.ps1 点击脚本存在', e instanceof Error ? e.message : String(e))
  }

  // ② CDP 端点可连
  const gw = new CdpGateway({ timeoutMs: 5000 })
  let connected = false
  try {
    await gw.connect(endpoint)
    connected = check(true, `CDP 端点可连（${endpoint}）`)
  } catch (e) {
    check(false, `CDP 端点可连（${endpoint}）`, '调试 Chrome 未运行；执行任意 boss 操作时会自动拉起（首次需在弹出的 Chrome 中登录 BOSS），或手动带 --remote-debugging-port 启动')
  }

  // ③ 能 attach zhipin.com 页面
  let attached = false
  if (connected) {
    try {
      await gw.attachToRecommendPage()
      attached = check(true, 'attach BOSS（zhipin.com）页面')
    } catch (e) {
      check(false, 'attach BOSS（zhipin.com）页面', '请确认已在该 Chrome 打开 BOSS 直聘页面')
    }
  }

  // ④ 页面含登录态特征 + ⑤ 姓名配对自检（共用同一份 snapshot，只读）
  if (attached) {
    try {
      const snap = (await gw.captureDomSnapshot()) as DomSnapshot
      const hit = snap.strings.some((s) => LOGIN_MARKERS.some((p) => p.test(s.trim())))
      check(hit, '页面登录态特征（筛选按钮/侧边菜单）', hit ? '' : '未找到，可能未登录或未打开 BOSS 页面')

      // ⑤ 姓名配对自检：视口内「打招呼」按钮 → pairCardName 配对率
      // 0 个按钮：可能已全部打过招呼/列表为空/不在推荐页——跳过不算失败
      // 0 配对且有按钮：锚定规则与当前页面布局失配 → 定向打招呼必然「找不到人」，fail
      const buttons = findButtonsByExactText(snap, GREET_TEXT)
      if (buttons.length === 0) {
        console.log('ℹ️ 姓名配对自检：当前视口无「打招呼」按钮（可能已全部打过招呼或不在推荐牛人页），跳过')
      } else {
        const viewport = viewportOf(snap)
        const names = buttons.map((b) => pairCardName(snap, b, viewport))
        const paired = names.filter((n): n is string => n !== null)
        if (paired.length === 0) {
          check(false, `姓名配对自检（${buttons.length} 个「打招呼」按钮配对成功 0）`,
            '锚定规则与当前页面布局失配：定向打招呼将「滚遍列表也找不到人」，请把 collect_logs 诊断包发给管理员')
        } else if (paired.length < buttons.length) {
          console.log(`✅ 姓名配对自检：${buttons.length} 个「打招呼」按钮配对成功 ${paired.length}（${paired.join('、')}）；${buttons.length - paired.length} 个未配上（无中文名卡片属正常，fail-safe 跳过）`)
        } else {
          console.log(`✅ 姓名配对自检：${buttons.length} 个「打招呼」按钮全部配对成功（${paired.join('、')}）`)
        }
      }
    } catch (e) {
      check(false, '页面登录态特征（筛选按钮/侧边菜单）', e instanceof Error ? e.message : String(e))
    }
  }

  // ⑥ OCR 引擎（简历读取，P2）：只查本机部署状态（探测 python/RapidOCR 可用性），不触达页面。
  //    WinRT 兜底 = 合法可用配置 → ℹ️ 信息行不算失败；AID_BOSS_OCR_ENGINE=rapid 强制而不可用 → 失败
  try {
    const plan = await resolveOcrEngine()
    if (plan.engine === 'rapid') {
      // 单次推理实测（bench）：装机验收拿真实耗时数据 + 加速后端（dml=DirectML GPU 加速，
      // 需装 onnxruntime-directml；cpu=纯 CPU）。bench 失败只降级为不展示耗时，不算装机失败
      let benchDetail = ''
      try {
        const { seconds, accel } = await benchRapidOcr(plan.python!)
        const accelLabel = accel === 'dml' ? 'DirectML GPU 加速' : 'CPU'
        benchDetail = `，${accelLabel}，单次推理实测 ${seconds.toFixed(1)}s`
      } catch (e) {
        benchDetail = `（推理实测未出数：${e instanceof Error ? e.message : String(e)}）`
      }
      // 捆绑便携环境标注（方案 A）：python 路径含 ocr-python = 用的是包内置环境（非用户自装
      // Python）。装机支持人员据此外观一眼区分「内置环境正常工作」vs「碰巧用了机器上的解释器」
      const bundledMark = /ocr-python[\\/]/.test(plan.python ?? '') ? '包内置环境；' : ''
      check(true, 'OCR 引擎（简历读取）', `RapidOCR（${bundledMark}python: ${plan.python}${benchDetail}）`)
    } else {
      console.log(`ℹ️ OCR 引擎（简历读取）：${plan.reason}`)
    }
  } catch (e) {
    check(false, 'OCR 引擎（简历读取）', e instanceof Error ? e.message : String(e))
  }

  await gw.close().catch(() => {})
  if (failed) {
    console.log('\n存在失败项，请按提示修复后重试（doctor 为只读检查，未对页面做任何操作）')
    return 1
  }
  console.log('\n全部检查通过，可以执行操作命令')
  return 0
}
