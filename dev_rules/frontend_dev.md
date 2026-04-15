# 前端开发指南

本文档为 Claude Code 在本项目前端工作时提供指导。

## 命令

```bash
cd frontend && npm install               # 安装前端依赖
cd frontend && npm run dev               # 开发服务器（端口 5173）
cd frontend && npm run build             # 生产环境构建
```

### 测试
```bash
cd frontend && npm test                   # 运行前端测试
cd frontend && npm run test:coverage      # 带覆盖率
cd frontend && npm run test:watch         # 监听模式
```

## 开发规范

### 日志规范
```javascript
// 长期保留日志
console.log('前端日志：开始验证用户凭证', { username });

// 临时调试日志（bug 修复后删除）
console.log('临时调试：API 响应', response);
```

### 缓存使用规范
**非必要，不使用缓存。** 优先使用直接请求：
```javascript
// ✅ 推荐
export const knowledgeAPI = {
  list: (params) => api.get('/knowledge/', { params }),
}
```

### 页面布局一致性规范
从 `SessionSidebar` 导航进入的页面**必须**保留 `SessionSidebar` + `AppHeader` 布局。

## 测试指南

### 测试目录结构

```
frontend/src/__tests__/      # 前端测试（Vitest + Vue Test Utils + MSW）
├── setup.ts                 # jsdom 环境 + MSW 启动
├── mocks/                   # API mock（handlers.ts、server.ts、fixtures.ts）
├── composables/             # Composable 逻辑测试
├── api/                     # API 层测试
└── components/              # 组件测试
```

### 为新功能编写测试

**前端组件/Composable 测试**，在 `frontend/src/__tests__/` 对应目录添加 `.test.ts` 文件。API 请求由 MSW 自动拦截（配置在 `mocks/handlers.ts`）。

## 语言说明

UI 文本为中文。代码标识符为英文。