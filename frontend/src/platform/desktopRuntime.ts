import type { PlatformRuntime } from './runtime'

export function readDesktopRuntime(): PlatformRuntime & { smokeMode: boolean } {
  const bridge = window.agentDesktop
  if (!bridge || bridge.version !== 1 || bridge.runtime.target !== 'desktop') {
    throw new Error('Agent Desktop runtime bridge is unavailable or incompatible')
  }
  return {
    target: 'desktop',
    apiBaseUrl: bridge.runtime.apiBaseUrl,
    smokeMode: bridge.runtime.smokeMode
  }
}

export function resolveHealthUrl(apiBaseUrl: string): string {
  return new URL('/health', apiBaseUrl).toString()
}

async function checkHealth(apiBaseUrl: string): Promise<boolean> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), 5000)
  try {
    const response = await fetch(resolveHealthUrl(apiBaseUrl), {
      method: 'GET',
      credentials: 'omit',
      cache: 'no-store',
      signal: controller.signal
    })
    return response.ok
  } catch {
    return false
  } finally {
    window.clearTimeout(timeout)
  }
}

export function mountDesktopConnectivity(apiBaseUrl: string): () => void {
  const banner = document.createElement('aside')
  banner.id = 'desktop-connectivity'
  banner.setAttribute('role', 'status')
  banner.style.cssText = [
    'position:fixed',
    'left:50%',
    'bottom:20px',
    'transform:translateX(-50%)',
    'z-index:2147483647',
    'display:none',
    'gap:12px',
    'align-items:center',
    'padding:10px 14px',
    'border-radius:8px',
    'background:#7f1d1d',
    'color:#fff',
    'box-shadow:0 4px 16px rgba(0,0,0,.25)'
  ].join(';')
  const message = document.createElement('span')
  message.textContent = '服务暂时不可用，Agent 页面仍可浏览。'
  const retry = document.createElement('button')
  retry.type = 'button'
  retry.textContent = '重试连接'
  retry.style.cssText = 'padding:4px 10px;border:1px solid #fff;border-radius:4px;background:transparent;color:inherit;cursor:pointer'
  banner.append(message, retry)
  document.body.append(banner)

  let generation = 0
  let disposed = false
  const refresh = async () => {
    const currentGeneration = ++generation
    retry.disabled = true
    const online = await checkHealth(apiBaseUrl)
    if (disposed || currentGeneration !== generation) return
    banner.style.display = online ? 'none' : 'flex'
    retry.disabled = false
  }
  const retryHandler = () => void refresh()
  const onlineHandler = () => void refresh()
  retry.addEventListener('click', retryHandler)
  window.addEventListener('online', onlineHandler)
  void refresh()
  return () => {
    disposed = true
    generation += 1
    retry.removeEventListener('click', retryHandler)
    window.removeEventListener('online', onlineHandler)
    banner.remove()
  }
}

export function renderSecureStartupFailure(): void {
  const root = document.querySelector<HTMLElement>('#app')
  if (!root) return
  const alert = document.createElement('main')
  alert.setAttribute('role', 'alert')
  alert.style.cssText = 'min-height:100vh;display:grid;place-items:center;padding:24px;text-align:center'
  alert.textContent = '安全凭证无法加载，Agent Desktop 已停止启动。请退出应用后重试。'
  root.replaceChildren(alert)
}

export async function runDesktopSmoke(apiBaseUrl: string): Promise<void> {
  const endpoint = (pathname: string) => new URL(pathname, apiBaseUrl).toString()
  try {
    const health = await fetch(endpoint('/health'))
    if (!health.ok) throw new Error('health')

    const scriptUrl = document.querySelector<HTMLScriptElement>('script[type="module"]')?.src
    if (!scriptUrl) throw new Error('renderer-script')
    const script = await fetch(scriptUrl)
    if (!script.ok || !script.headers.get('content-type')?.includes('javascript')) throw new Error('renderer-script-mime')
    const missingAsset = await fetch(new URL('/assets/missing-smoke-asset.js', location.origin))
    if (missingAsset.status !== 404) throw new Error('renderer-asset-404')

    const { complaintAPI } = await import('@/api/complaint')
    await complaintAPI.getStats('30d')

    const stream = await fetch(endpoint('/stream'), { headers: { Accept: 'text/event-stream' } })
    if (!stream.ok || !stream.body) throw new Error('stream')
    const reader = stream.body.getReader()
    const decoder = new TextDecoder()
    const first = await reader.read()
    if (first.done || !first.value || !decoder.decode(first.value).includes('chunk-1')) throw new Error('stream-first-chunk')
    let streamContent = ''
    for (;;) {
      const next = await reader.read()
      if (next.done) break
      streamContent += decoder.decode(next.value)
    }
    if (!streamContent.includes('chunk-3')) throw new Error('stream-last-chunk')

    const uploadBody = new FormData()
    uploadBody.append('file', new File(['desktop-upload-marker'], 'smoke.txt', { type: 'text/plain' }))
    const upload = await fetch(endpoint('/upload'), { method: 'POST', body: uploadBody })
    if (!upload.ok || !(await upload.json() as { received?: boolean }).received) throw new Error('upload')

    const download = await fetch(endpoint('/download'))
    if (!download.ok || (await download.arrayBuffer()).byteLength !== 65536) throw new Error('download')
    if (location.origin !== 'aidagent://app') throw new Error('origin')
    if (!document.querySelector('#app')) throw new Error('agent-renderer')
    history.pushState({}, '', '/t/smoke/knowledge')
    if (location.pathname !== '/t/smoke/knowledge') throw new Error('history')
    document.title = 'AGENT_DESKTOP_SMOKE_PASS'
  } catch (error) {
    const reason = error instanceof Error ? error.message.replace(/[^a-z0-9-]/gi, '_') : 'unknown'
    document.title = `AGENT_DESKTOP_SMOKE_FAIL:${reason}`
  }
}
