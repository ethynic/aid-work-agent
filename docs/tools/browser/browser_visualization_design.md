# 浏览器工具可视化设计文档

> 版本: v1.0 | 日期: 2026-05-21 | 状态: 待开发

## 1. 背景

### 1.1 现状

当前 `BrowserAutomationTool` 在后台 headless 运行 Playwright，前端用户只能看到文字进度（"正在打开页面..."、"正在点击..."），无法看到浏览器的实际画面。用户希望：

1. **实时看到 Agent 如何操作浏览器** — 页面导航、点击、填写表单等操作过程可视化
2. **使用成熟的开源方案** — 不自己造轮子
3. **可视化是锦上添花** — 如果实现复杂，工具在后台稳定运行也可以，可以用其他方式将画面流到前端

### 1.2 调研结论

详细调研见 [browser_agent_tools_research.md](./browser_agent_tools_research.md)，核心结论：

- **不需要引入新的浏览器 Agent 框架**（Browser Use、Skyvern 等）。现有 `BrowserAutomationTool` 基于语义快照 + LLM 决策的架构已经很完善
- **Playwright 1.59 Screencast API** 是最佳可视化方案：
  - 项目已使用 Playwright，只需升级到 1.59+
  - `page.screencast.start(onFrame=callback)` 可实时获取 JPEG 帧
  - 带宽消耗低（页面变化时才产生帧，约 50-125 KB/s）
  - 支持操作标注（在截图上显示点击位置、输入内容）

---

## 2. 方案设计

### 2.1 技术架构

```
┌──────────────────────────────────────────────────────────────┐
│ 后端                                                          │
│                                                                │
│  BrowserAutomationTool                                        │
│    └── BrowserOrchestrator                                    │
│          └── BrowserSession (Playwright)                      │
│                └── page.screencast.start(onFrame=cb)          │
│                      ↓ JPEG 帧回调                             │
│                _ws_clients[session_id] → 广播帧数据            │
│                                                                │
│  FastAPI                                                       │
│    ├── SSE 事件流: browser_view_ready 事件                     │
│    └── WebSocket /ws/browser-view/{session_id} 二进制帧推送    │
│                                                                │
├──────────────────────────────────────────────────────────────┤
│ 前端                                                          │
│                                                                │
│  useAgent.ts                                                  │
│    └── onBrowserView 回调 → 设置 browserViewUrl               │
│                                                                │
│  BrowserView.vue                                              │
│    ├── WebSocket 连接 → 接收 JPEG 二进制帧                     │
│    ├── <canvas> 或 <img> 实时渲染                              │
│    └── 连接状态指示器                                          │
│                                                                │
│  MessageItem.vue                                              │
│    └── 收到 browser_view_ready 事件时嵌入 BrowserView          │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 数据流

```
Agent 执行浏览器任务
  │
  ├─ 1. BrowserSession 启动浏览器
  ├─ 2. page.screencast.start(onFrame=broadcast_frame)
  ├─ 3. 通过 SSE 发送 browser_view_ready 事件
  │     {"type": "browser_view_ready", "session_id": "xxx", "ws_url": "/ws/browser-view/xxx"}
  │
  │  ┌── 前端收到事件，建立 WebSocket 连接 ──┐
  │  │                                       │
  ├─ 4. 编排器循环：navigate → click → fill...  │
  │     每次页面变化时：                       │
  │     onFrame(jpeg_bytes)                   │
  │       → 广播到所有 WebSocket 客户端        │
  │       → 前端 Canvas 渲染帧  ←─────────────┘
  │
  ├─ 5. 任务完成/出错
  ├─ 6. page.screencast.stop()
  └─ 7. 关闭 WebSocket 连接
```

### 2.3 带宽与性能估算

| 指标 | 估算值 |
|------|--------|
| 帧大小 | 800x600 JPEG (quality=50) ≈ 15-25 KB/帧 |
| 帧率 | 1-5 FPS（页面变化时才产生帧） |
| 带宽 | 约 50-125 KB/s |
| 内存增量 | 每会话约 5-10 MB（screencast 缓冲） |
| CPU 增量 | 可忽略（Playwright 内部实现，非应用层编码） |

---

## 3. 详细设计

### 3.1 后端改动

#### 3.1.1 升级 Playwright

**文件**: `requirements.txt`

```
playwright>=1.40.0  →  playwright>=1.59.0
```

#### 3.1.2 BrowserSession 增加 Screencast 能力

**文件**: `src/tools/browser/session.py`

新增方法和属性：

```python
class BrowserSession:
    # 类变量：管理所有 session 的 WebSocket 客户端
    _ws_clients: Dict[str, Set[WebSocket]] = {}

    async def start_screencast(self, quality: int = 50, width: int = 800, height: int = 600):
        """启动 screencast，帧变化时自动广播到 WebSocket 客户端"""
        if not self.page:
            return

        async def on_frame(frame_data: bytes):
            """Screencast 帧回调：广播到所有连接的客户端"""
            session_id = self.session_id
            clients = BrowserSession._ws_clients.get(session_id, set())
            disconnected = set()
            for ws in clients:
                try:
                    await ws.send_bytes(frame_data)
                except Exception:
                    disconnected.add(ws)
            # 清理断开的连接
            clients -= disconnected

        self._screencast = await self.page.screencast.start(
            on_frame=on_frame,
            size={"width": width, "height": height},
            quality=quality,
        )

    async def stop_screencast(self):
        """停止 screencast"""
        if self._screencast:
            await self._screencast.stop()
            self._screencast = None

    @classmethod
    def register_ws_client(cls, session_id: str, ws: WebSocket):
        """注册 WebSocket 客户端"""
        if session_id not in cls._ws_clients:
            cls._ws_clients[session_id] = set()
        cls._ws_clients[session_id].add(ws)

    @classmethod
    def unregister_ws_client(cls, session_id: str, ws: WebSocket):
        """移除 WebSocket 客户端"""
        if session_id in cls._ws_clients:
            cls._ws_clients[session_id].discard(ws)
            if not cls._ws_clients[session_id]:
                del cls._ws_clients[session_id]
```

#### 3.1.3 BrowserOrchestrator 启停 Screencast

**文件**: `src/tools/browser/orchestrator.py`

在编排器生命周期中管理 screencast：

```python
async def execute(self, task, url, ...):
    session = await self._ensure_session()

    # 启动 screencast
    await session.start_screencast()

    # 通知前端可以连接 WebSocket
    if self.progress_callback:
        await self.progress_callback({
            "type": "browser_view_ready",
            "session_id": self.session_id,
            "ws_url": f"/ws/browser-view/{self.session_id}"
        })

    try:
        # ... 现有的编排循环 ...
    finally:
        # 停止 screencast
        await session.stop_screencast()
```

#### 3.1.4 FastAPI WebSocket 端点

**文件**: `src/main.py`

```python
from fastapi import WebSocket, WebSocketDisconnect

@app.websocket("/ws/browser-view/{session_id}")
async def browser_view_websocket(websocket: WebSocket, session_id: str):
    """浏览器画面实时推送 WebSocket"""
    await websocket.accept()

    from src.tools.browser.session import BrowserSession
    BrowserSession.register_ws_client(session_id, websocket)

    try:
        # 保持连接，等待客户端断开
        while True:
            await websocket.receive_text()  # 心跳保活
    except WebSocketDisconnect:
        pass
    finally:
        BrowserSession.unregister_ws_client(session_id, websocket)
```

#### 3.1.5 SSE 事件透传

**文件**: `src/main.py` 的 `event_generator()` 中

在现有的 progress 事件处理中增加 `browser_view_ready` 事件类型的透传：

```python
# 现有代码已支持 dict 类型事件的直接透传
# browser_view_ready 事件从 orchestrator → agent callback → sync_progress_callback → results['progress']
# event_generator 会自动将其格式化为 SSE 事件并推送
```

无需额外修改，因为现有事件管道已支持任意 dict 类型事件的透传。

### 3.2 前端改动

#### 3.2.1 新增事件类型

**文件**: `frontend/src/types/index.ts`

```typescript
// 在 MessageStreamEvent 联合类型中新增
| { type: 'browser_view_ready'; session_id: string; ws_url: string; timestamp: number }
```

#### 3.2.2 SSE 解析新增回调

**文件**: `frontend/src/api/agent.ts`

在 SSEManager 的 `parseSSELine` 的 switch 中新增：

```typescript
case 'browser_view_ready':
  callbacks.onBrowserView?.(event.session_id, event.ws_url)
  break
```

#### 3.2.3 useAgent 新增状态

**文件**: `frontend/src/composables/useAgent.ts`

```typescript
// 新增全局状态
const browserViewUrl = ref<string | null>(null)
const browserViewSessionId = ref<string | null>(null)

// 在 SSE 回调中处理
onBrowserView: (sessionId: string, wsUrl: string) => {
  browserViewUrl.value = wsUrl
  browserViewSessionId.value = sessionId
  addProgress(`浏览器画面已就绪`, 'browser_view_ready', undefined, undefined, { wsUrl, sessionId })
}
```

#### 3.2.4 BrowserView 组件

**文件**: `frontend/src/components/BrowserView.vue`（新增）

核心功能：
- 接收 `wsUrl` prop，建立 WebSocket 连接
- 收到二进制 JPEG 帧后用 `URL.createObjectURL(new Blob([data], {type: 'image/jpeg'}))` 渲染到 `<img>` 标签
- 显示连接状态指示器（绿点=已连接，红点=已断开）
- 组件销毁时自动断开 WebSocket
- 自适应宽度，保持 4:3 宽高比

```vue
<template>
  <div class="browser-view">
    <div class="status-bar">
      <span class="status-dot" :class="status"></span>
      <span>{{ statusText }}</span>
    </div>
    <div class="viewport">
      <img v-if="currentFrame" :src="currentFrame" alt="Browser view" />
      <div v-else class="placeholder">等待画面...</div>
    </div>
  </div>
</template>
```

#### 3.2.5 集成到聊天界面

**文件**: `frontend/src/components/MessageItem.vue`

在消息的 progressMessages 渲染区域，检测 `type === 'browser_view_ready'` 的进度消息，嵌入 `BrowserView` 组件。

---

## 4. 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `requirements.txt` | 修改 | 升级 Playwright 到 1.59+ |
| `src/tools/browser/session.py` | 修改 | 新增 screencast 启停 + WebSocket 客户端管理 |
| `src/tools/browser/orchestrator.py` | 修改 | 编排器生命周期中管理 screencast |
| `src/main.py` | 修改 | 新增 WebSocket 端点 |
| `frontend/src/types/index.ts` | 修改 | 新增 browser_view_ready 事件类型 |
| `frontend/src/api/agent.ts` | 修改 | SSE 解析新增 onBrowserView 回调 |
| `frontend/src/composables/useAgent.ts` | 修改 | 新增 browserViewUrl 状态 |
| `frontend/src/components/BrowserView.vue` | 新增 | 浏览器画面实时渲染组件 |
| `frontend/src/components/MessageItem.vue` | 修改 | 嵌入 BrowserView |

---

## 5. 验证方式

1. 升级 Playwright：`pip install playwright>=1.59.0 && playwright install chromium`
2. 启动后端服务
3. 通过前端对话界面发送浏览器任务（如"帮我打开百度搜索天气"）
4. 前端应出现浏览器画面实时更新，直到任务完成
5. 任务完成后画面停止，WebSocket 自动断开
6. 前端构建验证：`cd frontend && npm run build`

---

## 6. 风险与注意事项

| 风险 | 影响 | 应对措施 |
|------|------|----------|
| Playwright 1.59 Screencast API 是新 API | 可能有性能或兼容性问题 | 先在开发环境实测帧率和延迟 |
| WebSocket 并发隔离 | 多用户同时查看不同 session | 通过 session_id 隔离 WebSocket 客户端集合 |
| screencast 资源消耗 | 未及时停止会浪费内存/CPU | 在 orchestrator 的 finally 块中确保 stop |
| Playwright 1.59 与现有代码兼容性 | 升级可能破坏现有 browser 工具 | 升级后回归测试所有浏览器工具功能 |
| onFrame 回调线程安全 | Playwright async 环境中的帧回调 | 使用 asyncio 原生，无需额外线程安全措施 |

---

## 7. 未来扩展（本次不实现）

1. **操作标注**：利用 Screencast API 的 action annotation 功能，在截图上显示点击位置、输入内容
2. **录制回放**：利用 `page.screencast.start(path="video.webm")` 录制操作视频，任务完成后用户可回放
3. **人工接管**：通过 WebSocket 双向通信，允许用户在前端 Canvas 上操作（鼠标/键盘事件 → CDP 输入事件）
4. **截图下载**：提供截图保存按钮
