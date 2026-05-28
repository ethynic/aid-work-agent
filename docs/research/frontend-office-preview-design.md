# 前端 Office 文件预览功能设计文档

> 日期：2026-05-28
> 状态：设计中

---

## 一、现状分析

### 1.1 现有预览能力

项目已有文件预览面板（`AttachmentPreviewPanel.vue`），采用侧边滑入面板架构，支持以下类型：

| 类型 | 支持格式 | 预览方式 |
|------|---------|---------|
| 图片 | png/jpg/jpeg/gif | `<img>` 直接渲染 |
| PDF | pdf | `<iframe>` 内嵌 |
| HTML | html/htm | `<iframe sandbox>` 内嵌 |
| Markdown | md/markdown | fetch → marked 渲染 |
| 文本/代码 | txt/json/csv/js/ts/py 等 | fetch → `<pre><code>` + highlight.js |
| **Word** | **docx** | **docx-preview 已集成** |

### 1.2 缺失的预览能力

| 类型 | 格式 | 当前状态 |
|------|------|---------|
| Word (.doc) | doc | 不支持（旧格式） |
| **Excel** | **xlsx/xls** | **不支持，点击直接下载** |
| **PPT** | **pptx/ppt** | **不支持，点击直接下载** |

### 1.3 关键发现

- **docx-preview 已经集成**（`package.json` 已有 `"docx-preview": "^0.3.7"`），且 `AttachmentPreviewPanel.vue` 中已有完整的 DOCX 渲染逻辑
- 但 `DownloadFileCard.vue` 的 `isPreviewable` 判断中**未包含 docx**，导致 DOCX 文件在 AI 产出文件卡片中点击后直接下载而非打开预览
- Excel 和 PPT 预览完全缺失

---

## 二、技术方案

### 2.1 总体方案

采用**纯前端方案**，无需后端支持，按文件类型分别集成：

| 文件类型 | 库 | 包体积(gzip) | 渲染质量 | 集成难度 |
|---------|-----|-------------|---------|---------|
| Word (.docx) | `docx-preview`（已集成） | ~80KB | 高 | 已完成 |
| Excel (.xlsx) | `xlsx` (SheetJS) | ~150KB | 中（数据准确，样式简化） | 低 |
| PowerPoint (.pptx) | `pptx-preview` | ~30KB | 中低 | 低 |

**总新增包体积**：约 180KB gzip（xlsx + pptx-preview），可按需懒加载。

### 2.2 Excel 预览方案

**选型：`xlsx` (SheetJS) + HTML 表格渲染**

```typescript
// 核心逻辑
import * as XLSX from 'xlsx'

async function loadExcel(blob: Blob) {
  const arrayBuffer = await blob.arrayBuffer()
  const workbook = XLSX.read(arrayBuffer, { type: 'array' })

  // 渲染第一个 Sheet
  const sheet = workbook.Sheets[workbook.SheetNames[0]]
  const html = XLSX.utils.sheet_to_html(sheet, { editable: false })
  container.innerHTML = html
}
```

**特性**：
- 支持多 Sheet（通过 Tab 切换）
- 支持合并单元格
- 数据内容完整渲染
- 不渲染颜色/边框/字体等样式（可接受，预览场景以内容为主）

**SheetJS npm 冻结问题**：
- npm 上的 `xlsx` 包冻结在 0.18.5（约 4 年前），存在已知安全漏洞
- **解决方案**：使用 `xlsx-js-style`（社区 fork，修复安全漏洞 + 增加样式支持），或使用官方 CDN
- 本项目选择 `xlsx-js-style`，MIT 协议，API 完全兼容

### 2.3 PPT 预览方案

**选型：`pptx-preview`**

```typescript
import pptxPreview from 'pptx-preview'

async function loadPptx(blob: Blob) {
  await pptxPreview.preview(container, blob, {
    slideScale: 1,
  })
}
```

**特性**：
- 支持幻灯片逐页渲染
- 支持文本、图片、基础形状
- 不支持动画、嵌入对象、SmartArt
- 对于企业内部系统的 PPT 预览场景（主要是内容阅读），渲染质量可接受

### 2.4 不支持的场景

| 场景 | 处理方式 |
|------|---------|
| .doc（旧 Word 格式） | 显示"不支持预览" + 下载按钮 |
| .xls（旧 Excel 格式） | 尝试用 xlsx 解析，失败则提示下载 |
| 复杂 PPT（动画、嵌入视频） | 静默降级，渲染能渲染的内容 |

---

## 三、影响范围

### 3.1 需要修改的文件

| 文件 | 修改内容 |
|------|---------|
| `frontend/package.json` | 新增 `xlsx-js-style`、`pptx-preview` 依赖 |
| `frontend/src/components/AttachmentPreviewPanel.vue` | 新增 Excel/PPT 预览模板和加载逻辑 |
| `frontend/src/components/DownloadFileCard.vue` | `isPreviewable` 增加 docx/xlsx/pptx 判断 |

### 3.2 不需要修改的文件

| 文件 | 原因 |
|------|------|
| `useAttachmentPreview.ts` | 全局状态管理无需变动 |
| `ChatContainer.vue` | 面板容器无需变动 |
| `AttachmentChip.vue` | 附件标签无需变动 |
| 后端代码 | 纯前端方案，无后端改动 |

---

## 四、详细设计

### 4.1 `DownloadFileCard.vue` 修改

```typescript
// isPreviewable 增加以下判断
const isPreviewable = computed(() => {
  // 现有判断...
  if (['html', 'htm'].includes(ext.value) || mime.value === 'text/html') return true
  if (ext.value === 'pdf' || mime.value === 'application/pdf') return true
  if (mime.value.startsWith('image/') || ['png', 'jpg', 'jpeg', 'gif'].includes(ext.value)) return true
  if (['md', 'markdown'].includes(ext.value)) return true

  // 新增：Office 文件
  if (mime.value.includes('wordprocessing') || ext.value === 'docx') return true
  if (mime.value.includes('spreadsheet') || ['xlsx', 'xls'].includes(ext.value)) return true
  if (mime.value.includes('presentation') || ['pptx', 'ppt'].includes(ext.value)) return true

  return false
})
```

### 4.2 `AttachmentPreviewPanel.vue` 修改

#### previewType 判断扩展

```typescript
const previewType = computed(() => {
  // ... 现有判断不变 ...

  // DOCX（已有）
  if (mime.includes('wordprocessing') || ext === 'docx') return 'docx'

  // Excel（新增）
  if (mime.includes('spreadsheet') || ['xlsx', 'xls'].includes(ext)) return 'excel'

  // PPT（新增）
  if (mime.includes('presentation') || ['pptx', 'ppt'].includes(ext)) return 'pptx'

  return 'unsupported'
})
```

#### Excel 预览模板

```html
<!-- Excel Preview -->
<div v-if="previewType === 'excel'" class="flex flex-col h-full">
  <!-- Sheet 标签栏 -->
  <div v-if="excelSheetNames.length > 1" class="flex border-b border-gray-200 px-4 bg-gray-50 flex-shrink-0">
    <button
      v-for="(name, idx) in excelSheetNames"
      :key="idx"
      class="px-3 py-2 text-sm border-b-2 transition-colors"
      :class="excelActiveSheet === idx
        ? 'border-primary-500 text-primary-600 font-medium'
        : 'border-transparent text-gray-500 hover:text-gray-700'"
      @click="switchExcelSheet(idx)"
    >
      {{ name }}
    </button>
  </div>
  <!-- 表格内容 -->
  <div ref="excelContainer" class="flex-1 overflow-auto p-4"></div>
</div>
```

#### PPT 预览模板

```html
<!-- PPTX Preview -->
<div v-if="previewType === 'pptx'" class="flex flex-col h-full">
  <!-- 幻灯片导航 -->
  <div v-if="pptxTotalSlides > 1" class="flex items-center justify-center gap-4 py-2 border-b border-gray-200 bg-gray-50 flex-shrink-0">
    <button @click="prevSlide" :disabled="pptxCurrentSlide <= 0" class="p-1.5 rounded hover:bg-gray-200 disabled:opacity-30">
      <!-- 左箭头 SVG -->
    </button>
    <span class="text-sm text-gray-500">{{ pptxCurrentSlide + 1 }} / {{ pptxTotalSlides }}</span>
    <button @click="nextSlide" :disabled="pptxCurrentSlide >= pptxTotalSlides - 1" class="p-1.5 rounded hover:bg-gray-200 disabled:opacity-30">
      <!-- 右箭头 SVG -->
    </button>
  </div>
  <!-- 幻灯片内容 -->
  <div class="flex-1 overflow-auto p-4 flex items-start justify-center">
    <div ref="pptxContainer" class="shadow-lg"></div>
  </div>
</div>
```

#### 加载逻辑

```typescript
// Excel 加载
async function loadExcel() {
  if (!props.attachment?.file_id) return
  loading.value = true
  error.value = null

  try {
    const [XLSX, blob] = await Promise.all([
      import('xlsx-js-style'),
      fetch(getFileUrl(props.attachment.file_id)).then(r => {
        if (!r.ok) throw new Error('文件加载失败')
        return r.blob()
      })
    ])

    const arrayBuffer = await blob.arrayBuffer()
    excelWorkbook.value = XLSX.read(arrayBuffer, { type: 'array' })
    excelSheetNames.value = excelWorkbook.value.SheetNames
    excelActiveSheet.value = 0

    await nextTick()
    renderExcelSheet(0)
  } catch (e) {
    error.value = '表格预览加载失败，请尝试下载查看'
  } finally {
    loading.value = false
  }
}

function renderExcelSheet(index: number) {
  if (!excelWorkbook.value || !excelContainer.value) return
  const sheet = excelWorkbook.value.Sheets[excelWorkbook.value.SheetNames[index]]
  const html = XLSX.utils.sheet_to_html(sheet, { editable: false })
  excelContainer.value.innerHTML = html
}

function switchExcelSheet(index: number) {
  excelActiveSheet.value = index
  renderExcelSheet(index)
}

// PPT 加载
async function loadPptx() {
  if (!props.attachment?.file_id) return
  loading.value = true
  error.value = null

  try {
    const [pptxModule, blob] = await Promise.all([
      import('pptx-preview'),
      fetch(getFileUrl(props.attachment.file_id)).then(r => {
        if (!r.ok) throw new Error('文件加载失败')
        return r.blob()
      })
    ])

    await nextTick()
    if (pptxContainer.value) {
      await pptxModule.default.preview(pptxContainer.value, blob)
    }
  } catch (e) {
    error.value = '演示文稿预览加载失败，请尝试下载查看'
  } finally {
    loading.value = false
  }
}
```

### 4.3 CSS 样式

```css
/* Excel 表格样式 */
:deep(.excel-preview-table) {
  border-collapse: collapse;
  width: 100%;
  font-size: 13px;
}
:deep(.excel-preview-table td),
:deep(.excel-preview-table th) {
  border: 1px solid #e5e7eb;
  padding: 4px 8px;
  text-align: left;
  white-space: nowrap;
}
:deep(.excel-preview-table th) {
  background-color: #f9fafb;
  font-weight: 500;
  color: #6b7280;
}

/* PPT 容器样式 */
:deep(.pptx-preview-container) {
  background: white;
}
```

---

## 五、性能考虑

### 5.1 按需懒加载

三个库都使用 `dynamic import()` 加载，只有在用户实际点击预览时才下载对应的 JS：

```typescript
// 用户点击 Word 预览时才加载 docx-preview
const { renderAsync } = await import('docx-preview')

// 用户点击 Excel 预览时才加载 xlsx
const XLSX = await import('xlsx-js-style')

// 用户点击 PPT 预览时才加载 pptx-preview
const pptxModule = await import('pptx-preview')
```

Vite 会自动将这些动态 import 拆分为独立的 chunk，不影响首屏加载速度。

### 5.2 大文件处理

| 场景 | 处理策略 |
|------|---------|
| Excel 超过 10MB | 提示"文件较大，预览可能较慢"，加载后只渲染当前 Sheet |
| PPT 超过 20 页 | 正常渲染，提供翻页导航 |
| Word 超过 100 页 | docx-preview 已支持分页，正常渲染 |

---

## 六、测试计划

| 测试项 | 验证内容 |
|--------|---------|
| Word (.docx) 预览 | 点击 AI 产出的 docx 文件，面板打开并正确渲染 |
| Excel (.xlsx) 预览 | 点击 xlsx 文件，表格内容正确显示，多 Sheet 可切换 |
| PPT (.pptx) 预览 | 点击 pptx 文件，幻灯片正确渲染 |
| DownloadFileCard 图标 | docx/xlsx/pptx 文件显示对应的 emoji 图标和类型标签 |
| 不可预览文件降级 | .doc/.ppt 等旧格式文件显示"不支持预览" + 下载按钮 |
| 移动端适配 | 面板在移动端全屏显示，Excel/PPT 预览可滚动 |
| 构建验证 | `npm run build` 无错误 |

---

## 七、实施步骤

| 步骤 | 内容 | 预计工作量 |
|------|------|-----------|
| 1 | 安装 `xlsx-js-style` 和 `pptx-preview` 依赖 | 5 分钟 |
| 2 | 修改 `DownloadFileCard.vue` 的 `isPreviewable` 判断 | 5 分钟 |
| 3 | 修改 `AttachmentPreviewPanel.vue` 的 `previewType` 判断 | 5 分钟 |
| 4 | 实现 Excel 预览逻辑和模板 | 30 分钟 |
| 5 | 实现 PPT 预览逻辑和模板 | 30 分钟 |
| 6 | 添加样式 | 10 分钟 |
| 7 | 构建验证 + 手动测试 | 15 分钟 |
| **合计** | | **约 1.5 小时** |
