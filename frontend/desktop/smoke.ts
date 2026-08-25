export async function runDesktopSmoke(apiBaseUrl: string): Promise<void> {
  const endpoint = (pathname: string) => new URL(pathname, apiBaseUrl).toString()
  try {
    if (!(await fetch(endpoint('/health'))).ok) throw new Error('health')
    const scriptUrl = document.querySelector<HTMLScriptElement>('script[type="module"]')?.src
    if (!scriptUrl || !(await fetch(scriptUrl)).ok) throw new Error('renderer-script')
    if ((await fetch(new URL('/assets/missing-smoke-asset.js', location.origin))).status !== 404) throw new Error('renderer-asset-404')
    if (!(await fetch(endpoint('/api/complaints/stats'))).ok) throw new Error('api')
    const stream = await fetch(endpoint('/stream'))
    const streamText = await stream.text()
    if (!stream.ok || !streamText.includes('chunk-1') || !streamText.includes('chunk-3')) throw new Error('stream')
    const body = new FormData(); body.append('file', new File(['desktop-upload-marker'], 'smoke.txt'))
    if (!(await fetch(endpoint('/upload'), { method: 'POST', body })).ok) throw new Error('upload')
    if ((await (await fetch(endpoint('/download'))).arrayBuffer()).byteLength !== 65536) throw new Error('download')
    if (location.origin !== 'aidagent://app' || !document.querySelector('[data-desktop-app]')) throw new Error('desktop-renderer')
    document.title = 'AGENT_DESKTOP_SMOKE_PASS'
  } catch (error) {
    const reason = error instanceof Error ? error.message.replace(/[^a-z0-9-]/gi, '_') : 'unknown'
    document.title = `AGENT_DESKTOP_SMOKE_FAIL:${reason}`
  }
}
