# 系统级防滥用与限额设计（Anti-Abuse Policy）

> 状态：📋 设计 v0.2，评审意见已合入
> 作者：2026-10-10；v0.2 修订：2026-10-11（修订记录见文末）
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

1. **硬闸与软约束分层**——确定性规则（预算、工具封禁）由系统代码强制执行，不依赖 LLM 自觉；语义类规则（拒绝创作/批量遍历）本质是提示词约束，定位为辅助防线，由系统统一下发注入，替代逐智能体手改。
2. **按成本判断，不按内容判断**——行业通行做法（LiteLLM budget / OpenAI Tier 限流）是用「花费 + 频率」这套与内容无关的确定性指标兜底，内容理解只做辅助。
3. **租户自治 + 作用域细化**——规则由租户管理员按「用户」「渠道配置」两个粒度勾选，因为「短时间大量积分」存在合法场景，是否收紧由最了解业务的租户决定。
4. **先观察后拦截**——所有规则支持「观察模式」（只记录事件不拦截），用真实数据校准阈值后再切「拦截模式」。观察模式并非只看不动——配合事件通知与人工禁用用户即可闭环运行（§3.4），enforce 自动拦截是增强而非前置条件。

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
| `credit_budget` | 时间窗积分预算 | 硬 | `period`: hour/day（day）；`warn_threshold`: 0.8×；`hard_threshold`: 必填 | 请求入口查 `chat_records` 聚合（§5.2） |
| `no_file_generation` | 禁止生成新文件 | 硬 | 无参数（预置封禁工具清单） | 工具级请求内 denylist（§5.3） |
| `no_creation` | 禁止创作类任务 | 软 | `custom_text`: 可选补充说明 | 提示词模块注入（§6） |
| `no_batch_enumeration` | 禁止批量遍历 | 软 | 同上 | 提示词模块注入（§6） |

默认参数仅是初值，租户管理员可改。**`credit_budget` 的具体阈值不做全局预设**——这正是「需要讨论多短时间、消耗多少积分」的解法：Phase 1 全部默认观察模式跑真实数据，用数据定阈值（§8）。

租户兜底绑定（§3.2）需要平台初值才能在租户未自定义时隐式生效：初值放 `configs/config.yaml`（如 `anti_abuse.tenant_default`：`period: day` + `hard_threshold` 初值），上线时按真实数据分布校准一次；租户管理员在防滥用中心保存自定义后覆盖。

**已评估并放弃的规则**：`duplicate_task`（重复任务检测）——精确指纹拦不住改写/翻译重问（恶意用户换措辞即可绕过），语义相似度需在入口引入 embedding 调用（延迟 + 新计费点）且易误伤「第一次没得到满意答案换个问法的正常用户」；其目标场景（变体重复重写）本质是短时间成本问题，已由 `credit_budget` 按成本拦截，与措辞和语言无关。同样评估后放弃 `max_output_per_request`（单请求输出/积分上限）——评审结论（2026-10-11）：数据分析等智能体单次请求合法消耗巨大且客户可接受，单请求上限会误伤此类正常用法；单请求失控场景由预算预检拦截后续请求 + 软规则提示词（§6）兜底。

### 3.2 绑定模型

```
规则预置（代码定义）          绑定（租户管理员勾选，存 DB）
┌────────────────┐          ┌──────────────────────────────┐
│ credit_budget   │ ◄─ 勾选 ─│ scope_type = tenant（兜底）   │
│ no_file_gen...  │          │   scope_id  = tenant_id      │
│ no_creation     │          │   （防滥用中心页配置，对未单独 │
│ no_batch_...    │          │    绑定的用户/入口生效）      │
└────────────────┘          │                              │
                             │ scope_type = user            │
                             │   scope_id  = user_id        │
                             │   （内部用户，用户管理页勾选）│
                             │                              │
                             │ scope_type = channel         │
                             │   scope_id  = config_id      │
                             │   （渠道配置页勾选，覆盖该配置 │
                             │    下现有 + 未来全部渠道用户）│
                             │ policies = [                │
                             │   {rule_id, params}          │
                             │ ]                            │
                             │ mode（行级）: observe|enforce │
                             └──────────────────────────────┘
```

- **作用域三种**：`tenant`（兜底）、`user`（内部用户，`users.user_id`，web 对话）和 `channel`（租户渠道配置，`tenant_channel_configs.config_id`，即某渠道 × 某数字员工，覆盖该入口下现有 + 未来全部渠道用户）。
- **tenant 兜底绑定**（v0.2 评审新增）：修复「未绑定的用户/入口零监控、通知闭环永不触发」的覆盖空洞——事故场景恰是未知用户突然滥用，管理员无法预先逐个绑定。兜底对全部未命中更具体绑定的用户/入口生效（含 web 内部用户与渠道用户）；租户未保存自定义行时按 `configs/config.yaml` 平台默认隐式生效（§3.1）。UI 入口在防滥用中心页面（§8.2）。
- **匹配优先级**：`user` 绑定 > `channel` 绑定 > `tenant` 兜底。web 对话先查该内部用户的 user 绑定，未命中走 tenant 兜底；渠道对话先查其渠道配置的 channel 绑定（不查 user 绑定），未命中走 tenant 兜底。渠道用户没有 user 绑定的勾选入口（用户管理页不放渠道用户规则 UI，防止两处口径冲突），因此不存在多绑定并集/合并逻辑。
- **为何渠道流量按渠道配置绑定**：渠道用户由 `auto_register` 自动注册（无租户管理员参与），按用户勾选永远只能覆盖存量、管不住未来用户；「渠道 × 数字员工」是治理的天然单位——「禁止这个客服入口生成文件」本就是入口级决策，勾选一遍覆盖全部渠道用户。
- **mode 行级配置**（对应 §4.1 DDL 的 `mode` 列，整绑定统一）：observe（只记事件 + 通知）/ enforce（真拦截），同一绑定下全部规则共用一个 mode，不逐规则混搭——取舍：无法「预算 observe + 工具封禁 enforce」组合，确需混搭时拆分作用域。整体功能默认全 observe。**mode 仅对硬规则有意义**：软规则的执行方式是「绑定即注入提示词」，没有 observe/enforce 之分——不注入等价于不绑定。
- 规则清单是代码内置的（新增规则 = 发版），绑定数据只有「勾选 + 参数」，不需要规则 CRUD 界面，避免过度设计。

### 3.3 超限行为

| 检查点 | 行为 |
|--------|------|
| 入口预检拦截（预算） | 请求不进入 LLM 执行；渠道侧回复一条固定礼貌话术（可按租户配置），web 侧返回结构化错误；写 `abuse_events` |
| 告警线（预算 warn_threshold） | 不拦截，写 `abuse_events`（action=warned）并通知租户管理员（§3.4） |
| 观察模式 | 一切放行，写 `abuse_events`（action=logged）；预算命中阈值时同样触发通知（§3.4） |

### 3.4 观察模式闭环：通知 + 防滥用中心 + 禁用用户

即使不开 enforce，观察模式也能形成完整的管理闭环：

1. **触发通知**：`credit_budget` 命中 warn_threshold 或 hard_threshold（observe 下不拦截，仅记 `abuse_events`）时——Phase 1 由定时扫描任务发现（§5.2，分钟级延迟，观察场景可接受），Phase 2 起入口预检实时发现——通过租户管理员通知基础设施（`src/notifications/`）发 `abuse.rule_triggered` 事件（事件类型已在通知 Phase 1 预定义）。站内信 + 已配置的 IM/邮箱四渠道外发；同一 `(tenant_id, user_id, rule_id)` 24h 内只通知一次（event_key 幂等去重），避免刷屏。
2. **防滥用中心·用量监控**（`AbuseCenter.vue`，§8.2）：通知内链接跳转（携带 user_id 定位），按用户展示观察期内的积分消耗明细：
   - 哪个用户、什么时间段、共消耗多少积分（`chat_records` 聚合，窗口内 `SUM(credit_cost)`）
   - 逐条请求明细：时间、问题/任务内容（`chat_records.user_message`）、单条积分
3. **处置（两层粒度互补）**：管理员认定滥用后——
   - **精准处置**：禁用个别用户（改 `users.status`，防滥用中心用量监控列表行内「停用」，渠道 + 内部用户均可），该用户无法继续对话、消耗立即停止，不影响同入口其他客户；发现误禁在「禁用记录」Tab 同一开关一键恢复（禁用记录按 `users.status = suspended` 全量列出，与该用户当前是否仍在消耗无关，解决「第一天禁用、第二天在用量页找不到恢复入口」的问题）
   - **入口处置**：在该渠道配置页把规则切 enforce，整个入口拦截

该闭环使「观察模式 + 人工禁用」即可投产使用，enforce 自动拦截定位为可选增强（Phase 2），而非闭环的前置条件。

**配套依赖**：渠道消息执行链目前不检查 `users.status`，需在渠道入口补检查——suspended 用户直接回固定话术（与防滥用 blocked 话术同机制），否则禁用对渠道流量不生效。

---

## 4. 数据设计

### 4.1 新增表

```sql
-- 规则绑定表（一个作用域一行）
CREATE TABLE IF NOT EXISTS abuse_policies (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    scope_type TEXT NOT NULL,            -- tenant | user | channel
    scope_id TEXT NOT NULL,              -- tenant_id（兜底行）/ users.user_id / tenant_channel_configs.config_id
    mode TEXT DEFAULT 'observe',         -- observe | enforce（行级，整绑定统一）
    policies JSONB DEFAULT '[]',         -- [{rule_id, params:{...}}]；空数组即等效停用，不设 enabled 列
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
    user_id TEXT,                        -- 触发用户（渠道绑定下为实际消耗的渠道用户；未映射外部用户为 NULL）
    rule_id TEXT NOT NULL,
    action TEXT NOT NULL,                -- logged | warned | blocked | terminated
    session_id TEXT,
    channel_type TEXT,                   -- wecom_kf / wecom / web / ...
    detail JSONB,                        -- 触发时快照：阈值、实际值等
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_abuse_events_tenant_time
    ON abuse_events(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_abuse_events_scope
    ON abuse_events(tenant_id, scope_type, scope_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_abuse_events_user
    ON abuse_events(tenant_id, user_id, created_at DESC);
```

要点：

- 单表单行绑定（`(tenant_id, scope_type, scope_id)` 唯一，含 tenant 兜底行，scope_id = tenant_id），不按规则拆行，勾选界面整存整取；不设 `enabled` 列——`policies` 空数组即等效停用，避免两套开关语义重叠。
- 不在 `users` / `tenant_channel_configs` 上加列：用户和渠道配置已有各自的读写链路，独立表避免改动既有 PATCH 逻辑，也便于平台侧统一管理。
- `abuse_events.user_id` 为一等列：渠道绑定下 scope_id 是 config_id，通知幂等键 `(tenant_id, user_id, rule_id)` 与「按用户查触发记录」都依赖它，不能只埋在 detail JSONB 里。
- **事件写入按同键去重**：同一 `(tenant_id, user_id, rule_id, action)` 1 小时内已存在则跳过写入——定时扫描每轮命中、enforce 下连续 blocked（滥用者高频发消息）都会刷表，靠去重兜住。表保持只追加不更新，需在文档中登记清理策略（建议随日志保留期清理，初期可不清理）。

### 4.2 既有表变更

`chat_records` 需补一个聚合索引（预算查询走它）：

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
├── service.py        # AntiAbuseService：load_policies / evaluate_usage / record_event（含 chat_records 聚合查询）
├── scanner.py        # 定时扫描任务（Phase 1 观察闭环）：聚合窗口用量 → 匹配绑定 → 写事件 + 通知
└── prompts.py        # 软规则的标准提示词模块文本（创作类拒绝、批量遍历拒绝）
```

惰性单例 `get_anti_abuse_service()`，模块顶层零副作用（遵守 backend_dev.md 包初始化规范）。

### 5.2 预算评估（credit_budget，Phase 1 定时扫描与 Phase 2 入口预检共用）

- **判定数据源直接查 `chat_records`**：`SELECT COALESCE(SUM(credit_cost),0) FROM chat_records WHERE tenant_id=%s AND user_id=%s AND created_at >= NOW() - interval '...'`。不维护 Redis 增量计数器——计数器跨 Gunicorn worker 与重启的一致性成本高，而 `chat_records` 本来就是逐请求落账的权威账本（billing_audit.md 通路①②③最终都落这张表），一次带索引的聚合查询在几百并发用户量级下开销可接受。
- **时间窗语义**：滚动窗口（NOW()-period），不是自然日边界，防止用户卡 0 点重置。
- **渠道侧的 user 归属**：渠道会话的 `user_id` 取该渠道用户映射后的系统 user_id（`chat_records` 现状即如此）。渠道绑定下预算按**该入口内每个渠道用户各自累计**（SQL 仍按 user_id 聚合，不是整个入口共享额度）。外部客户未映射时无 user_id：预算规则跳过（无法按用户累计），其余规则（工具封禁、软规则注入）仍按渠道绑定生效。
- **绑定匹配优先级**：`user` 绑定 > `channel` 绑定 > `tenant` 兜底（§3.2）。渠道用户 → 所属渠道配置绑定的映射经 `users.source` / `channel_sessions` 反查，实施时核实取数路径。
- `source_type` 过滤：预算统计**只统计会话执行类记录**（`source_type` ∈ chat / 渠道来源 / runner 来源），排除 `admin_llm_ops` 等管理后台记录——管理员操作不该计入用户预算。
- **Phase 1 定时扫描（观察闭环的驱动方式，v0.2 评审简化）**：scanner 任务每 5~10 分钟一轮（APScheduler / background_runner），对窗口内有消耗的用户逐个评估，命中阈值写 `abuse_events`（1h 同键去重，§4.1）+ 通知（24h 节流）。观察数据源本来就是 `chat_records`，扫描即可完整交付「通知 + 用量页 + 禁用」闭环，Phase 1 因此**不接入任何执行链拦截点**；warn 延迟为分钟级，对观察场景无实质影响。
- **规则绑定缓存（仅 Phase 2 需要）**：入口预检每请求都会读绑定，policies 读取走 `redis_client`，键前缀注册到 `CacheKeys`（如 `abuse_policies:{tenant_id}:{scope_type}:{scope_id}`），TTL 60s；绑定变更时主动失效。多 worker 一致性靠 Redis + 短 TTL，符合缓存规范。Phase 1 扫描任务低频运行，直接读 DB，无需缓存。

### 5.3 工具级封禁（no_file_generation）

- 预置封禁清单：文件产出类工具（word/excel/ppt/pdf 生成、cp 交付等），具体清单在 `rules.py` 内维护并注明工具 name。
- **实现位置：请求级 denylist 注入 `ToolExecutionContext`**，`ToolExecutor` 执行前检查工具 name 是否在 denylist，命中则返回结构化失败（「当前会话已开启禁止生成文件限制」），LLM 收到工具失败后自然改用文字回答。不改全局工具装配（`assembly.py`），因为该规则是按用户/渠道会话级的。
- LLM 侧无需感知：工具失败的 error message 会引导模型走文字路径。

---

## 6. 软规则：标准提示词模块注入

`no_creation` / `no_batch_enumeration` 的本质是行为约束，只能靠提示词，但要做**系统级统一下发**，替代 PM 逐智能体手改（实施阶段：Phase 3，硬规则闭环先行）：

- `prompts.py` 内置标准模块文本（以 PM 现有文案为底稿：创作类一律礼貌拒绝不读知识库不产文件；批量遍历礼貌说明引导分批或转人工；不影响正常销售话术情景化演绎）。
- **注入位置**：命中绑定的会话，在 system prompt 尾部追加「服务范围约束」段（web 路径 `agent.py` 的 `_build_system_prompt`，渠道路径 `channels/session.py` 组装处，Runner 链路经 runtime 装配）。注入内容 = 所有命中绑定的软规则模块拼接 + 租户自定义补充文本。
- **明确定位**：这是辅助防线，可被绕过，绕过后由硬规则（预算）兜底。两层合起来才是完整闭环。
- 好处：约束文本集中维护一处，所有智能体生效；租户管理员无需找开发者改提示词；智能体原生提示词不再需要塞防滥用内容。

---

## 7. 拦截点与调用链（Phase 2 范围）

> Phase 1 观察闭环由定时扫描驱动（§5.2），**不接入任何拦截点**；本节的入口预检与 enforce 分支全部在 Phase 2 实施。

### 7.1 统一入口签名

```python
decision = await anti_abuse.check_request(
    tenant_id=..., user_id=..., session_id=...,
    channel_type="wecom_kf",           # web 传 "web"
    scope_channel_config_id=...,       # 渠道绑定匹配用，web 为 None
    user_message=...,                  # 事件快照
)
# Decision: allowed / blocked(rule_id, message) / warn(rule_id)
```

内部完成：加载绑定（缓存）→ **匹配**（web：按 user_id 查 user 绑定；渠道：按 config_id 查 channel 绑定，不查 user 绑定；均未命中再查 tenant 兜底）→ 逐硬规则检查 → 写事件 → 返回决策。同时返回该请求应注入的软规则提示词模块（供执行层装配）。

### 7.2 三个拦截点

| 入口 | 位置 | 说明 |
|------|------|------|
| Web 对话 | `src/main.py` `/api/chat`（:820）与 `/api/chat/stream`（:1184） | handler 开头预检；blocked 时返回 429 + 结构化错误（含 debug 字段，遵守后端错误规范） |
| 渠道消息 | `src/channels/session.py` 执行入口（`process_message_sync` 调用前，:1082 附近） | 所有渠道（wecom_kf/wecom/dingtalk/feishu）统一走 session.py 执行链，是渠道侧最佳单点；blocked 时发固定话术并跳过执行 |
| Runner 执行链路 | `src/services/agent_runner/` runtime 入口 | 渠道接入 Runner 后对话执行走独立 Runner，实施时核实其与 session.py 的关系：若渠道流量已全部经 Runner，则在 Runner 请求装配处检查，避免双检 |

**实施前置**：先画清渠道流量实际调用图（`session.py` vs Runner——企业微信应用已接 Runner，session.py 是否仍是全渠道统一单点直接决定拦截点怎么接），确认单点后再动工；拦截点必须保证同一请求只检查一次（web 直连 vs 渠道 Runner 两条链路互斥，不会双检）。blocked 固定话术在 Runner 链路上的回发路径一并确认。

### 7.3 计费后置闭环

预算检查的数据源是 `chat_records`，其落账由既有计费链路完成（billing_audit.md 通路①②③），本功能**不改计费逻辑**，只做读侧聚合。唯一要求：确保所有对话入口的记录都带正确的 `tenant_id` + `user_id` + `credit_cost`（现状已满足）。

---

## 8. API 与前端

### 8.1 后端 API（`src/api/anti_abuse.py` 或并入 `src/saas/api/`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/saas/abuse/presets` | 返回规则目录（id/名称/说明/参数 schema/默认值），前端勾选界面渲染用 |
| GET | `/api/saas/abuse/policies?scope_type=tenant\|user\|channel&scope_id=` | 读绑定；tenant 兜底可省 scope_id，无自定义行时返回平台默认生效值（供 UI 展示「当前生效」） |
| PUT | `/api/saas/abuse/policies?scope_type=&scope_id=` | 整存整取绑定（mode + policies 数组），三种作用域统一（原 user / channel 两对 API 合并） |
| GET | `/api/saas/abuse/events?scope_type=&scope_id=&user_id=&page=` | 触发记录分页查询（租户管理员排查用） |
| GET | `/api/saas/abuse/usage?window=hour\|day&user_id=` | 窗口内积分消耗 top 用户汇总；带 user_id 返回该用户逐条请求明细（时间/内容/积分，`chat_records` 读侧；原汇总/明细两 API 合并） |
| GET | `/api/saas/users?status=suspended&source=` | 禁用记录列表（`UserDB.list_users` 扩展 status + source 筛选参数） |
| PUT | `/api/saas/users/{user_id}/status` | 用户停用/启用（内部 + 渠道用户，改 `users.status`，写 behavior_logs 审计） |

评审简化（v0.2）：原 users / channels 两对绑定 API 与 usage 汇总 / 明细两 API 已分别合并为上表统一端点；用户停用/启用补一个 status 更新接口（现有接口无此能力）；除此之外不新增处置接口。

- 全部走 `require_admin`（租户管理员），租户隔离按 SaaS 规范由 `TenantContextMiddleware` 保证；平台管理员代操作走 `X-Tenant-Id`。
- 参数校验：PUT 时对照 `presets` 的参数 schema 校验，非法参数 400。

### 8.2 前端（租户前台）

| 页面 | 改动 |
|------|------|
| 新页面 `AbuseCenter.vue`（防滥用中心） | 按「添加业务页面」规范注册 `configs/page_metadata.yaml`（status 先 planned，发布后改 published）。顶部「租户默认策略」卡片（`AbusePolicyEditor`，说明文案：对未单独绑定的用户/入口生效，无自定义时按平台默认）+ 三个 Tab：**用量监控**（窗口 top 用户 + 单用户逐条明细 + 搜索 + 行内停用/启用）、**禁用记录**（全租户 suspended 用户列表，渠道 + 内部、来源筛选、禁用时间/操作人、启用按钮、「查看消耗」跳转）、**触发记录**（`abuse_events` 分页）。通知铃铛与 IM/邮件通知链接直达（携带 user_id 定位到明细） |
| `TenantUserManager.vue` | 现有内部用户列表 + 防滥用设置（`AbusePolicyEditor`）+ 停用/启用开关；**不做渠道用户双 Tab**（v0.2 评审砍掉，渠道用户处置收敛到防滥用中心） |
| `ChannelConfig.vue` | 渠道配置详情内加「防滥用」区块/Tab，复用 `AbusePolicyEditor`（覆盖该配置下现有 + 未来全部渠道用户） |
| 新组件 `AbusePolicyEditor.vue` | 勾选 + 参数编辑组件，三处复用（防滥用中心 / 用户管理页 / 渠道配置页），数据源为 presets API |

「禁用记录」Tab 解决的评审问题：管理员第一天禁用的渠道用户，第二天不再出现在用量监控 top 列表里，恢复入口不能依赖用量页——禁用记录按 `users.status = suspended` 全量列出（与是否仍在消耗无关），带启用开关与审计信息（behavior_logs）。

禁用记录数据源：`UserDB.list_users` 现过滤 `source IS NULL` 且无 status 筛选，需扩展 status + source 参数（仅此查询扩展，不做完整渠道用户治理 Tab）；外部客户服务页（ExternalCustomerService）保持只读服务视角，不放治理操作。

前端遵循 Base* 组件与语义 token 规范，构建验证 `cd frontend && npm run build`。

---

## 9. 分阶段实施

### Phase 1：观察闭环（定时扫描，不接执行链）

范围：硬规则评估（observe）+ tenant 兜底绑定 + 定时扫描 + 通知 + 防滥用中心页面。软规则不在本阶段（见 Phase 3）。**不接入任何执行链拦截点**（唯一动执行链的点：渠道入口补 `users.status` 检查）。

1. 两张新表 + 索引（db_update.yaml + init-postgres.sql + database_system_table.md）
2. `src/services/anti_abuse/` 引擎：硬规则目录、绑定加载、窗口聚合评估、事件落库（含 1h 同键去重）
3. 定时扫描任务（5~10 分钟一轮）：`chat_records` 聚合 → 绑定匹配（user > channel > tenant 兜底，兜底无行按平台默认）→ 写事件 + 通知（`abuse.rule_triggered`，24h 节流）
4. 渠道入口补 `users.status` 检查（suspended 回固定话术）——禁用处置对渠道流量生效的前提
5. API 7 个（§8.1）+ 前端：`AbuseCenter.vue`（租户默认策略卡片 + 用量监控/禁用记录/触发记录三 Tab）、`TenantUserManager` 内部用户改造、`ChannelConfig` 防滥用区块、`AbusePolicyEditor` 组件；`UserDB.list_users` 扩展 status + source 筛选；presets 目录 Phase 1 仅含硬规则
6. 通知接入 + 用户停用/启用 status 接口

**Phase 1 的产出物就是阈值决策依据**：跑 2~4 周观察模式后，用 `abuse_events` + `chat_records` 聚合数据回答「多短时间、多少积分」——不再靠拍脑袋讨论。**Phase 1 结束即具备完整管理闭环**（通知 + 用量页 + 禁用用户，§3.4），不依赖后续阶段。

### Phase 2：拦截模式启用（可选增强）

1. **实施前置**：画清渠道流量实际调用图（`session.py` vs Runner），确定拦截单点与单次检查保证（§7.2）
2. `check_request` 接入三个拦截点（§7）+ enforce 分支生效（入口拦截 + 工具封禁 denylist）
3. 拦截话术可按租户配置（渠道默认话术由租户管理员在绑定界面设置）
4. 绑定缓存接入（redis_client 60s TTL，§5.2——仅本阶段需要）
5. 由租户管理员按 tenant / 内部用户 / 渠道入口逐个开启 enforce（全局开关默认 observe）

### Phase 3：软规则（标准提示词模块注入）

1. `rules.py` 补软规则目录（`no_creation` / `no_batch_enumeration`）与 `prompts.py` 标准提示词模块文本；presets 目录扩展软规则，勾选界面天然支持（API 与组件 Phase 1 已就绪）
2. 注入点接线：web 路径 `agent.py` 的 `_build_system_prompt`、渠道路径 `channels/session.py` 组装处、Runner 链路 runtime 装配（§6）
3. PM 将「销售助手」提示词中的手工防滥用条款迁移为 `no_creation` 等软规则绑定，验证统一下发效果

---

## 10. 风险与副作用控制

| 风险 | 控制 |
|------|------|
| 合法重度用户被误伤 | 默认 observe；阈值由租户按用户/渠道入口粒度设置；告警线先行；渠道级 enforce 开启前先用「禁用个别用户」精准处置 |
| 用户误禁用 | 停用/启用同一开关，一键恢复；status 变更走操作审计（behavior_logs）可追溯 |
| 预算查询拖慢入口 | 聚合查询走新索引；规则绑定 60s 缓存；查询超时 fail-open（放行 + 记 warning），限额失败不阻断业务 |
| Redis 故障影响绑定缓存 | 缓存失效时回源 DB 查询并记 warning，检查功能不中断（数据源是 PG） |
| 渠道 blocked 话术影响客服体验 | 话术文案租户可配；enforce 默认不开 |
| 事件表膨胀（扫描每轮命中 / enforce 连续 blocked 刷表） | 同键 `(tenant_id, user_id, rule_id, action)` 1h 去重（§4.1） |
| 扫描任务漏跑/失败 | 任务失败告警 + 下一轮自动补扫；评估幂等（重算不重复落事件） |
| 软规则提示词与智能体既有提示词冲突 | 注入段统一放在 system prompt 尾部「服务范围约束」标题下，语义为追加限制不覆盖原有行为 |
| 多 worker 内存隔离 | 绑定缓存走 `redis_client`（降级内存仅作兜底并记 warning），不引入进程内跨请求状态；Phase 1 无缓存（扫描直读 DB） |
| 双计/漏检 | 拦截点唯一性在 Phase 2 实施前以实际调用图核实（§7.2），单测覆盖 web/渠道两条链路各只触发一次 |

---

## 11. 测试要点

- 单元：规则参数校验、窗口聚合 SQL、绑定匹配优先级（user > channel > tenant 兜底；无自定义行时平台默认生效；web 查 user 绑定、渠道查 channel 绑定，不并集）、事件同键 1h 去重、observe/enforce 分支
- 集成（Phase 1）：定时扫描任务注入 mock 绑定与 mock `chat_records`，验证事件落库、预算聚合排除 admin source_type、渠道级绑定的预算按入口内每个渠道用户各自累计、命中阈值后通知触发与 24h 节流；`abuse_usage` API 租户隔离（只见本租户用户）；suspended 渠道用户被入口拦截回固定话术；禁用记录列表与启用恢复
- 集成（Phase 2 补充）：`/api/chat` 与渠道链路各注入 mock 绑定，验证 blocked 不触达 LLM、同一请求只检查一次
- 边界：Redis 不可用时缓存回源 DB（Phase 2）、绑定缓存失效时序（Phase 2）、时间窗滚动边界、渠道外部用户未映射时预算跳过但其余规则仍生效、渠道用户停用后恢复（开关切回）可继续对话

---

## 修订记录

- **v0.2（2026-10-11，评审合入）**：
  - 新增 `scope_type = tenant` 兜底绑定，修复覆盖空洞（未绑定用户/入口零监控、通知永不触发）；UI 入口为防滥用中心页面
  - Phase 1 改为定时扫描驱动观察闭环，**不接任何执行链拦截点**（唯一例外：渠道入口 `users.status` 检查）；入口预检与 enforce 整体移 Phase 2
  - 砍掉用户管理页渠道用户双 Tab，渠道用户处置收敛到防滥用中心（用量监控行内停用 + 禁用记录 Tab 含历史恢复，解决「第二天找不到恢复入口」）
  - `abuse_events` 增 `user_id` 一等列 + 索引；事件写入按同键 1h 去重防刷表
  - mode 定稿为行级（整绑定统一），修正 §3.2 图示；删除 `enabled` 列（空 policies 即停用）
  - 绑定 API 三种作用域合一、usage 汇总/明细两 API 合一
  - 放弃 `max_output_per_request`（单请求上限）：数据分析等智能体单请求合法消耗巨大且客户可接受，不设上限
- **v0.3（2026-10-11，Phase 1 实施合入）**：
  - Phase 1 观察闭环开发完成：`abuse_policies` / `abuse_events` 两表 + `idx_chat_records_tenant_user_time` 索引（db_update.yaml / init-postgres.sql / database_system_table.md 三处登记）；引擎模块落位 `src/services/anti_abuse/`（rules / service / scanner）；扫描任务挂 `src/scheduler/manager.py`（IntervalTrigger，默认 300s）
  - 渠道绑定匹配决策：扫描器按 `users.source`（渠道类型）匹配该渠道类型的 channel 绑定行（`JOIN tenant_channel_configs`，多份配置取 `updated_at` 最新一份）；Phase 2 请求上下文再精确到 config_id。API 的 channel 作用域仍按 config_id 精确配置（Phase 2 扫描器匹配口径对齐）
  - Runner 链路缺口登记（本 Phase 不实施）：wecom 应用等已接 Runner 的流量不经 `src/channels/session.py`，`users.status` 检查覆盖不到；Phase 2 统一拦截点解决
  - `test_behavior_log` 守卫测试调整：原用例要求 `user_behavior_logs` 增量块永远是 `db_update.yaml` 最新条目，属一次性守卫被按字面永久执行，会阻塞一切后续正常增量登记；改为断言 `user_behavior_logs` 条目不早于其紧邻前一条（全局有序性由既有格式/唯一/严格递增用例保证）
  - 通知外发复用既有 `publish_in_tx` + 通知 dispatcher 轮询，事件 `abuse.rule_triggered` 已注册（`src/notifications/events.py`）；TTL 24h，幂等键 `abuse:{tenant_id}:{user_id}:{rule_id}`
