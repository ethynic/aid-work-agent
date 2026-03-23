# AID Work Agent 前端

基于 Vue3 + TypeScript + TailwindCSS 的现代化聊天界面，通过 Server-Sent Events (SSE) 与后端 Agent 进行实时通信。

## 技术选型

### 为什么选择 SSE 而不是 WebSocket？

| 特性 | SSE | WebSocket |
|------|-----|-----------|
| 协议方向 | 单向（服务端→客户端） | 双向 |
| 实现复杂度 | 简单 | 复杂 |
| 自动重连 | 原生支持 | 需要手动实现 |
| 流式输出 | 原生支持 | 需要自行处理 |
| 兼容性 | 现代浏览器 | 所有现代浏览器 |
| 防火墙友好 | 通过HTTP，友好 | 可能被阻止 |

**SSE 更适合本项目的原因：**
1. Agent 的 `process_message` 是 AsyncGenerator，天然支持流式输出
2. 前端只需要接收 Agent 的进度和响应，不需要主动发送（消息通过 HTTP POST）
3. 实现简单，调试容易

## 项目结构

```
frontend/
├── index.html              # HTML入口
├── package.json            # 依赖配置
├── vite.config.ts          # Vite配置
├── tailwind.config.js      # TailwindCSS配置
├── src/
│   ├── main.ts             # Vue入口
│   ├── App.vue             # 根组件
│   ├── style.css           # 全局样式
│   ├── api/
│   │   └── agent.ts       # API调用层
│   ├── composables/
│   │   └── useAgent.ts     # Agent交互逻辑
│   ├── components/
│   │   ├── ChatContainer.vue    # 主容器
│   │   ├── ChatInput.vue        # 输入框
│   │   ├── MessageItem.vue      # 消息项
│   │   ├── MessageList.vue      # 消息列表
│   │   └── ProgressPanel.vue    # 进度面板
│   └── types/
│       └── index.ts        # TypeScript类型
```

## 快速开始

### 1. 安装依赖

```bash
cd frontend
npm install
```

### 2. 启动后端API服务

```bash
cd ..
python -m src.main
```

后端服务将在 `http://localhost:8000` 启动。

### 3. 启动前端开发服务器

```bash
cd frontend
npm run dev
```

前端将在 `http://localhost:3000` 启动。

### 4. 打开浏览器

访问 `http://localhost:3000` 查看界面。

## API 接口

后端 API 由 `src/main.py` 提供，所有接口前缀为 `/api`。

### SSE 流式聊天

```
POST /api/chat/stream
Content-Type: application/json

{
  "message": "用户消息",
  "session_id": "会话ID（可选）",
  "files": [] // 可选的文件列表
}
```

**响应：** Server-Sent Events 流

```
data: {"type": "connected", "session_id": "xxx"}

data: {"type": "progress", "data": "正在搜索...", "timestamp": 1699999999999}

data: {"type": "response", "data": "这是", "timestamp": 1699999999999}
data: {"type": "response", "data": "AI的", "timestamp": 1699999999999}
data: {"type": "response", "data": "回复", "timestamp": 1699999999999}

data: {"type": "complete", "timestamp": 1699999999999}
```

### 同步聊天

```
POST /api/chat
Content-Type: application/json

{
  "message": "用户消息",
  "session_id": "会话ID"
}
```

### 获取历史

```
GET /api/chat/history/{session_id}
```

### 删除会话

```
DELETE /api/chat/session/{session_id}
```

### 获取工具列表

```
GET /api/tools
```

## 前端组件说明

### ChatContainer.vue
主容器组件，包含头部、消息区域、进度面板和输入区域。

### MessageList.vue
消息列表组件，自动滚动到最新消息，支持空状态展示。

### MessageItem.vue
单条消息组件，支持 Markdown 渲染，包含用户消息和 AI 助手消息两种样式。

### ProgressPanel.vue
进度展示面板，可折叠，显示 Agent 执行过程中的所有进度消息。

### ChatInput.vue
输入框组件，支持：
- Enter 发送消息
- Shift+Enter 换行
- 发送时禁用输入

## 与后端 Agent 集成

前端通过 SSE 与后端的 `/api/chat/stream` 接口通信。后端会：

1. 接收前端消息
2. 调用 `master_agent.process_message()`
3. 通过 `progress_callback` 实时推送进度
4. 将 Agent 响应的每个 chunk 都通过 SSE 推送给前端

## 构建生产版本

```bash
cd frontend
npm run build
```

构建产物在 `dist/` 目录。

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| VITE_API_BASE_URL | API基础URL | /api |

## 注意事项

1. **CORS配置**：开发环境已允许所有来源，生产环境应限制
2. **超时设置**：API请求超时为5分钟，适应长时间运行的Agent任务
3. **Session管理**：使用内存存储，生产环境应使用Redis等外部存储
4. **文件上传**：当前支持base64编码的文件，后续可扩展为文件上传
