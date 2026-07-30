# AI 视频生成工具 MVP - 开发计划

> 关联：[技术设计文档](../system/content-production/mvp-design.md)（零歧义技术方案，开发前必读）/ [PRD v0.4](../system/content-production/ai-video-production-prd.md)
>
> 本计划按设计文档的文件/接口逐任务分解，每个任务标注：产出文件、对接点（设计文档章节）、验收标准。开发顺序按依赖关系排列，可并行处已标注。

---

## 开发流程约定

遵循项目三智能体开发流程（[dev_workflow](../../.claude/rules/dev_workflow.md)）：每个 Phase 完成 → 独立测试 → CodeReview → 提交。非平凡改动走完整流程。

---

## Phase 0：环境与配置（前置，0.5天）

**目标**：验证外部依赖可用（spike 已完成大半）。

| 任务 | 产出 | 对接 | 验收 |
|------|------|------|------|
| 0.1 配置项 | `src/config/settings.py` 加 WanxConfig + env 加载 | 设计§4 | `settings.llm.wanx` 可读（复用QWEN_API_KEYS） |
| 0.2 万相 API（✅ spike 已完成） | 实测通过：`wan2.7-r2v-2026-06-12` + `reference_image` + `first_frame` + **base64传图** | 设计§5、§1.1 | 假睫毛+模特图均成功生成 |
| 0.3 人脸裁剪验证 | OpenCV haarcascade 检测人脸+裁剪，消除鬼影 | 设计§1.2 | 长图裁剪后比例正常，无鬼影 |
| 0.4 FFmpeg 验证 | 确认环境有 ffmpeg，drawtext 烧录文字成功 | 设计§11 | 输出带AI标识的 mp4 |

> **Spike 完整结论（2026-07-30）**：①模型 `wan2.7-r2v-2026-06-12`（r2v）；②media 用 `reference_image`（非first_frame/last_frame）；③只需1张图；④**base64 直传**（无需公网URL/OSS）；⑤**长图须人脸裁剪**否则鬼影；⑥必须用 httpx。Phase 0 已基本无阻塞。

---

## Phase 1：数据层 + 文件资产 + 预处理（2-3天）

**目标**：建表 + MediaRegistry + 人脸裁剪。可与 Phase 2 并行。

| 任务 | 产出文件 | 对接 | 验收 |
|------|---------|------|------|
| 1.1 建表 | `src/video_gen/__init__.py`、`src/video_gen/db.py`（init_video_gen_tables） | 设计§3 | 启动幂等建 gen_sessions/gen_cards |
| 1.2 建表触发 | `src/db/database.py` L1306 后加 try/except | 设计§3.3 | 应用启动自动建表 |
| 1.3 MediaRegistry | `src/video_gen/media.py`（register_local/download_and_register/**read_as_base64**/burn_ai_label） | 设计§7、§11 | 注册mp4→可下载带AI标识；read_as_base64 读图转base64 |
| 1.4 人脸裁剪 | `src/video_gen/preprocess.py`（detect_face_and_crop） | 设计§1.2 | 长图→裁剪到人脸区域正常比例；纯产品图→居中裁剪 |

**1.3 验收**：register_local 写 Redis 含六字段；read_as_base64 返回 `data:image/jpeg;base64,...`；burn_ai_label 输出右下角"AI 生成内容"。
**1.4 验收**：352×2048长图→裁剪到人脸区域（接近16:9）；无鬼影。

---

## Phase 2：万相 Provider（1-2天，可与 Phase 1 并行）

**目标**：WanxProvider 提交+查询可用。

| 任务 | 产出文件 | 对接 | 验收 |
|------|---------|------|------|
| 2.1 WanxProvider | `src/video_gen/wanx_provider.py`（WanxProvider + submit/poll + WanxSubmitResult/WanxPollResult） | 设计§5 | submit 返回 task_id；poll 对 SUCCEEDED 任务返回 video_url |

**2.1 验收细节**：
- submit endpoint URL = `https://{workspace_id}.{region}.maas.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis`。
- header 含 `X-DashScope-Async: enable`。
- poll endpoint = `https://{workspace_id}.{region}.maas.aliyuncs.com/api/v1/tasks/{task_id}`。
- prompt_extend=false，watermark=false。
- httpx 非2xx 抛异常带 response.text。

---

## Phase 3：Service 层 + 场景预设（2-3天，依赖 Phase 1+2）

**目标**：VideoGenService 完整业务逻辑可用。

| 任务 | 产出文件 | 对接 | 验收 |
|------|---------|------|------|
| 3.1 场景预设常量 | `src/video_gen/scenes.py`（SCENES 字典，2个场景） | 设计§6 | list_scenes 返回2个；get_scene 正确取值 |
| 3.2 VideoGenService | `src/video_gen/service.py`（create_session/get_session/list_sessions/set_card_kept/regenerate_card/poll_pending_cards） | 设计§8 | create_session 写库+提交万相；poll_pending_cards 更新状态+下载成片 |

**3.2 验收细节（create_session）**：
- 校验 scene_id；expanded_prompt 空则用模板填空。
- **人脸裁剪**：preprocess.detect_face_and_crop(product_image_fid) → 裁剪图。
- **base64**：media.read_as_base64(裁剪图file_id) → 传给万相（无需公网URL）。
- card_count 个不同 seed，各调 wanx.submit。
- 写 gen_sessions(generating) + gen_cards(PENDING)。

**3.2 验收细节（poll_pending_cards）**：
- 扫 provider_status in (PENDING,RUNNING) 的 cards。
- SUCCEEDED：download_and_register（含烧录标识）→ 更新 output_fid。
- FAILED/CANCELED：写 error_msg。

---

## Phase 4：API 层 + 路由注册（1-2天，依赖 Phase 3）

**目标**：REST API 可调。

| 任务 | 产出文件 | 对接 | 验收 |
|------|---------|------|------|
| 4.1 API 路由 | `src/api/video_gen.py`（7个端点） | 设计§9 | 7端点均可调，返回 {success,data,error,debug} |
| 4.2 路由注册 | `src/main.py` L1679 后 include_router | 设计§9.4 | /api/video-gen/* 可访问 |
| 4.3 轮询 job | `src/scheduler/manager.py` 注册 30s poll job | 设计§10 | background_runner 启动后每30s跑一次 poll |

**4.1 端点清单**：GET /scenes、POST /sessions、GET /sessions、GET /sessions/{id}、PATCH /cards/{id}/kept、POST /cards/{id}/regenerate、GET /cards/{id}/download-url。

---

## Phase 5：前端 - 社媒工作台 Tab 对接（3-4天，依赖 Phase 4）

**目标**：视频生成作为社媒工作台"内容创作"Tab，完成完整抽卡闭环。

| 任务 | 产出文件 | 对接 | 验收 |
|------|---------|------|------|
| 5.1 API 客户端 | `frontend/src/api/videoGen.ts` | 设计§12.1 | 7个方法封装，带 auth header |
| 5.2 视频生成子组件 | `frontend/src/components/social-media/VideoGeneration.vue` | 设计§12.2 | 三区（向导/抽卡结果/历史）完整可用 |
| 5.3 工作台Tab改造 | `frontend/src/components/social-media/SocialMediaWorkbench.vue` 加Tab容器 | 设计§12.3 | "内容创作"Tab 引入 VideoGeneration；现有功能不破坏 |

**5.2 验收细节**：
- 向导：场景下拉、产品图上传（POST /api/upload，1张）、**人脸裁剪预览（可调整）**、文案框、条数选择、prompt预览可微调、开始抽卡按钮。
- 抽卡结果：v-for cards，视频预览/生成中/失败三态；留用开关、重新生成、下载按钮。
- 前端轮询：generating 状态的 session 每5s 刷新。
- 历史：listSessions 列表，点击切换。

**5.3 验收细节**：
- 工作台加 Tab（账号与发布/内容创作/运营数据），默认不影响现有功能。
- **不新增路由**（复用 /social-media），不改 agentRoutes.ts。

> v0.5：视频生成不是独立页面，是社媒工作台 Tab 内子组件。合并到社媒智能体，独立入口不进chat。

---

## Phase 6：端到端联调 + 验收（1-2天）

**目标**：完整跑通 PRD §0.6 全部验收项。

| 任务 | 验收点 | PRD§0.6 |
|------|--------|---------|
| 6.1 完整抽卡流程 | 选场景→传1张产品图→人脸裁剪→填文案→生成2-4条→卡片展示 | ①②③④ |
| 6.2 防变形 | 首尾帧模式成片产品无明显变形 | ⑤ |
| 6.3 合规标识 | 成片右下角"AI 生成内容" | ⑥ |
| 6.4 留用+精修 | 标记留用、重新生成（换prompt/seed） | ⑦⑧ |
| 6.5 下载 | 成片可下载 | ⑨ |
| 6.6 量化 | 单视频<15分钟、时长5s（MVP先用万相默认5s，15-30s目标在Phase1优化）、变形率<10% | ⑩ |

---

## 工期估算

| Phase | 工期 | 说明 |
|-------|------|------|
| Phase 0 | 0.5天 | spike 已完成大半，补人脸裁剪验证 |
| Phase 1 | 2-3天 | 数据层+MediaRegistry(base64)+人脸裁剪 |
| Phase 2 | 1-2天 | 万相 provider（与P1并行） |
| Phase 3 | 2-3天 | Service 层 |
| Phase 4 | 1-2天 | API + 路由 + 轮询job |
| Phase 5 | 3-4天 | 前端（社媒Tab对接+VideoGeneration子组件） |
| Phase 6 | 1-2天 | 联调验收 |
| **合计** | **10-17天（约2-3.5周）** | 单人全职；并行可压缩 |

> 设计文档预估 4-6 周（含缓冲），本计划是紧凑估算。实际按团队规模调整。

---

## 风险与备选

| 风险 | 触发条件 | 备选 |
|------|---------|------|
| 万相变形 | ✅ spike 已验证 reference_image 防变形有效 | Phase 6 用真实美妆图最终量化 |
| 长图鬼影 | ✅ spike 已验证：人脸裁剪可消除 | preprocess 检测不到人脸时居中裁剪 |
| FFmpeg 不可用 | Phase 0.4 失败 | Dockerfile 加装 ffmpeg；或临时用万相 watermark=true |
| 万相 task 查询过期(24h) | 长时间未轮询 | poll 时捕获下载失败，标 FAILED 提示重新生成 |
| blingbling光影效果不足 | AI视频生成闪光效果天生难 | 后期叠加闪光特效层（FFmpeg），Phase 2+ 评估 |
| 社媒工作台Tab改造破坏现有功能 | Phase 5.3 | Tab用v-if隔离，现有功能独立Tab不受影响 |

---

## 提交规范

- 每个 Phase 完成后走三智能体流程（开发→测试→CodeReview）再提交。
- 提交前 `git fetch` + 解决冲突 + 提交后 `git push`（遵循 AGENTS.md）。
- 不自动提交，仅当明确说"提交代码"才提交。
