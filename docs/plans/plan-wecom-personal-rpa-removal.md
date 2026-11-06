# wecom-personal-rpa 全量删除计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 0 | 计划登记（本文档 + ideas.md 条目） | ✅ 完成（2026-11-05） | 本文档创建，ideas.md 渠道集成分区新增条目 20261105-2300 |
| Phase 1 | 后端删除（渠道包/路由/枚举/引用修整） | ✅ 完成（2026-11-05） | 删 src/channels/wecom_personal_rpa/ 整目录、两个 saas API 文件、31 个单测文件、5 个集成测试文件；main.py/background_runner/channel_config/channel_config_db（含死方法 claim_client_if_unowned）/channel_factory/枚举与白名单/import 改指向 core.secret_crypto/.env.example 全部处理 |
| Phase 2 | 数据库脚本（init-postgres 清理 + cleanup SQL） | ✅ 完成（2026-11-05） | 两份 init-postgres.sql 删 6 表 DDL + 9 索引 + 单例唯一索引；新增 deploy/cleanup-wecom-personal-rpa.sql（幂等，待用户真机执行）；db_update.sql:555 注释改指 core secret_crypto |
| Phase 3 | 前端删除（页面/路由/枚举/测试） | ✅ 完成（2026-11-05） | 删 WecomPersonalRpaManager.vue、RpaBindingPanel.vue、wecomPersonalRpa.ts、useRpaPauseResume.ts 及测试；路由/菜单/ChannelConfig rpa 段/generateChannelKeypair/enums/MSW mock 清理 |
| Phase 4 | 客户端删除（clients/wecom-personal-rpa + .dockerignore） | ✅ 完成（2026-11-05） | C# 客户端整目录（含 publish/debug-out 等产物）删除；.dockerignore 注释更新；agent-desktop verify-package.mjs 过时禁含正则顺手清理 |
| Phase 5 | 文档与索引清理 + nginx conf | ✅ 完成（2026-11-05） | 11 份纯 rpa 文档删除；约 20 份文档提及改为「已随渠道清退删除」墓碑注记（wecom-cli-design、cache_usage §3.4、user-behavior-audit-log、background-runner、association-client-design 等）；nginx 5 份 conf rpa 路由段删除 |
| Phase 6 | 独立测试 + CodeReview + 主控终验 | ✅ 完成（2026-11-06） | 独立测试：受影响后端批次 155+966+19 用例通过（失败均经 HEAD worktree 对照确证为环境/预存），前端 typecheck/build/vitest 937 通过；CodeReview：无 P0/P1，5 个 P2 全部已修（2026-11-06 补修漏网 2 项：wecom-cli README 失实表述改墓碑、src/channels 与测试约 22 处简称残留按剩余渠道语义改写，受影响单测 111 用例复跑通过）；主控终验：import src.main 退出码 0、源码零残留（仅墓碑注记与豁免文件）、202 用例复核通过 |
| Phase 7 | 独立评审复核 | ✅ 完成（2026-11-06） | 逐文件核对 329 处改动：删除准确全面、无越界误删（豁免项 secret_crypto/RPA_SECRET_KEY/wecom-cli/wecom_conversation/desktop_automation/souyisou 完整）、全仓代码零残留、悬空引用为零、pytest 全量收集 13492 项无 import 错误；复跑受影响批次，22 个后端失败与 7 个前端 vitest 失败均经 HEAD worktree 失败集合比对与环境变量复现确证为预存/环境因素；P2-1/P2-2 修复复核通过（仅注释与文档改动，py_compile + import 检查通过） |

**遗留事项（待用户动作）**：① 真机执行 deploy/cleanup-wecom-personal-rpa.sql（清存量 6 表 + tenant_channel_configs rpa 配置行；执行前存量 rpa 行的 API 响应不再掩码，字段本身为 Fernet 密文无明文泄漏，建议尽早执行）；② 5 份 nginx conf 真机同步为非发版前置——旧配置仅多出指向已删路由的死规则，现网渠道转发行为零差异，可择机随下次 nginx 变更一并同步 reload；③ 代码已按用户指示提交（2026-11-06）。

## 背景

wecom-personal-rpa（企业微信个人账号 RPA，channel 类型 `wecom_personal_rpa`，DB 表前缀 `wecom_rpa_*`）已于 2026-09-29 废弃（commit 6d48163e），UI 执行职责由 runtime + wecom-cli 取代（见 [wecom-cli-design](../design/wecom/wecom-cli-design.md)）。本次按用户决策执行全量删除。

## 用户已确认的四项决策（2026-11-05）

1. **服务端整链路全删**：含会话存档 archive 入站栈、callback/WS 路由、出站 adapter/outbox。存量租户已全部迁移 runtime+wecom-cli，已部署 RPA 客户端全部下线。
2. **数据库只删代码 + 出清理脚本**：init-postgres.sql（含 nuodu）移除 DDL；另出 `deploy/cleanup-wecom-personal-rpa.sql`（DROP 6 表 + 删 tenant_channel_configs rpa 行 + DROP 唯一索引），**真机执行由用户决定，本次不执行**。
3. **前后端渠道枚举值一并删除**（接受历史 trace/行为日志/工作成果中该渠道标签退化为原始字符串）。
4. **文档全部提及清理**（含历史过程记录中的提及段落）。

## 硬性约束

- 全程不 git commit / push / stash / reset，改动留在工作区待用户验收。
- 不部署：不执行 *_update.sh、不重启容器、不在真机执行 SQL；nginx conf 只改仓库文件，真机重载留给用户。
- 不动替代方案：`clients/wecom-cli`、`src/wecom_conversation`、`src/services/agent_runner`（仅 `runtime/history.py` 白名单字符串随枚举清理）、`frontend/web/components/wecomConversation`。
- `src/core/secret_crypto.py` 渠道凭据加密公共原语**必须保留**；`.env.example` 的 `RPA_SECRET_KEY`（历史名兜底）**保留**。
- 勿误删无关 RPA：`clients/wechat-souyisou-rpa`、`src/desktop_automation`。

## 删除范围

### Phase 1 后端
- 整目录删 `src/channels/wecom_personal_rpa/`（含 archive/ 子包与 native_sdk）；删 `src/saas/api/wecom_personal_rpa_routes.py`、`src/saas/api/wecom_personal_rpa_admin.py`
- 删测试：`tests/integration/test_wecom_personal_rpa_{flow,media_upload,whitelist}.py`、`test_archive_callback_to_fetch_e2e.py`、`test_archive_sdk_fetch.py`、`tests/unit/test_wecom_personal_rpa_portal_binding.py`、`tests/unit/channels/wecom_personal_rpa/`（31 文件）
- 引用修整：`src/main.py`（router import + include_router）、`src/background_runner.py`（archive poller 启动）、`src/saas/api/channel_config.py`（rpa 分支 + generate-keypair 端点）、`src/saas/db/channel_config_db.py`（credential_codec rpa 分支，wechat_mp 分支保留）、`src/saas/services/channel_factory.py`（adapter 映射）
- 枚举/白名单字符串清理：`src/models/message.py`、`src/saas/models/enums.py`（两处枚举成员+显示名）、`src/core/cache_utils.py`（WECOM_RPA_SELF_ECHO_ESCAPE）、`src/api/monitor.py`、`src/core/agent.py`、`src/core/trace_semantics.py`、`src/tools/context.py`、`src/services/agent_runner/runtime/history.py`、`src/services/anti_abuse/{rules,scanner}.py`、`src/channels/ask_render.py`
- import 改指向 `src.core.secret_crypto`：`src/services/recruiting_notify_service.py`、`src/session_tasks/texts.py`、`src/weixin_conversation/name_contexts.py`、`src/weixin_marketing/event_sources.py`（3 处动态 import）
- 纯注释提及清理：`src/channels/base.py`、`src/db/models.py`、`src/memory/mid_term.py`、`src/core/compression_metrics.py`
- `.env.example`：删 `WECOM_RPA_AUDIT_HMAC_KEY`，保留 `RPA_SECRET_KEY`

### Phase 2 数据库脚本
- `deploy/init-postgres.sql`、`deploy/nuodu/init-postgres.sql`：删 6 张 `wecom_rpa_*` 表 DDL、`uq_tenant_channel_configs_wecom_personal_rpa` 唯一索引、注释提及
- 新建 `deploy/cleanup-wecom-personal-rpa.sql`（只创建，不执行）

### Phase 3 前端
- 删：`components/saas/WecomPersonalRpaManager.vue`、`RpaBindingPanel.vue`、`api/wecomPersonalRpa.ts`、`composables/useRpaPauseResume.ts` + 两个对应测试
- 路由/菜单：`agentRoutes.ts`、`portalRoutes.ts`、`MenuSidebar.vue`、`PortalLayout.vue`
- `ChannelConfig.vue` 删 rpa UI 段（~10 处）；`saasTenant.ts` 删 generateChannelKeypair
- `api/enums.ts` 删 WECOM_PERSONAL_RPA（UserSource/ChatRecordSourceType）、WecomRpa* 三个状态 Map、BehaviorResourceType.RPA_CLIENT
- 零散：`WorkOutcomes.vue`、`ContextCompressionManager.vue`、`ExternalCustomerService.vue`、`verify-desktop-build.mjs`
- 测试同步：`MenuSidebar.test.ts`、`routes.test.ts`、`mocks/handlers.ts`

### Phase 4 客户端
- 整目录删 `clients/wecom-personal-rpa/`；`.dockerignore` 清理对应条目

### Phase 5 文档与索引
- 删 11 份纯 rpa 文档（docs/system 6 + docs/plans 4 + docs/research 1）
- 清理约 15 份文档中的 rpa 提及段落（wecom-cli-design、cache_usage §3.4、user-behavior-audit-log-design、background-runner-design、agent-application-architecture-design、plan-agent-runner-service、plan-recruiting-error-containment、wecom_kf/message-merge、ask-user-interactive 设计+计划、upload-storage-compliance-audit、weixin-auto-chat-demo-agent2、ideas_finished 3 处、wecom-kf-context-loss-research、admin-element-plus/toast-migration 页面条目、backend_dev.md、database_dev.md、服务器部署现状.md）
- nginx conf 5 份删 rpa 路由段（agent/agent1/agent2/agent3/nuodu）

### Phase 6 验证
- 全仓 grep 零残留（豁免：本清理 SQL 与本文档）
- `python -c "import src.main"` 启动检查
- 后端受影响 pytest（含存量测试同步修改：test_background_runner、test_channel_config_db、test_verbose_feedback_contract、test_monitor_trace_display_semantics、test_trace_merge_semantics、test_behavior_log、test_notification_abuse_event、channels/test_make_send_response、channels/test_session_manager_persist、tools/test_context_channel_derive、test_mid_term/test_agent_integration_real、weixin_marketing/conftest、integration/test_channel_config_generate_keypair、integration/test_recruiting_notify）
- 前端 typecheck + build + 相关 vitest

### 有价值功能迁移清单

已交用户评估（见会话记录与调研结论），要点：出站限速与配额（令牌桶+连续失败自动暂停）、三层暂停/恢复、人工接管 Handoff、outbox 可靠投递模式、外部联系人 ID→姓名解析、渠道监控指标告警、客户端托管基础设施（服务监督/DPAPI/诊断包）、审计 HMAC、桌面健康检查增量、会话存档服务端拉取通道（产品决策项）、桌面自动化证伪经验。已被 wecom-cli 取代无需迁移：HMAC 签名/防重放、登录态三态+二维码、搜索定位/三种发送/100MB 签名 URL、超长分段、self-echo 方向守卫。
