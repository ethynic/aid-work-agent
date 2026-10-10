# 系统级防滥用与限额设计（Anti-Abuse Policy）

> 状态：📋 设计完成，待评审
> 作者：2026-10-10
> 关联事件：2026-09-29~30 生产 tenant_29304e2196e8「销售助手」wecom_kf 会话被个别用户滥用（5 万字小说 / 重复重写 xlsx / 500 场景话术等），短时间消耗大量积分；产品经理通过修改智能体提示词缓解，但被用户绕过。

---

## 1. 背景与问题定位

### 1.1 已发生的三类滥用

| 模式 | 实例 | 特征 |
|------|------|------|
| 超长创作 | "写 5 万字小说歌颂梁总罗总" | 单请求产出 token 极大，积分消耗集中 |
| 变体重复 | "移除缓存，重新再写 20000 字" / "20 个场景" → "500 个场景" → "20×20 条" | 归一化后高度相似的请求在短窗口内反复触发 |
| 批量穷举 | "把知识库所有产品都详细介绍一遍" | 单请求或少量请求拉取全库内容 |

### 1.2 现有防线的不足

- **提示词规则是软约束**：PM 已在「销售助手」提示词中加入防滥用条款，但用户改用其他措辞即可绕过；且该方案要求逐个智能体维护提示词，无法跨智能体复用、无法由租户管理员自助管控。
- **无限额机制**：系统只有登录限流（`src/api/rate_limit.py`，仅用于登录接口），没有任何按用户/渠道维度的用量预算、请求上限或重复检测。
- **无触发记录**：滥用发生后只能靠人工翻日志，租户管理员无感知、无工具。

### 1.3 设计原则

1. **硬闸与软约束分层**——确定性规则（预算、次数、重复、工具封禁）由系统代码强制执行，不依赖 LLM 自觉；语义类规则（拒绝创作/批量遍历）本质是提示词约束，定位为辅助防线，由系统统一下发注入，替代逐智能体手改。
2. **按成本判断，不按内容判断**——行业通行做法（LiteLLM budget / OpenAI Tier 限流）是用「花费 + 频率」这套与内容无关的确定性指标兜底，内容理解只做辅助。
3. **租户自治 + 作用域细化**——规则由租户管理员按「用户」「渠道配置」两个粒度勾选，因为「短时间大量积分」存在合法场景，是否收紧由最了解业务的租户决定。
4. **先观察后拦截**——所有规则支持「观察模式」（只记录事件不拦截），用真实数据校准阈值后再切「拦截模式」。

---

## 2. 行业参照

| 方案 | 机制 | 可借鉴点 |
|------|------|---------|
| LiteLLM Proxy（开源，业界引用最多） | 按 user/key/team 设 `max_budget` + 时间周期 + 超限行为；另有 tpm/rpm 限制、超限冷却 | 预算三元组语义：**周期（period）+ 上限（max_budget）+ 超限行为（block/allow+notify）**，本设计的预算规则直接沿用 |
| OpenAI / Anthropic Tier 限流 | RPM / TPM / 每日用量分档 | 多维限流（次数 + token + 成本）而非单一维度 |
| 云厂商 Budgets（AWS/GCP） | 预算告警线 + 硬上限两档 | 「告警线先通知、硬限线才拦」的渐进处置 |
| Azure AI Content Safety / Bedrock Guardrails | 内容分类门禁 | 对「创作类滥用」判定成本高、误杀率高，**不建议**作为系统级实现方式；本设计用提示词模块替代 |

---

## 3. 规则模型

### 3.1 预置规则目录

规则分两类。**硬规则**由系统代码确定性执行；**软规则**以标准「服务范围约束」提示词模块形式注入 system prompt（见 §6）。

| 规则 ID | 名称 | 类型 | 参数（默认值） | 执行机制 |
|---------|------|------|---------------|---------|
| `credit_budget` | 时间窗积分预算 | 硬 | `period`: hour/day/month（day）；`warn_threshold`: 0.8×；`hard_threshold`: 必填 | 请求入口查 `chat_records` 聚合（§5.2） |
| `request_count` | 时间窗请求次数 | 硬 | `period`: hour/day（hour）；`max_requests`: 必填 | 同上，COUNT 聚合 |
| `max_output_per_request` | 单请求输出预算 | 硬 | `max_completion_tokens`: 8000；`max_iterations`: 使用引擎默认 | 请求入口注入运行时预算，超限中断（§5.3） |
| `duplicate_task` | 重复任务检测 | 硬 | `window_minutes`: 60；`max_similar`: 3 | 归一化指纹 + Redis 窗口计数（§5.4） |
| `no_file_generation` | 禁止生成新文件 | 硬 | 无参数（预置封禁工具清单） | 工具级请求内 denylist（§5.5） |
| `no_creation` | 禁止创作类任务 | 软 | `custom_text`: 可选补充说明 | 提示词模块注入（§6） |
| `no_batch_enumeration` | 禁止批量遍历 | 软 | 同上 | 提示词模块注入（§6） |

默认参数仅是初值，租户管理员可改。**`credit_budget` 与 `request_count` 的具体阈值不做全局预设**——这正是「需要讨论多短时间、消耗多少积分」的解法：Phase 1 全部默认观察模式跑真实数据，用数据定阈值（§8）。

### 3.2 绑定模型

```
规则预置（代码定义）          绑定（租户管理员勾选，存 DB）
┌────────────────┐          ┌──────────────────────────────┐
│ credit_budget   │ ◄─ 勾选 ─│ scope_type = user            │
│ request_count   │          │   scope_id  = user_id        │
│ max_output_...  │          │ scope_type = channel         │
│ duplicate_task  │          │   scope_id  = config_id      │
│ no_file_gen...  │          │ policies = [                │
│ no_creation     │          │   {rule_id, params, mode}    │
└────────────────┘          │ ]  mode: observe|enforce     │
                             └──────────────────────────────┘
```

- **作用域两种**：`user`（租户内某个用户，`users.user_id`）和 `channel`（租户渠道配置，`tenant_channel_configs.config_id`，即某渠道 × 某数字员工）。
- **同一请求可能命中多个绑定**（该用户绑了规则 A，其所在渠道又绑了规则 B）：取**并集全部执行**，任一硬规则拦截即拦截。不做「最严格优先」的合并逻辑，规则间参数独立，实现最简单、语义最可预期。
- **mode 按绑定条目配置**：observe（只记事件）/ enforce（真拦截）。整体功能默认全 observe。
- 规则清单是代码内置的（新增规则 = 发版），绑定数据只有「勾选 + 参数」，不需要规则 CRUD 界面，避免过度设计。

### 3.3 超限行为

| 检查点 | 行为 |
|--------|------|
| 入口预检拦截（预算/次数/重复） | 请求不进入 LLM 执行；渠道侧回复一条固定礼貌话术（可按租户配置），web 侧返回结构化错误；写 `abuse_events` |
| 告警线（预算 warn_threshold） | 不拦截，只写 `abuse_events`（action=warned） |
| 运行中超限（max_output_per_request） | 中断执行，返回已生成部分 + 超限说明 |
| 观察模式 | 一切放行，仅写 `abuse_events`（action=logged） |

---

## 4. 数据设计

### 4.1 新增表

```sql
-- 规则绑定表（一个作用域一行）
CREATE TABLE IF NOT EXISTS abuse_policies (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    scope_type TEXT NOT NULL,            -- user | channel
    scope_id TEXT NOT NULL,              -- users.user_id 或 tenant_channel_configs.config_id
    mode TEXT DEFAULT 'observe',         -- observe | enforce
    policies JSONB DEFAULT '[]',         -- [{rule_id, params:{...}}]
    enabled INTEGER DEFAULT 1,
    updated_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_abuse_policies_scope UNIQUE (tenant_id, scope_type, scope_id)
);

-- 触发事件表（观察与拦截都记录，审计 + 阈值校准数据源）
CREATE TABLE IF NOT EXISTS abuse_events (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    action TEXT NOT NULL,                -- logged | warned | blocked | terminated
    session_id TEXT,
    channel_type TEXT,                   -- wecom_kf / wecom / web / ...
    detail JSONB,                        -- 触发时快照：阈值、实际值、消息指纹等
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_abuse_events_tenant_time
    ON abuse_events(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_abuse_events_scope
    ON abuse_events(tenant_id, scope_type, scope_id, created_at DESC);
```

要点：

- 单表单行绑定（`(tenant_id, scope_type, scope_id)` 唯一），不按规则拆行，勾选界面整存整取。
- 不在 `users` / `tenant_channel_configs` 上加列：用户和渠道配置已有各自的读写链路，独立表避免改动既有 PATCH 逻辑，也便于平台侧统一管理。
- `abuse_events` 只追加不更新，需在文档中登记清理策略（建议随日志保留期清理，初期可不清理）。

### 4.2 既有表变更

`chat_records` 需补一个聚合索引（预算/次数查询走它）：

```sql
CREATE INDEX IF NOT EXISTS idx_chat_records_tenant_user_time
    ON chat_records(tenant_id, user_id, created_at DESC);
```

### 4.3 db_update.yaml 登记

按 [.claude/rules/database_dev.md](../../.claude/rules/database_dev.md) 在 `deploy/db_update.yaml` 追加一个增量块（建两表 + 建索引，全部幂等），同时更新 `deploy/init-postgres.sql` 与 `docs/system/database_system_table.md`。

---

## 5. 执行引擎

### 5.1 模块结构

```
src/services/anti_abuse/
├── __init__.py
├── rules.py          # 规则目录定义（id、名称、参数 schema、默认参数、软规则提示词模板）
├── service.py        # AntiAbuseService：load_policies / check_request / record_event
├── windows.py        # 聚合查询（chat_records SUM/COUNT）+ 重复指纹窗口计数
└── prompts.py        # 软规则的标准提示词模块文本（创作类拒绝、批量遍历拒绝）
```

惰性单例 `get_anti_abuse_service()`，模块顶层零副作用（遵守 backend_dev.md 包初始化规范）。

### 5.2 预算/次数检查（credit_budget / request_count）

- **判定数据源直接查 `chat_records`**：`SELECT COALESCE(SUM(credit_cost),0), COUNT(*) FROM chat_records WHERE tenant_id=%s AND user_id=%s AND created_at >= NOW() - interval '...'`。不维护 Redis 增量计数器——计数器跨 Gunicorn worker 与重启的一致性成本高，而 `chat_records` 本来就是逐请求落账的权威账本（billing_audit.md 通路①②③最终都落这张表），一次带索引的聚合查询在几百并发用户量级下开销可接受。
- **时间窗语义**：滚动窗口（NOW()-period），不是自然日边界，防止用户卡 0 点重置。
- **渠道侧的 user 归属**：渠道会话的 `user_id` 取该渠道用户映射后的系统 user_id（`chat_records` 现状即如此），外部客户未映射时退化为按 `session_id` 维度检查（detail 中标注）。
- **规则绑定缓存**：policies 读取走 `redis_client`，键前缀注册到 `CacheKeys`（如 `abuse_policies:{tenant_id}:{scope_type}:{scope_id}`），TTL 60s；绑定变更时主动失效。多 worker 一致性靠 Redis + 短 TTL，符合缓存规范。
- `source_type` 过滤：预算统计**只统计会话执行类记录**（`source_type` ∈ chat / 渠道来源 / runner 来源），排除 `admin_llm_ops` 等管理后台记录——管理员操作不该计入用户预算。

### 5.3 单请求输出预算（max_output_per_request）

积分在请求执行前无法精确预估（取决于模型实际输出），故用 **token 预算近似**：

- 入口检查通过后，把 `max_completion_tokens`、`max_iterations` 注入本次执行上下文（web 路由与 `channels/session.py` 传给 `process_message_sync`，Runner 链路经 contracts 传递）。
- AgentEngine 主循环在迭代中检查累计 completion_tokens，超限则终止执行并返回已生成内容 + 「已达本次输出上限」说明，`abuse_events` 记 action=terminated。
- 依赖现状：agent_engine 已有迭代次数上限机制，本规则是在其上叠加 token 维度，改动集中在 engine 的预算检查点。

### 5.4 重复任务检测（duplicate_task）

- **指纹**：`sha256(归一化消息)`，归一化 = 去除首尾空白 + 全角转半角 + 去除标点与空白 + 截断前 N 字符（N=200）。精确匹配而非相似度匹配——simhash/编辑距离在短消息上误杀率高，而滥用场景（改缓存、改数字重写）通常核心短语不变；精确指纹 + 合理窗口已能覆盖 1.1 的实例。Phase 1 用精确指纹，观察模式数据若显示绕过率高再升级。
- **存储**：`redis_client`，键 `abuse:dup:{tenant_id}:{scope_id}:{hash}`，值为窗口内出现次数，TTL = `window_minutes`。Redis 降级时**跳过该规则并记 warning**（不因 Redis 故障阻断正常对话）。
- 触发阈值 `max_similar`：窗口内相同指纹第 N 次出现时拦截。

### 5.5 工具级封禁（no_file_generation）

- 预置封禁清单：文件产出类工具（word/excel/ppt/pdf 生成、cp 交付等），具体清单在 `rules.py` 内维护并注明工具 name。
- **实现位置：请求级 denylist 注入 `ToolExecutionContext`**，`ToolExecutor` 执行前检查工具 name 是否在 denylist，命中则返回结构化失败（「当前会话已开启禁止生成文件限制」），LLM 收到工具失败后自然改用文字回答。不改全局工具装配（`assembly.py`），因为该规则是按用户/渠道会话级的。
- LLM 侧无需感知：工具失败的 error message 会引导模型走文字路径。

---

## 6. 软规则：标准提示词模块注入

`no_creation` / `no_batch_enumeration` 的本质是行为约束，只能靠提示词，但要做**系统级统一下发**，替代 PM 逐智能体手改：

- `prompts.py` 内置标准模块文本（以 PM 现有文案为底稿：创作类一律礼貌拒绝不读知识库不产文件；批量遍历礼貌说明引导分批或转人工；不影响正常销售话术情景化演绎）。
- **注入位置**：命中绑定的会话，在 system prompt 尾部追加「服务范围约束」段（web 路径 `agent.py` 的 `_build_system_prompt`，渠道路径 `channels/session.py` 组装处，Runner 链路经 runtime 装配）。注入内容 = 所有命中绑定的软规则模块拼接 + 租户自定义补充文本。
- **明确定位**：这是辅助防线，可被绕过，绕过后由硬规则（预算/重复/输出预算）兜底。两层合起来才是完整闭环。
- 好处：约束文本集中维护一处，所有智能体生效；租户管理员无需找开发者改提示词；智能体原生提示词不再需要塞防滥用内容。

---

## 7. 拦截点与调用链

### 7.1 统一入口签名

```python
decision = await anti_abuse.check_request(
    tenant_id=..., user_id=..., session_id=...,
    channel_type="wecom_kf",           # web 传 "web"
    scope_channel_config_id=...,       # 渠道绑定匹配用，web 为 None
    user_message=...,                  # 重复检测 + 事件快照
)
# Decision: allowed / blocked(rule_id, message) / warn(rule_id)
```

内部完成：加载绑定（缓存）→ 匹配 user 绑定 + channel 绑定 → 逐硬规则检查 → 写事件 → 返回决策。同时返回该请求应注入的软规则提示词模块与运行时预算（供执行层装配）。

### 7.2 三个拦截点

| 入口 | 位置 | 说明 |
|------|------|------|
| Web 对话 | `src/main.py` `/api/chat`（:820）与 `/api/chat/stream`（:1184） | handler 开头预检；blocked 时返回 429 + 结构化错误（含 debug 字段，遵守后端错误规范） |
| 渠道消息 | `src/channels/session.py` 执行入口（`process_message_sync` 调用前，:1082 附近） | 所有渠道（wecom_kf/wecom/dingtalk/feishu）统一走 session.py 执行链，是渠道侧最佳单点；blocked 时发固定话术并跳过执行 |
| Runner 执行链路 | `src/services/agent_runner/` runtime 入口 | 渠道接入 Runner 后对话执行走独立 Runner，实施时核实其与 session.py 的关系：若渠道流量已全部经 Runner，则在 Runner 请求装配处检查，避免双检 |

**实施注意**：拦截点必须保证同一请求只检查一次（web 直连 vs 渠道 Runner 两条链路互斥，不会双检；实施时以实际调用图核实为准）。

### 7.3 计费后置闭环

预算检查的数据源是 `chat_records`，其落账由既有计费链路完成（billing_audit.md 通路①②③），本功能**不改计费逻辑**，只做读侧聚合。唯一要求：确保所有对话入口的记录都带正确的 `tenant_id` + `user_id` + `credit_cost`（现状已满足）。

---

## 8. API 与前端

### 8.1 后端 API（`src/api/anti_abuse.py` 或并入 `src/saas/api/`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/saas/abuse/presets` | 返回规则目录（id/名称/说明/参数 schema/默认值），前端勾选界面渲染用 |
| GET | `/api/saas/users/{user_id}/abuse_policies` | 读用户绑定 |
| PUT | `/api/saas/users/{user_id}/abuse_policies` | 整存整取绑定（mode + policies 数组） |
| GET | `/api/saas/channels/{config_id}/abuse_policies` | 读渠道配置绑定 |
| PUT | `/api/saas/channels/{config_id}/abuse_policies` | 存渠道绑定 |
| GET | `/api/saas/abuse_events?scope_type=&scope_id=&page=` | 触发记录分页查询（租户管理员排查用） |

- 全部走 `require_admin`（租户管理员），租户隔离按 SaaS 规范由 `TenantContextMiddleware` 保证；平台管理员代操作走 `X-Tenant-Id`。
- 参数校验：PUT 时对照 `presets` 的参数 schema 校验，非法参数 400。

### 8.2 前端（租户前台）

| 页面 | 改动 |
|------|------|
| `TenantUserManager.vue` | 用户行操作加「防滥用设置」：弹框（BaseModal，md）内规则勾选列表 + 参数输入 + observe/enforce 切换；列表加规则状态标识列 |
| `ChannelConfig.vue` | 渠道配置详情内加「防滥用」区块/Tab，复用同一勾选组件 |
| 新组件 `AbusePolicyEditor.vue` | 勾选 + 参数编辑共用组件（两个页面复用），数据源为 presets API |
| 触发记录 | 先不做独立页面；`abuse_events` 查询入口放在防滥用设置弹框内「查看触发记录」（分页列表）。独立页面视 Phase 2 需求再定 |

前端遵循 Base* 组件与语义 token 规范，构建验证 `cd frontend && npm run build`。

---

## 9. 分阶段实施

### Phase 1：引擎 + 观察模式（先跑数据，不拦截）

1. 两张新表 + 索引（db_update.yaml + init-postgres.sql + database_system_table.md）
2. `src/services/anti_abuse/` 引擎：规则目录、绑定加载（含缓存）、check_request（observe 全放行）、事件落库
3. 三个拦截点接入（只调 check_request，不执行拦截分支）
4. API 5 个 + `AbusePolicyEditor` 组件 + 两个页面接入
5. 触发记录查询

**Phase 1 的产出物就是阈值决策依据**：跑 2~4 周观察模式后，用 `abuse_events` + `chat_records` 聚合数据回答「多短时间、多少积分」——不再靠拍脑袋讨论。期间 PM 可把「销售助手」提示词中的手工防滥用条款迁移为 `no_creation` 等软规则绑定。

### Phase 2：拦截模式启用

1. enforce 分支生效（入口拦截 + 运行时预算中断 + 工具封禁）
2. 拦截话术可按租户配置（渠道默认话术由租户管理员在绑定界面设置）
3. 由租户管理员按用户/渠道逐个开启 enforce（全局开关默认 observe）

### Phase 3（可选，视 Phase 2 效果）

- 预算告警线通知租户管理员（站内/渠道消息）
- 异常检测（相对用户自身基线的突增告警），防绕过精确指纹的高级重复模式

---

## 10. 风险与副作用控制

| 风险 | 控制 |
|------|------|
| 合法重度用户被误伤 | 默认 observe；阈值由租户按用户粒度设置；告警线先行 |
| 预算查询拖慢入口 | 聚合查询走新索引；规则绑定 60s 缓存；查询超时 fail-open（放行 + 记 warning），限额失败不阻断业务 |
| Redis 故障影响重复检测 | 该规则降级跳过（§5.4），其余规则不受影响（数据源是 PG） |
| 渠道 blocked 话术影响客服体验 | 话术文案租户可配；enforce 默认不开 |
| 软规则提示词与智能体既有提示词冲突 | 注入段统一放在 system prompt 尾部「服务范围约束」标题下，语义为追加限制不覆盖原有行为 |
| 多 worker 内存隔离 | 绑定缓存与重复指纹全部走 `redis_client`（降级内存仅作兜底并记 warning），不引入进程内跨请求状态 |
| 双计/漏检 | 拦截点唯一性在实施时以实际调用图核实（§7.2），单测覆盖 web/渠道两条链路各只触发一次 |

---

## 11. 测试要点

- 单元：规则参数校验、窗口聚合 SQL、指纹归一化、Decision 组合（user+channel 并集）、observe/enforce 分支
- 集成：`/api/chat` 与渠道 session 链路各注入 mock 绑定，验证 blocked 不触达 LLM、事件落库、预算聚合排除 admin source_type
- 边界：Redis 不可用降级、绑定缓存失效时序、时间窗滚动边界、渠道外部用户未映射退化为 session 维度
