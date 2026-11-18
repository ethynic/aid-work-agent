import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { ref } from 'vue'
import GuidesCenter from '@/components/saas/GuidesCenter.vue'
import { getGuideDoc, getGuidesCatalog } from '@/api/guides'

const push = vi.fn()

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { doc_path: '' } }),
  useRouter: () => ({ push }),
}))
vi.mock('@/composables/useTenantAuth', () => ({
  useTenantAuth: () => ({ admin: ref({ user_id: 'admin' }), isLoggedIn: ref(true), logout: vi.fn() }),
}))
vi.mock('@/api/guides', () => ({
  getGuidesCatalog: vi.fn(),
  getGuideDoc: vi.fn(),
}))

const CATALOG = {
  categories: [
    {
      dir: 'deployment',
      name: '部署与运维',
      docs: [
        { path: 'deployment/backup.md', title: '备份与灾难恢复', updated_at: '2026-11-18 10:00', size_bytes: 100 },
        { path: 'deployment/nginx.md', title: 'Nginx 反向代理', updated_at: '2026-11-17 09:00', size_bytes: 80 },
      ],
    },
    {
      dir: 'development',
      name: '开发环境与工具',
      docs: [
        { path: 'development/mcp_server.md', title: 'MCP Server 使用文档', updated_at: '2026-11-16 08:00', size_bytes: 60 },
      ],
    },
  ],
}

const DOC_CONTENT = [
  '# 备份与灾难恢复',
  '',
  '简介段落。',
  '',
  '## 备份策略',
  '正文。',
  '',
  '### 异地容灾',
  '正文。',
  '',
  '相关文档：[部署总览](./DEPLOYMENT.md)',
].join('\n')

let wrapper: ReturnType<typeof mount> | undefined

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getGuidesCatalog).mockResolvedValue(CATALOG)
  vi.mocked(getGuideDoc).mockResolvedValue({
    path: 'deployment/backup.md',
    title: '备份与灾难恢复',
    content: DOC_CONTENT,
    updated_at: '2026-11-18 10:00',
  })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
})

async function render() {
  wrapper = mount(GuidesCenter, {
    global: {
      stubs: { AppHeader: true },
      provide: { toggleSidebar: vi.fn() },
    },
  })
  await flushPromises()
  return wrapper
}

it('renders catalog grouped by category and opens the default doc', async () => {
  const w = await render()
  expect(getGuidesCatalog).toHaveBeenCalledOnce()
  expect(w.text()).toContain('部署与运维')
  expect(w.text()).toContain('开发环境与工具')
  expect(w.text()).toContain('备份与灾难恢复')
  expect(w.text()).toContain('MCP Server 使用文档')
  // 默认打开第一篇文档，正文渲染为 HTML（标题不再是原始 # 文本）
  expect(getGuideDoc).toHaveBeenCalledWith('deployment/backup.md')
  expect(w.find('.markdown-body h1').text()).toBe('备份与灾难恢复')
})

it('builds the in-page TOC from rendered h2/h3/h4 with injected anchor ids', async () => {
  const w = await render()
  const headings = w.findAll('.markdown-body h2, .markdown-body h3, .markdown-body h4')
  expect(headings).toHaveLength(2)
  expect(headings[0].attributes('id')).toBe('guide-h-0')
  const tocButtons = w.findAll('aside button').filter(b => b.text() === '备份策略' || b.text() === '异地容灾')
  expect(tocButtons.map(b => b.text())).toEqual(['备份策略', '异地容灾'])
})

it('sidebar click navigates to the doc route', async () => {
  const w = await render()
  const target = w.findAll('aside button').find(b => b.text() === 'MCP Server 使用文档')!
  await target.trigger('click')
  expect(push).toHaveBeenCalledWith('/portal/guides/development/mcp_server.md')
})

it('intercepts relative .md links in the doc and routes within the viewer', async () => {
  const w = await render()
  const link = w.find('.markdown-body a[href="./DEPLOYMENT.md"]')
  expect(link.exists()).toBe(true)
  await link.trigger('click')
  expect(push).toHaveBeenLastCalledWith('/portal/guides/deployment/DEPLOYMENT.md')
})

it('shows load error message when doc fetch fails', async () => {
  vi.mocked(getGuideDoc).mockRejectedValue(new Error('HTTP 404: Not Found'))
  const w = await render()
  await flushPromises()
  expect(w.text()).toContain('加载失败')
})
