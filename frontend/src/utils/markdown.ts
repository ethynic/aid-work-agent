import { marked } from 'marked'
import { markedHighlight } from 'marked-highlight'
import hljs from 'highlight.js'

// 配置 marked 使用 highlight.js 进行代码高亮
// 只在模块加载时初始化一次，避免重复注册导致性能问题
marked.use(markedHighlight({
  langPrefix: 'hljs language-',
  highlight(code: string, lang: string) {
    const language = hljs.getLanguage(lang) ? lang : 'plaintext'
    return hljs.highlight(code, { language }).value
  }
}))

/**
 * 将 markdown 文本渲染为 HTML
 */
export function renderMarkdown(content: string): string {
  return marked(content) as string
}

export { marked }
