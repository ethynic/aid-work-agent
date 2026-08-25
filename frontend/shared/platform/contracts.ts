export interface CredentialStore {
  hydrate(): Promise<Record<string, string>>
  set(key: string, value: string): Promise<void>
  delete(key: string): Promise<void>
}

export interface ApiResolver {
  resolve(pathname: string): string
}

export interface ApiRequest {
  method?: 'GET' | 'POST'
  headers?: Readonly<Record<string, string>>
  body?: unknown
  signal?: AbortSignal
}

export interface ApiTransport {
  request<T>(url: string, request?: ApiRequest): Promise<T>
}

export class HttpStatusError extends Error {
  constructor(readonly status: number) {
    super(`API request failed with status ${status}`)
    this.name = 'HttpStatusError'
  }
}

export function createApiResolver(apiBaseUrl: string): ApiResolver {
  const base = new URL(apiBaseUrl)
  return { resolve: (pathname) => new URL(pathname.replace(/^\//, ''), `${base.toString().replace(/\/$/, '')}/`).toString() }
}

export function createFetchTransport(fetcher: typeof fetch = fetch): ApiTransport {
  return {
    async request<T>(url: string, request: ApiRequest = {}): Promise<T> {
      const response = await fetcher(url, {
        method: request.method ?? 'GET',
        headers: request.headers,
        body: request.body === undefined ? undefined : JSON.stringify(request.body),
        signal: request.signal,
        credentials: 'omit',
        cache: 'no-store',
      })
      if (!response.ok) throw new HttpStatusError(response.status)
      return response.json() as Promise<T>
    },
  }
}
