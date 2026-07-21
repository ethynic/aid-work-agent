# 开发计划：旅游行程 HTML 导出

> 关联设计：[itinerary-html-export-design.md](../subagent/travel-consultant/itinerary-html-export-design.md)
> 创建日期：2026-07-20
> 流程：三智能体（开发 → 测试 → CodeReview），见 [dev_workflow.md](../../.claude/rules/dev_workflow.md)

## 目标

旅游顾问详细行程默认以 HTML 长图交付（图片独立行 + 分档布局），x-to-image 内部完成图片 base64 内联；保留 Word 为可选备份。

---

## Phase A：x-to-image 图片 base64 内联能力（工具层）

> 目标：让 `x_to_image(content=html, content_type="html")` 能把 HTML 里的 `file_id:` / 远程 URL 图片自动转成 base64 渲染。向后兼容（tenant_id 可选）。

- [ ] A.1 扩展 `src/tools/_image_inliner.py`：新增 `inline_images_as_data_uri(text, tenant_id, user_id, fetch_remote=True)`
  - 复用 `_HTML_IMG_FILE_ID_PATTERN` / `_HTML_IMG_REMOTE_PATTERN` / `_are_sub`
  - file_id → `registry.get_ref_by_file_id` + `resolve_local_path` → 读文件 → base64 data URI（mime 取 ImageRef.mime_type，兜底 image/png）
  - 远程 URL → `registry.fetch_to_local` → 读文件 → base64
  - 单图失败保留原 src，记 warning，不阻断
- [ ] A.2 `src/services/x_to_image/models.py`：`XToImageInput` 加 `tenant_id: Optional[str]=None` / `user_id: Optional[str]=None`
- [ ] A.3 `src/services/x_to_image/renderers/html_renderer.py`：`render()` 在 `_shoot_full_page` 前，`if inp.tenant_id:` 调 `inline_images_as_data_uri`，异常回退原 HTML
- [ ] A.4 `src/tools/image/x_to_image_tool.py`：照搬 WordProcessTool 双轨租户注入
  - `__init__` 加 `_tenant_id`/`_user_id` 属性 + `set_tenant_id`/`set_user_id` 方法
  - `execute()` 双轨获取（注入优先，`get_current_tenant_id` 兜底），传入 `XToImageInput`
- [ ] A.5 启动安全自检：`python -c "from src.tools.image.x_to_image_tool import XToImageTool; from src.services.x_to_image.renderers.html_renderer import HtmlRenderer"`；确认 import 不拉起 master_agent

**Phase A 验收**：用一段含 `<img src="file_id:...">` 的 HTML（手工造一个注册过的 file_id）调 `x_to_image_service.convert`，断言长图含图、图片非空。

---

## Phase B：旅游顾问 SUBAGENT.md 改造

> 目标：详细行程主输出从 Markdown+word_process 改为 HTML+x_to_image；Word 降为可选。

- [ ] B.1 改「阶段二·第四步：细化行程」：客户确认后，按标准 HTML 模板生成完整 HTML → `x_to_image(content=html, content_type="html", output_name=...)` → `cp` 注册 PNG
- [ ] B.2 重写「详细行程图片规范」：景点信息行下方独立图片行（colspan）+ 分档（`i1`/`i23`/`imany`）+ `<img src="file_id:...">` 引用规则
- [ ] B.3 嵌入标准 HTML 模板示例（基于原型 v2：标题区 + 5 列表 + 独立图片行 + 内联 CSS + 无 JS）
- [ ] B.4 新增「Word 可选备份」：客户要可编辑/打印时，额外 `word_process(content=行程Markdown)`
- [ ] B.5 同步「行为约束」相关条目（原第 8/9 条 word_process 描述）

**Phase B 验收**：人工/真实行程跑一遍旅游顾问，确认生成 HTML 长图、图片布局符合 1/2/3/4 张分档、Word 可按需生成。

---

## Phase C：测试与端到端

- [ ] C.1 `tests/unit/tools/test_image_inliner.py`：`inline_images_as_data_uri` 的 file_id/远程/单图失败/mime/空输入用例
- [ ] C.2 `tests/unit/services/x_to_image/test_renderers.py`：html_renderer 有/无 tenant_id 路径、图片解析注入
- [ ] C.3 `tests/unit/tools/image/test_x_to_image_tool.py`：set_tenant_id 注入、ContextVar 兜底、tenant_id 传入 service
- [ ] C.4 `tests/integration/services/x_to_image/test_x_to_image_integration.py`：行程 HTML（1/2/3/4 张图）端到端 → 长图非空
- [ ] C.5 回归：x-to-image text/markdown 渲染器不受影响；word_process 链路不受影响
- [ ] C.6 全量测试：`./scripts/dev_test.sh tests/unit/tools/test_image_inliner.py tests/unit/services/x_to_image/ tests/unit/tools/image/test_x_to_image_tool.py tests/integration/services/x_to_image/ -p no:cacheprovider -q`

---

## 三智能体流程

| 阶段 | 职责 | 验收门 |
|------|------|--------|
| 开发智能体 | 实现 A/B/C，自测全绿 | 改动文件清单全部完成，相关测试绿 |
| 测试智能体 | 独立跑测试 + 回归 + 启动安全检查 | 测试全绿 + import/build 安全 |
| CodeReview 智能体 | 独立审查，修 P0/P1 | 正确性/资源/并发/启动安全/配置一致/测试质量过审 |

## 完成后

- [ ] 更新 `docs/ideas.md` 状态（🔧 部分完成 → ✅，移至 `docs/ideas_finished.md`）
- [ ] 更新设计文档「状态」为已实现
