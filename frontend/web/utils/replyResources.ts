import { inject, type InjectionKey } from 'vue'
import { getFileUrl, getFileDownloadUrl } from '@/api/agent'

export type ReplyFileResolver = (fileId: string, download?: boolean) => string
export const replyFileResolverKey: InjectionKey<ReplyFileResolver> = Symbol('reply-file-resolver')

export function useReplyResources() {
  const sharedResolver = inject(replyFileResolverKey, null)
  const fallbackResolver: ReplyFileResolver = (fileId, download = false) =>
    download ? getFileDownloadUrl(fileId) : getFileUrl(fileId)
  return {
    shared: !!sharedResolver,
    fileUrl: (fileId: string, download = false) => sharedResolver
      ? sharedResolver(fileId, download)
      : fallbackResolver(fileId, download),
    // 恒有 resolver：聊天等未 provide 的上下文回落到默认 /api/files URL，
    // 让 renderMarkdown 的文件引用后处理在所有场景生效
    resolver: sharedResolver || fallbackResolver,
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
    if (match) return decodeURIComponent(match[1])
    // LLM 偶尔在正文 markdown 链接里写裸文件 id（file_ + 12 位 hex，无 file_id: 前缀），
    // 不识别会被浏览器按站内相对路径解析，点击后被前端路由接管跳成新会话空白页
    const bare = url.pathname.match(/^\/(file_[0-9a-f]{12}(?:\.[a-zA-Z0-9]+)?)(?:\/download)?$/)
    return bare ? decodeURIComponent(bare[1]) : null
  } catch {
    return null
  }
}
