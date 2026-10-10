// Only the fixed notification entry is accepted as a login return URL.
export function notificationReturnUrl(value: unknown): string | null {
  if (typeof value !== 'string' || !value.startsWith('/notification?')) return null
  try {
    const url = new URL(value, window.location.origin)
    if (url.origin !== window.location.origin || url.pathname !== '/notification' || url.hash || url.username || url.password) return null
    const ref = url.searchParams.get('ref')
    if (!ref || !/^[a-zA-Z0-9_-]{1,160}$/.test(ref)) return null
    const result = new URLSearchParams({ ref })
    const tenant = url.searchParams.get('tenant')
    if (tenant && /^[a-zA-Z0-9_-]{1,128}$/.test(tenant)) result.set('tenant', tenant)
    return `/notification?${result}`
  } catch { return null }
}
