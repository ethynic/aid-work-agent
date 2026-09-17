# BOSS 直聘端侧会话任务接入设计（boss.chat_reply.v1）

版本 V1.8 · 2026-09-17 · 设计交付（已吸收实现前复核意见；冻结渲染失败分流与 deferred HTTP 200 union；微信不注册频控 gate、不扩大 prepare-send binding 锁面；补齐控制请求处理节奏、人工解阻、binding 定位和 B0b 统计口径），待开发。启动开发前须另立开发计划文档并登记。

关联：[端侧会话任务设计](edge-session-task-design.md)（权威协议，本文是其第二个场景适配，**不改变其任何冻结契约，包括 §10/§11 的 execution_links 唯一约束与幂等物化语义**）、[中立执行底座](desktop-cli-automation-design.md)、[原 BOSS 场景设计 §11](../weixin/weixin-marketing-automation-design.md#11-第二场景boss-直聘聊天自动化待独立立项)、[原实施衔接 §12](../../plans/weixin/plan-weixin-marketing-automation.md#12-boss-聊天自动化实施衔接-待独立立项)、[BOSS 发送验证设计](../recruiting/boss-send-verification-design.md)、[BOSS CLI 权威设计](../recruiting/boss-resume-assistant-native-cdp-design.md)。登记入口：[ideas.md](../../ideas.md)（20260908-1432 条目）。

## 0. 对既有 BOSS 设计的继承与替代

原 §11/§12 写于端侧会话任务架构诞生之前。2026-09-11 架构演进后，观察/等待/就绪调度改由端侧 Runtime 执行器承接。本文按"业务约束保留、执行架构替换"处理：

| 原 §11/§12 内容 | 本文处理 |
|---|---|
| §11.1 业务范围、生命周期、转人工 | **保留**，生命周期直接映射端侧任务状态机 |
| §11.2 Observer 采样/水位/合批/频控 | 架构**替代**（引擎承接采样/合批/水位），频控参数与去重规则**保留**为场景约束（§5.5 双闸门） |
| §11.3 受限决策 select_script/fill_slots/handoff、敏感主题 | **保留**，落为场景钩子；动作词汇经归一化进通用层 |
| §11.4 发布预授权、话术按 ID/版本冻结 | **保留**；"版本"落为新建不可变话术版本表（§5.3） |
| §11.5 bs_boss_chat_* 全表族 | threads/messages/cursors/decisions/delivery_links **由通用 session_task_* 表替代**；保留候选人绑定表、频控账本与异常队列、沟通日志投影、handoff 通知扩展 |
| §11.6 P0′ 四项门禁 | **保留**，全部未验证；B0 拆两段（§8），写后证据双指标 |
| §12 里程碑 P0′–P5′ | 阶段划分**修订**为 B0a–B5（§9），P0′ 门禁不变 |

## 1. 决策表（V1 唯一选择）

| 决策 | V1 选择 |
|---|---|
| scenario_key | `boss.chat_reply.v1`；端到端显式携带：claim 响应新增（**live claim 必填，缺失拒绝 adopt**）、Runtime meta 持久化、桥按 key 选择；仅历史 schema 版本 recovery meta 回退微信（§6.1） |
| 目标载体 | BOSS 网页版（用户日常 Chrome，attach-only） |
| 场景包 | `src/boss_conversation/` |
| 账号模式 | 登录态即身份（`account_identity_version=0`）；账号切换检测=期望指纹 HMAC-SHA256（设备 DPAPI 持久密钥）端到端链路；指纹不可得→24h 有效期+人工重验。`binding_version` 与账号检测独立 |
| 任务表兼容 | `account_binding_id` 承载 `account_scope_id` 化名 scope，不改既有列约束 |
| 候选人绑定 | `bs_boss_conversation_bindings`，verified 才可自动发送；同名/同职位歧义禁发 |
| 开场白 | 禁用：BossTaskSpecPayload 无 opening_text 字段 |
| 受限决策输出 | `select_script|fill_slots|handoff` → 渲染冻结后归一化 `reply|handoff`；不产生 done |
| 完成规则与计轮 | V1 仅 rounds；计轮继承微信语义（`delivery_state=succeeded` 计轮，submitted 计轮） |
| 话术版本 | 新建不可变 `bs_boss_reply_script_versions`（append-only，lineage_id+version_no）；全链路统一 `script_version_id` |
| 发送操作 | `boss_send_to_v2`：HMAC envelope + Win32 + DOM 回读验证；旧工具不动 |
| 发送回执 | 三态：接受→`applied/submitted`+提交证据；气泡证据→`applied/verified`+验证证据；接受不可证→`unknown/unknown`。**evidence 判定失败不拒绝回执**（归一化 unknown+审计+ACK，沿微信现状） |
| **执行单元幂等** | **严格沿用权威协议：一个 decision 仅一组执行单元，`UNIQUE(tenant_id, decision_id)` 不变；prepare-send 幂等返回原 invocation；不存在同 decision 替代物化**（§5.5.4）。若产品要求许可阶段竞态也可自动重试，必须单独修订 edge-session-task-design 的 attempt/link/dedupe 模型并作为底座变更独立开发回归，不在本场景设计内顺带修改 |
| 观察操作 | `boss_session_observe` 只读，永不进写集合 |
| 频控 | 间隔≥60s、10min≤3、日≤10（Asia/Shanghai）。双闸门：BOSS prepare-send 物化前在既有锁链后追加 `binding` 锁，于同一事务调用**纯计算预检**并落库 + **write-authorize 权威复判**；微信 gate=None，不增加 binding 锁。窗口时间统一 `reserved_at`。触发计数=当日累计（成功不清零，跨日原子重置，effective_count 含本次加一）。write-authorize 复判失败一律保守：**不自动重试**，先提交 binding 同步阻断+控制请求+审计，再返回拒绝；human_required 由独立处理器完整迁移（§5.5.4/§5.5.5） |
| 回执接纳 | 身份/claim/device/request/permit/delivery 归属非法 → 拒绝不 ACK；**evidence 判定失败 → 接纳回执、归一化 unknown、保守 settled、审计、ACK**（§5.5.4/§7.3） |
| 转人工通知 | 站内通知 + recruiting_notify_service handoff 类型（渠道授权后开启） |
| 设备/桌面 | 共享桌面锁；boss v2 manifest `shared_lock_capable=true, protocol_version=2` |
| 桥密钥 | BOSS v2 开启时注入独立随机临时 key（env，不落盘）；v1 形态不注入 |

V1 不做：主动打招呼、找新候选人、薪资/录用/到面承诺、自动邀约/拒绝、自动标记不合适、多账号、简历 OCR 改动、peer_confirmed/judged、自定义完成表达式、批量写许可、跨设备接力、多时区、可暂停 invocation、同 decision 多次物化。

## 2. 复用与新增总览

| 层 | 复用（零改/仅配置） | 新增/泛化 |
|---|---|---|
| 云端通用 | 任务生命周期、租约/fence、事件 ACK、决策 worker、预算计费、受控加密文本、确认链、execution_lane、工作台组件、站内通知；**execution_links 唯一约束与幂等物化原样保留** | §4 场景描述器化（9 处去微信化 + envelope 分派 + authorize_operation 收调用方连接 + settle 钩子 + send_eligibility_gate 纯计算钩子 + prepare-send 写职责 + **控制请求行/阻断标记**） |
| Runtime | 引擎、就绪队列、write-authorize/journal/outbox、桌面锁、DPAPI、providerManager（通用 runner 执行链零改动）；SessionStore 物理格式/加密/append 接口不变 | BossBridge、桥注册、claim/meta 增加 scenario_key 与期望指纹、指纹比较、boss v2 manifest、桥密钥、**send_deferred 事件协议与回放扩展** |
| Provider | operation 骨架、MCP server 模式、ChatReadExecutor、open_chat 身份校验、WinMouseClicker、登录辅助 | `boss_session_observe`（含指纹）、`boss_send_to_v2`（含提交证据）、发送 verifier、DOM 对齐器 |
| 场景 | — | `src/boss_conversation/` 全部（含频控账本/异常队列 schema） |

## 3. 分层与所有权

| 位置 | 所有权 |
|---|---|
| `src/session_tasks/` + `src/local_tools/` | 通用任务协议与许可/回执链（描述器化后不含微信/BOSS 语义）；持有全部通用状态写职责（门禁结果落库、human_required 迁移、控制请求消费） |
| `src/boss_conversation/` | 场景描述器（纯计算判断）、候选人绑定、话术版本、受限决策钩子、渲染、频控判断逻辑与账本/异常队列 schema、通知路由、投影 |
| `clients/agent-tool-runtime/src/sessionTasks/` | 持久执行引擎 + 桥注册表 + 指纹比较 + send_deferred 事件持久化/回放 |
| `clients/boss-resume-assistant/` | 会话观察、消息对齐、发送/提交证据/写后验证、指纹上报 |
| `frontend/web/components/sessionTasks/` | 工作台组件复用；招聘后台"聊天自动化"入口 |

## 4. 云端通用层泛化：场景描述器（前置工程）

### 4.1 ScenarioDescriptor 单一注册对象

```python
class ScenarioDescriptor(Protocol):
    scenario_key: str
    spec_validator: Callable[[dict], dict]
    required_send_capability: str
    operation_descriptor: dict
    receipt_policy: dict   # {mode, context, submission_evidence_namespace, verified_evidence_namespace}
    binding_resolver: BindingResolver
    decision_hooks: ScenarioDecisionHooks
    adapter: ScenarioAdapter   # 含 settle_operation_result / validate_submission_evidence / validate_evidence / authorize_operation(含频控权威复判)
    workbench_label_resolver: Callable
    send_eligibility_gate: Optional[Callable]  # BOSS 使用；纯计算，只读判断；微信为 None
```

- **send_eligibility_gate 契约**：可选钩子；存在时签名为 `gate(read_cursor, task, decision) -> {eligible} | {deferred, next_send_eligible_at, effective_count} | {terminal: "human_required", reason}`。纯计算零写入；判断触发的写操作全部由通用 prepare-send 在其既有事务统一执行（§5.5.1 职责表）。**微信描述器设为 None，通用层直接走既有 prepare-send，不额外锁场景 binding、不新增门禁写操作**；B1.0 特征测试锁定。
- 注册点唯一：`register_scenario(descriptor)` 原子注册，任一失败整体回滚。

### 4.2 九处去微信化清单

| # | 位置 | 处理 |
|---|---|---|
| 1 | `constants.py:48` REQUIRED_DEVICE_CAPABILITIES | 通用能力 + 描述器 `required_send_capability` |
| 2 | `decisions.py:1856-1858` | operation descriptor 由描述器提供 |
| 3 | `decisions.py:1319-1372` action 分派 | 通用词汇不改；BOSS 钩子归一化 |
| 4 | `decisions.py:1553` | receipt 策略由描述器提供 |
| 5 | `service.py` 4 处 | binding_resolver 由描述器提供 |
| 6 | `models.py` | 缺省微信保留；spec 校验按场景分派（§4.3） |
| 7 | `workbench.py`、`api.py` bindings_router | label/门控/路由按场景拆分 |
| 8 | `scheduler/manager.py:393` | "任一启用场景描述器注册成功" |
| 9 | `operation_result.py:125,303` | submitted 接纳读描述器 receipt_policy；证据按 phase 分流两个校验入口；微信现状 B1.0 特征锁定 |

### 4.3 spec 校验按场景分派与 API envelope 调整

envelope `spec` 类型改 `dict`；create 按请求 scenario_key（缺省微信）分派；PATCH 先查任务行权威 scenario_key（不得切换，不一致 409）；微信错误路径保真（B1.0 三类特征用例）；加密/摘要/确认/发布基于场景校验后规范化结果。

## 5. 场景包 `src/boss_conversation/`

### 5.1 常量与配置

`SCENARIO_KEY="boss.chat_reply.v1"`、`PROVIDER_KEY="boss-recruiting"`、`OPERATION_MESSAGE_SEND="boss_send_to_v2"`、`REPLY_TEXT_MAX_CHARS=500`、`receipt_policy={mode:"submission", context:"boss_reply", submission_evidence_namespace:"boss-submission", verified_evidence_namespace:"boss-send-verifier"}`、payload_ref 前缀 `boss-reply`。config.yaml `boss_conversation:` 节点（enabled/tenant_allowlist/敏感词表/resume 字段白名单），热读门控照抄微信范式；双门控复用通用层机制。

### 5.2 候选人绑定与账号模式

**表 `bs_boss_conversation_bindings`**：UUID id、tenant/user/device、`account_scope_id`、candidate_name、job_id、resume_id（可空）、identity_version、verification_status、`login_fingerprint_hash`（可空）、encrypted_identity_evidence、verified_at/expires_at、频控触发计数列（`rate_trigger_date DATE`、`rate_trigger_count INT`、`last_rate_decision_id UUID`），以及同步阻断列（`automation_blocked BOOLEAN NOT NULL DEFAULT FALSE`、`automation_block_reason TEXT NULL`、`automation_block_epoch INTEGER NOT NULL DEFAULT 0`、`automation_blocked_at TIMESTAMPTZ NULL`）。

- account_scope_id 兼作任务表 `account_binding_id` 列值；创建从既有"已沟通"数据导入 pending；verified 仅接纳 Provider 真机双锚唯一命中证据；同租户同设备同 candidate_name+job_id 一条有效绑定。
- **登录指纹**：`HMAC-SHA256(设备 DPAPI 持久密钥, normalize(login_anchor))`，只传摘要，锚点不落日志。
- **账号切换链路**：Provider 观察带摘要 → claim 带期望值 → Runtime meta 持久化 → 每次观察与锁内发送前观察比对（身份校验四字段）→ 不符：控制事件（不含指纹）+blocked → 服务端消费后该设备全部 BOSS 绑定 invalid。期望 null → 跳过，24h 有效期人工重验（降级承诺，UI 明示）。
- **同步阻断语义**：`automation_blocked=true` 是 prepare-send 与 write-authorize 共同在 `binding FOR UPDATE` 后检查的硬门禁；异常事务只增大 `automation_block_epoch` 并置阻断，不得自动清除。仅明确人工恢复流程可在 `subject→task→binding` 锁序下清除，且必须带期望 block epoch，禁止旧控制请求清除新阻断。
- 绑定管理 API：`/api/boss-conversation/bindings`。

### 5.3 话术版本（新建不可变版本表）

```sql
bs_boss_reply_script_versions (
  id UUID PK, tenant_id TEXT NOT NULL,
  lineage_id UUID NOT NULL, version_no INT NOT NULL,
  content_hash TEXT NOT NULL, template TEXT NOT NULL, slot_schema JSONB NOT NULL,
  source_script_id UUID NULL, source_job_id UUID NULL,   -- 历史来源引用，无 FK
  source_job_name TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (tenant_id, lineage_id, version_no)
)
```

无指向 jobs/scripts 的 FK；lineage_id 非空无 NULL 陷阱；append-only（仅场景 API 写入+测试断言+spec 冻结副本纵深防御）；发布冻结 `{script_version_id, content_hash, frozen_template, slot_schema}`；全链路统一 `script_version_id`；占位符 `{slot_name}` 白名单语法、逐字替换、缺失/未知/超长→handoff。

### 5.4 BossTaskSpecPayload（完整冻结）与受限决策

```python
class BossScriptRef(BaseModel):
    model_config = {"extra": "forbid"}
    script_version_id: UUID; content_hash: str
    frozen_template: str = Field(min_length=1, max_length=2000)
    slot_schema: Dict[str, SlotField]

class BossSlotSource(BaseModel):
    model_config = {"extra": "forbid"}
    source: Literal["peer_message", "resume_field"]
    field: Optional[str] = None

class BossTaskSpecPayload(BaseModel):
    model_config = {"extra": "forbid"}
    goal: str; reply_policy: ReplyPolicy
    completion_rule: BossCompletionRule   # 仅 rounds
    limits: TaskLimits; work_window: Optional[WorkWindow]
    scripts: List[BossScriptRef]          # 1..10，总模板 ≤10000
    slot_evidence_sources: Dict[str, BossSlotSource]   # 键集合=slot_schema 键集合
```

resume_field 白名单（key_info.* 子集）、服务端取值、模型不输出 resume 槽位值；模型输出 `select_script|fill_slots|handoff` + reason_code（`low_confidence|sensitive_topic|missing_evidence|ambiguous|policy_conflict|rate_limited`）；script_version_id+content_hash 双匹配；敏感主题优先 handoff；**仅模型输出 schema 非法允许一次模型修复重试**，重试仍失败→human_required；确定性渲染归一化 `reply` 冻结；一期允许纯关键词规则。

**确定性渲染失败分流（冻结）**：渲染器绝不调用模型修复、也不消耗模型修复重试次数。发布校验必须拒绝模板中的未知占位符，因此正常运行时不应出现；运行时分流如下：

- 必需 slot、resume 字段或证据缺失 → 直接生成 `ready + action=handoff + reason_code=missing_evidence`，走既有 handoff/通知路径；
- slot 值合法但最终正文超过发送上限 → `ready + action=handoff + reason_code=policy_conflict`；
- 已发布模板仍出现未知占位符、content_hash/template 不一致或其他渲染不变量破坏 → 系统异常，转 `human_required(reason=render_invariant_violation)` 并写脱敏审计，不伪装成 missing_evidence；
- 上述 handoff 是正常业务转人工回复，不记为系统故障，不创建发送 invocation。

### 5.5 完成语义、rounds 计轮与频控双闸门

**完成规则**：V1 仅 rounds；计轮继承微信语义（submitted 计轮）；UI 对 submitted 显示"已执行发送（未核验送达）"。

#### 5.5.1 职责切分：描述器纯计算，通用层持有全部写操作

| 环节 | 谁执行 | 写操作 |
|---|---|---|
| 门禁判断 gate | 描述器（仅 BOSS 注册） | **零写入**；通用 prepare-send 先持有既有 subject/task/assignment/decision 锁，再按场景 resolver 锁 BOSS binding，并在同一事务、同一 cursor 内调用，返回判断与 effective_count；微信 gate=None，完全绕过该扩展 |
| 门禁结果落库 | **通用 prepare-send Phase A 事务** | 不释放上述锁即按决策 ID 去重更新触发计数列；terminal→完整 human_required 迁移（§5.5.5）；更新 epoch/control_seq；阻断后续授权；审计与通知事件 |
| 权威复判 | BOSS 适配器 authorize_operation（permit 事务同一 cursor） | 通过→同事务插 reserved；失败→返回结构化拒绝，通用 permits 提交 binding 同步阻断+控制请求+审计后再返回拒绝（§5.5.4） |
| 结算 | BOSS 适配器 settle_operation_result（operation_result 事务内，SAVEPOINT） | §5.5.4 |

#### 5.5.2 第一闸门：prepare-send 物化前预检（纯计算 + 通用层写）

- BOSS gate 只读计算：日上限→`terminal(rate_limit)`；间隔/10min 窗未满足→`deferred(next_send_eligible_at, effective_count)`；否则 eligible。
- deferred → prepare-send **不物化 invocation**，返回 `{deferred:true, next_send_eligible_at}`；通用层事务内完成触发计数落库（§5.5.3）。
- 微信 gate=None；通用层不解析 BOSS binding、不新增 binding 行锁或门禁写操作；通用 runner 与 write-authorize 不感知 deferred。
- **原子顺序冻结**：所有场景沿用既有 `subject→task→assignment→decision` 校验；仅 gate 非空时，再通过场景 resolver 获取并锁定 binding，检查同步阻断，跨日归一化计数，以同一 cursor 调 gate，并在不释放锁的情况下落库 effective_count/terminal 结果后提交。gate 纯计算仅表示不写库，不表示无锁读取。只有 eligible（含 gate=None 的既有场景）才进入 Phase B 既有底座幂等物化；Phase B 不再持有 Phase A 行锁，期间若控制状态变化，由 task/decision/epoch 校验及 write-authorize 最终复判阻断。

#### 5.5.3 窗口时间口径、effective_count 与 next_send_eligible_at（冻结）

- **全部窗口以 `reserved_at` 为发送发生时刻**；settled 沿用最初 reserved_at（settled_at 仅结算时刻）；60s 间隔**含 reserved 行**；10 分钟窗解除=窗内第 3 条可计数行的最早 reserved_at+10min；日上限=当日（Asia/Shanghai）reserved+settled ≥10。
- **effective_count 公式**：

```
stored_count    = 跨日归一化后的数据库计数（rate_trigger_date 非当日→按 0）
effective_count = stored_count      （当前 decision 已计数过）
                | stored_count + 1  （当前 decision 首次触发）
next_send_eligible_at 按 effective_count 计算；持久化后的 count 必须等于 gate 使用的 effective_count
```

- **决策去重依据（冻结的不变量+测试）**：`last_rate_decision_id` 只防"连续重复的同一 decision"；严格正确性依赖系统不变量——**同一 binding 任意时刻只有一个有效任务（占用唯一约束），旧 decision 被 supersede 后不会再执行（prepare-send 复验 input_version/未 superseded）**。B2 必须有该不变量的定向测试（supersede 后旧 decision 重复触发计数、并发 gate 等用例）。
- 退避附加按 effective_count：≤1→+0；2→+2min；3→+5min；≥4→terminal human_required（reason=repeated_rate_trigger）。

#### 5.5.4 第二闸门：write-authorize 权威复判、失败处置与结算

**复判（BOSS 适配器 authorize_operation，permit 事务同一 cursor）**：重算日数/10min 窗/60s 间隔（口径同上）+ binding 有效 + 任务/授权 epoch 有效。通过→同事务插 reserved（reserved_at=now）→签发 permit。

**失败处置（全部保守，不自动重试；撤回 V1.5 的替代物化）**：

| 分类 | 处置 |
|---|---|
| `rate_window_race`（正常窗冲突的竞态：门禁通过后账本被迟到回执补账/并发/恢复结算改变） | 拒绝 permit，当前 invocation 未授权终结（既有终态路径）；**不为同 decision 创建替代 invocation**（权威协议一个 decision 一组执行单元）；任务 human_required（reason=rate_window_race）+ 脱敏审计。若产品要求该竞态自动重试，必须单独修订 edge-session-task-design 的 attempt/link/dedupe 模型并作为影响微信链路的底座变更独立开发回归 |
| `rate_daily_cap` | 拒绝 permit + human_required（reason=rate_limit） |
| `rate_ledger_anomaly`（数据异常） | 拒绝 permit + human_required（reason=rate_ledger_anomaly）+ 审计 |

**许可拒绝的提交语义（冻结）**：扩展通用 `AuthorizeDecision`，增加可选 `control_action` 与 `audit_code`；适配器只返回结构化判断，不自行提交事务。对 BOSS 上述三类拒绝，通用 permits 在当前事务内锁定 binding、置 `automation_blocked=true` 且 `automation_block_epoch+1`、写幂等控制请求与脱敏审计，**先 commit 这些拒绝副作用，再向 Runtime 返回许可拒绝**。不得沿用当前 `allowed=false → rollback` 路径把控制副作用回滚。微信适配器始终返回 `control_action=None`，其既有 denied→rollback 行为保持不变。控制请求/阻断/审计任一写入失败时不得签发 permit，也不得声称已转人工。

**频控账本完整 schema（冻结）**——正常路径仅在预留成功时创建 reserved 行，许可失败分类不写账本行；唯一例外是 operation-result 发现 slot 缺失时补建 settled 异常行：

```sql
bs_boss_conversation_rate_slots (
  id BIGSERIAL PK,
  tenant_id TEXT NOT NULL,
  user_id TEXT,
  binding_id UUID NOT NULL,
  decision_id UUID NOT NULL,
  delivery_id UUID NOT NULL,
  status TEXT NOT NULL,          -- reserved | settled | released
  reserved_at TIMESTAMPTZ NOT NULL,
  settled_at TIMESTAMPTZ,        -- status=settled 时非空
  released_at TIMESTAMPTZ,       -- status=released 时非空
  settlement_effect TEXT,        -- submitted | verified | unknown | not_started（结算依据）
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (tenant_id, delivery_id),
  UNIQUE (tenant_id, decision_id),
  CHECK (status IN ('reserved','settled','released')),
  CHECK (settlement_effect IS NULL OR settlement_effect IN ('submitted','verified','unknown','not_started')),
  CHECK ((status='reserved' AND settled_at IS NULL AND released_at IS NULL)
      OR (status='settled'  AND settled_at IS NOT NULL AND released_at IS NULL)
      OR (status='released' AND released_at IS NOT NULL AND settled_at IS NULL)),
  CHECK ((status='reserved' AND settlement_effect IS NULL)
      OR (status='settled' AND settlement_effect IN ('submitted','verified','unknown'))
      OR (status='released' AND settlement_effect='not_started'))
);
CREATE INDEX idx_boss_rate_windows ON bs_boss_conversation_rate_slots (tenant_id, binding_id, reserved_at)
  WHERE status IN ('reserved','settled');
```

**异常队列完整 schema（冻结）**——敏感异常详情不落明文，只存错误码与受控引用：

```sql
bs_boss_rate_settlement_anomalies (
  id BIGSERIAL PK,
  tenant_id TEXT NOT NULL,
  user_id TEXT,
  task_id UUID NOT NULL,
  binding_id UUID NOT NULL,
  delivery_id UUID NOT NULL,
  invocation_id UUID NOT NULL,
  error_code TEXT NOT NULL,       -- rate_slot_missing | rate_slot_state_conflict | settlement_failed 等
  status TEXT NOT NULL DEFAULT 'pending',  -- pending | processing | resolved | ignored
  retry_count INT NOT NULL DEFAULT 0,
  next_retry_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (tenant_id, delivery_id),
  CHECK (status IN ('pending','processing','resolved','ignored')),
  CHECK (retry_count >= 0)
)
```

**结算与回执接纳（SAVEPOINT + evidence 语义二分，冻结）**——operation_result 处理顺序：

1. **回执接纳性检查**：身份、claim、device、request、permit 绑定、delivery 归属——任一非法 → **拒绝回执、不 ACK、不修改账本**（既有拒绝路径，唯一拒绝面）；
2. **evidence 判定（不拒绝回执，沿微信现状 `operation_result.py:390-415`）**：evidence 缺失、无法验证、digest 不匹配或已被复用 → **接纳并持久化原始回执（attempt 保留原始上报值）、不承认 applied/verified、delivery 归一化 `unknown/unknown`、写 `evidence_invalid` 审计、频控按保守语义结算 settled、正常 ACK、禁止自动重发**；
3. `SAVEPOINT rate_settlement` → 执行频控结算。找到 reserved 行时按原始机器事实转换：`submitted/verified/unknown → settled`，明确未开始的失败/取消/过期 → `released(not_started)`；
4. **正常结算**：找到 reserved 行且守卫更新成功 → 正常提交全部；
5. **缺失补建**：对于应占额度的 `submitted/verified/unknown`，未找到 slot → 以 `INSERT ... ON CONFLICT` 幂等补建 settled（保守占额度），`reserved_at` 优先取已校验 permit 的创建/签发时刻，缺失时取 attempt started_at，禁止用回执到达时间拉长窗口；明确未开始且未取得 permit 的结果没有 slot 属正常情况，不补建。即使补建成功，其他应占额度结果的 slot 缺失本身仍是不变量破坏，必须写异常队列 `rate_slot_missing` + 脱敏审计 + 锁 binding 置同步阻断 + 写 human_required 控制请求，随后提交主事务并正常 ACK；不得把补建成功视作正常结算；
6. **结算/补建失败**：`ROLLBACK TO SAVEPOINT rate_settlement` → 持久化原始回执 + 写异常队列（`rate_slot_state_conflict|settlement_failed`）+ 脱敏审计 + 锁 binding 置同步阻断 + 写 human_required 控制请求 → **提交主事务并正常 ACK**。

正常 ACK 条件=第 1 步全部合法（无论 evidence 判定与结算成败）；仍拒绝条件=第 1 步任一非法。微信现有 evidence-invalid 行为由 B1.0 特征测试锁定。

#### 5.5.5 human_required 完整控制迁移与锁序矩阵（P1-2）

**统一迁移函数**（cursor 级，同事务）：任务状态、subject 状态、`authorization_epoch +1`、`server_control_seq +1`、未用授权撤销/因 epoch 失效、控制事件、审计、去重通知。描述器只返回 `terminal/reason`，迁移由通用服务层执行。

**事务锁序矩阵（全链路冻结；替代 V1.5 的简化锁序声明）**：

| 事务路径 | 行锁顺序（依序） | 说明 |
|---|---|---|
| prepare-send Phase A（门禁与落库） | 所有场景：subject → task → assignment → decision；**仅 gate 非空的 BOSS 再锁 binding** | BOSS 同一事务/同一 cursor 调 gate 并落库；微信 gate=None，不新增 binding 锁；human_required 可在 subject/task 已持锁内执行；deferred/terminal 不进入 Phase B |
| prepare-send Phase B（仅 eligible） | 既有底座幂等物化各自事务 | Phase A 已提交、不再持其行锁；`UNIQUE(tenant_id,decision_id)`、稳定 dedupe key 与最终 authorize 复判保持现有语义 |
| write-authorize（permit，`permits.py:127-149` 现状） | subject → run → invocation → delivery → **binding → rate slot**（adapter 复判+预留） | binding/rate slot 为 delivery 之后的链尾延伸；先检查 `automation_blocked`，拒绝控制副作用按 §5.5.4 先提交再返回拒绝 |
| operation-result（`operation_result.py` 现状） | invocation → attempt → delivery → **binding** → rate slot（SAVEPOINT 结算/补建）→控制请求行 | **保持现有锁序不重排、不获取 subject/task 锁**；binding 是与发送路径共享的同步互斥点 |
| 人工接管控制迁移（统一函数） | subject → task →（撤销授权相关行） | 已持锁事务直接调用，或独立处理器按此序获取 |
| 绑定失效（指纹事件消费） | 无锁枚举 → 每个关联项独立事务 `subject → task → binding` | 多项按 task_id/binding_id 稳定排序；加锁后重验设备/指纹版本；禁止持 binding 再取 subject |

**operation-result 异常转人工的两步机制（binding 同步阻断，不重排现有核心锁序）**：

1. 回执事务内：在 delivery 后获取 `binding FOR UPDATE`，将 `automation_blocked=true`、记录 reason/time、`automation_block_epoch+1`，再写控制请求行；
2. **阻断语义**：prepare-send Phase A 与 write-authorize 都必须获取同一 binding 行锁并检查 `automation_blocked`；检查与后续门禁/许可动作受 binding 行锁串行，保证独立处理器执行迁移前不会有新授权穿透；仅查询控制请求表不构成同步屏障，禁止作为唯一门禁；
3. 独立处理器不持有 delivery/rate slot 锁，按 `subject→task→binding` 获取锁，校验请求的 expected control/block epoch 后执行完整迁移并消费控制请求；过期请求标 stale，不得覆盖较新的阻断；
4. 控制请求/阻断标记/审计任一写入失败时，主事务不得声称已转人工；数据库暂时错误按接口既有可重试错误处理，机器副作用事实仍由 Runtime outbox 重投。

**通用控制请求表 schema（冻结）**：

```sql
session_task_control_requests (
  id BIGSERIAL PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  task_id UUID NOT NULL,
  expected_control_epoch INTEGER NOT NULL,
  expected_block_epoch INTEGER NOT NULL,
  reason TEXT NOT NULL,
  source_type TEXT NOT NULL,       -- permit_denied | rate_settlement
  source_ref TEXT NOT NULL,        -- 受控 delivery/invocation 引用，不存敏感正文
  status TEXT NOT NULL DEFAULT 'pending', -- pending | processing | applied | stale | failed
  processing_owner TEXT,
  processing_lease_expires_at TIMESTAMPTZ,
  retry_count INTEGER NOT NULL DEFAULT 0,
  next_retry_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (tenant_id, task_id, expected_control_epoch, reason),
  CHECK (status IN ('pending','processing','applied','stale','failed')),
  CHECK (retry_count >= 0)
)
```

**控制请求处理器（B1.2 最小实现冻结）**：挂入既有 scheduler manager，默认 5 秒 tick；以 `FOR UPDATE SKIP LOCKED`/条件 UPDATE 原子认领 `pending` 或处理租约已过期的 `processing`，写 `processing_owner/processing_lease_expires_at`。校验 expected control/block epoch：不符→stale；成功→applied；暂时失败按限次退避重试，默认 10 次后→failed 并产生站内告警。无论 processing/failed，binding 的 `automation_blocked` 均保持 true，禁止处理器失败后自动恢复发送。

**binding 定位（operation-result）**：invocation 已按既有流程 `FOR UPDATE` 后，从其受信 `business_ref.task_ref` 普通读取 session task 的 `conversation_binding_id`，随后按矩阵获取 `binding FOR UPDATE`；加锁后重新读取并核对 invocation task_ref、task conversation_binding_id、tenant 与 binding 归属仍一致，不一致按 `rate_ledger_anomaly` 阻断。定位阶段不得先锁 task，也不得从客户端回执参数接受 binding_id。

**人工解阻（B4）**：绑定管理 API 增加 owner-only `unblock`，请求必须携带 `expected_block_epoch` 做 CAS，成功时清除 blocked/reason/time 并将 block epoch 再递增，写审计。**binding 解阻不等于任务恢复**：human_required 任务仍须通过既有显式 resume，并选择 fresh baseline；工作台呈现两个独立动作，可引导顺序但不得在一次隐式请求中同时完成。

**无死锁论证**：prepare-send 与 write-authorize 都先取 subject；operation-result 只按 `invocation→attempt→delivery→binding→rate slot` 前进且从不再取 subject/task；控制处理器与绑定失效处理器获取 subject 时不持有 delivery/rate slot，并统一按 `subject→task→binding`；不存在 `binding→subject` 反向边。binding 行同时是回执异常与后续发送的同步屏障。**B1.2 必须增加许可、回执、暂停、绑定失效、频控结算五路并发死锁与阻断穿透测试**；在矩阵通过前不声称“微信行为零变化”。

#### 5.5.6 其他

自发回声不触发决策（沿通用层回显归属）；一期允许纯关键词规则实现受限决策点；日口径 Asia/Shanghai；只读预检（gate 纯计算阶段）零写入——写入只发生在通用层事务内。

### 5.6 通知与沟通日志投影

- 转人工：通用层 human_required + 站内通知已有；场景按 owner_employee_id 路由经 recruiting_notify_service 新增 handoff 类型；无配置/失败保留站内待办标"未通知"，限次退避只补通知。
- 投影：`bs_recruiting_operator_resume_comm_logs` 补列 `source_delivery_id UUID NULL, source_message_id TEXT NULL` + `UNIQUE(tenant_id, source_delivery_id)`；队列 `bs_boss_comm_log_projection_queue`（tenant_id/user_id/delivery_id/binding_id/resume_id/status/retry_count/next_retry_at/last_error_code，`UNIQUE(tenant_id, delivery_id)`，仅错误码不落敏感详情）。verified（或 unknown 人工判定后）入队，后台 job 幂等 upsert；不触发旧回写，禁止双写。

## 6. Runtime 侧

### 6.1 scenario_key 与期望指纹端到端

1. claim 响应新增 `scenario_key`（live claim 必填）、`expected_login_fingerprint_hash`（可空，微信恒 null）；旧 Runtime 忽略未知字段。
2. live claim 缺 scenario_key → 拒绝 adopt（协议回归），记录协议错误，不回退微信。
3. AssignmentMeta 增加 `schema_version`：历史 v1 回放按微信；新写入一律 v2；桥查不到场景→blocked。
4. 分配前置防线：仅 BossBridge 装配且 v2Send=true 时上报 `boss_send_to_v2`；服务端按场景校验能力后分配。
5. 指纹比较：`ObserverResult` 增加可选摘要字段；身份校验三字段扩四字段；不符→控制事件（不含指纹）→blocked；期望 null→跳过。

### 6.2 场景桥注册表与 send_deferred 事件协议（P2-6 冻结）

- 桥注册表按 scenario_key；BossBridge 实现 ObserverFn 与 prepare 钩子（`kind:'boss.send.v2'` HMAC envelope）。
- **prepare-send HTTP 语义（冻结）**：prepared、deferred、superseded 均是协议内正常结果，统一返回 **HTTP 200**；不得用 202/409 表达 deferred。Runtime `apiClient` 使用判别联合并**先判断 status/deferred，再读取 invocation_id**，禁止把 deferred 的空 invocation 误判为 superseded：

  ```text
  {status:"prepared", invocation_id, decision_id, ...}
  | {status:"deferred", deferred:true, invocation_id:null,
     server_now, deferred_until, retry_after_ms, deferred_reason, response_revision}
  | {status:"superseded", invocation_id:null, decision_status:"superseded"}
  ```

  认证、参数、租约、fence、协议或服务端故障仍使用既有非 2xx 错误；只有合法的频控等待返回 deferred 200。
- **deferred 事件协议（冻结）**：

```
prepare-send deferred response = {
  status:"deferred", deferred:true, invocation_id:null,
  server_now, deferred_until, retry_after_ms, deferred_reason, response_revision
}
event_type = "send_deferred"
payload = {
  decision_id, batch_id, input_version,
  server_now, deferred_until, retry_after_ms,
  deferred_reason, response_revision
}
```

- **追加规则**：仅当到期时间或服务端响应版本变化时才追加新事件；同一 deferred 响应不重复追加；
- **持久化顺序**：写盘成功后才更新内存调度；**写盘失败 fail-closed，不得继续调用 prepare-send**；
- **当前进程调度**：使用服务端计算的 `retry_after_ms` 配合本地单调时钟；校验其与 `server_now/deferred_until` 的差值在容许误差内并做上限 clamp，禁止直接依赖客户端墙上时钟计算等待；
- **回放**：以同一 decision 最后一条 send_deferred 为准；绝对 `deferred_until` 只作恢复提示。到期在未来→按提示调度；已过期、客户端时钟明显漂移或无法可靠判断→立即+短抖动重新调用 prepare-send，由服务端权威门禁决定是否继续 deferred；旧日志缺字段→按未 deferred 处理（向后兼容）；极端未来时间/非法格式→拒绝或 blocked，不静默放行；
- **清除时机**：supersede、进入 executing、进入 waiting_peer 时清除该 decision 的 deferred；
- `server_now/deferred_until` 使用服务端绝对 UTC 时间；它们不能单独消除客户端时钟偏差，因此运行期以 `retry_after_ms`+单调时钟为准，重启后仍必须重走服务端 prepare-send，永不因本地判断到期而绕过服务端门禁或 write-authorize。旧 Runtime/微信历史日志不受影响。

### 6.3 manifest、配置与桥密钥注入

boss-recruiting v2 变体（protocol_version=2、shared_lock_capable=true、observe 只读不进写集合）；`v2Send===true` 时注入 `AIDWORK_BOSS_BRIDGE_KEY`（独立随机 32B hex，不落盘）；Provider 校验链 HMAC→有效期→绑定一致；引擎相位机/公平队列/outbox/桌面锁零改动复用。

## 7. Provider 侧（boss-resume-assistant）

### 7.1 观察工具 `boss_session_observe`（只读）

满足 `session_observer_v1` 冻结 schema + 指纹摘要；open_chat 双锚 → DOMSnapshot → ChatReadExecutor → 对齐分配 local_message_id；对齐键=binding_version+sender+精确文本+相邻序列+重复序号；sender 无法判定→整体 `gap(sender_unknown)`；虚拟化未验证前新消息以窗口底部追加判定；异常→gap 走既有退避；铁律 attach-only/禁 Runtime.*/Win32 写优先。

### 7.2 发送工具 `boss_send_to_v2`

envelope 校验 → 头部锚点核验 → Win32 聚焦 → 草稿逐字相同复用/不同拒绝 → SendInput → Enter（逐事件投递检查=提交证据原料）。旧 19 工具不动。

### 7.3 双证据与回执三态

| 证据 | 命名空间 | 校验入口 |
|---|---|---|
| 提交证据（applied/submitted） | `boss-submission:<request_id>:1` | `validate_submission_evidence` |
| 验证证据（applied/verified） | `boss-send-verifier:<request_id>:<n>` | `validate_evidence` |

三态：接受可证→`applied/submitted`+提交证据；气泡证据成立→`applied/verified`+验证证据；接受不可证→`unknown/unknown`。**evidence 判定失败不拒绝回执**（§5.5.4 语义二分：归一化 unknown+审计+ACK+保守 settled）。缺证据的 applied/verified 判定不成立（非拒绝回执）。禁止 "effect=verified" 等非冻结表达。

## 8. P0′ 门禁（B0 拆两段；写后证据双指标）

| 项目 | 证据 / 不通过处理 | 阶段 |
|---|---|---|
| 写后证据（双指标） | ① B0b 至少 30 次授权正常发送，观测命令接受率 `applied(submitted+verified) ≥95%`；② 同时报告 accepted 与 `verified/applied` 的 Wilson 95% 区间，按区间保守端冻结写后验证率下限（30 次仅作可行性证据，不宣称统计证明总体≥95%）；③ B5 按冻结下限与授权成本确定复测样本量并达标；④ 错误场合 verified=0、接受不可证 applied=0；⑤ 相同正文连续发送误判=0；⑥ unknown 不重发 | B0b→B3→B5 复测 |
| 候选人唯一性 | 各路径同名/同职位/账号切换（含指纹链路）/绑定过期；不能唯一禁发 | B0a + B3 联调 |
| 长会话虚拟化 | 多屏长历史/滚动裁剪/重开重启/窗口重叠/连续相同正文 | B0a |
| 未读可靠性 | 大列表/视口外/当前会话/徽章清零/漏读重复/突发 | B0a |

B0a=现有只读工具验证三项只读门禁；B0b=实验原型 verifier（非生产工具）至少 30 次授权样本，记录原始计数与 Wilson 95% 区间并冻结写后验证率下限；它是可行性门禁而非“总体成功率已被统计证明”。补充：同桌面互斥、决策 JSON 稳定性、风控累积（授权账号小样本）。授权边界：独立授权测试账号与配合候选人；微信授权不延伸到候选人。

## 9. 实施阶段与零回归边界

### 9.1 B1 分步

| 步骤 | 内容 | 放行条件 |
|---|---|---|
| B1.0 特征测试 | 微信现状（不改行为）：缺省场景、claim/restart meta 恢复、submitted 回执接纳全链、旧 verified 回执、**evidence-invalid 归一化 unknown+审计+ACK 现状**、人工 self 判断、能力缺失拒绝、旧任务日志恢复、共享桌面锁、unknown 不重发、API 路径与前端路由、spec 非法错误路径三类、prepare-send 无门禁写操作且不额外锁场景 binding | 全绿基线固化 |
| B1.1 描述器引入 | ScenarioDescriptor（可选 gate/settle/双命名空间）+ 注册原子性；微信描述器复刻旧常量且 gate=None | 行为及锁面零变化；特征测试全绿 |
| B1.2 泛化收尾 | 九处去微信化 + envelope 分派 + authorize_operation 收调用方连接及结构化 control_action + settle 钩子（SAVEPOINT）+ BOSS prepare-send Phase A 门禁原子事务 + binding 同步阻断/控制请求及租约处理器 + **许可、回执、暂停、绑定失效、频控结算五路并发死锁与阻断穿透测试**（包含微信路径，按 §5.5.5 矩阵） | 微信后端全量 + Runtime + Provider 恢复/回执测试与基线一致；并发测试通过；微信 prepare-send 结果和锁面等价；微信普通 denied 仍 rollback 且不产生控制请求 |
| B1.3 BOSS 注册 | BOSS 描述器注册；默认关闭 | BOSS 关闭微信全绿；开启 fake 通过 |
| B1.4 出口 | 工作台与真机在 B3/B4 接 | — |

### 9.2 阶段总表

| 阶段 | 交付 | 依赖 |
|---|---|---|
| B0a | 唯一性/虚拟化/未读可靠性证据 | 独立授权；可与微信 C5 并行 |
| B0b | 实验 verifier 可行性 + 至少30次授权样本原始计数/Wilson 95%区间 + 写后验证率下限冻结 | B0a；独立授权 |
| B1 | §9.1 五步；微信零回归（含锁序矩阵死锁测试） | 无 |
| B2 | 场景包全部+绑定表（触发计数列+同步阻断列）+话术版本表+**频控账本/异常队列完整 schema（§5.5.4）**+双闸门+effective_count/去重不变量测试+缺失 slot 补建仍升级异常测试+投影队列+fake 端到端 | B1.3 |
| B3 | scenario_key/指纹端到端、send_deferred（server_now/deferred_until/retry_after_ms）事件协议与回放、BossBridge+桥密钥、v2 manifest、observe/send_v2、生产 verifier+提交证据 | B2 + B0a；B0b 冻结 |
| B4 | 工作台接入、绑定管理页、话术版本管理、handoff 通知、投影 job | B3 |
| B5 | 单候选人连续验收（≥10 轮含突发/接管/重启/频控 deferred 重试）、双指标复测、旧 BOSS 回归、同桌面混用、发布/回滚手册 | B0 全门禁+B1–B4+独立测试/CR |

验收对照通用层 A1–A7/A9–A11 + BOSS 附加项（prepare-send Phase A 原子门禁、write-authorize 三分类及拒绝副作用提交、binding 同步阻断、reserved_at 口径、effective_count/去重不变量、SAVEPOINT 补账与缺失 slot 异常升级、五路并发死锁/阻断穿透测试、evidence 语义二分、human_required 完整迁移、send_deferred 单调时钟协议、指纹链路、双证据、投影幂等、A8′ 旧 BOSS 回归、B1.0 微信锁定清单）。capability 默认关闭；fake 通过不标真机通过。

## 10. 开放问题（需用户决策后冻结）

1. 测试账号与候选人授权范围、配合方式、时段与发送上限（B0 前置）。
2. `REPLY_TEXT_MAX_CHARS` 初值（暂 500）与敏感词表、resume 字段白名单初版。
3. 转人工通知渠道授权与 owner_employee_id 映射来源。
4. 完成模式 UI 占位（V1 服务端已拒绝非 rounds）。
5. boss v2Send 开关是否与微信共用总开关。
6. 登录指纹不可得时降级承诺（24h+人工重验）是否可接受，或要求指纹强制门禁。
7. write-authorize `rate_window_race` 当前设计为保守转人工；若产品要求该竞态自动重试，须单独修订 edge-session-task-design 的 attempt/link/dedupe 模型（底座变更，影响微信链路，独立开发回归）——是否需要列入路线图。
