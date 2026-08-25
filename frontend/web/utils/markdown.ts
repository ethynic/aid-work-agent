import { marked } from 'marked'
import { markedHighlight } from 'marked-highlight'
// ✅ 优化：只导入 highlight.js 核心和需要的语言，减少约 150KB 打包体积
import hljs from 'highlight.js/lib/core'
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import python from 'highlight.js/lib/languages/python'
import json from 'highlight.js/lib/languages/json'
import html from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'
import bash from 'highlight.js/lib/languages/bash'
import sql from 'highlight.js/lib/languages/sql'
import yaml from 'highlight.js/lib/languages/yaml'
import markdown from 'highlight.js/lib/languages/markdown'
import plaintext from 'highlight.js/lib/languages/plaintext'

// 注册常用语言
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('python', python)
hljs.registerLanguage('json', json)
hljs.registerLanguage('html', html)
hljs.registerLanguage('css', css)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('yaml', yaml)
hljs.registerLanguage('markdown', markdown)
hljs.registerLanguage('plaintext', plaintext)

// 配置 marked 使用 highlight.js 进行代码高亮
marked.use(markedHighlight({
  langPrefix: 'hljs language-',
  highlight(code: string, lang: string) {
    const language = hljs.getLanguage(lang) ? lang : 'plaintext'
    return hljs.highlight(code, { language }).value
  }
}))

// 统一让所有链接在新 Tab 打开，避免覆盖当前聊天页面
const linkRenderer = ({ href, title, tokens }: any) => {
  const text = marked.Parser.parseInline(tokens ?? [])
  const titleAttr = title ? ` title="${title}"` : ''
  return `<a href="${href}"${titleAttr} target="_blank" rel="noopener noreferrer">${text}</a>`
}

// Markdown 内嵌图片渲染：
// - `file_id:file_xxx` 是后端约定的图片资产引用 scheme，浏览器原生不识别
//   需要转成 /api/files/file_xxx/download 才能加载
// - 其他 URL（http/https/相对路径）保持原样
// - 统一加 loading="lazy" + class，方便后续样式 / lightbox 扩展（Phase 2 P2.8）
const imageRenderer = ({ href, title, text }: any) => {
  let src = href
  if (typeof href === 'string' && href.startsWith('file_id:')) {
    src = `/api/files/${href.slice('file_id:'.length)}/download`
  }
  const altAttr = (text ?? '').replace(/"/g, '&quot;')
  const titleAttr = title ? ` title="${title.replace(/"/g, '&quot;')}"` : ''
  return `<img src="${src}" alt="${altAttr}"${titleAttr} class="md-inline-image" loading="lazy">`
}

marked.use({ renderer: { link: linkRenderer, image: imageRenderer } })

/**
 * 将 markdown 文本渲染为 HTML
 */
export function renderMarkdown(content: string): string {
  return marked(content) as string
}

export { marked }
