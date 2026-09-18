# BOSS 端侧会话任务接入开发计划（B0a–B5）

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| B1.0 | 微信现状特征测试基线 | ✅ 完成（2026-09-17） | 新增 `tests/unit/session_tasks/test_characterization_weixin.py`（25 用例，含 superseded 200 判别/evidence-invalid 归一化/spec 错误三类快照）与 `clients/agent-tool-runtime/tests/sessionMetaV1Characterization.test.ts`（4 用例）；三智能体流程完成：独立测试隔离库 25/25、云端回归 264/268（4 失败均为预先存在/环境：2 DB 时钟偏差、2 `_mark_link` helper 缺 phase 既有断链，与本任务无关已论证）、Runtime 177/177、冒烟 15/15；CR 零 P0/P1（4 条 P2/nit 仅记录），独立重跑全绿。零生产代码改动 |
| B1.1 | ScenarioDescriptor 引入（微信 gate=None 复刻） | ✅ 完成（2026-09-17） | 主体实现零行为变化（常量逐字/原函数直引/no-op/调用点未切换）。codex P1（register_scenario 覆盖写下失败不恢复旧稳定注册、未预检三 key 一致）已按审核意见修复：写入前校验 descriptor/adapter/hooks 三 key 一致（不一致 ScenarioDescriptorError 零写入）、模块级 RLock 串行全程（与 registration._LOCK 不同粒度、获取顺序唯一 registration→本锁，无嵌套死锁）、写前快照旧对象、失败逆序恢复（旧值按对象身份恢复/无旧值删除新增）、保留原异常且回滚失败 logger.error 显式记录、docstring 如实声明恢复调用前状态（非"回滚到全空"）。新增 `src/weixin_conversation/descriptor.py`（常量复刻+gate=None+settle no-op+绑定查询面）、`src/session_tasks/scenario_descriptor.py`、`tests/unit/session_tasks/test_scenario_descriptor.py` 29 用例（含五类定向 + _WeixinBindingResolver 行为锁定 5 用例真实 DB，与现有查询行为/字段语义一致）；registration.py 改经描述器注册（三处一致、幂等存活复核含描述器、reset 三清）。内部复审三重通过：测试智能体逐点核查回滚语义与三张覆盖写表行为严格一致、受影响回归失败集合与基线零偏移；CR 复审 codex 六条要求全 PASS、无新引入缺陷；主控终检 54/54（隔离库）+ imports ok + RLock 确认。ScenarioAdapter 协议补 settle/validate_submission_evidence 声明按 codex 建议本轮不加（runtime_checkable 协议加成员会改变既有 fake 适配器 isinstance 判定面，B1.2 随调用点一并处理）。环境事实修正：隔离库不避开时钟类失败（同 PG 服务器同样偏快），B1.2 放行口径不以"隔离库绿"当时钟类修复证据。codex 二审通过（54/54 复跑确认；4 条非阻断建议中 2 条文档类已顺带修正，2 条登记为 B1.2 顺带项：register 演进带写入后动作时异常分支需恢复本表、恢复动作改每表独立 best-effort） |
| B1.2 | 九处泛化收尾+门禁/阻断/控制请求+五路并发测试 | 🔧 进行中（codex 统一审核 12 阻断+6 非阻断已全部修复，待独立测试+CR 复审） | **12 阻断逐项**：①P0 operation-result 锁序重排为冻结矩阵 invocation→attempt(FOR UPDATE)→delivery(无条件锁+归属校验，缺失拒绝不ACK)→binding→rate slot，evidence 分支复用已锁 delivery，新增 unknown 路径锁序插桩断言与 delivery 缺失拒绝测试；②通用生命周期脱离微信开关：描述器新增 scenario_enabled，publish/create_draft/create_decision/claim/resume/workbench 全部按权威 task.scenario_key 分派（claim 按候选逐个判断），boss/weixin 描述器各自实现；③claim 响应新增 scenario_key（缺省微信），特征用例转新行为断言；④Phase A fail-closed：gate（纯计算）与 guard（锁/检查/落库）职责切分——guard.gate_transaction 接收 gate 并在锁内调用，只有显式 eligible 进 Phase B，空字典/未知 outcome/缺 effective_count/非法 deferred（非正整数 retry、非未来时间）一律 SEND_GATE_MALFORMED，guard/gate 同有同无否则 SEND_GATE_CONFIG_INVALID；⑤_mark_retry/failed 转换校验 owner+有效租约，rowcount=0→lease_lost（不加次数不告警）；⑥迁移 False→受租约保护的 retry 转换真实推进；⑦十次失败→failed+脱敏审计+/notifications 站内告警（notifications 白名单加 failed，告警落 expected_control_epoch 与迁移成功后 human_required 通知 unique 键不冲突，专项测试实证）+loguru 改 {}；⑧binding 定位实现无锁定位→锁→锁后重读核对（revalidation_mismatch→异常升级，升级阻断按任务行当前绑定，并发改绑测试实证）；⑨settle 结构化结果 normal/anomaly_committed（补建保留且升级，适配器协议与 fake 注入测试）；⑩bindings API/capabilities 按 scenario_key 路由 BindingResolver（未知场景 400 fail-closed，缺省微信兼容）；⑪决策 worker 与控制 worker 独立 try 域独立记录启动失败；⑫prepare-send 三分支统一 status=prepared|deferred。修复轮复审：测试智能体 12 项逐项 PASS（锁序插桩实证 delivery<binding、fail-open 以 execution link 为空实证根除、104 用例绿、五路双遍 5/5、da 175 全绿）；CR 复审 codex 12 项代码级全 PASS，自行补修 2 处残留（_finish 日志 %s、superseded 早期分支漏 status），独立重跑 99/99+5/5+39/39；主控终检 125/125（隔离库 615s）+五路 5/5+空库 2/2+Runtime 179/179。遗留 P2/nit：eligible isinstance(int) 未排 bool（B1.3 接 boss 前补）、isolated-db 包装器 FORCE DROP 清理报错（环境）、fake 测试表不 drop、C3 存量 18 处 loguru %s（非本阶段引入） 。三审修复轮：8 P1（唯一键纳 block_epoch 四处同步、binding 缺失/归属异常拒绝不 ACK+受信 task_row、settle 严格枚举 None/严格 anomaly_committed、resume 按任务场景分派、effective_count type-is-int ≥1 排 bool 负数、deferred 冻结字段强类型+server_now 比较+±2000ms 容差不依赖应用机钟、create-draft 场景关闭仍可存草稿、bindings 路由 callable 校验 400）+4 P2（改绑升级按 binding id 排序加锁、五路 result_b 必须成功+settled 查询、临时 schema 真缺表迁移测试、scheduler 独立失败域测试）全部落实；主控亲跑全套：定向六套件 125/125、五路 5/5、空库 2/2、Runtime 179/179、session_tasks 349/4（基线名单逐一同名）、da 175 全绿、tools 15/0、wm 243/1（文档类预先存在）；CR 复审 8P1+4P2 全 PASS 无必修，登记 P2-A（guard=None 场景 settle 异常升级面收窄，B2 接真实账本前决断）/P2-B（fake 跨日归一漏清 last_rate_decision_id，B2 实现输入）/P2-C（未知 settle 返回原文入审计，建议受控化） 。四审修复轮：P0-1 改绑交叉死锁——首锁前 SAVEPOINT binding_locate、重验不一致 ROLLBACK 释放旧锁、升级段按旧/当前 id 排序重锁并以 anomaly 快照二次确认、再变拒绝不 ACK，双事务真并发 A→B/B→A 测试（双向覆盖旧 ID 大小）；P1-2 aware datetime 双查+统一 UTC（双 naive/单侧 naive 参数化拒绝）；P1-3 guard=None settle 异常/非法返回→回滚主事务+SETTLEMENT_ESCALATION_UNAVAILABLE 不 ACK（outbox 重投），微信 no-op 不受影响；gate 契约迁移 V1.9 标记键形态（标记恰一/字段集精确/effective_count 原生 int≥1 排 bool/受控码 regex）；补齐5 审计只落类型名不落 repr、补齐6 SAVEPOINT 探针先写后回滚、补齐7 五路 slot 绑定 result_a 精确到行、补齐8 UUID 临时 schema 真旧四元表迁移、补齐9 真并发双 block（独立连接+barrier，epoch={1,2} 无丢失）、补齐10 scenario_key 漂移升级测试；邻域枚举自检：gate 16 畸形/datetime 5 异常/幂等键 3/定位 9 分支均有测试支撑（定位分支 1/2/3/8/9 无直接用例已登记）。验证（主控终态重跑）：定向七套件 131/131、五路 5/5、空库 2/2、Runtime 179/179、session_tasks 371/4 基线名单逐一同名（并行负载漂移的 2 个边缘用例复跑消失）、da 172/3、tools 15/0、wm 235/9。CR 复审：P0-1 与 3 P1 全 PASS（SAVEPOINT 释放行锁 PG 语义核实、锁序无反向边、V1.9 三处逐条一致无静默偏离）、补齐 6 项证明力成立、无新引入 P0/P1；P2/nit 登记：定位分支 1/2/3/8/9 测试缺口（B2 前补 2-3 廉价用例）、互抄 tzinfo 双 naive 无专门用例（实现已覆盖）、受控码 regex $ 尾换行（建议 \Z）、死代码 2 处、交叉改绑测试未断言至少一方 acked 。五审修复轮：测试加第二 barrier——crossed_lock 首次调用先 real_guard_lock() 取旧锁再等第二 barrier（后续排序重锁不进 barrier），首锁前 SET LOCAL lock_timeout=3s（新实现 ROLLBACK 连同撤销对通过路径无影响）；顺带修正 UUID 命名/注释反向、受控码 re.match$ 改 fullmatch（decisions/operation_result 各一处+尾换行拒绝测试）、双 block 测试去 task FOR UPDATE 改普通读+读后会齐（真正竞争 binding 锁）。变异验证（主控亲手双向复现）：注释 ROLLBACK TO SAVEPOINT → 测试 17.6s 稳定 DeadlockDetected（AB-BA 环实证，无 60s 悬挂）；恢复后 cr4 9/9、源码 0 变异残留。回归：cr4 9/9、cr3+b12 44/44、五路 5/5、特征 25/25 |superseded（旧 Runtime 忽略新增字段）。**6 非阻断**：a 幂等重 prepare 仍锁 binding 查 automation_blocked（跳过计数不跳过阻断）；b claim 能力不匹配跳过候选继续找兼容场景（保留单候选 409 语义）；c 五路并发逐线程精确结果断言（允许码矩阵+result_a 必须成功+attempt/invocation 终态覆盖 unknown）+3 处脚本缺陷修复（_fresh_conn 上下文/storm_env 补 service 门控/weixin 风暴绑定改名称上下文形态+租约续期）；d 统一迁移补 task_transition 脱敏审计；e permits 拒绝副作用只落受控 control_action/audit_code（不持久化自由文本 reason）；f 新增 db_update.yaml 最新块从旧 schema 执行且可重复执行的迁移测试（datetime 严格递增断言）。验证：隔离库 cr_fixes(21)+b12_generalization(24)+scenario_descriptor(28)+migrations(5) = 79/79；特征基线 25/25（隔离库）；五路并发 5/5（真实 PG，加强断言后）；boss 骨架 21/21（协议新增成员兼容）；回归 session_tasks 362/366 + desktop_automation 175/175（失败集合 ⊆ 既有 DB 时钟偏差环境集，零新增）。**三审 8 P1+4 P2 已全部修复**：P1-1 幂等键纳入 expected_block_epoch（唯一索引 uq_session_task_control_requests_idem，四处 schema/doc 同步，注意 PG 自动约束名 63 字符截断——DROP 用截断名；同代双代请求 stale/applied 并发测试）；P1-2 定位失败分类处置：task/binding 缺失与 guard 归属不符行→BINDING_LOCATION_FAILED 拒绝不 ACK（无受信升级对象），锁后重验增加 invocation 冻结 scenario_key 核对（改绑窗口仍走 anomaly 升级）；P1-3 settle 返回值白名单（None 或严格 anomaly_committed 形态），未知值 ROLLBACK SAVEPOINT 按结算异常升级（三种垃圾返回参数化测试：slot 无行+控制请求+阻断）；P1-4 resume_task 删除缺省微信 capabilities 前置门控（恢复门控按任务行场景分派）；P1-5 effective_count type() is int 且 ≥1（bool/负数/0 参数化 fail-closed）；P1-6 deferred 全冻结字段强类型（server_now/deferred_reason/response_revision）+时间一致性只依赖 DB 侧（until>now 且 retry 与毫秒差 ±2000ms 容差），不用应用机墙钟；P1-7 create-draft 移除场景关闭 403（草稿仍可存，关闭时建草稿+发布 403 测试），旧 create-draft-403 断言作废重写；P1-8 绑定 API resolver 成员 callable 校验（受控 400）+boss 骨架 resolver 显式抛 ScenarioDescriptorError；P2-1 改绑异常升级按 binding id 排序多行锁协议（消除 A→B/B→A 反向边）；P2-2 五路并发 result_a/result_b 必须成功+五线程结果记录全+fake 路径 rate-slot settled 断言；P2-3 迁移测试改临时 schema 真'缺表旧库'验证（建齐+重复执行+约束/索引断言）；P2-4 scheduler 失败域测试（决策 job 注入失败，控制 job 仍注册）。三审后复跑：隔离库 cr3(21)+cr_fixes(23)+b12(24)+descriptor(28)+migrations(3) = 99/99（含 P1-1 双代请求/定位拒绝/settle 未知值/strict gate/草稿语义/绑定 API/scheduler 域新用例）；特征 25/25（隔离库）；五路并发 5/5（加强断言后）；回归 session_tasks+desktop_automation+boss 骨架 557/561（4 失败 ⊆ 既有 DB 时钟偏差环境集，零新增）。顺带修复：共享库遗留旧 4 列唯一约束按 PG 63 字符截断名清理（三处 DDL DROP 名同步修正）。**四审 1 P0+3 P1+6 补齐已全部修复（实现与设计 V1.9 对齐）**：P0-1 定位协议重写：首锁旧 binding 前建 SAVEPOINT binding_locate，重验不一致 ROLLBACK 释放旧锁（PG 回滚释放 SAVEPOINT 后获取的行锁），升级段按旧/当前 binding id 排序重新加锁（同全序无反向边）→ 二次读取 task 以 anomaly 时刻快照为基准确认 binding/scenario 未再变化 → 再变 BINDING_LOCATION_FAILED 拒绝不 ACK（无界重试禁止）；新增交叉改绑 A→B/B→A 双事务真并发测试（显式 id 覆盖旧 ID 大于/小于新 ID 两方向，断言无死锁/无悬挂/结果协议内/acked 任务有控制请求）；P1-2/P1-4 gate 严格判别联合迁移 V1.9 词汇：标记键恰好一个、字段集精确匹配、terminal reason/settle reason 受控码 ^[a-z][a-z0-9_]{0,63}$、datetime 必须 aware（tzinfo+utcoffset 双查，互抄 tzinfo 的双 naive/单侧 naive 均拒绝）、统一转 UTC 比较、禁止应用机墙钟；P1-3 guard 缺失时 settle 异常/非法返回 → SETTLEMENT_ESCALATION_UNAVAILABLE 回滚主事务不 ACK（outbox 重投），微信 no-op None 不受影响；补齐5 审计只落受控码与返回类型名（settlement_unknown_result:<TypeName>，不落原始 repr），settle reason 受控码校验不合规按未知值处理；补齐6 SAVEPOINT 测试改探针式（先 INSERT 探针 slot 再返回非法值，断言探针已回滚+升级三件套）；补齐7 五路 rate-slot 断言绑定 result_a（invocation→attempt.delivery_id→slot 精确到行=settled）；补齐8 迁移测试改真上一版 schema（临时 schema 名 UUID 防并行互删；预置旧四元 UNIQUE 表→执行最新块两遍→断言旧约束删除/五元索引在位/同 control+reason 不同 block epoch 两行共存）；补齐9 真并发双 block+控制请求测试（独立连接+barrier，epoch={1,2} 无丢失更新，两行请求共存）；补齐10 scenario_key 定位窗口漂移定向测试（异常升级+控制请求）。四审后复跑：隔离库 cr4(21)+cr3(23)+cr_fixes(23)+b12(24)+descriptor(28)+migrations(3) = 106/106；特征 25/25（隔离库）；五路并发 5/5（补齐7 精确断言后）；回归 561/568（7 失败均 ⊆ 既有 DB 时钟偏差环境集：c3_decisions/c3_gate/name_contexts×2/permits ExpireSweep×3，零新增）。过程教训（测试缺陷 4 处即修）：cr3 递归自引用 monkeypatch、del 恢复连原方法删除、deferred 用例漏预置 last_reserved_at、交叉改绑测试模块级补丁未还原污染后续定位用例（已全部改 monkeypatch 自动还原）。
| B1.3 | BOSS 描述器注册（默认关闭） | ✅ 完成（2026-09-18，待 B1 统一 codex 审核） | 新增 src/boss_conversation/ 骨架（constants/config 热读门控默认 false fail-closed/descriptor/registration 双门控）+config.yaml boss_conversation 节点（enabled:false）+main.py 生命周期与 scheduler 组合根接线（组合根由微信短路改为逐场景独立 try 不短路，双场景同启各自注册、异常隔离，调用序测试实证）；描述器骨架 fail-closed 完备（spec 拒绝一切经 envelope 转 400、receipt_arguments 空参必拒 submitted、authorize/双证据 NotImplementedError、gate/binding_guard=None）；顺带项 _finish 租约归属守卫（owner+lease DB 侧双条件，0 行 rollback，lease_lost 重领）。验证：定向 99/99、session_tasks 334/10 与 weixin_marketing 235/9 均为基线集合零偏移、tools 15/0、启动安全 4 项 OK（boss 默认实调 False）；CR 无 P0/P1，_mark_retry 无租约守卫残留如实登记 B2 收紧（测试报告附非阻断观察 2 条）。75/75 复跑确认 |
| B1.4 | B1 出口验收 | 🔧 进行中（四审修复后重新取证完成，随 B1.2 待 codex 五审） | 出口证据（主控亲跑，最终代码状态）：①定向套件 103/103（特征 25+descriptor 29+b12 24+骨架 21+迁移 4，隔离库 224s）；②五路并发 5/5（真实 PG/独立连接/lock_timeout=5s，含阻断穿透与微信零控制请求断言）；③空库引导 2/2 + 迁移增量 4/4（全新初始化与增量升级双路径可重复）；④Runtime 179/179；⑤微信全量回归与基线一致（引用 B1.3 测试智能体同代码状态结果：session_tasks 334/10、weixin_marketing 235/9、tools 15/0，基线集合逐条同名）；⑥BOSS capability/config 默认关闭（config.yaml enabled:false+骨架用例零打桩实调 ensure_registered=False）；⑦微信 gate=None/prepare-send 锁面不扩大/普通 denied 仍 rollback（b12 显式断言两条+特征基线）。测试命令与结论已归档本表各行；B1 出口放行 B2 。修复后重取证：定向六套件 125/125（隔离库）、五路并发 5/5、空库引导 2/2、Runtime 179/179；微信全量回归引用同代码状态测试复审结果（session_tasks 266/4 基线精确一致、da 175 全绿、tools 15/0）；默认关闭与两条显式断言维持通过 |
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
