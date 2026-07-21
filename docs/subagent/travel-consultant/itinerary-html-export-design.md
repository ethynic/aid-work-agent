# 旅游行程 HTML 导出设计文档

> 创建日期：2026-07-20
> 状态：设计完成，待评审
> 关联：旅游顾问子智能体（`subagents/travel-consultant/SUBAGENT.md`）、x-to-image 工具（`src/tools/image/x_to_image_tool.py`）、图片资产管线（`src/core/image_asset.py`）
> 开发计划：[plan-itinerary-html-export.md](../../plans/plan-itinerary-html-export.md)

---

## 1. 背景与问题

旅游顾问子智能体在「阶段二·第四步」为客户生成详细行程文档。**现有链路**：智能体产出 5 列 Markdown 行程表 → `word_process` → `md_to_word.py`（Pandoc）→ docx；景点图片以 `file_id:` 引用，由 `_image_inliner` 解析后追加在表格之后的独立「景点图集」章节。

**客户痛点**：图片落在表格下方独立章节，不在表格内。部分客户希望「图片嵌在对应景点行的下方」。经调研（见 §3）与原型验证，确认两个事实：

1. **Pandoc 的 pipe_tables 本身支持单元格内嵌图片**——现有「图片在表格外」是 `SUBAGENT.md` 的 prompt 设计选择，不是技术限制。
2. 但 **Word 路径对图片布局的控制力弱**（图片尺寸、多图横排、圆角阴影等需额外 python-docx 后处理），且「不同客户各种样式需求」难以用 Word 模板灵活满足。

## 2. 目标与非目标

### 2.1 目标

| 编号 | 目标 |
|------|------|
| G1 | 旅游顾问详细行程默认以 **HTML 长图（PNG）** 交付，图片布局精确可控 |
| G2 | 图片布局规则：**独立成行**（紧跟景点信息行下方、跨列），分档展示——1 张大图 / 2~3 张等宽横排 / ≥4 张每行最多 3 张自动换行 |
| G3 | 图片 base64 内联由 **x-to-image 工具内部完成**，智能体只写 `<img src="file_id:xxx">`，绝不接触 base64 |
| G4 | 保留 **Word 作为可选备份**（客户需要可编辑/打印文档时），现有 `word_process` 链路不破坏 |
| G5 | 本期只做 **一套标准 HTML 模板**，不做多租户多样式（预留扩展，见 §9） |

### 2.2 非目标

- 不做多套模板 / 租户自定义模板（延后，见 §9 扩展性）
- 不改动 `word_process` / `md_to_word` 现有行为（Word 备份路径完全复用现状）
- 不改动 x-to-image 的 text / markdown 渲染器（仅 HTML 渲染器新增图片解析）
- 不引入新的重型依赖

## 3. 方案选型

### 3.1 Word（图片嵌表格）vs HTML 长图

| 维度 | Word（图片嵌表格） | **HTML 长图（采纳）** |
|------|------|------|
| 图片布局控制 | 弱，需 python-docx 后处理才能控宽/横排 | ✅ 强，CSS flex / `object-fit` / 圆角 / 阴影全控 |
| 视觉档次 | 朴素表格 | ✅ 卡片 + 渐变 + badge，专业 |
| 多客户样式定制 | 难（改 Word XML / 模板） | ✅ 易（换 CSS 模板即可，未来可租户上传） |
| 交付形态 | 可编辑 docx | ⚠️ 不可编辑图片 |
| 客户二次修改 | 直接改 | 需重新生成 |
| 打印/归档 | 自然分页 | 长图打印需缩放 |
| IM 渠道展示 | 需下载 | ✅ 直接发图，体验好 |
| 实现成本 | 改 prompt + 加 docx 后处理 | 改 prompt（生成 HTML）+ 调现成 x-to-image + 给 x-to-image 加图片解析 |

**决策**：HTML 长图为主交付，Word 为可选备份。HTML 路径一并解决「多客户多样式」的长期诉求（CSS 模板远比 Word 排版灵活）。

### 3.2 已验证的关键技术约束

| 约束 | 验证结论 | 影响 |
|------|---------|------|
| Pandoc pipe_tables 单元格内嵌图片 | ✅ 支持（单图/图文混排/多图横排/换行多图） | Word 备份路径若要嵌图，仅改 prompt 即可 |
| `md_to_word` 完整流程对表格内图片 | ✅ 不破坏（normalize/CJK/模板样式均兼容） | Word 备份路径无需改工具 |
| headless Chromium 加载本地图片 | ❌ `file:///` 与裸绝对路径均被 `about:blank` 安全策略禁止；✅ base64 data URI 可加载 | **HTML 图片必须 base64 内联**，决定 §5.1 |
| x-to-image 工具 | ✅ 已实现并注册（`agent.py:431`），旅游顾问 `inherit:true` 已可用 | 工具层就绪，仅需加图片解析能力 |
| 租户注入 | ✅ agent 主循环 `hasattr(tool,'set_tenant_id')` 钩子自动注入 | x-to-image 工具实现 `set_tenant_id` 即可，无需改 agent.py |

## 4. 整体流程

### 4.1 主路径：HTML 长图

```
旅游顾问搜索景点(attraction_search) → 取每个景点 cover_image.file_id
  → 按标准 HTML 模板生成完整行程 HTML(内联 CSS + <img src="file_id:file_xxx">)
  → x_to_image(content=html, content_type="html", output_name="贵州行程")
       内部：
         1) HtmlRenderer 渲染前：解析图片(file_id/远程URL → base64 data URI)
         2) Playwright headless set_content + full_page 截图 → 单张长图 PNG
  → cp(source_file_path=image_path, display_name="贵州行程.png") 注册下载
  → 客户收到行程长图
```

### 4.2 备路径：Word（客户要可编辑/打印时）

```
旅游顾问 → word_process(content=行程Markdown)  # 现状不变
  → cp 注册 .docx 下载
```

两条路径共用同一份行程数据（景点、时段、项目），仅渲染出口不同。

## 5. 详细设计

### 5.1 x-to-image 图片 base64 内联（核心工具改造）

**为什么必须做**：headless Chromium 对 `set_content` 注入的页面（origin=`about:blank`）禁止加载本地文件（`file:///` 和绝对路径均失败），只有 base64 data URI 能可靠渲染。因此 HTML 里的 `<img src="file_id:file_xxx">` 或 `<img src="https://...">` 必须在渲染前转成 base64 data URI。

**为什么放在工具层而不是智能体**：base64 字符串体积大、占用 LLM 上下文 token、智能体手写易错；在转长图时转换对智能体零负担、零消耗。

#### 5.1.1 新增图片解析函数

扩展 `src/tools/_image_inliner.py`，新增 `inline_images_as_data_uri()`（与现有 `inline_images()` 并列，复用其正则与 ImageRegistry 调用逻辑，仅输出形式不同）：

```python
async def inline_images_as_data_uri(
    text: str,
    tenant_id: str,
    user_id: Optional[str] = None,
    fetch_remote: bool = True,
) -> Tuple[str, List[ImageRef]]:
    """把 HTML 中的 <img src="file_id:xxx"> / <img src="https://..."> 解析为 base64 data URI。

    与 inline_images() 的区别：输出 base64 data URI（而非本地路径），
    专供 x-to-image 的 set_content 渲染场景（about:blank 禁止加载本地文件）。

    - file_id:file_xxx → registry.get_ref_by_file_id + resolve_local_path → 读文件 → base64
    - https://...      → registry.fetch_to_local 下载 → 读文件 → base64
    - 单图失败保留原 src，记 warning，不阻断整图
    """
```

实现要点：
- 复用 `_HTML_IMG_FILE_ID_PATTERN` / `_HTML_IMG_REMOTE_PATTERN` 正则与 `_are_sub` 异步替换框架
- 解析得到本地路径后，`Path.read_bytes()` → `base64.b64encode` → `data:{mime};base64,{b64}`
- mime 从 ImageRef.mime_type 取（兜底 `image/png`）
- 单图失败（file_id 找不到 / 下载失败 / 文件读失败）保留原 src，记 warning，不抛异常
- 超大图保护：单文件 > 10MB 跳过（沿用 `ImageRegistry.fetch_to_local` 的限制；本地 file_id 已注册的图通常不大）

#### 5.1.2 XToImageInput 增加 tenant_id / user_id

`src/services/x_to_image/models.py` 的 `XToImageInput` 增加两个可选字段：

```python
@dataclass
class XToImageInput:
    ...
    tenant_id: Optional[str] = None   # 提供时启用 HTML 图片 base64 内联
    user_id: Optional[str] = None     # 远程图片下载时附带
```

#### 5.1.3 HtmlRenderer 接入图片解析

`src/services/x_to_image/renderers/html_renderer.py` 的 `render()` 在 `_shoot_full_page` 之前增加一步：

```python
async def render(self, inp, work_dir):
    content = Path(inp.source).read_text("utf-8") if inp.is_file_path else inp.source
    # 新增：tenant_id 提供时，把图片引用解析为 base64 data URI
    if inp.tenant_id:
        from src.tools._image_inliner import inline_images_as_data_uri
        try:
            content, _refs = await inline_images_as_data_uri(
                content, tenant_id=inp.tenant_id, user_id=inp.user_id
            )
        except Exception as e:
            logger.warning(f"[HtmlRenderer] 图片内联失败，回退原始 HTML: {e}")
    full_html = _wrap_to_full_doc(content)
    path = await self._shoot_full_page(full_html, inp, work_dir)
    return [path]
```

- 仅 HTML 渲染器接入（text/markdown 渲染器暂不涉及景点图片场景）
- `tenant_id` 缺省时行为与现状完全一致（向后兼容）

#### 5.1.4 x_to_image_tool 双轨租户注入（照搬 WordProcessTool）

`src/tools/image/x_to_image_tool.py`：

1. `__init__` 加 `self._tenant_id: Optional[str] = None` / `self._user_id`，加 `set_tenant_id()` / `set_user_id()` 方法
2. `execute()` 双轨获取（注入优先，ContextVar 兜底）后传入 `XToImageInput`
3. agent 主循环的 `hasattr(tool, 'set_tenant_id')` 钩子（`agent.py:2164` / `3195`）会自动注入，**无需改 agent.py**

```python
# execute() 内
tenant_id = self._tenant_id
if not tenant_id:
    try:
        from src.saas.context import get_current_tenant_id
        tenant_id = get_current_tenant_id()
    except Exception:
        tenant_id = None
# 同理 user_id
inp = XToImageInput(..., tenant_id=tenant_id, user_id=user_id)
```

### 5.2 HTML 行程标准模板

模板固化在 `SUBAGENT.md` 中作为智能体生成示例。关键规范：

#### 5.2.1 文档结构

- 完整 HTML 文档（DOCTYPE + head + body），**所有 CSS 内联在 `<style>`**，**不含任何 JavaScript**
- body 固定宽度 752px（适配 x-to-image 默认 width=800，留 padding）
- 字体栈：`'Microsoft YaHei','PingFang SC',sans-serif`

#### 5.2.2 行程表格：5 列 + 独立图片行

```
| 天数 | 时段 | 行程安排 | 游玩项目 | 备注 |
```

- 景点信息行：`<tr><td class="day">Dx</td><td><span class="time">时段</span></td>...</tr>`
- **图片独立行**：有图片的景点信息行下方紧跟 `<tr class="img-row"><td colspan="5"><div class="imgs {档位}">...<img>...</div></td></tr>`
- 无图片的行（纯交通/无 cover_image）不跟图片行

#### 5.2.3 图片分档布局规则（关键）

| 图片数 | 容器 class | 布局 |
|--------|-----------|------|
| 1 张 | `imgs i1` | 单张大图，占满图片行宽度，高 230px |
| 2~3 张 | `imgs i23` | flex 等宽横排，高 140px |
| ≥4 张 | `imgs imany` | flex + `flex-wrap`，每张 ~33.3%，**每行最多 3 张**自动换行，高 120px |

CSS（已原型验证）：

```css
.imgs { display:flex; gap:8px; flex-wrap:wrap; }
.imgs img { object-fit:cover; border-radius:8px; box-shadow:0 3px 8px rgba(0,0,0,.18); }
.imgs.i1 img { width:100%; height:230px; }
.imgs.i23 img { flex:1 1 0; min-width:0; height:140px; }
.imgs.imany img { flex:1 1 calc(33.333% - 6px); min-width:calc(33.333% - 6px); height:120px; }
tr.img-row td { padding:10px 12px; background:#fafbfd; }
```

智能体根据每个景点的图片数量选择对应 class（智能体知道图片张数，无需 JS 判断）。

#### 5.2.4 图片引用

- 智能体在 HTML 中写 `<img src="file_id:{cover_image.file_id}" alt="{景点名}">`
- 一个景点多张图时，用该景点的多张图 file_id（来源：attraction_search 返回的图片字段，后续若 cover_image 扩展为多图则直接取）
- 由 x-to-image 内部解析为 base64（智能体不碰 base64）

### 5.3 旅游顾问 SUBAGENT.md 改造

改动「阶段二·第四步：细化行程」及其后的「详细行程图片规范」「行为约束」相关条目：

1. **详细行程主输出改为 HTML**：第四步「先展示 → 客户确认 → 生成」的「生成」环节，从 `word_process(context=Markdown)` 改为：
   - 按标准 HTML 模板生成完整 HTML
   - 调 `x_to_image(content=html, content_type="html", output_name="{目的地}{天数}行程")` 转长图
   - 调 `cp` 注册 PNG 下载
2. **对话中展示**：仍先在对话里给客户看行程（可用精简文本/Markdown 概览，客户确认后再生成 HTML 长图）
3. **图片规范章节**：从「景点图集独立章节」改为「景点信息行下方独立图片行 + 分档布局」
4. **Word 作为可选**：增加一条——「客户明确要求可编辑 Word / 打印归档时，额外调 `word_process(content=行程Markdown)` 生成 docx」
5. **行为约束**：对应条目同步更新（第 8/9 条关于 word_process 的描述）

> 对话展示环节是否也改成 HTML 预览，还是保留 Markdown 概览，在开发阶段与产品确认；本文档默认「对话用 Markdown 概览 + 最终交付 HTML 长图」。

### 5.4 Word 备份路径

完全复用现状：`word_process(content=Markdown) → md_to_word(Pandoc) → docx`。本期不改动。如未来要让 Word 也支持图片嵌表格，仅需改 SUBAGENT.md prompt（Pandoc pipe_tables 已支持，§3.2 已验证），无需改工具——但本期不做。

## 6. 改动文件清单

| 文件 | 改动 | 类型 |
|------|------|------|
| `src/tools/_image_inliner.py` | 新增 `inline_images_as_data_uri()` | 核心 |
| `src/services/x_to_image/models.py` | `XToImageInput` 加 `tenant_id`/`user_id` | 核心 |
| `src/services/x_to_image/renderers/html_renderer.py` | `render()` 接入图片解析 | 核心 |
| `src/tools/image/x_to_image_tool.py` | 双轨租户注入 + 传 tenant_id | 核心 |
| `subagents/travel-consultant/SUBAGENT.md` | 行程导出改 HTML + 标准模板 + Word 可选 | 核心 |
| `tests/unit/tools/test_image_inliner.py` | 新函数单测 | 测试 |
| `tests/unit/services/x_to_image/test_renderers.py` | html_renderer 图片解析单测 | 测试 |
| `tests/unit/tools/image/test_x_to_image_tool.py` | 工具带 tenant_id 单测 | 测试 |
| `tests/integration/services/x_to_image/test_x_to_image_integration.py` | 行程 HTML 端到端 | 测试 |

`agent.py` 不改动（hasattr 钩子自动识别 `set_tenant_id`）。

## 7. 容错与兜底

| 场景 | 兜底 |
|------|------|
| 单张图片 file_id 找不到 / 下载失败 / 读文件失败 | 保留原 `<img src="file_id:xxx">`，记 warning，不阻断整图（浏览器渲染时该 img 显示 broken，但不影响其他图） |
| 整体图片解析异常 | HtmlRenderer 回退原始 HTML（图片不显示但行程文字正常） |
| Playwright/Chromium 不可用 | x-to-image 现有降级：返回 `success=False`，智能体可 fallback 到 word_process |
| 长图超 `max_height`（默认 20000px） | x-to-image 现有截断 + 底部提示；5~6 天行程通常远低于上限 |
| tenant_id 缺失（非 SaaS / 调试） | 跳过图片解析，HTML 原样渲染（远程 URL 图加载不了，但行程结构正常） |

## 8. 测试方案

- **单元**：
  - `inline_images_as_data_uri`：file_id→base64、远程URL→base64、单图失败容错、mime 正确、空输入
  - `html_renderer.render`：有/无 tenant_id 两条路径、图片解析注入正确
  - `x_to_image_tool.execute`：set_tenant_id 注入、ContextVar 兜底、tenant_id 传入 service
- **集成**：完整行程 HTML（含 1/2/3/4 张图四种档位）→ `x_to_image_service.convert` → 断言长图生成、图片非空、尺寸合理
- **回归**：x-to-image 现有 text/markdown 渲染器不受影响；word_process 链路不受影响
- 环境走 `./scripts/dev_test.sh`（容器/宿主机自动探测）

## 9. 扩展性（本期不做，预留）

- **多套 HTML 模板**：未来把标准模板从 SUBAGENT.md 抽出为可加载模板文件（`src/skills/travel-itinerary-template/` 或类似），按租户配置选择，支持「商务/活泼/简约」等风格切换
- **租户自定义模板**：租户在管理后台上传参考 HTML，系统按其结构渲染（需模板变量占位符机制）
- 这些扩展建立在本次「图片 base64 内联 + HTML 渲染」能力之上，本期完成后天然支持

## 10. 风险与回滚

| 风险 | 应对 |
|------|------|
| base64 膨胀导致长图体积大 | 行程图通常每张几十 KB，5~6 天行程总图可控；x-to-image 已有 PNG→JPEG(quality=85) 自动降级 |
| 智能体生成的 HTML 不规范导致渲染异常 | 模板固化 + 内联 CSS 无 JS，降低出错面；HtmlRenderer `_wrap_to_full_doc` 兜底补全文档结构 |
| 图片落不到对应景点行（智能体拼错 colspan/档位） | 模板示例清晰 + 开发阶段用真实行程验证 1/2/3/4 张四种情况 |
| 回滚 | SUBAGENT.md 可回退到 Markdown+word_process；x-to-image 改动向后兼容（tenant_id 可选，缺省行为不变） |

## 11. 关联文档

- [x-to-image 服务设计](../../tools/x-to-image/x-to-image-design.md)
- [图片资产管线设计](../../system/image-asset-pipeline-design.md)
- [旅游子智能体设计](travel_subagent_design.md)
- 开发计划：[plan-itinerary-html-export.md](../../plans/plan-itinerary-html-export.md)
