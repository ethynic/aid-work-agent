# 前端 Office 文件预览功能设计文档

> 日期：2026-05-28（2026-10-09 修订定稿）
> 状态：✅ 已完成开发与手动验收（2026-10-10）

---

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 样例文件与构建基线 | ✅ 完成（2026-10-10） | 5 个样例落盘 `test_uploads/office_preview/`（oversize 实测 18.6MB）；基线入口 chunk gzip 33.69 kB |
| Phase 2 | 三格式预览开发 + 单元测试 | ✅ 完成（2026-10-10） | 新增 utils/officePreview.ts 阈值/截断纯函数 + 4 个测试文件 57 项定向测试全过；全量 vitest（豁免 1 例存量路由快照失败）与 vue-tsc+vite build 门禁通过 |
| Phase 3 | 独立测试与代码评审 | ✅ 完成（2026-10-10） | 发现 2 个 P1（loadExcel/loadPptx 快速切换附件的过期竞态）已修复并由评审员复核清零；遗留 P2/nit 详见当次工作流报告 |
| Phase 4 | 手动浏览器验收 | ✅ 完成（2026-10-10） | 用户真机手动验收通过（Word 整页缩放/Excel 整表缩放与表格样式/三格式渲染/文件类型徽标/附件组件紧凑化）。验收历程：Excel/PPT 验收通过。docx 两轮反馈修复（2026-10-10）：①首验窄面板内容双向裁切 → 容器宽度判定 + overflow-auto；②二验要求仿 PPT 整页缩放 → 终版为 CSS zoom 整页等比缩放（保留 A4 版式，上限 1 / 下限 0.3，resize 重算）。同轮新增 FileTypeIcon 统一文件类型徽标（word/excel/ppt/pdf/图片/md/txt/code/html/压缩包）。三轮验收（2026-10-10）：整页缩放未生效的根因是 docx-preview 缺省把 <style> 注入渲染容器、firstElementChild 命中 style 而非 wrapper——改为按类名定位；Excel 表格同模式整表缩放；FileTypeIcon 补齐输入框附件标签与面板顶部两处漏改入口。全量 511 项测试与构建门禁通过。四轮验收（2026-10-10）Excel 表格样式优化：列宽按内容自适应不拉伸（width: max-content）、内边距 px-3 py-1.5、表头底部分隔线、数据行隔行浅底 |

性能实测（构建产物）：入口 chunk gzip 增量 ≈0 kB（33.69→33.71）；xlsx 懒加载 chunk 626.97 kB / gzip 322.75 kB；pptx 懒加载 chunk 313.66 kB / gzip 87.89 kB（echarts 已按 manualChunks 单独拆分）。

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

| 文件类型 | 库 | 真实体积 | 渲染质量 | 集成难度 |
|---------|-----|---------|---------|---------|
| Word (.docx) | `docx-preview`（已集成） | ~80KB gzip | 高 | 已完成，仅需在入口放行 |
| Excel (.xlsx) | `xlsx-js-style` | ~150KB gzip | 中（数据准确，无颜色边框，合并单元格保留） | 低 |
| PowerPoint (.pptx) | `pptx-preview` | **~1.2MB min / 400KB+ gzip**（主包仅 ~134KB，但硬依赖 echarts@5.5 + jszip + lodash，echarts 全量约 1MB） | 中（文本/图片/形状/主题色；动画、SmartArt 降级） | 低 |

**体积说明**：三库均 dynamic import 懒加载，用户首次点击对应格式预览时才下载，之后浏览器缓存，不影响首屏。注意 pptx-preview 曾被低估为 ~30KB，实际以 echarts 硬依赖为准——接受该懒加载 chunk 是"纯前端、零后端改动"路线的已知代价。

### 2.2 Excel 预览方案

**选型：`xlsx-js-style`（SheetJS 社区 fork）+ HTML 表格渲染**

```typescript
// 核心逻辑
import * as XLSX from 'xlsx-js-style'

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

### 2.5 已评估并排除的备选方案（登记备查，避免重复调研）

| 方案 | 排除理由 |
|------|---------|
| 后端 LibreOffice headless（office→pdf→png，"微信式转图"） | 镜像 +400~500MB、需处理 soffice 并发锁与中文字体包；项目曾引入又下架（`src/tools/pdf/pdf_router.py` 注明"Word 转 PDF 已下架，LibreOffice 无法保证表格格式保真"）。微信内置预览即此类服务端转码，但腾讯实现（TBS/X5 内核）闭源，Web 场景的开源等价物只有 LibreOffice 这条路 |
| 后端 openpyxl 生成时转 HTML（仅 Excel） | 覆盖不了用户上传的 xlsx（除非再加实时转换 API，超时/缓存/并发复杂度回归）；且与 Word/PPT 形成两套预览架构。决定性因素是覆盖范围：前端方案一套代码同时覆盖 AI 产出与用户上传两类入口 |
| `@vue-office/excel` / `@vue-office/pptx` | 前者 ~430KB gzip 的完整电子表格组件，为"看一眼内容"过重；后者内部即 pptx-preview 的封装，直接用 pptx-preview 更小 |
| Luckysheet / Univer / x-spreadsheet | 完整电子表格应用框架，过重；Luckysheet 与 x-spreadsheet 已停更（后继 Univer） |
| Free Spire.XLS / Aspose / PyMuPDF Pro | 免费版有行数/页数限制与试用水印；商业授权收费；PyMuPDF 开源版不支持 Office，Office 渲染仅在商业许可层 |
| excel2img（pywin32 COM 截图） | 依赖 Windows + 本机安装 MS Excel，Linux 容器不可用 |

---

## 三、影响范围

### 3.1 需要修改的文件

| 文件 | 修改内容 |
|------|---------|
| `frontend/package.json` | 新增 `xlsx-js-style`、`pptx-preview` 依赖 |
| `frontend/web/components/AttachmentPreviewPanel.vue` | 新增 Excel/PPT 预览模板、加载逻辑与移动端内容层适配 |
| `frontend/web/components/DownloadFileCard.vue` | `isPreviewable` 增加 docx/xlsx/pptx 判断（含大小门槛） |

> 注：早期版本写的 `frontend/src/` 路径已随前端目录重构变为 `frontend/web/`（commit `0d48fb36`）。

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

  // 新增：Office 文件（大小门槛见 5.2，超限在卡片上点击直接下载）
  const size = props.file.file_size ?? 0
  if (mime.value.includes('wordprocessing') || ext.value === 'docx') return size <= PREVIEW_MAX_BYTES.doc
  if (mime.value.includes('spreadsheet') || ['xlsx', 'xls'].includes(ext.value)) return size <= PREVIEW_MAX_BYTES.excel
  if (mime.value.includes('presentation') || ['pptx', 'ppt'].includes(ext.value)) return size <= PREVIEW_MAX_BYTES.doc

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

### 4.4 移动端内容层适配

面板壳层已具备移动端适配（手机全屏 `w-full md:max-w-[80vw]`、关闭按钮 44px 触控目标、`safe-area-inset-bottom`），这里只补内容层：

| 类型 | 适配方式 |
|------|---------|
| Word | 整页缩放模式（仿 PPT，2026-10-10 二次验收修订：先按窗口宽度判定 ignoreWidth 重排，首验窄面板内容双向裁切；改按容器宽度重排后二验仍不满足，终版为整页缩放）——`ignoreWidth: false` 保留 A4 原始页宽与版式，渲染后读取 section 内联页宽，按容器内容宽度对 wrapper 设置 CSS `zoom` 等比缩放整页可见；上限 1（不放大），下限 0.3（触底退回横向滚动）；窗口 resize 防抖重算缩放，无需重新渲染 |
| Excel | 表格容器 `overflow-auto` 横向滚动即可，这是表格的自然交互，无需特殊处理 |
| PPT | `pptx-preview` 传 `slideScale` 按容器宽度等比缩放，整页可见，细节滚动查看 |

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

### 5.2 大小与行数门槛（硬门槛）

纯前端预览是"JS 解析 + 一次性建全部 DOM"，超大文件会卡死低端手机浏览器；且 xlsx 压缩比极高，文件大小对 Excel 是很差的代理指标（2MB 文件展开可能是几十万行）。门槛在打开预览**之前**判断——`attachment.size` / `file_size` 已知，无需先下载文件：

| 类型 | 门槛 | 超出行为 |
|------|------|---------|
| Excel | > 10MB | 不预览，走"文件较大，建议下载查看"提示 + 下载按钮（复用现有不支持预览 UI） |
| Excel | 单 sheet > 1000 行 | 渲染前 1000 行：从 `sheet['!ref']` 解码行列数，重写 `!ref` 截断后渲染；顶部必须显示显眼截断提示"仅显示前 1000 行，完整内容请下载"（静默截断会让用户误以为表格就这么大） |
| Word / PPT | > 20MB | 不预览，同 Excel 提示 |

- 三个阈值（10MB / 20MB / 1000 行）定义为具名常量集中一处，便于调整。
- `DownloadFileCard.vue` 的 `isPreviewable` 与面板 `previewType` 判定处加同一判断：超大文件在下载卡片点击直接下载，连面板都不打开；其他入口（消息附件 chip）打开面板后显示提示。
- PPT 超过 20 页、Word 超过 100 页：正常渲染（pptx 翻页导航、docx 自带分页），不设门槛。
- 不做分片渲染 / 虚拟滚动 / Web Worker：这些库不支持，自己包一层等于造轮子；超界引导下载是合理的产品边界。

---

## 六、测试计划

| 测试项 | 验证内容 |
|--------|---------|
| Word (.docx) 预览 | 点击 AI 产出的 docx 文件，面板打开并正确渲染 |
| Excel (.xlsx) 预览 | 点击 xlsx 文件，表格内容正确显示，多 Sheet 可切换 |
| PPT (.pptx) 预览 | 点击 pptx 文件，幻灯片正确渲染 |
| 文件类型图标 | docx/xlsx/pptx/图片/pdf/md/txt 等显示各自独立的品牌色徽标图标（FileTypeIcon 组件，附件 chip 与文件卡片一致），一眼可辨（2026-10-10 验收反馈升级，替代原 emoji 方案） |
| 不可预览文件降级 | .doc/.ppt 等旧格式文件显示"不支持预览" + 下载按钮 |
| 移动端适配 | 手机全屏面板；docx 窄屏自动重排不横向溢出；pptx 按容器缩放整页可见；Excel 横向滚动 |
| 大小与行数门槛 | 超限文件不打开预览并提示下载；>1000 行 sheet 截断渲染且顶部显示截断提示 |
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
