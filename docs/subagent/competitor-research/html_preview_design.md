# HTML 文件预览功能 — 开发设计文档

> 版本: v1.0 | 创建: 2026-05-19 | 状态: 待审核

## 一、需求背景

竞品研究子智能体生成的报告为 HTML 格式（逐页生成），用户需要在前端直接预览这些 HTML 文件，而非查看源代码。

当前前端 `AttachmentPreviewPanel.vue` 将 HTML 文件归为 `text` 类型，用 `<pre><code>` 显示源码，不支持渲染预览。

---

## 二、现状分析

### 2.1 前端预览类型判断

`AttachmentPreviewPanel.vue:152-176` 的 `previewType` 计算属性：

```
image   ← mime.startsWith('image/') 或 ext 为 png/jpg/jpeg/gif
pdf     ← mime 为 application/pdf 或 ext 为 pdf
markdown ← ext 为 md/markdown
text    ← ext 在 textExts 列表中（包含 html）或 mime 匹配 text/* 等
docx    ← mime 包含 wordprocessing 或 ext 为 docx
unsupported ← 其他
```

**问题**：`html` 在 `textExts` 列表中（第 168 行），被判定为 `text` 类型，显示为源码。

### 2.2 后端 MIME 类型映射

项目中存在 **3 处** 独立的 `mime_type_map` 字典，均缺少 `.html`：

| 位置 | 文件 | 行号 |
|------|------|------|
| 上传路由 | `src/main.py` | 677-690 |
| 磁盘扫描恢复 | `src/main.py` | 818-831 |
| 注册下载工具 | `src/tools/file/register_download_tool.py` | 89-104 |

HTML 文件注册后 `mime_type` 为 `application/octet-stream`，浏览器收到后不会按 HTML 解析。

### 2.3 文件服务路由

`/api/files/{file_id}` 使用 `FileResponse(content_disposition_type="inline")` 返回文件，`media_type` 取自 `file_info["mime_type"]`。如果 MIME 正确为 `text/html`，浏览器 iframe 可以直接渲染。

---

## 三、改动方案

### 改动总览

| # | 文件 | 改动 | 说明 |
|---|------|------|------|
| 1 | `frontend/src/components/AttachmentPreviewPanel.vue` | 新增 HTML 预览分支 | 从 textExts 中移除 html，新增 `previewType === 'html'` iframe 渲染 |
| 2 | `src/main.py:677` | MIME 映射加 `.html` | 上传路由的 mime_type_map |
| 3 | `src/main.py:818` | MIME 映射加 `.html` | 磁盘扫描恢复的 mime_type_map |
| 4 | `src/tools/file/register_download_tool.py:89` | MIME 映射加 `.html` | 注册下载工具的 mime_type_map |

---

### 改动 1：前端 — AttachmentPreviewPanel.vue

#### 1.1 previewType 判断调整

**位置**：`previewType` 计算属性（约第 152-176 行）

**改动逻辑**：在 Markdown 判断之后、text 判断之前，新增 HTML 判断；同时从 `textExts` 中移除 `html`。

```typescript
const previewType = computed(() => {
  if (!props.attachment) return 'unsupported'
  const mime = props.attachment.mime_type || ''
  const name = props.attachment.name || ''
  const ext = name.includes('.') ? name.split('.').pop()!.toLowerCase() : ''

  // 图片
  if (mime.startsWith('image/') || ['png', 'jpg', 'jpeg', 'gif'].includes(ext)) return 'image'

  // PDF
  if (mime === 'application/pdf' || ext === 'pdf') return 'pdf'

  // Markdown
  if (ext === 'md' || ext === 'markdown') return 'markdown'

  // HTML（新增）
  if (ext === 'html' || ext === 'htm' || mime === 'text/html') return 'html'

  // 文本/代码（从 textExts 中移除 'html'）
  const textExts = ['txt', 'json', 'csv', 'js', 'ts', 'py', 'vue', 'css', 'xml', 'yaml', 'yml', 'sh', 'bat', 'sql', 'log', 'ini', 'conf', 'md']
  const textMimes = ['text/', 'application/json', 'application/javascript', 'application/xml']
  if (textExts.includes(ext) || textMimes.some(m => mime.startsWith(m))) return 'text'

  // DOCX
  if (mime.includes('wordprocessing') || ext === 'docx') return 'docx'

  return 'unsupported'
})
```

**判断优先级说明**：HTML 判断放在 Markdown 之后、text 之前。原因：
- ext=html 时必须命中 `html` 而非 `text`
- MIME 为 `text/html` 时也应命中 `html`（`text/` 前缀匹配会在 text 分支命中，需提前拦截）

#### 1.2 模板新增 HTML 预览区域

**位置**：模板中 PDF iframe 之后、Text/Code 区域之前（约第 91 行之后）

```html
<!-- HTML 预览 -->
<iframe
  v-if="previewType === 'html' && attachment?.file_id"
  :src="getFileUrl(attachment.file_id)"
  class="w-full h-full border-0"
  sandbox="allow-same-origin"
  @load="loading = false"
></iframe>
```

**sandbox 属性说明**：

| 属性值 | 作用 | 是否需要 |
|--------|------|----------|
| `allow-same-origin` | 允许 iframe 内容被视为同源，使 `/api/files/` 的 Cookie 认证正常工作 | **是** — 不加此属性，iframe 请求不带 Cookie，文件接口返回 401 |
| `allow-scripts` | 允许执行 JavaScript | **否** — 竞品报告是纯 HTML+CSS，不需要 JS |
| `allow-forms` | 允许表单提交 | **否** |
| `allow-popups` | 允许弹出窗口 | **否** |

不设置 `allow-scripts` 可防止 HTML 中潜在的恶意脚本执行。

#### 1.3 watch 中设置 loading

**位置**：watch attachment 变化的回调（约第 262-283 行）

在 image/pdf 的 loading 判断之后，增加 html：

```typescript
// 图片、PDF、HTML 设置 loading
if (previewType.value === 'image' || previewType.value === 'pdf' || previewType.value === 'html') {
  loading.value = true
}
```

#### 1.4 detectLanguage 清理

`detectLanguage` 函数中 `html` 的映射可保留，不影响功能（该函数仅在 text 类型预览时使用，HTML 走 iframe 不会调用此函数）。

---

### 改动 2：后端 — main.py 上传路由 MIME 映射

**位置**：`src/main.py:677-690`

在 `mime_type_map` 字典中增加：

```python
'.html': 'text/html',
'.htm': 'text/html',
```

插入位置：在 `.gif` 和 `.mp3` 之间（或字典末尾均可）。

---

### 改动 3：后端 — main.py 磁盘扫描 MIME 映射

**位置**：`src/main.py:818-831`

同改动 2，在该 `mime_type_map` 字典中增加：

```python
'.html': 'text/html',
'.htm': 'text/html',
```

---

### 改动 4：后端 — register_download_tool.py MIME 映射

**位置**：`src/tools/file/register_download_tool.py:89-104`

在该 `mime_type_map` 字典中增加：

```python
'.html': 'text/html',
'.htm': 'text/html',
```

---

## 四、数据流验证

以竞品研究子智能体生成一个 HTML 报告页为例，验证完整数据流：

```
1. 子智能体调用 content_generate 生成 HTML 内容
   ↓
2. 将 HTML 写入文件：storage/competitor_research/{session_id}/report/company_overview.html
   ↓
3. 调用 register_download_file(file_path="...company_overview.html", display_name="公司概况.html")
   ↓
4. register_download_tool 内部：
   - suffix = ".html" → mime_type = "text/html"  ← 改动 4 生效
   - 复制文件到 storage/uploads/{tenant_id}/{user_id}/file_xxx.html
   - 写入 Redis: {file_id, name, path, mime_type: "text/html", ...}
   - 返回 {file_id: "file_xxx", download_url: "/api/files/file_xxx/download"}
   ↓
5. 前端收到附件信息：{file_id: "file_xxx", name: "公司概况.html", mime_type: "text/html"}
   ↓
6. AttachmentPreviewPanel 判断 previewType：
   - ext = "html" → 命中新增的 HTML 分支 → previewType = "html"  ← 改动 1 生效
   ↓
7. 模板渲染 iframe：
   - src = "/api/files/file_xxx"
   - sandbox="allow-same-origin"
   ↓
8. 浏览器 iframe 请求 GET /api/files/file_xxx
   ↓
9. main.py serve_file 路由：
   - _get_file_info 从 Redis 获取 mime_type = "text/html"  ← 改动 2/3 确保 fallback 也正确
   - FileResponse(media_type="text/html", content_disposition_type="inline")
   ↓
10. 浏览器收到 Content-Type: text/html，在 iframe 中渲染 HTML 页面 ✅
```

---

## 五、安全性分析

### 5.1 XSS 风险

| 攻击路径 | 风险 | 防护 |
|----------|------|------|
| HTML 中嵌入 `<script>` 执行恶意代码 | 低 | sandbox 不含 `allow-scripts`，JS 被浏览器阻止执行 |
| HTML 中嵌入 `<iframe>` 加载外部页面 | 低 | sandbox 阻止嵌套浏览上下文（默认行为） |
| HTML 中嵌入 `<form>` 提交数据 | 低 | sandbox 不含 `allow-forms` |
| HTML 中使用 `javascript:` 协议链接 | 低 | sandbox 阻止脚本执行 |

### 5.2 同源策略

`allow-same-origin` 使 iframe 中的内容被视为同源。这意味着：

- **正面**：iframe 请求 `/api/files/` 时携带 Cookie，通过后端鉴权
- **风险**：理论上 iframe 内容可以访问父页面的 DOM（如果同时有 `allow-scripts`）
- **缓解**：不设置 `allow-scripts`，即使同源也无法执行 JS 操作父页面

### 5.3 内容来源

HTML 报告由 `content_generate` 工具生成，内容来源于 LLM 输出。LLM 生成的 HTML 不含 JavaScript，仅包含 CSS 样式和 HTML 结构。SUBAGENT.md 中的提示词会明确要求不生成 `<script>` 标签。

---

## 六、边界情况

| 场景 | 预期行为 |
|------|----------|
| 用户上传一个 HTML 文件 | 同样支持 HTML 预览（MIME 正确时） |
| HTML 文件中引用外部 CSS/JS | 外部资源加载失败（sandbox 限制），页面使用内联样式 |
| 超大 HTML 文件（>5MB） | iframe 加载较慢，loading 状态持续到 @load 触发 |
| HTML 文件 MIME 为 application/octet-stream（旧数据） | ext=html 仍然命中 html 预览类型（优先用 ext 判断） |
| 磁盘扫描恢复的 HTML 文件 | 改动 3 确保 fallback 时 MIME 正确 |
| htm 扩展名 | 同样支持（判断条件包含 htm） |
| 非 HTML 文件被重命名为 .html | 浏览器尝试渲染，如果内容不是 HTML 则显示空白或乱码 — 可接受 |

---

## 七、测试验证

### 7.1 手动测试

| # | 步骤 | 预期结果 |
|---|------|----------|
| 1 | 创建一个测试 HTML 文件，通过对话让子智能体生成并注册 | 前端出现 HTML 附件 |
| 2 | 点击附件预览 | 预览面板打开，显示渲染后的 HTML（非源码） |
| 3 | 检查 iframe 的 sandbox 属性 | 不包含 allow-scripts |
| 4 | 预览包含 `<script>alert(1)</script>` 的 HTML | 不弹窗，脚本被阻止 |
| 5 | 点击下载按钮 | 文件下载，内容完整 |
| 6 | 预览普通 .txt 文件 | 仍然显示为文本源码（不受影响） |
| 7 | 预览 .pdf 文件 | 仍然显示 PDF iframe（不受影响） |
| 8 | 预览 .md 文件 | 仍然渲染 Markdown（不受影响） |

### 7.2 回归验证

改动影响预览类型判断的优先级，需确认以下文件类型不受影响：

- `.txt` → text（源码显示）
- `.json` → text（源码显示）
- `.md` → markdown（渲染显示）
- `.pdf` → pdf（iframe）
- `.png/.jpg` → image（img 标签）
- `.docx` → docx（docx-preview 渲染）
- `.xlsx` → unsupported（下载提示）

---

## 八、改动差异汇总

### AttachmentPreviewPanel.vue

```diff
--- a/frontend/src/components/AttachmentPreviewPanel.vue
+++ b/frontend/src/components/AttachmentPreviewPanel.vue
@@ -88,6 +88,14 @@
         @load="loading = false"
       ></iframe>

+      <!-- HTML Preview -->
+      <iframe
+        v-if="previewType === 'html' && attachment?.file_id"
+        :src="getFileUrl(attachment.file_id)"
+        class="w-full h-full border-0"
+        sandbox="allow-same-origin"
+        @load="loading = false"
+      ></iframe>
+
       <!-- Text/Code Preview -->
       <div v-if="previewType === 'text' || previewType === 'markdown'" class="p-4">

@@ -165,8 +173,12 @@
       // Markdown
       if (ext === 'md' || ext === 'markdown') return 'markdown'

+      // HTML
+      if (ext === 'html' || ext === 'htm' || mime === 'text/html') return 'html'
+
       // 文本/代码
-      const textExts = ['txt', 'json', 'csv', 'js', 'ts', 'py', 'vue', 'html', 'css', 'xml', 'yaml', 'yml', 'sh', 'bat', 'sql', 'log', 'ini', 'conf', 'md']
+      const textExts = ['txt', 'json', 'csv', 'js', 'ts', 'py', 'vue', 'css', 'xml', 'yaml', 'yml', 'sh', 'bat', 'sql', 'log', 'ini', 'conf', 'md']
       const textMimes = ['text/', 'application/json', 'application/javascript', 'application/xml']
       if (textExts.includes(ext) || textMimes.some(m => mime.startsWith(m))) return 'text'

@@ -267,7 +279,7 @@
     // 图片和 PDF 设置 loading
-    if (previewType.value === 'image' || previewType.value === 'pdf') {
+    if (previewType.value === 'image' || previewType.value === 'pdf' || previewType.value === 'html') {
       loading.value = true
     }
```

### main.py（两处 mime_type_map 同样改动）

```diff
         '.gif': 'image/gif',
+        '.html': 'text/html',
+        '.htm': 'text/html',
         '.mp3': 'audio/mpeg',
```

### register_download_tool.py

```diff
         '.zip': 'application/zip',
+        '.html': 'text/html',
+        '.htm': 'text/html',
     }
```
