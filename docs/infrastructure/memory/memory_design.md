# 记忆系统设计文档

> 版本: v1.5 | 最后更新: 2026-05-19 | 状态: Phase 3 已完成
>
> v1.5 变更：Phase 3 长期记忆已实现；每日总结对话上下文精简，仅保留用户问题+助手最终回答，去除工具调用中间信息
> v1.4 变更：长期记忆存储路径增加租户隔离，`storage/memory/{tenant_id}/memory_{user_id}.md`
> v1.4 变更：长期记忆存储路径增加租户隔离，`storage/memory/{tenant_id}/memory_{user_id}.md`

## 1. 现状分析

### 1.1 当前架构

项目设计了三层记忆架构，但仅短期记忆实际投入使用：

| 层级 | 状态 | 存储方式 | 关键文件 |
|------|------|----------|----------|
| 短期记忆 (Short-Term) | 已实现 | 进程内 `deque` 滑动窗口 | `src/memory/short_term.py` |
| 中期记忆 (Conversation Summary) | 仅有数据模型 | 进程内 `dict`（未持久化） | `src/memory/models.py` |
| 长期记忆 (User Preference) | ✅ 已实现 | MD 文件（`storage/memory/{tenant_id}/memory_{user_id}.md`） | `src/memory/long_term.py` |

### 1.2 现有代码结构

```
src/memory/
├── __init__.py          # 导出 MemoryManager, ShortTermMemory, MemoryItem
├── short_term.py        # 短期记忆核心实现
├── models.py            # 数据模型 (MemoryItem, ConversationMemory, UserPreference)
└── manager.py           # 三层记忆门面类（未接入生产）
```

### 1.3 集成点

- `src/core/agent.py:117` — Agent 直接实例化 `ShortTermMemory()`
- `src/core/dialog_manager.py:43` — DialogManager 创建独立的 `ShortTermMemory` 实例
- `src/subagents/factory.py:109-110` — 工厂将同一 memory 实例注入主/子智能体
- ~~`src/tools/skill/skill_complete_tool.py` — 技能完成后执行上下文压缩~~（**已废弃**：2026-07-14 skill_complete 工具已彻底删除，详见 [skill-complete-removal-design.md](../../system/skill-complete-removal-design.md)）
- `src/config/settings.py:157-165` — 配置模型定义

### 1.4 当前配置

```yaml
# configs/config.yaml
memory:
  short_term:
    max_messages: 10
    ttl: 3600  # 秒
```

---

## 2. 问题清单

| # | 问题 | 严重程度 | 状态 | 备注 |
|---|------|----------|------|------|
| P1 | 恢复历史会话时，Agent 的 ShortTermMemory 为空 | 高 | ✅ 已修复 | `process_message()` 每次从 DB 加载历史（agent.py:1241-1276） |
| P2 | MemoryManager 未接入生产代码 | 高 | ✅ 已修复 | Agent 使用 `MemoryManager`（agent.py:127-130） |
| P3 | Agent 使用硬编码默认值，未读取配置 | 中 | ✅ 已修复 | 从 `settings.memory.short_term.max_messages` 读取 |
| P4 | Agent 和 DialogManager 各自创建独立实例 | 中 | ✅ 已修复 | DialogManager 为死代码，已标注未投入使用 |
| P5 | `get_relevant_context(query)` 的 query 参数未使用 | 中 | ❌ 未修复 | 等待 Phase 4 语义检索能力 |
| P6 | 重要性衰减机制 `decay_importance()` 未启用 | 低 | ❌ 未修复 | 低优先级，后续按需启用 |
| P7 | 无记忆管理 API | 低 | ❌ 未修复 | 计划在 Phase 3（长期记忆 API）中一并实现 |
| P8 | TTL 仅依赖惰性清理，过期会话可能不被释放 | 低 | ✅ 已修复 | 后台任务 `_memory_cleanup_loop()` 每 300s 清理（main.py:256-268） |

---

## 3. 改进计划

---

### Phase 1: 修复现有问题 ✅ 已完成

> 目标：消除已知缺陷，统一架构，为后续扩展铺路
> 预计工期：1-2 天
> 状态：**已完成**（2026-05）

#### 任务 1.1: 统一配置来源 ✅

**问题**: Agent 直接调用 `ShortTermMemory()` 使用硬编码默认值(100条/3600s)，而配置文件设为10条。

**方案**:
- 修改 `src/core/agent.py` 中 Agent 初始化，从 `settings.memory.short_term` 读取配置
- 修改 `src/core/dialog_manager.py` 同样使用统一配置
- 确保所有 ShortTermMemory 实例都从同一配置源创建

**涉及文件**:
- `src/core/agent.py` — Agent.__init__ 中 memory 初始化
- `src/core/dialog_manager.py` — DialogManager.__init__ 中 memory 初始化

**验收标准**:
- [x] Agent 的 ShortTermMemory max_messages 与 config.yaml 一致
- [x] 修改 config.yaml 中的 max_messages 后，Agent 行为随之变化

---

#### 任务 1.2: 消除双实例问题 ✅

**问题**: Agent 和 DialogManager 各自创建独立的 ShortTermMemory，数据不互通。

**方案**:
- 分析 DialogManager 的 memory 使用场景，确定是否需要独立实例
- 如果 DialogManager 仅用于对话管理，改为共享 Agent 的 memory 实例
- 如果确实需要独立存储，明确各自的职责边界并在代码中注释说明

**涉及文件**:
- `src/core/agent.py`
- `src/core/dialog_manager.py`

**验收标准**:
- [x] 明确 Agent 和 DialogManager 各自 memory 的职责
- [x] 消除不必要的重复实例（DialogManager 为死代码，已标注未投入使用）

---

#### 任务 1.3: 将 MemoryManager 接入生产流程 ✅

**问题**: MemoryManager 已实现但从未被使用，Agent 直接操作 ShortTermMemory。

**方案**:
- 修改 Agent 通过 MemoryManager 间接操作 ShortTermMemory
- MemoryManager 的短期记忆方法直接代理到 ShortTermMemory，不改变现有行为
- 确保子智能体工厂、技能工具等所有使用 memory 的地方都通过统一入口

**涉及文件**:
- `src/core/agent.py` — 将 `self.memory = ShortTermMemory()` 改为 `self.memory = MemoryManager()`
- `src/subagents/factory.py` — 适配 MemoryManager 类型
- `src/subagents/executor.py` — 适配 MemoryManager 类型
- ~~`src/tools/skill/skill_complete_tool.py` — 适配 MemoryManager 接口~~（**已废弃**：skill_complete 工具已删除）

**注意事项**:
- MemoryManager 需要暴露与 ShortTermMemory 兼容的接口（add, get_context, to_llm_messages 等）
- 现有技能上下文压缩逻辑必须继续正常工作

**验收标准**:
- [x] 所有现有测试通过
- [x] Agent 通过 MemoryManager 操作短期记忆
- [x] 子智能体和技能工具正常工作

---

#### 任务 1.4: 增加主动清理机制 ✅

**问题**: TTL 过期仅依赖惰性清理（get_context 时检查），无活跃请求的会话永远不会被清理。

**方案**:
- 在 MemoryManager 中添加 `cleanup_expired()` 方法，主动遍历并清理过期会话
- 使用 FastAPI 的后台任务或定时任务（如 APScheduler）定期执行清理
- 清理间隔可配置，默认 300 秒（5 分钟）

**涉及文件**:
- `src/memory/manager.py` — 添加定时清理逻辑
- `src/config/settings.py` — 添加清理间隔配置
- `configs/config.yaml` — 添加配置项
- `src/main.py` — 注册定时任务（如需要）

**验收标准**:
- [x] 过期会话在 5 分钟内被主动清理
- [x] MemoryManager.get_stats() 准确反映当前活跃会话数

---

### Phase 2: 短期记忆修复 & 中期记忆 — 会话内上下文压缩（部分完成）

> 目标：(1) 修复短期记忆的已知问题；(2) 当单次会话历史过长时，自动压缩总结早期对话，控制上下文长度
> 预计工期：3-5 天
> 状态：短期记忆修复已完成（2.1.x）；中期记忆压缩 Phase 1-7 已完成（v3.0 同步路径，见 [context_compression_design.md](./context_compression_design.md)），后台定时任务扫描（Phase 8）待开发

#### 设计理念

**中期记忆不是跨会话的**，而是同一 session 内的上下文压缩机制：

```
┌─────────────── Session 上下文结构 ───────────────┐
│                                                    │
│  [摘要区] ← 中期记忆：早期对话的压缩总结             │
│    前N轮对话的摘要内容                               │
│                                                    │
│  [近期区] ← 短期记忆：最近的消息原文                  │
│    最近 max_messages 条消息原文                      │
│    （含附件路径信息）                                │
│                                                    │
└────────────────────────────────────────────────────┘
```

当 session 的消息数量超过阈值时，将早期消息通过 LLM 压缩为摘要，只保留摘要 + 最近的短期记忆消息作为上下文。

---

#### 2.1 短期记忆问题修复 ✅ 已完成

##### 任务 2.1.1: 会话恢复时加载历史消息到 Agent ✅

**问题**: 用户恢复历史会话时，前端能显示历史消息，但 Agent 的 `ShortTermMemory` 是空的。Agent 完全不知道之前的对话内容。

**现状**:
- 前端 `switchSession()` 从 `GET /api/sessions/{id}/messages` 加载历史用于显示
- 后端 `ShortTermMemory` 在 Agent 初始化时为空 `Dict[str, deque]`
- 没有任何代码在用户恢复会话时将 DB 中的消息加载到 ShortTermMemory
- Agent 收到新消息时，只看到当前 `process_message()` 调用中的上下文

**方案**:
- 在 `Agent.process_message()` 开始处理新消息前，检查该 session 的 ShortTermMemory 是否为空
- 如果为空，从 `MessageDB.list_by_session(session_id)` 加载最近的 N 条历史消息
- 加载的消息格式需与 ShortTermMemory 中的格式一致（role, content, timestamp 等）
- 只加载最近的 `max_messages` 条，更早的历史不加载（由中期记忆的摘要机制处理）

**涉及文件**:
- `src/core/agent.py` — `process_message()` 开头增加历史加载逻辑
- `src/memory/short_term.py` — 新增 `load_history(session_id, messages)` 方法批量加载

**注意事项**:
- 加载历史时要处理附件信息的还原（见任务 2.1.2）
- 避免重复加载，只在 ShortTermMemory 为空时执行
- 加载的总量不超过 `max_messages`

**验收标准**:
- [x] 用户恢复历史会话后发送新消息，Agent 能感知之前的对话
- [x] 只在首次进入空 session 时加载，不重复加载
- [x] 加载的历史消息数量不超过 `max_messages`

---

##### 任务 2.1.2: 历史消息中的附件信息保留 ✅

**问题**: 历史消息中的附件信息（文件路径）在恢复会话后丢失，Agent 不知道用户之前上传过什么文件。

**现状**:
- 附件信息以文本形式嵌入用户消息内容：`[附件: file.pdf]\n【已上传文件路径】\n- file.pdf: /path/to/file`
- 存入 DB 时，`metadata.attachments` 保存了文件信息，但消息 `content` 中已经包含了路径文本
- ShortTermMemory 中的消息也包含附件路径文本

**方案**:
- 从 DB 加载历史消息时，直接使用消息的 `content` 字段（其中已包含附件路径文本）
- 不需要额外处理附件，因为附件信息已经在消息文本中了
- 但需要验证：确保 `MessageDB.list_by_session()` 返回的 `content` 确实包含附件路径信息

**附件上下文中的体现方式**:
- 用户消息中的附件信息已经在 `content` 中以文本形式存在：`[附件: filename]\n【已上传文件路径】\n  - filename: /path/to/file`
- 这是 Agent 能理解的格式，无需额外转换
- 摘要压缩时也应保留附件路径信息（见任务 2.2）

**验收标准**:
- [x] 从 DB 恢复的消息中包含附件路径信息
- [x] Agent 在后续对话中能引用之前上传的文件

---

##### 任务 2.1.3: 统一 ShortTermMemory 的 max_messages 配置 ✅

**问题**: 配置文件 `config.yaml` 设置 `max_messages: 10`，但 Agent 使用 `ShortTermMemory()` 默认值 100。

**方案**:
- 统一将 `max_messages` 的有效值改为 **100**（当前实际运行值，也更适合生产环境）
- 更新 `config.yaml` 中的值为 100，与实际行为一致
- Agent 初始化时从 `settings.memory.short_term.max_messages` 读取
- 确保所有 ShortTermMemory 实例都使用配置值

**涉及文件**:
- `configs/config.yaml` — 更新 `max_messages: 100`
- `src/core/agent.py` — 从 settings 读取 max_messages
- `src/core/dialog_manager.py` — 同步配置来源

**验收标准**:
- [x] 所有 ShortTermMemory 实例的 max_messages 与配置一致
- [x] 配置值修改后重启服务生效

---

#### 2.2 中期记忆 — 会话内上下文压缩 🔧 部分完成（同步路径已实现，定时任务待开发）

> **方案已细化**：完整设计见 [context_compression_design.md](./context_compression_design.md)，开发计划见 [context_compression_dev_plan.md](./context_compression_dev_plan.md)，业界调研见 [context_compression_research.md](../../research/context_compression_research.md)。
>
> 下方为早期占位草案，已被上述文档取代，仅保留作为决策对照。

##### 任务 2.2.1: 设计上下文压缩机制

**核心逻辑**:

```
每次 Agent 处理新消息前:
  1. 检查当前 session 的消息总数
  2. 如果消息数 > 压缩阈值（如 100 条）:
     a. 取前 M 条早期消息（如前 70 条）
     b. 调用 LLM 压缩总结为摘要
     c. 将摘要存入 session 的"摘要区"
     d. 从 ShortTermMemory 中移除已总结的早期消息
     e. 保留最近的 N 条消息（如最近 30 条）
  3. 构建上下文: 摘要区 + 短期记忆区
```

**压缩触发条件**:

```python
# 配置
mid_term:
  compress_threshold: 100   # 触发压缩的消息总数阈值
  keep_recent: 30           # 压缩后保留的最近消息数
  summary_max_tokens: 1000  # 摘要的最大 token 数
```

**上下文组装结构**:

```
发给 LLM 的 messages 数组:
[
  {"role": "user", "content": "[对话历史摘要]\n{摘要内容}"}  ← 中期记忆（摘要区）
  {"role": "user", "content": "..."},                       ← 短期记忆（近期区）
  {"role": "assistant", "content": "..."},
  {"role": "user", "content": "最新用户消息"},
  ...
]
```

> 注意：摘要作为一条 `user` 消息注入，因为 LLM API 不允许在对话中间插入 `system` 消息。
> 使用 `[对话历史摘要]` 前缀明确标识，避免 Agent 与正常对话混淆。

**摘要存储**:
- 摘要存储在 ShortTermMemory 的一个特殊字段中（如 `_summary`），按 session_id 隔离
- 每次压缩时，新摘要与已有摘要合并（不是替换），确保信息不丢失
- 摘要属于 session 生命周期，session 过期时随 ShortTermMemory 一起清理

**摘要 Prompt（草案）**:

```
请总结以下对话历史，生成一份简洁的摘要。要求：
1. 保留关键事实和决策（谁说了什么、决定了什么）
2. 保留用户提到的文件和附件路径信息
3. 保留工具调用的关键结果
4. 保留用户的需求和意图
5. 省略客套、重复和无关细节
6. 摘要不超过 {max_tokens} tokens

已有摘要（如有）：
{existing_summary}

需要总结的新对话：
{messages}
```

**涉及文件**:
- `src/memory/short_term.py` — 新增 `_summary: Dict[str, str]` 字段和摘要相关方法
- `src/memory/mid_term.py`（新增）— 压缩总结逻辑
- `src/core/agent.py` — `_build_messages()` 中整合摘要区 + 短期记忆区
- `src/config/settings.py` — 新增中期记忆配置模型

**验收标准**:
- [ ] 消息数超过阈值时自动触发压缩
- [ ] 压缩后上下文 = 摘要 + 保留的近期消息
- [ ] 摘要保留关键信息（决策、文件路径、工具结果）
- [ ] 多次压缩时摘要增量合并，不丢失信息
- [ ] 压缩过程不阻塞用户请求（异步或快速完成）

---

##### 任务 2.2.2: 技能完成后的上下文保留

> **已废弃（2026-07-14）**：`SkillCompleteTool` 及其上下文压缩机制已彻底删除。详见 [skill-complete-removal-design.md](../../system/skill-complete-removal-design.md)。技能完成后由 LLM 直接给出最终回复，无显式压缩。

**现状**: 技能完成后，`SkillCompleteTool` 会将中间消息压缩为一条摘要。这是已有的压缩机制。

**方案**:
- 保持现有技能上下文压缩机制不变
- 技能摘要消息在会话中期记忆压缩时，作为普通消息被纳入摘要
- 技能摘要中的关键结果信息自然保留在摘要中

**涉及文件**:
- 无需修改，现有机制与新压缩机制自然兼容

**验收标准**:
- [ ] 技能摘要消息正常参与中期记忆压缩
- [ ] 技能执行结果不丢失

---

### Phase 3: 长期记忆 — 基于 Markdown 文件的用户记忆 ✅ 已完成

> 目标：使用结构化 MD 文件持久化用户长期记忆，支持系统自动总结和用户手动编辑
> 预计工期：5-7 天
> 状态：**已完成**（2026-05）

#### 设计理念

长期记忆采用**文件存储方案**而非数据库，原因：
- 用户需要**直接查看和编辑**自己的记忆文件，MD 文件天然可读可编辑
- 记忆内容是**结构化文本**，不是结构化数据，MD 的标题+内容格式完美匹配
- 简单直观，无需额外的管理界面，前端可直接渲染 MD 或提供编辑器
- 方便调试和运维，直接查看文件即可了解用户画像

---

#### 3.1 存储设计

**文件路径规则**:

```
storage/memory/{tenant_id}/memory_{user_id}.md
```

**目录结构**:

```
storage/
  memory/
    tenant_abc123/
      memory_user_a1b2c3d4e5f6.md
      memory_user_f7g8h9i0j1k2.md
      ...
    tenant_def456/
      memory_user_x1y2z3a4b5c6.md
      ...
```

> `tenant_id` 为租户 ID（如 `tenant_abc123`），`user_id` 格式为 `user_{uuid4_hex[:12]}`。
> 每个租户的用户记忆文件存放在独立子目录中，实现**租户级别的数据隔离**。
> 存储路径遵循项目现有的 `storage/` 目录约定（参考 `storage/tenants/`）。
>
> **租户隔离说明**：
> - 非租户用户（SaaS 模式禁用时）的 `tenant_id` 为 `None`，文件存放在 `storage/memory/default/` 目录下
> - API 和存储层通过 `tenant_id` 参数确保不同租户的用户记忆完全隔离，无法跨租户访问

---

#### 3.2 MD 文件格式规范

每个用户的记忆文件是一个结构化的 Markdown 文件，由**标题分类**和**内容条目**组成：

```markdown
# 用户记忆

> 最后更新: 2026-04-20
> 自动更新: 系统于 2026-04-20 14:30 自动整理

## 个人介绍

- 姓名：张三
- 职位：销售经理
- 部门：华东销售部

## 语言偏好

- 喜欢简洁直接的回复风格，不需要过多解释
- 偏好中文交流

## 工作习惯

- 通常在上午 9:00-12:00 和下午 14:00-18:00 活跃
- 习惯先看摘要再看详情
- 常用工具：合同审核、客户查询、订单管理

## 常用联系人

- 李四（客户经理）- 负责华东区客户
- 王五（法务）- 合同相关问题
- 客户A公司 - 赵六（采购总监）

## 用户明确要求记住的事项

- 每周五下午需要提交周报，提醒我
- 我对花生过敏，团建订餐时注意
- 合同金额超过 50 万需要走特批流程

## 从对话中分析的行为习惯

- 频繁查询合同状态，可能负责合同跟踪工作
- 偏好使用表格形式展示数据
- 经常在对话中使用语音转文字输入

## 业务相关知识

- 公司审批流程：部门经理 → 总监 → VP
- 客户分级标准：A类（年采购>100万）、B类（50-100万）、C类（<50万）
- 常见合同条款偏好：付款条件 Net 60，质保期 12 个月
```

**格式规则**:

| 规则 | 说明 |
|------|------|
| 一级标题 | 固定为 `# 用户记忆`，作为文件标识 |
| 元信息 | 使用引用块 `>` 记录最后更新时间和更新来源 |
| 二级标题 | 记忆类型分类，见下方分类定义 |
| 列表条目 | 每条记忆以 `- ` 开头，一行一条，简洁明确 |
| 空行分隔 | 各分类之间保留一个空行 |
| 编码 | UTF-8 |

**预定义记忆分类（二级标题）**:

| 分类 | 说明 | 更新来源 |
|------|------|----------|
| `## 个人介绍` | 姓名、职位、部门等基本信息 | 用户编辑 / 系统从对话提取 |
| `## 语言偏好` | 回复风格、语言、格式偏好 | 系统分析 / 用户编辑 |
| `## 工作习惯` | 活跃时间、常用工具、工作流程 | 系统分析 |
| `## 常用联系人` | 频繁提及的人及其角色/关系 | 系统从对话提取 |
| `## 用户明确要求记住的事项` | 用户主动说"记住这个"的内容 | 用户触发 |
| `## 从对话中分析的行为习惯` | 系统观察到的用户使用模式 | 系统分析 |
| `## 业务相关知识` | 用户在对话中分享的业务规则和知识 | 系统从对话提取 / 用户编辑 |

> 分类不限于以上预定义类别。系统分析和用户编辑都可以添加新的二级标题分类。
> 新分类应使用简洁的中文描述性标题。

---

#### 3.3 更新机制

长期记忆有**两种更新途径**：

##### 途径一：系统每日自动总结

**触发条件**: 每天定时执行（建议凌晨 2:00 或低峰时段）

**执行流程**:

```
1. 遍历所有活跃用户（当天有会话的用户，按 tenant_id 分组）
2. 收集该用户当天的会话内容（仅用户问题 + 助手最终回答，去除工具调用上下文）
3. 调用 LLM 分析会话内容，提取值得长期记忆的信息
4. 读取用户现有的 storage/memory/{tenant_id}/memory_{user_id}.md
5. 将新提取的信息**增量合并**到现有文件中
6. 更新文件的"最后更新"元信息，标注"自动更新"
```

**上下文精简策略**: 为了减少 LLM 上下文长度和成本，收集对话内容时执行以下过滤：

| 过滤项 | 说明 |
|--------|------|
| 时间戳前缀 | 去除用户消息中的 `[当前时间: ...]` 前缀 |
| 附件区块 | 去除 `[Attachments]` 及后续的文件路径、大小等工具中间信息 |
| 工具调用详情 | 仅保留 `user` 和 `assistant` 角色消息，不包含工具调用/返回消息 |
| 消息截断 | 单条消息超过 500 字符时截断，保留核心语义 |

**LLM 提取 Prompt（草案）**:

```
你是一个用户记忆分析助手。请分析以下今天的对话记录，提取值得长期记住的用户信息。

提取规则（仅提取以下类型的信息）：
1. 用户明确要求记住的事项（如"记住我的偏好是..."、"帮我记一下..."）
2. 用户的个人特征（职位、部门、职责范围）
3. 用户的偏好（回复风格、语言、格式偏好）
4. 用户的习惯性工作行为（常用工具、工作流程、工作时间段）
5. 用户提及的业务知识和规则
6. 用户频繁联系的同事或客户

不要提取：
- 一次性的任务内容（如"帮我查个订单"）
- 临时性的对话上下文
- 敏感信息（密码、密钥、完整身份证号等）

当前用户记忆文件内容：
{existing_memory_content}

今天的对话记录：
{today_conversations}

请输出需要新增或修改的记忆条目，格式为：
## [分类标题]
- [记忆内容]

如果某个已有分类需要更新，直接输出该分类和更新后的完整条目。
如果是新分类，使用新的二级标题。
如果今天的对话没有值得长期记忆的信息，输出：无更新
```

**合并策略**:

- 同分类下：新条目**追加**到现有条目末尾，不删除已有条目
- 已有条目与新条目冲突时：以**新条目为准**替换旧条目
- 新分类：直接追加到文件末尾
- 无更新时不修改文件

##### 途径二：用户手动编辑

**实现方式**:
- 前端提供记忆文件编辑页面
- 用户可以直接查看和编辑自己的 `memory_{user_id}.md` 文件内容
- 支持 MD 编辑器（预览 + 编辑模式）
- 保存时校验格式合法性（一级标题必须存在）
- 用户编辑的内容不做任何过滤或修改，原样保存

**API 设计**:

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/memory/long-term` | 获取当前用户的记忆文件内容 |
| PUT | `/api/v1/memory/long-term` | 更新当前用户的记忆文件内容 |

请求/响应格式：

```json
// GET 响应
{
  "success": true,
  "data": {
    "content": "# 用户记忆\n\n> 最后更新: ...",
    "updated_at": "2026-04-20T14:30:00",
    "updated_by": "system"  // 或 "user"
  }
}

// PUT 请求
{
  "content": "# 用户记忆\n\n> 最后更新: ..."
}
```

---

#### 3.4 任务拆分

##### 任务 3.4.1: 实现长期记忆文件存储层 ✅

**方案**:
- 新增 `src/memory/long_term.py`，实现 `LongTermMemory` 类
- 负责文件的读写、格式校验、目录初始化
- 文件不存在时自动创建包含元信息的空模板
- 按 `{tenant_id}` 子目录隔离不同租户的用户记忆

**核心接口**:

```python
class LongTermMemory:
    def __init__(self, storage_dir: str = "storage/memory"):
        ...

    def get_memory(self, tenant_id: str, user_id: str) -> str:
        """读取用户记忆文件内容，不存在返回空模板。
        tenant_id 为 None 时使用 'default' 目录。"""

    def save_memory(self, tenant_id: str, user_id: str, content: str, updated_by: str = "user") -> None:
        """保存用户记忆文件，更新元信息。
        自动创建 storage/memory/{tenant_id}/ 目录（如不存在）。"""

    def merge_memory(self, tenant_id: str, user_id: str, new_sections: dict[str, list[str]]) -> None:
        """增量合并新记忆到现有文件（系统自动总结用）"""

    def get_memory_sections(self, tenant_id: str, user_id: str) -> dict[str, list[str]]:
        """解析文件，返回 {分类标题: [条目列表]} 的结构"""

    def memory_exists(self, tenant_id: str, user_id: str) -> bool:
        """检查用户是否有记忆文件"""

    def _get_file_path(self, tenant_id: str, user_id: str) -> str:
        """获取记忆文件的完整路径。
        tenant_id 为 None 时使用 'default'。
        返回: storage/memory/{tenant_id}/memory_{user_id}.md"""
```

**涉及文件**:
- `src/memory/long_term.py`（新增）

**验收标准**:
- [x] 文件不存在时自动创建空模板（含目录自动创建）
- [x] 能正确读取和保存 MD 文件
- [x] 增量合并不丢失已有条目
- [x] 格式校验拒绝无效内容（缺失一级标题等）
- [x] 不同租户的用户记忆文件存储在各自子目录中
- [x] tenant_id 为 None 时使用 'default' 目录

---

##### 任务 3.4.2: 实现后端记忆 API ✅

**方案**:
- 新增 `src/api/memory.py`，实现 `GET /api/v1/memory/long-term` 和 `PUT /api/v1/memory/long-term`
- 遵循项目现有 API 模式（参考 `src/api/credentials.py`）
- 使用 `Depends(get_current_user)` 鉴权
- 用户只能访问自己租户下自己的记忆文件
- `tenant_id` 从请求上下文中自动获取（`get_current_tenant_id()`），用户不可指定

**涉及文件**:
- `src/api/memory.py`（新增）
- `src/main.py` — 注册路由

**验收标准**:
- [x] API 遵循项目 REST 规范
- [x] 用户只能访问自己的记忆文件
- [x] 支持 GET 获取和 PUT 更新
- [x] 租户隔离正确，无法跨租户访问

---

##### 任务 3.4.3: 实现系统每日自动总结 ✅

**方案**:
- 新增 `src/memory/memory_summarizer.py`，实现每日定时总结逻辑
- 收集当天所有活跃用户的会话内容（**仅用户问题 + 助手最终回答**，去除工具调用上下文）
- 调用 LLM 提取值得记忆的信息
- 通过 `LongTermMemory.merge_memory()` 增量合并

**上下文精简**: `_get_user_conversations()` 收集对话时，通过 `_clean_message_content()` 过滤：
- 用户消息：去除 `[当前时间:...]` 时间戳前缀、`[Attachments]` 附件区块及文件路径信息
- 助手消息：保留最终回答文本（DB 中已不含工具调用细节）
- 单条消息超过 500 字符时截断

**执行流程**:
```
定时触发（每天凌晨）
  → 查询当天有会话的用户列表（含 tenant_id）
  → 按 tenant_id 分组，逐用户处理：
      → 从 DB 收集当天会话（仅 user/assistant 角色的 content，去除工具调用上下文）
      → 读取现有记忆文件（storage/memory/{tenant_id}/memory_{user_id}.md）
      → 调用 LLM 分析（带提取 Prompt）
      → 解析 LLM 输出为 {分类: 条目} 结构
      → 增量合并到记忆文件
      → 记录日志
```

**配置**:
```yaml
memory:
  long_term:
    enabled: true
    storage_dir: "storage/memory"    # 根目录，实际文件在 storage/memory/{tenant_id}/ 下
    summary_cron: "0 2 * * *"        # 每天凌晨2点
    max_users_per_run: 50            # 单次最多处理用户数
```

**涉及文件**:
- `src/memory/memory_summarizer.py`（新增）— 总结逻辑
- `src/memory/long_term.py` — 文件操作
- `src/config/settings.py` — 新增长期记忆配置模型
- `configs/config.yaml` — 新增配置项

**验收标准**:
- [x] 定时任务按配置时间自动执行
- [x] 能从当天会话中提取有效记忆
- [x] 提取的记忆增量合并到现有文件
- [x] 执行过程有完整日志记录
- [x] 按租户隔离存储，不同租户用户记忆不混淆

---

##### 任务 3.4.4: 实现对话中用户主动要求记住的功能 ✅

**方案**:
- 在 Agent 处理消息时，检测用户"记住"类意图
- 通过 LLM 意图识别或关键词匹配触发
- 将用户要求的内容立即写入记忆文件（无需等待每日总结）
- 支持的表达方式：`帮我记住...`、`记住...`、`以后记住...`、`记一下...`

**实现方式**:
- 作为 Agent 内置的一种特殊意图处理
- 检测到"记住"意图后，提取内容并调用 `LongTermMemory.merge_memory(tenant_id, user_id, ...)`
- 写入 `## 用户明确要求记住的事项` 分类
- 回复用户确认已记住

**涉及文件**:
- `src/core/agent.py` — 添加意图检测和处理
- `src/memory/long_term.py` — 写入记忆

**验收标准**:
- [x] 用户说"记住XX"时，内容被写入记忆文件
- [x] 写入到正确的分类下
- [x] Agent 回复确认已记住
- [x] 写入到正确的租户目录下

---

##### 任务 3.4.5: 将长期记忆注入 LLM 上下文 ✅

**方案**:
- 在 `MemoryManager.to_llm_messages()` 中整合长期记忆
- 新会话开始时，加载用户记忆文件内容
- 将记忆内容格式化为系统提示词的一部分注入

**上下文注入格式**:
```
[用户记忆]
个人介绍：
- 姓名：张三，职位：销售经理，部门：华东销售部

语言偏好：
- 喜欢简洁直接的回复风格
- 偏好中文交流

用户明确要求记住的事项：
- 每周五下午需要提交周报，提醒我
```

**注入策略**:
- 注入到系统提示词末尾，作为 `[用户记忆]` 区块
- 记忆内容过长时（超过 token 阈值），优先注入以下分类：
  1. `## 用户明确要求记住的事项`（最高优先级）
  2. `## 语言偏好`
  3. `## 工作习惯`
  4. `## 常用联系人`
  5. `## 个人介绍`
  6. 其他分类按需截断

**涉及文件**:
- `src/memory/manager.py` — to_llm_messages 增强
- `src/core/agent.py` — 新会话时加载长期记忆（传入 tenant_id）

**验收标准**:
- [x] 新会话自动加载用户长期记忆
- [x] 记忆以适当格式注入系统提示词
- [x] 记忆过长时有合理的截断策略
- [x] 不影响系统提示词的核心指令
- [x] 加载时按 tenant_id 隔离，不会加载到其他租户用户的记忆

---

##### 任务 3.4.6: 前端记忆管理页面 ❌ 未实现

**方案**:
- 在前端设置页面中新增"我的记忆"功能
- 用户可查看和编辑自己的记忆 MD 文件
- 使用 MD 编辑器组件（带预览）
- 保存调用 `PUT /api/v1/memory/long-term`

**页面设计**:

```
┌─────────────────────────────────────────┐
│  设置 > 我的记忆                         │
├─────────────────────────────────────────┤
│                                         │
│  ℹ️ 这是系统记录的关于你的偏好和习惯，      │
│     你可以直接编辑。系统每天会自动整理。    │
│                                         │
│  ┌─────────────────────────────────┐    │
│  │ # 用户记忆                      │    │
│  │                                 │    │
│  │ > 最后更新: 2026-04-20          │    │
│  │                                 │    │
│  │ ## 个人介绍                     │    │
│  │ - 姓名：张三                    │    │
│  │ - 职位：销售经理                │    │
│  │ ...                             │    │
│  │                                 │    │
│  │ ## 语言偏好                     │    │
│  │ - 喜欢简洁直接的回复            │    │
│  │ ...                             │    │
│  └─────────────────────────────────┘    │
│                                         │
│  [编辑] [保存] [取消]                    │
│                                         │
│  最后更新: 2026-04-20 14:30 (系统自动)   │
└─────────────────────────────────────────┘
```

**涉及文件**:
- `frontend/src/components/UserMemory.vue`（新增）— 记忆管理页面
- `frontend/src/api/memory.ts`（新增）— API 调用
- `frontend/src/main.ts` — 添加路由
- `frontend/src/components/SettingsDialog.vue` — 添加记忆 tab 或入口

**验收标准**:
- [ ] 用户可以查看自己的记忆文件
- [ ] 支持编辑和保存
- [ ] 保存后立即生效（下次对话可感知）
- [ ] 显示最后更新时间和更新来源（系统/用户）

---

### Phase 4: 高级记忆功能 ❌ 未实现

> 目标：实现语义检索和管理能力
> 预计工期：按需安排
> 状态：**未实现**

#### 任务 4.1: 语义检索能力

**方案**:
- 接入向量数据库（优先 PGVector，利用现有 PostgreSQL）
- 为对话摘要和用户偏好生成向量嵌入
- 实现 `get_relevant_context(query)` 的真正语义搜索
- 检索结果按相似度排序，返回最相关的 K 条记忆

**技术选型**:
- 向量存储：PGVector（PostgreSQL 扩展，与现有架构一致）
- 嵌入模型：使用当前 LLM 提供商的 embedding API（DashScope/ZhipuAI）
- 相似度算法：余弦相似度

**涉及文件**:
- `src/memory/vector_store.py`（新增）— 向量存储和检索
- `src/memory/manager.py` — 集成语义检索
- `src/memory/repositories.py` — 向量数据库操作

**验收标准**:
- [ ] 用户提问时能检索到语义相关的历史对话
- [ ] 检索延迟 < 500ms
- [ ] 支持跨会话的语义关联

---

#### 任务 4.2: 记忆管理 API

**方案**:
- 暴露 REST API 用于记忆管理
- 管理员可查看/清理/导出用户记忆
- 用户可查看和删除自己的历史数据
- API 遵循项目 RESTful 规范

**API 设计（草案）**:

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/memory/sessions` | 查看活跃会话列表 |
| GET | `/api/v1/memory/sessions/{session_id}` | 查看会话详情 |
| DELETE | `/api/v1/memory/sessions/{session_id}` | 清除指定会话记忆 |
| GET | `/api/v1/memory/preferences/{user_id}` | 查看用户偏好 |
| PUT | `/api/v1/memory/preferences/{user_id}` | 更新用户偏好 |
| GET | `/api/v1/memory/stats` | 记忆系统统计信息 |

**涉及文件**:
- `src/api/memory_router.py`（新增）— API 路由
- `src/memory/manager.py` — 暴露管理接口

**验收标准**:
- [ ] API 遵循项目 REST 规范和认证要求
- [ ] 支持多租户数据隔离
- [ ] 管理员和普通用户有不同权限

---

#### 任务 4.3: 跨会话记忆关联

**方案**:
- 基于语义相似度自动关联不同会话中的相关记忆
- 用户开启新会话时，主动推送相关的历史上下文
- 支持用户通过自然语言查询历史对话

**验收标准**:
- [ ] 新会话能自动关联到相关历史对话
- [ ] 用户可以自然语言查询历史记忆
- [ ] 关联结果准确且不泄露跨租户数据

---

## 4. 配置设计

完成后的完整配置结构：

```yaml
memory:
  short_term:
    max_messages: 100       # 每会话最大消息数（同时也是压缩阈值参考值）
    ttl: 3600               # 会话过期时间（秒）
  mid_term:
    enabled: true           # 是否启用中期记忆（会话内压缩）
    compress_threshold: 100 # 触发压缩的消息总数阈值
    keep_recent: 30         # 压缩后保留的最近消息数
    summary_max_tokens: 1000  # 摘要的最大 token 数
  long_term:
    enabled: true           # 是否启用长期记忆
    storage_dir: "storage/memory"    # 记忆文件根目录，实际文件在 storage/memory/{tenant_id}/ 下
    summary_cron: "0 2 * * *"        # 每日自动总结执行时间
    max_users_per_run: 50            # 单次总结最多处理用户数
    max_inject_tokens: 2000          # 注入上下文的最大 token 数
  cleanup_interval: 300     # 过期会话清理间隔（秒）
```

---

## 5. 文件变更规划

### 新增文件

| 文件 | Phase | 说明 |
|------|-------|------|
| `src/memory/mid_term.py` | P2 | 会话内上下文压缩总结逻辑 |
| `src/memory/long_term.py` | P3 | 长期记忆文件存储层（含租户隔离） |
| `src/memory/memory_summarizer.py` | P3 | 每日自动总结逻辑 |
| `src/api/memory.py` | P3 | 长期记忆 API |
| `frontend/src/components/UserMemory.vue` | P3 | 前端记忆管理页面 |
| `frontend/src/api/memory.ts` | P3 | 前端记忆 API 调用 |
| `src/memory/vector_store.py` | P4 | 向量存储与语义检索 |

### 修改文件

| 文件 | Phase | 说明 |
|------|-------|------|
| `src/memory/short_term.py` | P1-P2 | 统一配置、新增 load_history、摘要字段 |
| `src/memory/manager.py` | P1-P3 | 接入生产、中期压缩、长期记忆整合 |
| `src/core/agent.py` | P1-P3 | 统一配置、MemoryManager 接入、历史加载、记住意图（传入 tenant_id） |
| `src/core/dialog_manager.py` | P1 | 统一配置/共享实例 |
| `src/subagents/factory.py` | P1 | 适配 MemoryManager |
| `src/subagents/executor.py` | P1 | 适配 MemoryManager |
| ~~`src/tools/skill/skill_complete_tool.py`~~ | ~~P1~~ | ~~适配接口~~（已废弃，skill_complete 已删除） |
| `src/config/settings.py` | P1-P3 | 扩展配置模型 |
| `configs/config.yaml` | P1-P3 | 扩展配置项 |
| `src/main.py` | P3 | 注册记忆 API 路由 |
| `frontend/src/main.ts` | P3 | 添加记忆页面路由 |
| `frontend/src/components/SettingsDialog.vue` | P3 | 添加记忆功能入口 |

---

## 6. 测试要求

每个 Phase 均需补充对应测试：

| Phase | 测试文件 | 测试内容 |
|-------|----------|----------|
| P1 | `tests/unit/test_memory.py` | 配置统一、MemoryManager 接入、定时清理 |
| P2 | `tests/unit/test_short_term_history.py` | 历史消息加载、附件信息保留 |
| P2 | `tests/unit/test_mid_term.py` | 压缩触发、摘要生成、增量合并 |
| P2 | `tests/integration/test_context_compression.py` | 完整压缩流程集成测试 |
| P3 | `tests/unit/test_long_term_memory.py` | MD 文件读写、格式校验、增量合并、租户隔离 |
| P3 | `tests/unit/test_memory_summarizer.py` | 每日总结逻辑、LLM 输出解析 |
| P3 | `tests/api/test_memory_api.py` | 长期记忆 API 接口测试（含租户隔离验证） |
| P3 | `tests/integration/test_memory_inject.py` | 长期记忆注入上下文集成测试 |
| P4 | `tests/integration/test_semantic_search.py` | 语义检索集成测试 |

---

## 7. 待讨论事项

> 以下问题需要团队讨论确认后再进入开发
>
> 注：第 2/3/4 项（压缩时机、延迟/异步、摘要持久化）已在 [context_compression_design.md](./context_compression_design.md) 中解决（双阈值 token+消息数触发、v3.0 采同步压缩 + 后台定时任务补漏、独立 `chat_context_summaries` 表持久化）。

1. **DialogManager 的 memory 是否需要独立？** 需要确认其使用场景
2. **中期记忆压缩时机**：✅ 已解决（见 design §3.1 双阈值：token 70% 或 消息数 150）
3. **中期记忆压缩的延迟问题**：✅ 已解决（v3.0 采同步压缩，独立超时 + 硬截断降级 + 后台定时任务补漏）
4. **中期记忆摘要的存储位置**：✅ 已解决（独立 `chat_context_summaries` 表持久化，原消息标记 compacted）
5. **长期记忆的分类是否需要固定？** 当前定义了 7 个预定义分类，是否允许系统/用户自由添加新分类
6. **每日总结的时机和频率**：凌晨 2 点是否合适？是否需要更频繁（如每 6 小时）
7. **"记住"意图的识别方式**：关键词匹配 vs LLM 意图识别，前者简单但漏检率高
8. **记忆文件的并发写入保护**：系统自动总结和用户手动编辑同时发生时的冲突处理策略
9. **记忆文件的 token 预算**：注入上下文时分配多少 token 给长期记忆，避免占用过多上下文窗口
10. **用户删除记忆后的行为**：删除某条记忆后，系统下次总结是否会重新提取相同信息

---

## 附录: 版本变更记录

| 版本 | 日期 | 变更内容 |
|------|------|----------|
| v1.0 | 2026-04-24 | 初始版本 |
| v1.3 | 2026-04-24 | Phase 1 实施计划补充 |
| v1.5 | 2026-05-19 | Phase 3 长期记忆已实现：存储层、API、Agent 集成、记住意图、每日自动总结（前端页面待实现） |
| v1.4 | 2026-05-19 | 长期记忆存储路径增加租户隔离：`storage/memory/{tenant_id}/memory_{user_id}.md`，所有接口方法增加 `tenant_id` 参数 |
