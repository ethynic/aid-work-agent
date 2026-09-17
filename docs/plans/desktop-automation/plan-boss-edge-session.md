# BOSS 端侧会话任务接入开发计划（B0a–B5）

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| B1.0 | 微信现状特征测试基线 | ✅ 完成（2026-09-17） | 新增 `tests/unit/session_tasks/test_characterization_weixin.py`（25 用例，含 superseded 200 判别/evidence-invalid 归一化/spec 错误三类快照）与 `clients/agent-tool-runtime/tests/sessionMetaV1Characterization.test.ts`（4 用例）；三智能体流程完成：独立测试隔离库 25/25、云端回归 264/268（4 失败均为预先存在/环境：2 DB 时钟偏差、2 `_mark_link` helper 缺 phase 既有断链，与本任务无关已论证）、Runtime 177/177、冒烟 15/15；CR 零 P0/P1（4 条 P2/nit 仅记录），独立重跑全绿。零生产代码改动 |
| B1.1 | ScenarioDescriptor 引入（微信 gate=None 复刻） | ✅ 完成（2026-09-17） | 主体实现零行为变化（常量逐字/原函数直引/no-op/调用点未切换）。codex P1（register_scenario 覆盖写下失败不恢复旧稳定注册、未预检三 key 一致）已按审核意见修复：写入前校验 descriptor/adapter/hooks 三 key 一致（不一致 ScenarioDescriptorError 零写入）、模块级 RLock 串行全程（与 registration._LOCK 不同粒度、获取顺序唯一 registration→本锁，无嵌套死锁）、写前快照旧对象、失败逆序恢复（旧值按对象身份恢复/无旧值删除新增）、保留原异常且回滚失败 logger.error 显式记录、docstring 如实声明恢复调用前状态（非"回滚到全空"）。新增 `src/weixin_conversation/descriptor.py`（常量复刻+gate=None+settle no-op+绑定查询面）、`src/session_tasks/scenario_descriptor.py`、`tests/unit/session_tasks/test_scenario_descriptor.py` 29 用例（含五类定向 + _WeixinBindingResolver 行为锁定 5 用例真实 DB，与现有查询行为/字段语义一致）；registration.py 改经描述器注册（三处一致、幂等存活复核含描述器、reset 三清）。内部复审三重通过：测试智能体逐点核查回滚语义与三张覆盖写表行为严格一致、受影响回归失败集合与基线零偏移；CR 复审 codex 六条要求全 PASS、无新引入缺陷；主控终检 54/54（隔离库）+ imports ok + RLock 确认。ScenarioAdapter 协议补 settle/validate_submission_evidence 声明按 codex 建议本轮不加（runtime_checkable 协议加成员会改变既有 fake 适配器 isinstance 判定面，B1.2 随调用点一并处理）。环境事实修正：隔离库不避开时钟类失败（同 PG 服务器同样偏快），B1.2 放行口径不以"隔离库绿"当时钟类修复证据。codex 二审通过（54/54 复跑确认；4 条非阻断建议中 2 条文档类已顺带修正，2 条登记为 B1.2 顺带项：register 演进带写入后动作时异常分支需恢复本表、恢复动作改每表独立 best-effort） |
| B1.2 | 九处泛化收尾+门禁/阻断/控制请求+五路并发测试 | 📋 待开发 | — |
| B1.3 | BOSS 描述器注册（默认关闭） | 📋 待开发 | — |
| B1.4 | B1 出口验收（微信零回归/默认关闭/证据归档） | 📋 待开发 | — |
| B2 | boss_conversation 场景包（fake） | 📋 待开发 | — |
| B3 | Runtime+Provider 接线 | 📋 待开发 | — |
| B4 | 工作台与通知投影 | 📋 待开发 | — |
| B5 | 真机闭环与灰度 | 📋 待开发 | — |
| B0a/B0b | P0′ 真机门禁验证 | 📋 待开发 | 等待用户授权测试账号/候选人（设计 §10.1），不阻塞 B1 |

权威设计：[boss.chat_reply.v1 端侧接入设计 V1.8](../../design/desktop-automation/boss-edge-session-design.md)（下称"设计"）。本计划是其执行拆分，不改变设计的任何冻结契约；实现与设计冲突时以设计为准并回填本计划。

## 1. 执行指令

- 开发前通读设计全文与本计划，再读 AGENTS.md 与 `.claude/rules/`（backend_dev/testing/dev_workflow 必读）。每阶段按 dev-workflow 三智能体流程：开发自测 → 独立测试智能体 → 独立 CodeReview 智能体 → 主控修复整合；不可用时明确缺口，不伪称完成。
- **零回归是 B1 的硬门禁**：B1.0 特征测试基线固化前不动任何通用代码；B1.1–B1.3 每步以"特征测试全绿 + 微信全量回归与基线一致"为放行条件。
- 禁止：自动提交/推送；capability/开关默认开启；把 fake 通过写成真机通过；为赶进度放宽 evidence/回执/锁序语义；混入无关重构。
- 每阶段完成后更新本进度表，再同步 `docs/ideas.md`（20260908-1432）状态。

## 2. 依赖与顺序

| 阶段 | 依赖 | 外部条件 |
|---|---|---|
| B1.0→B1.4 | 串行 | 无（立即可启动） |
| B2 | B1.4 | 无 |
| B3 | B2 + B0a 门禁 + B0b 下限冻结 | B0 需授权测试账号/候选人（当前等待授权） |
| B4 | B3 | 通知渠道授权（设计 §10.3，可后置） |
| B5 | B0 全门禁 + B1–B4 + 独立测试/CR | 授权范围冻结 |

微信 C5 收尾与本计划并行，共享代码（session_tasks/local_tools）的改动必须协调时段，不覆盖对方工作区改动。

## 3. B1.0 微信现状特征测试（不改任何行为）

文件所有权（新增）：`tests/unit/session_tasks/test_characterization_weixin.py`（云端）、`clients/agent-tool-runtime/tests/`（Runtime 测试源码；禁止直接编辑 `dist/tests/` 构建产物）。锁定清单（设计 §9.1 B1.0）：

1. 缺省 scenario_key=weixin.conversation.v1 的 create/PATCH/claim/renew 全链；
2. claim/restart meta 恢复（AssignmentMeta v1 回放语义）；
3. submitted 回执接纳全链：`operation_result` submitted 分支 + `validate_submission_evidence`（`weixin-submission:<request_id>:1` 精确匹配）+ receipt_policy 三处硬编码现状；
4. 旧 verified 回执（evidence_ref 登记/ON CONFLICT 仲裁）；
5. **evidence-invalid 现状**：归一化 `unknown/unknown` + `evidence_invalid` 审计 + attempt 保留原始上报 + ACK（`operation_result.py:390-415`）；
6. 人工 self 判断、能力缺失拒绝分配、旧任务日志恢复、共享桌面锁、unknown 不重发；
7. 现有 API 路径与前端路由可用性（imports/路由冒烟）；
8. spec 非法错误三类（多余字段/缺字段/类型错误）的 HTTP 状态码与错误字段路径快照；
9. prepare-send 无门禁写操作、不额外锁场景 binding（gate=None 前提锁定）；
10. prepare-send 既有 200 判别语义：prepared / `{"invocation_id":null,"decision_status":"superseded"}`（`decisions.py:1712`）/ 409 错误路径。

放行：全绿基线固化，记录测试命令与数量到本表完成记录。

## 4. B1.1 ScenarioDescriptor 引入

- 新增 `src/session_tasks/scenario_descriptor.py`：描述器协议（设计 §4.1，gate 可选）+ `register_scenario` 原子注册（描述器/TrustedAdapterRegistry/决策钩子三处同事务语义——进程内注册顺序上全有或全无）。
- 微信描述器 `src/weixin_conversation/descriptor.py`：复刻全部旧常量（能力、operation descriptor、receipt_policy 双命名空间、binding_resolver、label resolver、spec_validator=原 `validate_task_spec`、**gate=None**、settle no-op）。
- 既有 `weixin_conversation/registration.py` 改为经描述器注册；通用层调用点暂仍读旧路径（B1.2 切换）。
- 放行：行为与锁面零变化，特征测试全绿。

## 5. B1.2 泛化收尾（风险最高阶段）

按设计 §4.2 九处清单逐项切换 + §4.3 envelope 调整 + §5.5 通用机制。文件所有权：`src/session_tasks/{constants,service,decisions,models,workbench,api,init_tables}.py`、`src/desktop_automation/adapters.py`、`src/local_tools/{permits,operation_result}.py`、`src/scheduler/manager.py`、`src/weixin_conversation/`（仅参数声明移入描述器）、`deploy/init-postgres.sql`、`deploy/db_update.yaml`、`tests/unit/session_tasks/test_migrations.py` 及本阶段定向/集成测试。只修改实际需要的文件，不因清单存在而机械触碰。

关键交付：

1. 九处去微信化（表见设计 §4.2），微信行为经特征测试逐项比对不变；
2. envelope `spec` 改 dict + 场景分派（PATCH 读任务行权威 scenario_key）；
3. `authorize_operation` 收调用方连接/cursor + 结构化 `AuthorizeDecision`（control_action/audit_code）；**微信 denied→rollback 保持，BOSS 拒绝副作用先提交再返回**（设计 §5.5.4 提交语义）；
4. `settle_operation_result` 钩子 + operation_result SAVEPOINT 结算/补建/异常升级（设计 §5.5.4 顺序 1–6）+ binding 定位（受信 business_ref 链路 + 锁后重验，§5.5.5）；
5. prepare-send Phase A 原子门禁（仅 gate 非空场景追加 binding 锁）+ 触发计数落库 + deferred/superseded/prepared 200 判别联合响应（`superseded` 沿既有 200 语义）；
6. `session_task_control_requests` 表 + 处理器（scheduler manager 挂载，5s tick，processing owner/租约，10 次重试→failed+告警，processing/failed 保持阻断）；**控制请求表只负责异步迁移，不是同步发送门禁**。通用层提供场景 binding guard 扩展，B1.2 用 fake 场景验证：prepare-send/write-authorize 均在 `binding FOR UPDATE` 后检查 `automation_blocked`，阻断检查与许可/门禁动作由同一 binding 行锁串行；B2 再接真实 BOSS binding 字段与描述器实现；
7. **五路真实 PostgreSQL 并发死锁与阻断穿透测试**（许可/回执/暂停/绑定失效/频控结算，含微信路径）：位于 `tests/integration/`，使用独立数据库连接并发执行，设置有限 `lock_timeout/statement_timeout`，断言无死锁、无超时、无 permit 穿透；mock cursor/串行单测只验证分支，不得代替本门禁；
8. 数据库变更按项目规范同步维护初始化、增量升级与迁移测试：`deploy/init-postgres.sql`、`deploy/db_update.yaml`（唯一且严格递增时间）、相应 `init_tables.py`、`tests/unit/session_tasks/test_migrations.py`；新增/修改系统核心表时同步 `docs/system/database_system_table.md`。

放行：微信后端全量 + Runtime + Provider 恢复/回执与基线一致；真实 PostgreSQL 五路并发测试通过；"微信 prepare-send 结果和锁面等价；微信普通 denied 仍 rollback 且不产生控制请求"两条显式断言通过。若当前环境无法运行真实 PostgreSQL 并发测试，B1.2 保持未完成并记录阻塞，不以 mock 结果替代。

## 6. B1.3 BOSS 描述器注册

- `src/boss_conversation/` 骨架：constants/config（boss_conversation 节点，enabled=false）/descriptor/空实现钩子；
- `configs/config.yaml` 新增节点（默认关闭）；scheduler 注册改"任一启用场景"；
- 放行：BOSS 关闭时微信全绿；开启时 fake 装载通过（完整逻辑 B2 交付）。

## 7. B1.4 B1 出口验收

不新增业务能力，仅对 B1.0–B1.3 最终代码状态做出口检查并归档证据：微信特征测试与受影响全量回归保持基线一致；真实 PostgreSQL 五路并发门禁通过；BOSS capability/config 默认关闭；微信 gate=None、prepare-send 锁面不扩大；普通微信 denied 仍 rollback；迁移在全新初始化与增量升级两条路径均可重复执行。测试命令、退出状态、用例数量、独立测试与 CR 结论写入本计划进度表。全部通过后才放行 B2。

## 8. B2 场景包（fake 端到端）

文件所有权（新增）：`src/boss_conversation/` 全部 + `tests/unit/boss_conversation/`（跨通用层契约测试仍放 `tests/unit/session_tasks/`）+ 表迁移（bindings 含触发计数/同步阻断列、script_versions、rate_slots、settlement_anomalies、control_requests 已在 B1.2、投影队列）。所有表/字段变更同步维护 `deploy/init-postgres.sql`、`deploy/db_update.yaml`、场景/系统 `init_tables.py` 与迁移测试；业务 `bs_` 表遵守 tenant_id/user_id/created_at 规范。

交付（设计 §5 全部）：绑定管理 API（含 owner-only unblock CAS）、话术版本表与发布冻结、BossTaskSpecPayload 分派校验、受限决策钩子与确定性渲染（§5.4 三级失败分流）、双闸门（gate 实现 + authorize 复判三分类）、effective_count/去重不变量定向测试、SAVEPOINT 补建仍升级异常测试、投影队列、fake provider 端到端（观察→决策→渲染→门禁→许可→发送→回执→结算→投影）。

必测（设计 §5.5.3/§5.5.4）：effective_count 加一时点、跨日原子重置、superseded 后旧 decision 不再触发、reserved_at 窗口口径、缺失 slot 补建仍写异常队列、控制请求 stale 不覆盖新阻断。

放行：fake 全链通过 + 独立测试/CR；capability 仍默认关闭。

## 9. B3 Runtime+Provider 接线

- Runtime：claim/meta v2（scenario_key+期望指纹）、指纹四字段比较、桥注册表+BossBridge、send_deferred 事件协议（单调时钟/回放/清除时机）、boss v2 manifest、`AIDWORK_BOSS_BRIDGE_KEY` 注入。
- Provider（boss-resume-assistant）：`boss_session_observe`（DOMSnapshot+指纹+sender_unknown→gap）、`boss_send_to_v2`（HMAC envelope+Win32+逐事件投递检查）、提交证据/生产 verifier（按 B0b 冻结结论）。
- 放行：隔离真机冒烟（只读先行）+ 独立测试/CR。

## 10. B4 工作台与通知投影

sessionTasks 组件接招聘后台路由；绑定管理页（含 unblock）；话术版本管理；handoff 通知类型（渠道授权后开启）；投影 job。复用 Base* 与语义 token，frontend-design skill 约束适用。

## 11. B5 真机闭环与灰度

单候选人连续验收（≥10 轮含突发/接管/重启/deferred 重试）、双指标复测（按 B0b 冻结下限）、旧 BOSS 全套回归、同桌面混用、发布/回滚手册（场景+租户限定）。真机报告落 `docs/research/boss-cli/`（实施时创建并登记 research_index）。

## 12. 验收对照

通用层 A1–A7/A9–A11 + 设计 §9.2 BOSS 附加项逐项列 PASS/FAIL/BLOCKED，不省略。capability 默认关闭；fake 通过不标真机通过；C5/微信锁定项不因本计划放宽。
