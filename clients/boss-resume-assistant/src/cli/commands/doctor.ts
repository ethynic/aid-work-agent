/**
 * CLI 子命令 doctor：只读环境检查（标准 §4 强制命令面）。
 *
 * 用法：
 *   node dist/src/cli/index.js doctor [--cdp-port 9222]
 *
 * 检查 ① win-click.ps1 存在 ② CDP 端点可连 ③ 能 attach zhipin.com 页面
 * ④ 页面含登录态特征（「筛选」按钮或侧边菜单文案）。任一失败退出码 1。
 * 只读：绝不点击、不产生任何业务写动作。
 */
import { CdpGateway } from '../../main/cdp/CdpGateway.js'
import { DEFAULT_CDP_PORT } from '../../main/chrome/ChromeAttacher.js'
import { WinMouseClicker } from '../../main/input/WinMouseClicker.js'
import type { DomSnapshot } from '../../main/boss/domSnapshot.js'

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
    check(false, `CDP 端点可连（${endpoint}）`, '请确认 Chrome 已带 --remote-debugging-port 启动')
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

  // ④ 页面含登录态特征
  if (attached) {
    try {
      const snap = (await gw.captureDomSnapshot()) as DomSnapshot
      const hit = snap.strings.some((s) => LOGIN_MARKERS.some((p) => p.test(s.trim())))
      check(hit, '页面登录态特征（筛选按钮/侧边菜单）', hit ? '' : '未找到，可能未登录或未打开 BOSS 页面')
    } catch (e) {
      check(false, '页面登录态特征（筛选按钮/侧边菜单）', e instanceof Error ? e.message : String(e))
    }
  }

  await gw.close().catch(() => {})
  if (failed) {
    console.log('\n存在失败项，请按提示修复后重试（doctor 为只读检查，未对页面做任何操作）')
    return 1
  }
  console.log('\n全部检查通过，可以执行操作命令')
  return 0
}
