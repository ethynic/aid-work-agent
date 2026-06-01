# register_download_file 工具重新设计

> 版本: v1.0 | 创建: 2026-05-14 | 状态: 待审核

## 1. 现状分析

### 1.1 当前架构

```
LLM 调用 register_download_file(file_path, display_name)
  → 工具验证文件存在
  → 生成 file_id = "file_{uuid4_hex[:12]}"
  → 调用 _get_tenant_upload_dir() 获取目录
    → 依赖 ContextVar（current_tenant_id / current_user_id）
  → 复制文件到 storage/uploads/{tenant_id}/{user_id}/{file_id}{suffix}
  → 注册到内存字典 uploaded_files[file_id] = {...}
  → 返回 {file_id, download_url, ...}

SSE → 前端收到 tool_result 事件
  → useAgent.ts 的 onToolResult 回调
  → 走 else 分支：addProgress("register_download_file执行完成", 'tool_result', ..., result)
  → result（含 file_id, download_url）存在 ProgressMessage.result 中
  → 但前端从未解析此 result，下载链接不显示
```

### 1.2 现存问题

| # | 问题 | 严重程度 | 说明 |
|---|------|----------|------|
| **P1** | 下载链接不展示 | 高 | 前端收到 tool_result 后只显示"执行完成"文本，下载信息被埋在 progressMessages 的 result 字段中，用户看不到 |
| **P2** | tenant_id/user_id 获取不可靠 | ~~高~~ 已解决 | ~~`_get_tenant_upload_dir()` 依赖 ContextVar，但 Agent 在 `ThreadPoolExecutor` 线程中运行，ContextVar **不跨线程传播**~~ AsyncGenerator 迁移后 Agent 在 FastAPI event loop 中直接运行，ContextVar 正常传播 |
| **P3** | 工具无法获取当前用户信息 | 高 | `RegisterDownloadFileTool.execute()` 没有接收 user/tenant 参数的通道，`BaseTool` 接口无上下文注入机制 |
| **P4** | 重启后文件丢失注册 | 中 | `uploaded_files` 是内存字典，重启后丢失。虽然有磁盘扫描恢复（`_get_file_info`），但不可靠且不存储 display_name |
| **P5** | 同一文件被多次注册 | 低 | LLM 可能对同一文件调用多次 register_download_file，每次都复制一份新文件 |

### 1.3 关键代码路径

**后端工具**: `src/tools/file/register_download_tool.py`（137 行）

**文件存储**: `src/main.py:268-289` `_get_tenant_upload_dir()`
- 依赖 `src/saas/context.py` 的 ContextVar
- 线程池中 ContextVar 为空 → 文件存到 `conversation/`

**Agent 用户注入**: `src/core/agent.py:1212-1216`
- 邮件工具通过 `set_user_id()` 注入，但 `register_download_file` 没有类似机制

**前端 SSE 处理**: `frontend/src/composables/useAgent.ts:195-221`
- `onToolResult` 对 `register_download_file` 无特殊处理

**前端消息渲染**: `frontend/src/components/MessageItem.vue:64-102`
- progressMessages 只显示文本，不渲染可交互元素

**消息持久化**: `src/main.py:1177-1183`
- assistant 消息的 `metadata.progressMessages` 保存到 DB
- 切换会话时从 DB 加载恢复

---

## 2. 新设计

### 2.1 总体方案

```
┌──────────────────────────────────────────────────────────┐
│ 后端改动                                                  │
│                                                           │
│ 1. Agent 注入 user_id 到工具（复用 set_user_id 模式）      │
│ 2. 工具从注入的 user_id + tenant_id 构建存储路径            │
│ 3. tool_result 返回结构化下载信息                           │
│ 4. 下载信息作为独立字段存入 assistant 消息的 metadata        │
│                                                           │
│ 前端改动                                                  │
│                                                           │
│ 5. onToolResult 识别 register_download_file，提取下载信息   │
│ 6. 收集到 assistantMessage.downloadableFiles 数组          │
│ 7. MessageItem 底部渲染下载卡片                            │
│ 8. 从 DB 恢复历史消息时，从 metadata 重建 downloadableFiles  │
└──────────────────────────────────────────────────────────┘
```

### 2.2 后端改动

#### 2.2.1 工具增加 user_id/tenant_id 支持

**修改文件**: `src/tools/file/register_download_tool.py`

**方案**: 复用邮件工具的 `set_user_id()` 模式，同时新增 `set_tenant_id()`：

```python
class RegisterDownloadFileTool(BaseTool):
    # ... 现有代码 ...

    def __init__(self):
        self._user_id: Optional[str] = None
        self._tenant_id: Optional[str] = None

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_tenant_id(self, tenant_id: str):
        self._tenant_id = tenant_id

    async def execute(self, **kwargs) -> Dict[str, Any]:
        # ... 验证参数 ...

        # 使用注入的 user_id/tenant_id 构建存储路径
        # 不再依赖 ContextVar
        user_id = self._user_id or "anonymous"
        tenant_id = self._tenant_id

        upload_dir = _resolve_upload_dir(tenant_id, user_id)
        # ...
```

独立函数 `_resolve_upload_dir` 取代对 `_get_tenant_upload_dir()` 的依赖：

```python
def _resolve_upload_dir(tenant_id: Optional[str], user_id: str) -> Path:
    """根据 tenant_id 和 user_id 确定文件存储目录"""
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    base_dir = _PROJECT_ROOT / "storage" / "uploads"

    if tenant_id and user_id:
        upload_dir = base_dir / tenant_id / user_id
    elif tenant_id:
        upload_dir = base_dir / tenant_id
    elif user_id:
        upload_dir = base_dir / user_id
    else:
        upload_dir = base_dir / "conversation"

    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir
```

#### 2.2.2 Agent 注入 user_id 和 tenant_id

**修改文件**: `src/core/agent.py`

在现有邮件工具 `set_user_id` 注入点（约 line 1212-1216）附近，增加对 `register_download_file` 工具的注入：

```python
if user:
    for tool_name in ("email_send", "email_read", "email_list_folders"):
        tool = self.tool_registry.get_tool(tool_name)
        if tool and hasattr(tool, 'set_user_id'):
            tool.set_user_id(user.user_id)

    # 注入用户信息到文件下载工具
    download_tool = self.tool_registry.get_tool("register_download_file")
    if download_tool:
        if hasattr(download_tool, 'set_user_id'):
            download_tool.set_user_id(user.user_id)
        if hasattr(download_tool, 'set_tenant_id'):
            # 从 Agent 的 session 上下文获取 tenant_id
            download_tool.set_tenant_id(self._get_tenant_id(session_id))
```

**tenant_id 获取方式**: 需要新增一个轻量方法。选项有：
- A) `process_message` 增加 `tenant_id` 参数（推荐，最直接）
- B) 从 session 的 DB 记录中查询

推荐方案 A：在 `process_message` 签名中增加 `tenant_id: Optional[str] = None`，调用处已有 `chat_tenant_id` 变量可以直接传入。

**调用处修改** (`src/main.py`):
```python
async for event in agent.process_message(
    user_input=full_message,
    session_id=session_id,
    user=agent_user,
    tenant_id=chat_tenant_id,   # 新增
    attachments=attachments,
    cancel_check=lambda: sse_manager.is_cancelled(session_id),
):
```

#### 2.2.3 文件存储路径规范

| 场景 | 路径 |
|------|------|
| 有租户有用户 | `storage/uploads/{tenant_id}/{user_id}/{file_id}{suffix}` |
| 有租户无用户 | `storage/uploads/{tenant_id}/{file_id}{suffix}` |
| 无租户有用户 | `storage/uploads/{user_id}/{file_id}{suffix}` |
| 无租户无用户 | `storage/uploads/conversation/{file_id}{suffix}` |

#### 2.2.4 下载信息注入 assistant 消息 metadata

**目标**: 将下载文件信息独立存储在 assistant 消息的 `metadata.downloadableFiles` 字段中，与 progressMessages 分离，确保前端稳定解析。

**修改文件**: `src/main.py`

在 SSE 流结束后、保存 assistant 消息前，从 `async for` 迭代中收集的 `tool_result` 事件中提取 `register_download_file` 的结果：

```python
# 从 tool_result 事件中提取下载文件信息
downloadable_files = []
for event in collected_events:
    if (event.get("type") == "tool_result"
        and event.get("toolName") == "register_download_file"
        and event.get("success") is True):
        result = event.get("result", {})
        if result.get("file_id"):
            downloadable_files.append({
                "file_id": result["file_id"],
                "file_name": result.get("file_name", "未命名文件"),
                "file_size": result.get("file_size", 0),
                "download_url": result.get("download_url", ""),
                "mime_type": result.get("mime_type", ""),
            })

# 保存 assistant 消息
if full_response:
    metadata = {"progressMessages": results.get('progress', [])}
    if downloadable_files:
        metadata["downloadableFiles"] = downloadable_files
    MessageDB.create(
        session_id=session_id,
        role="assistant",
        content=full_response,
        metadata=metadata
    )
```

**为什么这样做**:
- `downloadableFiles` 是独立字段，前端不需要解析 progressMessages 就能获取下载信息
- 持久化到 DB，历史消息加载时也能恢复下载卡片
- 下载信息是结构化的，比从 SSE 实时流中解析更稳定

### 2.3 前端改动

#### 2.3.1 类型定义

**修改文件**: `frontend/src/types/index.ts`

新增 `DownloadableFile` 类型和 `ChatMessage` 字段：

```typescript
export interface DownloadableFile {
  file_id: string
  file_name: string
  file_size: number
  download_url: string
  mime_type?: string
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp: number
  attachments?: AttachmentInfo[]
  progressMessages?: ProgressMessage[]
  downloadableFiles?: DownloadableFile[]  // 新增
}
```

#### 2.3.2 SSE 实时流 — 提取下载信息

**修改文件**: `frontend/src/composables/useAgent.ts`

在 `onToolResult` 回调中增加对 `register_download_file` 的特殊处理：

```typescript
const onToolResult = (toolName: string, result: any, success: boolean) => {
  // ... 现有的 toolName 分支 ...

  // 特殊处理：提取下载文件信息
  if (toolName === 'register_download_file' && success && result?.file_id) {
    const lastMsg = messages.value[messages.value.length - 1]
    if (lastMsg && lastMsg.role === 'assistant') {
      if (!lastMsg.downloadableFiles) {
        lastMsg.downloadableFiles = []
      }
      lastMsg.downloadableFiles.push({
        file_id: result.file_id,
        file_name: result.file_name || '未命名文件',
        file_size: result.file_size || 0,
        download_url: result.download_url || `/api/files/${result.file_id}/download`,
        mime_type: result.mime_type || '',
      })
    }
  }

  // 通用处理（保持不变）
  addProgress(`... ${toolDisplayName}执行完成`, 'tool_result', toolName, undefined, result)
}
```

#### 2.3.3 历史消息恢复

**修改文件**: `frontend/src/composables/useAgent.ts`

在 `switchSession` 加载历史消息时，从 `metadata.downloadableFiles` 恢复：

```typescript
messages.value = result.messages?.map(m => ({
  role: m.role as 'user' | 'assistant',
  content: m.content,
  timestamp: new Date(m.created_at.endsWith('Z') ? m.created_at : m.created_at + 'Z').getTime(),
  progressMessages: m.metadata?.progressMessages || [],
  attachments: m.metadata?.attachments || undefined,
  downloadableFiles: m.metadata?.downloadableFiles || undefined,  // 新增
})) || []
```

#### 2.3.4 消息渲染 — 下载卡片

**修改文件**: `frontend/src/components/MessageItem.vue`

在 AI 回复的最底部（执行详情之后）渲染下载卡片区域：

```vue
<template>
  <div class="flex-1 min-w-0">
    <!-- ... 现有内容：头像、文本、附件标签、执行详情 ... -->

    <!-- 下载文件卡片区域（仅 assistant 消息显示） -->
    <div
      v-if="message.role === 'assistant' && downloadableFiles.length > 0"
      class="mt-3 space-y-2"
    >
      <DownloadFileCard
        v-for="file in downloadableFiles"
        :key="file.file_id"
        :file="file"
      />
    </div>
  </div>
</template>
```

**新增组件**: `frontend/src/components/DownloadFileCard.vue`

卡片设计：

```
┌──────────────────────────────────────────────────────┐
│ 📄 报价单_2026.xlsx                        [下载 ↓]  │
│    128 KB · Excel 工作簿                              │
└──────────────────────────────────────────────────────┘
```

长方形小卡片，包含：
- 左侧：文件类型图标（根据 MIME 类型）
- 中间：文件名 + 文件大小
- 右侧：下载按钮

点击整个卡片触发下载（`window.open(download_url)` 或 `<a>` 标签）。

```vue
<template>
  <a
    :href="downloadUrl"
    download
    class="flex items-center gap-3 p-3 rounded-lg border border-gray-200
           bg-gray-50 hover:bg-blue-50 hover:border-blue-300
           transition-colors cursor-pointer group no-underline"
  >
    <!-- 文件图标 -->
    <div class="w-9 h-9 rounded flex items-center justify-center flex-shrink-0"
         :class="iconBgClass">
      <span class="text-lg">{{ iconEmoji }}</span>
    </div>

    <!-- 文件信息 -->
    <div class="flex-1 min-w-0">
      <div class="text-sm font-medium text-gray-800 truncate group-hover:text-blue-700">
        {{ file.file_name }}
      </div>
      <div class="text-xs text-gray-400 mt-0.5">
        {{ formatSize(file.file_size) }}
        <span v-if="fileTypeLabel"> · {{ fileTypeLabel }}</span>
      </div>
    </div>

    <!-- 下载按钮 -->
    <div class="flex-shrink-0 text-gray-400 group-hover:text-blue-600 transition-colors">
      <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586
                 a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
      </svg>
    </div>
  </a>
</template>
```

#### 2.3.5 会话缓存同步

**修改文件**: `frontend/src/composables/useAgent.ts`

确保 `sessionMessagesCache` 缓存包含 `downloadableFiles`（`JSON.parse(JSON.stringify(...))` 已自动包含所有字段，无需额外改动）。

---

## 3. 数据流图（新设计）

### 3.1 实时 SSE 流

```
用户: "帮我生成一个报价单"
  │
  ▼
Agent.process_message()
  │
  ├─ 注入 user_id/tenant_id 到 RegisterDownloadFileTool
  │
  ├─ LLM → 调用 skill_execute 生成文件
  ├─ LLM → 调用 register_download_file(file_path, display_name)
  │     │
  │     ├─ 从 self._user_id / self._tenant_id 构建路径
  │     ├─ 复制到 storage/uploads/{tenant_id}/{user_id}/{file_id}.xlsx
  │     ├─ 注册到 uploaded_files
  │     └─ 返回 {file_id, file_name, download_url, ...}
  │
  ├─ SSE → tool_result 事件 → 前端
  │     │
  │     ▼
  │   useAgent.onToolResult:
  │     ├─ 识别 register_download_file
  │     ├─ 提取 file_id/file_name/download_url
  │     └─ 追加到 assistantMessage.downloadableFiles[]
  │
  ├─ SSE → content chunks → 前端（流式文本）
  │
  └─ SSE → complete → 流结束
        │
        ▼
      后端保存:
        assistant metadata = {
          progressMessages: [...],
          downloadableFiles: [{file_id, file_name, download_url, ...}]
        }
```

### 3.2 历史消息恢复

```
用户切换回旧会话
  │
  ▼
switchSession(sessionId)
  │
  ├─ 内存缓存命中 → 直接使用（downloadableFiles 已在缓存中）
  │
  └─ 内存缓存未命中 → GET /api/sessions/{id}/messages
        │
        ▼
      DB 返回 messages:
        metadata.downloadableFiles: [{file_id, file_name, ...}]
        │
        ▼
      前端构建 ChatMessage:
        downloadableFiles: metadata.downloadableFiles
        │
        ▼
      MessageItem 渲染:
        底部显示 DownloadFileCard[] 卡片
```

---

## 4. 涉及文件汇总

### 新增文件

| 文件 | 说明 |
|------|------|
| `frontend/src/components/DownloadFileCard.vue` | 下载文件卡片组件 |

### 修改文件

| 文件 | 改动 | 说明 |
|------|------|------|
| `src/tools/file/register_download_tool.py` | 中 | 增加 `_user_id`/`_tenant_id` 属性和 setter，用独立路径构建函数替代 ContextVar |
| `src/core/agent.py` | 小 | `process_message` 增加 `tenant_id` 参数，注入 user_id/tenant_id 到工具 |
| `src/main.py` | 小 | 调用 `process_message` 时传入 `tenant_id`，保存 assistant 消息时提取 downloadableFiles |
| `frontend/src/types/index.ts` | 小 | 新增 `DownloadableFile` 类型，`ChatMessage` 增加 `downloadableFiles` 字段 |
| `frontend/src/composables/useAgent.ts` | 中 | `onToolResult` 增加 register_download_file 特殊处理，`switchSession` 恢复 downloadableFiles |
| `frontend/src/components/MessageItem.vue` | 小 | 底部增加 DownloadFileCard 渲染区域 |

---

## 5. 验收标准

### 功能验收

- [ ] LLM 生成文件后调用 `register_download_file`，助手回复底部显示下载卡片
- [ ] 点击卡片能下载文件
- [ ] 同一次回复生成多个文件时，显示多个下载卡片
- [ ] 切换到其他会话再切回，下载卡片仍然显示
- [ ] 重启后端服务后，历史消息中的下载卡片仍显示，且文件可下载
- [ ] 文件存储到正确的 `storage/uploads/{tenant_id}/{user_id}/` 路径
- [ ] 无租户场景下文件存储到 `storage/uploads/{user_id}/` 路径

### 稳定性验收

- [ ] `register_download_file` 工具不再依赖 ContextVar（消除线程问题）
- [ ] 下载信息独立存储在 `metadata.downloadableFiles`，不依赖 progressMessages 解析
- [ ] SSE 实时流和历史消息加载两条路径都能正确显示下载卡片
- [ ] LLM 偶尔多次调用 `register_download_file` 时不会产生重复卡片（前端按 file_id 去重）

### 兼容性验收

- [ ] 现有用户上传附件功能不受影响（`AttachmentChip` 独立运作）
- [ ] 现有 `/api/files/{file_id}/download` 接口不变
- [ ] 现有 progressMessages 展示不受影响
- [ ] 旧消息（没有 downloadableFiles 字段）不报错，不显示下载区域
