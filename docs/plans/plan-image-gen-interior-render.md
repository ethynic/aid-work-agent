# 产品效果图助手（interior-render）开发计划

> 2026-11-06 开发完成；本文档保留实现要点与真机冒烟定版清单。索引入口见 docs/ideas.md 条目 20261106-1310。

## 背景

用户提供了第三方（千问办公/workbuddy）技能包 `ext_package/qwen-interior-render/`（SKILL.md v1.2.0 + scripts/sample_color.py 取色脚本），功能：户型图 + 产品图 → 真实感室内效果图。基于它创建新子智能体「产品效果图助手」，主要使用场景在 **wecom_kf 渠道**：微信客户发户型图 → 智能体结合知识库产品图渲染效果图 → 回发微信。

**用户裁决**：① 通用工具形态接入；② 按张计费；③ 模型可配置，默认 DashScope `qwen-image-edit`，真机冒烟定版。（2026-11-06 定版：`qwen-image-3.0-pro`，1K 输出 0.25 元/张，wire 形态按 ext/qwen-image API文档.md 适配）

## 已实现内容

| 模块 | 文件 | 要点 |
|---|---|---|
| 服务层 | `src/services/image_gen/`（base/dashscope_provider/service/factory 内联） | 双 wire 形态（async_task 默认 + sync fallback）config `mode` 切换；提交 → 轮询 → 下载 → `ImageRegistry.register(move=True)`；api_key 回退 qwen keys |
| 配置 | `configs/config.yaml` image_gen 块 + `src/config/settings.py` | `dashscope.{api_key, model, mode, base_url, default_size}`、`vl.{model, allowed_models}`、`billing.image_usage_factor: 100`；env 覆盖 IMAGE_GEN_MODEL / IMAGE_GEN_MODE / IMAGE_VL_MODEL |
| 计费 | `src/services/billing.py` / `src/services/session_record.py` / 种子 SQL | `calculate_image_credit_cost[_with_breakdown]`（读 token_cost_prices.price_per_call，零 DDL）；`add_image_usage` + save() 合入 credit_cost；`db_update.yaml` 2026-11-06 块（qwen-image-3.0-pro 0.25 元/张，1K 档单价已按百炼控制台核实）+ init-postgres.sql 同步 |
| 工具 | `src/tools/image/image_render_tool.py` / `image_read_tool.py` | catalog 自动发现；顶层 `images` 键自动发 SSE 图片事件；image_read 返回 `file_path` 桥接 skill_execute 取色脚本；VL token 手动记账；失败 sanitize_error 脱敏 |
| 技能 | `src/skills/interior-render-1.0.0/` | 上游方法论全量移植（Phase 0-5、模板 A/B、关键纪律），工具映射改写（qwenwork_* → image_read/image_render/skill_execute）；sample_color.py 零改动 |
| 子智能体 | `subagents/interior-render/SUBAGENT.md` | tools.allowed: [image_render, image_read, skill_execute, cp]；skills.allowed: [interior-render]；租户开通走管理后台 subagent_definitions |
| 渠道 | `src/saas/api/channel_routes.py` / `src/channels/wecom_kf/message.py` | 智能体期过滤器放行 image（video/file 仍过滤）；`[图片消息]` 输入增强；KF_FILTER_HINT_MESSAGE 文案更新；`_PROCESSABLE_KF_MESSAGE_TYPES` 注释同步 |

## 验证状态

- 单测 115 个全绿：image 计费（11）+ dashscope provider（17）+ 工具两个（32）+ 路径沙箱 guard（5）+ 技能移植/取色脚本/子智能体（13）+ wecom_kf 消息回归（41）
- 既有回归：credit_billing + session_record_manager 81 个全绿
- 容器内 import 验证：工具零副作用导入（拉起 18 个 src 模块，不拉起 agent）、Catalog 发现、SkillRegistry/SUBAGENT.md 加载
- CodeReview（2026-11-06）：无 P0；P1-1 路径沙箱已修（`src/tools/image/_path_guard.py`：本地路径限当前租户目录 + 项目根内租户存储区之外 + tmp，跨租户拒绝）、P2 已修（轮询 monotonic 计时、独立落账 to_thread + breakdown 单价快照、测试断言补实）；P1-2 2MB 发图门槛转入冒烟清单 E2E 验证

## 真机冒烟定版清单（决策点）

| 项 | 内容 | 不通时的处置 |
|---|---|---|
| S1 | ~~端点形态验证~~ 已由 API 文档定版（qwen-image-3.0 统一异步端点 image-generation/generation + messages body，sync 走 multimodal-generation/generation）；真机仅验证连通性 | 失败查 dashscope_provider body 组装 |
| S2 | 参考图 data URL 直传与顺序（[产品图, 户型图]） | 失败查 dashscope_provider body 组装 |
| S3 | 结果 url 下载与 ImageRegistry 注册 | 查 _download_to_temp |
| S4 | ~~单价核实~~ 已定版：qwen-image-3.0-pro 1K 输出 0.25 元/张（输入 0.02 元/张忽略不计；2K 档 0.5 元/张，默认 1280*960 属 1K 档）；种子行已同步 | 运营改 DB 调价 |
| S5 | qwen-vl-plus 读图质量（户型结构判读）；token_cost_prices 已有 LLM 定价行（init-postgres.sql:320），Runner 结算口径无需补 | config `image_gen.vl.model` / `allowed_models` 换模型 |
| S6 | **生成图 >2MB 发微信验证**（CodeReview P1-2）：`_send_image_ref_as_image` 对 >2MB 跳过发送且 images 通道无 files 兜底，1280*960 写实 PNG 极易超限，出图成功计费后客户可能收不到图 | 不通则发送侧压缩/降级（如 >2MB 转 JPEG 压质）或 images 通道补 files 兜底 |
| E2E | wecom_kf 端到端：客户发户型图 → 判读 → 知识库产品图（shared_ranges 授权）→ 取色 → 出图 → 择优 → 微信收图 + usage_breakdown.image 计费核对 | 分段排查 |

**产品口径待确认（CodeReview P2-6）**：`KF_FILTER_HINT_MESSAGE`（「请直接发送图片」）对所有 wecom_kf 租户全局生效，非效果图租户客户发文件也会收到该提示。如需按租户/子智能体区分文案，后续改造。

## 关键参考

- `src/video_gen/wanx_provider.py` — provider 仿写模板
- `src/core/image_asset.py` — ImageRegistry/ImageRef 契约
- `.claude/rules/billing_audit.md` §3 — 计费自检（本次已按五条件核对：工具内统一计费 + 无 record 兜底独立落库）
- `.claude/rules/skill_integration.md` — 技能整合流程
