import { inject, type InjectionKey } from 'vue'
import { getFileUrl, getFileDownloadUrl } from '@/api/agent'

export type ReplyFileResolver = (fileId: string, download?: boolean) => string
export const replyFileResolverKey: InjectionKey<ReplyFileResolver> = Symbol('reply-file-resolver')

export function useReplyResources() {
  const sharedResolver = inject(replyFileResolverKey, null)
  return {
    shared: !!sharedResolver,
    fileUrl: (fileId: string, download = false) => sharedResolver
      ? sharedResolver(fileId, download)
      : download ? getFileDownloadUrl(fileId) : getFileUrl(fileId),
    resolver: sharedResolver || undefined,
  }
}

/** Recognize our file references, leaving external links unchanged. */
export function replyFileId(value: string): string | null {
  if (value.startsWith('file_id:')) return value.slice(8)
  try {
    const base = new URL(import.meta.env.VITE_API_BASE_URL || '/api', window.location.origin)
    const url = new URL(value, window.location.origin)
    if (url.origin !== window.location.origin && url.origin !== base.origin) return null
    const match = url.pathname.match(/\/files\/([^/]+)(?:\/download)?$/)
    return match ? decodeURIComponent(match[1]) : null
  } catch {
    return null
  }
}
