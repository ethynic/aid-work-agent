/**
 * MSW Node 服务器
 *
 * 用于在 Node.js 环境中拦截 API 请求
 */
import { setupServer } from 'msw/node'
import { handlers } from './handlers'

export const server = setupServer(...handlers)
