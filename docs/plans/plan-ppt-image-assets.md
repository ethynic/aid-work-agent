# 开发计划：PPT 图片资产接入（分析图表嵌入）

> 配套设计：[`docs/tools/ppt/ppt-image-assets-design.md`](../tools/ppt/ppt-image-assets-design.md)

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 设计文档 + 计划 + ideas.md 登记 | ✅ 完成（2026-09-23） | 设计评审通过（agent 自主调度、契约而非引导、语义随图传递） |
| Phase 2 | image_assets.py：校验 + reconcile | ✅ 完成（2026-09-23） | validate_images / reconcile_image_slides / normalize_spec_image_paths，normcase(realpath) 根域检查 |
| Phase 3 | 入参契约 + 模式边界 + 描述更新 | ✅ 完成（2026-09-23） | ImageAssetInput 三必填；仅 topic/outline 支持 images；spec 节点路径校验；错误信息脱敏分支 |
| Phase 4 | planner 注入 + python-pptx 兜底渲染 | ✅ 完成（2026-09-23） | 资源清单进 prompt + 硬性规则 11；layouts/image.py 等比适配 + 缺图降级 |
| Phase 5 | 测试：新增单测 + ppt 回归 | ✅ 完成（2026-09-23） | 修复后 16 用例（image_assets）+ 7 入口用例（ppt_tool）；ppt 套件 98 passed；html 导出用例因既有 marker 配置 deselected |
| Phase 6 | 独立测试智能体 + CodeReview 智能体 + 整合 | ✅ 完成（2026-09-23） | 测试：93 passed 采信、无 P0/P1；CR：1 P1 + 6 P2/nit 全部修复，重跑 98 passed，CR 复核修复增量通过；import 终检通过。待真机验收与部署 |

## 任务拆分

### Phase 2 — image_assets.py（新模块）

- [x] `ImageAsset` 数据结构（校验后的 path/title/caption 绝对路径形态）
- [x] `validate_images(raw_images, tenant_root) -> (validated, errors)`：扩展名/存在/大小/根域包含检查（normcase(realpath)），逐条错误
- [x] `resolve_tenant_root()`：tool context → saas context → `_anonymous`
- [x] `reconcile_image_slides(plan, validated_images)`：image 页路径匹配回写（normcase 全路径 → basename 唯一）、未命中丢弃记 warning、未引用图片 summary 前追加
- [x] `normalize_spec_image_paths(spec, tenant_root)`：spec 模式 image/raster 节点校验与绝对化

### Phase 3 — 入参契约（ppt_process_tool.py / input_normalizer.py）

- [x] `ImageAssetInput`（path/title/caption 必填，strip 非空）+ `PptProcessInput.images`（≤20 项）
- [x] `NormalizedPptInput` 透传 images；`_detect_mode` 不受影响
- [x] execute：topic/outline 模式走校验+注入+reconcile；spec 模式做节点路径校验；template/html 模式传 images 报错
- [x] `TOOL_DESCRIPTION` 补 images 契约说明；成功 message 附嵌入图片数

### Phase 4 — planner 与兜底渲染

- [x] `plan_from_topic` / `plan_from_content` 可选 `images` 参数，prompt 附加资源清单；system prompt 补 image_path 原样使用规则
- [x] `layouts/image.py::render_image`（python-pptx：标题 + add_picture 等比适配 + caption）
- [x] `generator._create_slide` 路由 `layout == "image"`；文件缺失降级文字页

### Phase 5 — 测试

- [x] `tests/unit/tools/test_ppt_image_assets.py`：根域校验（越界/不存在/坏扩展/超限/空 caption）、reconcile（未引用追加、转抄自愈、未命中丢弃）
- [x] `test_ppt_tool.py` 追加：images 传入 planner prompt、template/html/spec 拒绝、e2e python-pptx 兜底路径真实图片渲染出 picture 形状
- [x] ppt 全量回归：test_ppt_tool / test_ppt_input_normalizer / test_ppt_spec / test_ppt_renderer / test_ppt_template / test_ppt_html_*

### 三智能体流程（高风险：LLM 可控路径文件读取 + 租户边界）

- [x] 开发自测（主控者承担开发；16 + 7 新增用例、ppt 套件 98 passed）
- [x] 独立测试智能体：新增测试 + ppt 回归 + 安全用例复核（93 passed 采信；junction/穿越逃逸实测被拒；无 P0/P1，3 P2 已修）
- [x] 独立 CodeReview 智能体：路径校验、租户边界、异常分支、测试有效性（无 P0；1 P1 空文件校验 + 6 P2/nit 已修）
- [x] 主控者整合验证（import 终检通过；CR 复核修复增量通过，2026-09-23）
