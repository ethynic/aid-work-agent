# 文生图/图生图能力缺失核查与生图模型选型调研

> 调研日期：2026-09-28。背景：项目当前疑似缺少文生图（text-to-image）/图生图（image-to-image）工具，本文先核查代码库现状，再调研当前（2026-09）可用的主流生图模型与 API，给出接入建议。本文只调研不开发。

## 结论（TL;DR）

1. **项目确实没有任何 AI 生图能力**。现有能力中：「通义万相」仅用于图生视频（`src/video_gen/wanx_provider.py`，r2v 模式必填参考图）；`x_to_image` 是文本/Markdown/HTML 渲染截图（非 AI 生成）；VL 只做图像理解。`src/llm/providers/` 只封装了 chat/stream_chat，无 images 端点。
2. **首选接入路径：复用现有智谱 + DashScope 双账号体系**，成本最低、无需新开账号：
   - 文生图：智谱 `cogview-4`（约 ¥0.06/张，同步接口，支持汉字生成）或百炼 `wan2.5-t2i`（约 ¥0.05/张）；
   - 图生图/图像编辑：百炼 `qwen-image-edit`（支持 1–3 张参考图、改字/增删物体/风格保持）。
3. **效果上限选项：字节 Seedream 4.0/5.0（火山方舟）**，中文语义理解与多图融合评测领先、文生图+图生图+编辑一体，但需新开火山方舟账号，作为二期或效果不满意时的升级项。
4. 国际模型（Google Nano Banana Pro / GPT Image 2 / FLUX 2）在指令遵循和 4K 质量上领先但单价贵（Nano Banana Pro 约为 GPT Image 的 10 倍）且有网络/合规门槛，与本项目"国内大模型提供商"定位不符，仅作技术参考。

## 一、代码库现状核查（事实）

全仓关键词搜索（text2image / img2img / cogview / dall-e / flux / seedream / 文生图 / 图生图 等）无命中。容易误判的三个近似项：

| 近似项 | 实际能力 | 位置 |
|---|---|---|
| wanx（通义万相） | **图生视频**（r2v，必填 reference_image），非文生图 | `src/video_gen/wanx_provider.py` |
| x_to_image | 文本/Markdown/HTML **渲染为长图**（截图式，非 AI 生成） | `src/services/x_to_image/service.py`、`src/tools/image/x_to_image_tool.py` |
| VL（GLM/Qwen-VL） | 图像**理解**（输入方向） | `src/wechat_mp/vision.py`、`src/knowledge/parsers/image_parser.py` 等 |

当前已对接的模型 API 形态：chat（4 家 LLM provider）、embeddings（dashscope）、VL（复用 chat 通道）、视频生成（wanx + minimax 双 provider，异步任务模式，`src/video_gen/factory.py`）、ASR、短信。**无任何 images generations/edits 端点**。

对开发有利的现状：
- `src/config/settings.py` 已有 `qwen.api_keys`（dashscope）与 zhipu 配置，两家生图 API 可直接复用现有 key 体系；
- `src/video_gen/` 已有 provider + factory + 异步任务轮询的成熟模式，万相文生图同样是「提交任务 → 轮询取结果」的异步协议，可参考。

## 二、生图模型调研（2026-09）

### 2.1 国内模型（重点，均为事实性信息 + 价格以各控制台实时为准）

| 模型 | 厂商/平台 | 能力 | 参考价格 | 备注 |
|---|---|---|---|---|
| cogview-4 / cogview-4-plus | 智谱 bigmodel.cn | 文生图（plus 支持 2K） | 约 ¥0.06/张 | 首个支持生成汉字的开源文生图模型；**MaaS 端点仅文生图，无图生图/编辑**；接口 `POST /api/paas/v4/images/generations`，与现有智谱 chat 同域名同鉴权 |
| GLM-Image（hd） | 智谱 | 文生图，最高 2048×2048 | 约 ¥0.12/张 | 智谱高规格生图 |
| wan2.5-t2i-preview / wan2.2-t2i-flash | 阿里百炼 DashScope | 文生图 | 约 ¥0.05/张（flash 更低） | 异步任务协议（与已对接的万相视频同族）；新用户有免费额度 |
| qwen-image-edit（2509 等） | 阿里百炼 | **图生图/图像编辑**：改字、增删物体、风格与身份保持 | 约 ¥0.2–0.3/张（待控制台核实） | 支持 1–3 张参考图输入；已开源且有硅基流动等第三方托管 |
| qwen-image-3.0-pro | 阿里百炼 | 文生图 + 图生图/编辑一体 | 待核实 | 编辑模式同样支持 1–3 参考图 |
| Seedream 4.0 / 5.0 Pro | 字节火山方舟 | 文生图 + 单/多图参考 + 编辑一体，秒级 2K | 5.0 Pro：≤236 万像素 ¥0.3/张，>236 万 ¥0.6/张 | 多篇评测中文语义理解与多图融合领先，效果对标 Nano Banana；需新开火山方舟账号 |
| 腾讯混元生图 / 文心一格 | 腾讯/百度 | 文生图 | — | 生态与评测存在感弱于上述三家，未深入 |

### 2.2 国际模型（参考，不推荐本项目管理接入）

- **Google Nano Banana Pro / Nano Banana 2**（Gemini 系）：指令遵循与 4K 质量公认领先，但单价最贵（对比数据：单次生成约 400 credits，为 GPT Image 的 10 倍）；
- **GPT Image 2**：按质量/尺寸分层计价，性价比与质量均衡；
- **FLUX 2**：质量与 Nano Banana 持平（第三方打分 4.5/5 vs 4.5/5），入门成本低，开源生态好，可自部署；
- Midjourney V7 / Ideogram / Recraft V4：各有所长（艺术风格/文字渲染/矢量）。

本项目定位为国内提供商（Qwen + ZhipuAI），国际模型存在网络连通、计费与合规门槛，仅在客户明确要求时考虑（FLUX 开源自部署是唯一现实路径）。

### 2.3 关键差异点（选型判断依据）

1. **智谱生图便宜但只有文生图**：如果需要图生图/编辑，智谱 MaaS 目前不提供，这是它与百炼的最大差距。
2. **百炼是"全家桶"**：文生图（wan2.5）+ 图像编辑（qwen-image-edit）+ 已有视频/嵌入/LLM 账号，一个 key 全覆盖，异步协议与 `src/video_gen/wanx_provider.py` 高度同构。
3. **Seedream 效果最强**（推断，基于多篇第三方评测一致性）：尤其中文提示词、多图参考融合、电商场景；代价是新账号 + 价格约为万相的 6–12 倍。
4. 汉字/中文文字渲染需求（海报、公众号配图）：cogview-4 与 Qwen-Image 系列均有专门优化，Seedream 同样强。

## 三、接入建议（建议，未开发）

- **一期（最小成本）**：新建 `src/image_gen/` 模块（或并入 video_gen 同级的生成类模块），按 `src/video_gen/` 的 provider + factory 模式实现两个 provider：
  - `zhipu CogView`：同步 HTTP，文生图，复用 zhipu 现有 base_url/key 配置；
  - `dashscope wan/qwen-image-edit`：异步任务模式，文生图 + 图生图，复用 qwen 现有 key 配置。
  - Agent 工具层新增 `generate_image`（text2image）与 `edit_image`（img2image）工具注册到 `src/tools/`，与 XToImageTool 并列。
- **二期（效果升级）**：视一期使用反馈引入火山方舟 Seedream provider。
- 计费登记：新工具需同步接入 LLM 调用计费体系（参照 `plans/llm-billing-audit-20260912.md` 的计费缺口规范，生图按张计费）。

## 参考来源

- [2026 年 AI 生图模型价格对比（boxr.tools）](https://boxr.tools)
- [智谱开放平台文档](https://docs.bigmodel.cn)
- [火山引擎开发者社区：Seedream 4.0 玩法与速度](https://developer.volcengine.com)
- [Best AI Image Generation Models in 2026 – Modelize](https://www.modelize.studio/blog/best-ai-image-generation-models-2026)
- [Nano Banana Pro vs GPT Image 2 vs Flux 2 Flash – MindStudio](https://www.mindstudio.ai/blog/artlist-studio-nano-banana-gpt-image-2-flux-2-flash-comparison)
- 阿里云百炼控制台模型价格页（help.aliyun.com / docs.bailian.console.aliyun.com）
