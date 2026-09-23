# 设计：PPT 图片资产接入（分析图表嵌入）

> 场景：用户要求数据分析后生成 PPT 报告。agent 自主调度 `analyze_data` → 整理结论 → `ppt_process`，本文档只解决其中缺失的一环：**图片资产如何进入 PPT 工具**。

## 1. 背景与问题

- `analyze_data` 返回 `conclusion`（文本结论）+ `artifacts`（图表 PNG，带 `download_path`，落盘 `storage/tenants/{tid}/report/`），这些已在调用方 agent 的对话上下文里——**文本上下文传递已经打通**，agent 可整理为 markdown 大纲经 `content` 传入。
- `ppt_process` 底层管道已具备图片能力：planner 支持 `image` 布局页（image_path）、spec_builder 有 `_image()` 生成 ImageNode、node 渲染器渲染图片——但**入口没有约定**：`file_paths` 只声明接受 .pptx/.html，`image_path` 无任何路径校验（LLM 可控路径直通子进程读文件，安全裸面）。

## 2. 设计原则

1. **契约而非引导**：不新增任何主智能体/分析侧的链式提示词；只在 `ppt_process` 入参契约中声明"能吃什么"（`images` 参数说明），何时调、传什么由 agent 自主判断。
2. **语义随图传递**：裸路径无法支撑 planner 编排（不知道每张图放哪、说明什么）。`images` 每项必填 `title` + `caption`，语义来源是调用方 agent（它刚读过 conclusion + artifacts），契约层面强制"带含义"。
3. **不做 VL 图片理解**：调用方是语义最全、成本为零的来源；VL 猜含义既慢又可能错。未来 agent 只拿裸图的场景再考虑可选兜底。

## 3. 方案

### 3.1 入参契约（ppt_process_tool.py）

```python
class ImageAssetInput(BaseModel):
    path: str        # 图片文件路径（如 analyze_data artifacts 的 download_path）
    title: str       # 必填，如"月度销售额趋势"
    caption: str     # 必填，该图说明什么结论/怎么读
```

- `PptProcessInput` 新增 `images: Optional[List[ImageAssetInput]]`（≤20 项）；title/caption strip 后非空，否则 schema 校验打回。
- `images` 不参与模式探测（`_detect_mode` 不变）；`images` 单独传入不构成有效输入（`require_content_or_file` 不放宽）。

### 3.2 路径校验（新模块 image_assets.py）

- **允许根域 = 当前租户存储根** `storage/tenants/{tenant_id}`（图表在 `report/`、会话文件在 `conversation/`）。租户解析：tool 执行上下文 → saas context → `_anonymous` 兜底，与 `_get_tenant_upload_dir` 一致。**租户 A 无法引用租户 B 的文件。**
- 校验项：扩展名 {.png, .jpg, .jpeg}；文件存在且非空；单文件 ≤ 20MB；`normcase(realpath())` 包含检查防穿越（Windows 大小写归一，模式同 `weixin_marketing/assets.py`、根域包含检查同 `analysis_artifacts.py`）。
- 校验失败 **fail fast**：整体打回，错误逐条列出问题项（缺 caption 在 schema 层打回，其余在 execute 层）。
- 通过后规范化为绝对路径再注入。

### 3.3 模式边界

| 模式 | images 行为 |
|------|------------|
| topic / outline（auto） | **支持**：资源清单注入 planner prompt，编排后确定性回收（见 3.4/3.5） |
| spec | 不接受 `images`（报错提示直接在 spec 中写 image 节点）；spec 内 image/raster 节点路径做**同样的根域校验 + 绝对路径规范化**，堵住绕过 `images` 的路径 |
| template / html | 不支持，传入即报错（明确错误信息，不静默丢弃） |

### 3.4 planner 资源注入（planner.py）

`plan_from_topic` / `plan_from_content` 增加可选 `images` 参数，prompt 末尾附加：

```
可用图片资源（每项含标题、说明、本地路径；将合适的图片编排为 image 页）：
1. 《月度销售额趋势》——Q1-Q3 各月销售额折线对比，Q3 环比 +23%
   路径: C:\...\storage\tenants\t1\report\月度销售额趋势_20260923_120000.png
```

system prompt 硬性规则补一条：image 页的 `image_path` 必须原样取自资源清单路径，不得自行编造。

### 3.5 漏图与错图回收（image_assets.py，确定性后处理）

planner LLM 的路径转抄不可全信，plan 返回后统一 reconcile：

1. 遍历 plan 中的 image 页，将其 `image_path` 与已校验图片清单匹配（normcase 全路径相等 → 退化 basename 相等且唯一）；命中则**回写为校验后的绝对路径**（自愈 LLM 转抄误差），未命中的 image 页丢弃并记 warning。
2. 未被任何 image 页引用的图片，在最后一个 summary 页前**强制追加 image 页**（title + caption），保证"传了必进"。
3. spec 模式对 spec 内 image/raster 节点执行同样的校验与规范化（不追加）。

### 3.6 渲染

- **pptxgenjs 主路径**：现有 ImageNode 链路不变，注入的是校验后绝对路径。
- **python-pptx 兜底路径**：新增 `layouts/image.py::render_image`（标题 + add_picture 等比适配 + caption），`generator._create_slide` 路由 `layout == "image"`；文件缺失降级为文字页并记 warning，不中断生成。

### 3.7 工具描述与结果反馈

- `TOOL_DESCRIPTION` 入参说明补 `images` 一段（契约说明：接受分析等工具产出的图片路径 + 标题 + 说明，将以图片页嵌入；路径须为本租户存储内可读文件）。
- 成功结果 message 附"已嵌入 N 张图片"，给 agent 反馈闭环。

## 4. 安全分析

- **LLM 可控路径 → 本地任意文件读取**：根域限定当前租户存储目录 + realpath 包含检查 + 扩展名/大小限制；spec 内嵌路径同样受控（3.3/3.5）。
- **跨租户**：根域含 tenant_id，路径穿越与跨租户引用同被 normcase(realpath) 包含检查拦截；session 级隔离不需要（图表按租户落盘）。
- **错误信息**：校验失败信息不含绝对路径以外的敏感内容；沿用 `_format_user_error` 脱敏风格，逐条列出"第 N 张图：文件不存在/越界/超限"。

## 5. 非目标

- 不改数据分析侧任何代码；不加主智能体链式引导提示词。
- 不做 VL 图片理解、不做图片裁剪/美化。
- 不改 planner 布局体系与现有五种生成模式的路由逻辑。
