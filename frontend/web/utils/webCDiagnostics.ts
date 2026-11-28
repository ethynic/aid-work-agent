/**
 * webC 聊天页真机诊断（2026-11-24 iOS 微信右侧裁切排查）
 *
 * 背景：iOS 微信 WKWebView 独有的视口问题（右侧裁切、横向滚动条）在 Chromium 探针中
 * 无法复现，必须依赖真机数据。在聊天页 3 秒内连点 10 次启用 vConsole + 布局诊断
 * （入口在 WebCChat.vue 的 onPageTap，sessionStorage 记忆会话内不丢），
 * 测试同学在 vConsole 面板点「复制诊断」把报告贴回给开发。
 *
 * 诊断重点（对应嫌疑假设）：
 * - visualViewport.scale > 1：iOS 聚焦小字号输入框自动 zoom 且不还原（首要嫌疑）
 * - visualViewport.width < innerWidth：视觉视口窄于布局视口（放大态的等价表述）
 * - 文档 scrollWidth > clientWidth：真实内容横向溢出（溢出元素明细一并采集）
 *
 * vConsole 来源：vendor/vconsole.min.js（v3.15.0，MIT，从 qb2.0/smart-mall 拷贝，
 * 与其 VconsoleCopy 插件同源；动态 import，普通用户不加载这 282KB）。
 */

interface Snapshot {
  ts: string
  tag: string
  innerW: number
  innerH: number
  clientW: number
  docSW: number
  vvScale: number | null
  vvWidth: number | null
  vvOffsetLeft: number | null
  vvOffsetTop: number | null
}

const MAX_HISTORY = 60
const history: Snapshot[] = []

function now(): string {
  const d = new Date()
  const pad = (n: number, len = 2) => String(n).padStart(len, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`
}

interface DiagRefs {
  getTextarea: () => HTMLTextAreaElement | null
  getScrollContainer: () => HTMLElement | null
}

/** 采集当前布局快照 + 溢出元素明细，拼成可读文本 */
function collectReport(tag: string, refs: DiagRefs): string {
  const doc = document.documentElement
  const vv = window.visualViewport
  const lines: string[] = []
  lines.push(`== webC 布局诊断 [${tag}] @ ${now()} ==`)
  lines.push(`UA: ${navigator.userAgent}`)
  lines.push(`dpr: ${window.devicePixelRatio}`)
  lines.push(`window.innerWidth/Height: ${window.innerWidth}/${window.innerHeight}`)
  lines.push(`docEl.clientWidth/Height: ${doc.clientWidth}/${doc.clientHeight}`)
  lines.push(`docEl.scrollWidth: ${doc.scrollWidth}  body.scrollWidth: ${document.body.scrollWidth}`)
  lines.push(`scrollX/Y: ${Math.round(window.scrollX)}/${Math.round(window.scrollY)}`)
  if (vv) {
    lines.push(`visualViewport: width=${Math.round(vv.width)} height=${Math.round(vv.height)} scale=${vv.scale.toFixed(4)} offsetLeft=${Math.round(vv.offsetLeft)} offsetTop=${Math.round(vv.offsetTop)}`)
    if (vv.scale > 1.01 || Math.round(vv.width) < window.innerWidth) {
      lines.push(`!! 检测到页面处于放大态（focus zoom 未还原的表现）`)
    }
  } else {
    lines.push(`visualViewport: 不支持`)
  }
  const ta = refs.getTextarea()
  if (ta) lines.push(`textarea font-size: ${getComputedStyle(ta).fontSize}`)
  const sc = refs.getScrollContainer()
  if (sc) lines.push(`消息区 clientWidth/scrollWidth: ${sc.clientWidth}/${sc.scrollWidth}`)
  // 溢出元素：右缘超出视口或左缘越界的最外层元素（最多 10 个）
  const offenders: string[] = []
  for (const el of Array.from(document.querySelectorAll('*'))) {
    const r = el.getBoundingClientRect()
    if (r.width > 0 && (r.right > doc.clientWidth + 1 || r.left < -1)) {
      const cls = String(el.className && (el.className as SVGElement['className']).baseVal !== undefined ? (el.className as SVGElement['className']).baseVal : el.className)
      offenders.push(`<${el.tagName.toLowerCase()}> .${cls.slice(0, 60)} L${Math.round(r.left)} R${Math.round(r.right)} W${Math.round(r.width)}`)
      if (offenders.length >= 10) break
    }
  }
  lines.push(offenders.length ? `越界元素:\n  ${offenders.join('\n  ')}` : `越界元素: 无`)
  // 内部横向滚动容器
  const wide: string[] = []
  for (const el of Array.from(document.querySelectorAll('*'))) {
    if (el.scrollWidth > el.clientWidth + 1 && el.clientWidth > 100) {
      wide.push(`<${el.tagName.toLowerCase()}> .${String(el.className).slice(0, 50)} cw${el.clientWidth}/sw${el.scrollWidth}`)
      if (wide.length >= 6) break
    }
  }
  lines.push(wide.length ? `内部横向滚动容器:\n  ${wide.join('\n  ')}` : `内部横向滚动容器: 无`)
  return lines.join('\n')
}

/** 快照入历史并打到 console（vConsole 面板可见） */
function logSnapshot(tag: string, refs: DiagRefs): void {
  const vv = window.visualViewport
  const snap: Snapshot = {
    ts: now(),
    tag,
    innerW: window.innerWidth,
    innerH: window.innerHeight,
    clientW: document.documentElement.clientWidth,
    docSW: document.documentElement.scrollWidth,
    vvScale: vv ? Math.round(vv.scale * 10000) / 10000 : null,
    vvWidth: vv ? Math.round(vv.width) : null,
    vvOffsetLeft: vv ? Math.round(vv.offsetLeft) : null,
    vvOffsetTop: vv ? Math.round(vv.offsetTop) : null,
  }
  history.push(snap)
  if (history.length > MAX_HISTORY) history.shift()
  console.log(`[webC诊断] ${tag}\n${collectReport(tag, refs)}`)
}

/** 剪贴板：优先 async clipboard，失败降级 execCommand（iOS 微信 WebView 兼容） */
async function writeClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch { /* 降级 */ }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}

/** 诊断历史拼成复制文本：快照流水 + 当前完整明细 */
function buildCopyText(refs: DiagRefs): string {
  const rows = history.map(s =>
    `${s.ts} [${s.tag}] inner=${s.innerW} client=${s.clientW} docSW=${s.docSW} vv.scale=${s.vvScale} vv.w=${s.vvWidth} vv.dx=${s.vvOffsetLeft}`)
  return `webC 真机诊断报告\n${rows.join('\n')}\n\n${collectReport('复制时快照', refs)}`
}

/** 入口：动态加载 vConsole、注册「复制诊断」按钮、挂监听（聚焦/缩放/转向） */
export async function setupWebCDiagnostics(refs: DiagRefs): Promise<void> {
  // v3.15.0 UMD 在 Vite 构建下被 CJS 包装（走 module.exports 分支），window.VConsole
  // 恒为 undefined——必须从动态 import 的 default 导出取构造器（2026-11-25 真机修复）
  interface VConsoleCtor {
    new (): { addPlugin: (p: unknown) => void }
    VConsolePlugin: new (id: string, name: string) => {
      on: (event: string, cb: (...args: never[]) => void) => void
    }
  }
  // @ts-expect-error 第三方 UMD 包无类型声明
  const mod = (await import('../vendor/vconsole.min.js')) as { default?: VConsoleCtor }
  const VC = mod.default ?? (window as unknown as { VConsole?: VConsoleCtor }).VConsole
  if (!VC) {
    throw new Error('vConsole 构造器缺失（vendor 加载异常）')
  }
  const vConsole = new VC()

  // 「复制诊断」工具按钮：注册到 vConsole 面板（API 用法同 qb2.0/smart-mall VconsoleCopy）
  const plugin = new VC.VConsolePlugin('webc_diag', '诊断')
  plugin.on('addTool', (cb: (tools: unknown[]) => void) => {
    cb([{
      name: '复制诊断',
      global: true,
      onClick: async () => {
        const text = buildCopyText(refs)
        const ok = await writeClipboard(text)
        console.log(`[webC诊断] 复制${ok ? '成功' : '失败（可长按选择下方日志复制）'}：\n${text}`)
      },
    }])
  })
  vConsole.addPlugin(plugin)

  // 监听：聚焦/失焦（zoom 触发点）、视口缩放/滚动、转向、页面回显
  let timer: ReturnType<typeof setTimeout> | null = null
  const throttled = (tag: string) => {
    if (timer) return
    timer = setTimeout(() => { timer = null; logSnapshot(tag, refs) }, 300)
  }
  const ta = refs.getTextarea()
  ta?.addEventListener('focus', () => logSnapshot('输入框聚焦', refs))
  ta?.addEventListener('blur', () => logSnapshot('输入框失焦', refs))
  window.visualViewport?.addEventListener('resize', () => throttled('视口resize'))
  window.visualViewport?.addEventListener('scroll', () => throttled('视口scroll'))
  window.addEventListener('orientationchange', () => logSnapshot('转向', refs))
  window.addEventListener('pageshow', () => logSnapshot('pageshow', refs))

  logSnapshot('初始', refs)
  console.log('[webC诊断] 已启用：复现问题（发消息、聚焦输入框）后点「复制诊断」，把结果发给开发')
}
