# 主智能体系统提示词优化设计

> 关联文档：
> - 反向关联：[基础设施/Prompt 全生命周期管理](../../infrastructure/prompt-lifecycle-design.md)（解决"版本管理机制"，本文档解决"提示词内容本身"）
> - 开发计划：[agent-system-prompt-optimization-dev-plan.md](../../plans/agent-system-prompt-optimization-dev-plan.md)
> - 涉及模板：`src/prompts/templates/master_agent.md`、`src/prompts/templates/subagent_base.md`
> - 涉及子智能体：`subagents/travel-consultant/SUBAGENT.md`

## 一、背景与目标

### 1.1 触发本次优化的两个直接问题

**问题 A：固定提示词内容质量与结构落后于业界主流**

当前的 `master_agent.md` / `subagent_base.md` 是项目早期形成的版本，存在以下结构性短板：

1. **流程指令过度具体、过度啰嗦**：用大量「⚠️ 关键」「⚠️ 重要规则」「第二步 / 第三步 / 第四步」的步骤化文字，反复强调"创建计划后立即调用工具、不要回复用户正在执行"等行为。这类细节级指令对当前一代大模型（Claude 4.x / GPT-5.x / DeepSeek V3）已经基本不需要——它们天然懂得"调用工具后继续推进"。
2. **缺乏原则性、价值观层面的指引**：业界主流 agent（Claude Code、Codex、OpenClaw）都采用「原则 + 边界 + 行为风格」三层结构，靠少量高质量原则引导模型自主判断，而不是堆砌 if-then 规则。
3. **"你生成的文件"段落与实际系统行为严重不符**：当前模板写"生成的文件会**自动**以卡片形式展示给用户，用户可直接下载，禁止提及文件名"——但事实上并非所有工具都自动注册下载（见问题 B）。这段过时描述会误导 agent。

**问题 B：文件注册下载规则散落在各子智能体中，缺乏主提示词层的统一约束**

**真实链路**（核心认知，必须先厘清）：

文件"生成"和"在 chat 界面以 file card 展示供下载"是**两件不同的事**：

1. **文件生成**：word/excel/ppt/pdf_process 等工具会生成文件，并往 Redis 写入 `uploaded_file:{file_id}` 元数据，返回值含 `file_id` 和 `download_url`。
2. **chat 界面展示 file card**：前端通过回调收集 `downloadable_files`，**白名单只包含三个工具**——`{"register_download_file", "write", "cp"}`（见 `src/main.py:1598` 和 `src/saas/api/channel_routes.py:458,603,1456`）。

| 工具/Skill | 生成文件 | 写 Redis file_id | 返回值含 file_id | **在 chat 界面展示 file card** |
|-----------|---------|-----------------|-----------------|------------------------------|
| `word_process` / `excel_process` / `ppt_process` / `pdf_process` | ✅ | ✅ | ✅ | ❌ **不在前端白名单，用户看不到下载卡片** |
| `write` | ✅ | ✅ | ✅ | ✅（在白名单） |
| `cp` | ✅ | ✅ | ✅ | ✅（在白名单） |
| `register_download_file` | ❌（仅注册） | ✅ | ✅ | ✅（在白名单） |
| `travel-quote` / `competitor-research` 等 skill | ✅ | ❌ 不写 | 仅返回 `file_path` | ❌ |

**结论**：

1. **agent 调用 `word_process` 等工具后，用户在前端 chat 界面看不到下载卡片**——必须再调用 `register_download_file` 或 `cp` 把文件"二次注册"才会出现。
2. 旅游顾问 SUBAGENT.md 反复强调"每次调用 `word_process` 或 `travel-quote` 后必须立即调用 `register_download_file`"是**完全正确且必要**的。
3. **当前问题不是"冗余"而是"缺失统一约束"**：这个规则只写在旅游顾问的 md 里，其他子智能体（如 trade-specialist、after-sales 等）和主智能体模板都没有这条规则，导致 agent 在没有这条规则的场景下会"忘记注册"，用户看不到文件。
4. 正确做法：把"任何工具/skill 生成了文件 → 立即调用 `register_download_file` 或 `cp` 注册"这条**通用规则**写进主系统提示词；子智能体 md 删除重复说明，只保留业务流程描述。

### 1.2 设计目标

| 目标 | 衡量标准 |
|------|---------|
| **G1 提示词精简** | 主提示词去除冗余步骤指令、原则化；字符数目标降低 15%+（注：因新增「文件交付规则」段落本身约 800 字符，纯瘦身幅度有限，核心价值是结构化与新增约束） |
| **G2 结构对齐业界** | 采用「身份 → 核心原则 → 能力边界 → 工具与技能 → 输出规范 → 文件交付规则 → 上下文」分层结构 |
| **G3 统一文件交付规则** | "工具/skill 生成文件 → 必须调用 register_download_file 或 cp 注册"作为通用规则固化在主提示词，子智能体不再重复 |
| **G4 修正错误描述** | 删除"文件自动以卡片展示"这类与实际行为不符的描述（实际只有 `write`/`cp`/`register_download_file` 三个工具的结果会被前端捕获展示） |
| **G5 旅游顾问瘦身** | SUBAGENT.md 删除文件交付相关说明（约 4 处），保持业务流程描述完整 |

### 1.3 非目标（Out of Scope）

- ❌ 不修改 `prompt-lifecycle-design.md` 的版本管理机制
- ❌ 不改造工具实现（如让 `travel-quote` skill 自动注册下载）——这是后续独立工作
- ❌ 不重写所有子智能体的 SUBAGENT.md，本次只清理 `travel-consultant`
- ❌ 不修改 `reply_style`（回复风格）注入逻辑

---

## 二、业界主流 agent 提示词范式调研

### 2.1 Claude Code（Anthropic 官方 CLI）

**结构特点**：
1. **身份 + 价值观开头**：明确"你是谁、你的核心使命是什么"
2. **核心行为原则**（System 段）：简短、原则化（如 "Bias toward action"、"Match scope to request"），不写步骤
3. **工具使用规范**：每个工具有独立 `usage_guide`，按需注入而非全部塞进 system
4. **执行动作的边界**：明确"什么动作需要确认"（destructive ops、shared state）
5. **Tone and Style**：极简（"short and concise"、"Don't narrate"）

**关键洞察**：从 Sonnet 4.5 起，Anthropic 在持续**减少**提示词中的硬性规则、增加对模型判断力的信任（"Rigid rules became guidelines"）。

### 2.2 OpenAI Codex CLI

**结构特点**：
1. **分层注入**：系统提示词（prefix）+ AGENTS.md（项目级指令）+ 用户输入
2. **AGENTS.md 按目录作用域**：每个目录的 `AGENTS.md` 注入为独立 user-role 消息
3. **outcome-first**：GPT-5.x 提示指南强调"短而结果导向的提示优于流程堆叠"

**关键洞察**：把"项目级规则"和"模型级规则"分离，类似我们的 master_agent.md（模型级）+ SUBAGENT.md（项目级）。

### 2.3 OpenClaw

**结构特点**（来自 `src/agents/system-prompt.ts` 实证研究）：
1. **Base Identity**：一行话身份声明
2. **Tooling**：列出可用工具名 + 一行说明（"Call tools exactly as listed"）
3. **Tool Call Style**：明确"Default: do not narrate routine, low-risk tool calls（直接调用，不要解说）"
4. **Skills (mandatory)**：强制要求回复前扫描 `<available_skills>`
5. **Memory Recall / Workspace / Runtime / User Identity**：动态注入运行时信息
6. **SOUL.md / IDENTITY.md / USER.md / AGENTS.md**：分层注入人格、身份、用户档案、项目指令

**关键洞察**：OpenClaw 明确区分「常驻规则」和「按需注入的运行时上下文」，且对"何时该解说、何时不该解说"有明确指引。

### 2.4 三家共识与我们的差距

| 维度 | 业界共识 | 我们现状 | 差距 |
|------|---------|---------|------|
| **原则 vs 步骤** | 原则化引导，少量高质量原则 | 步骤化堆砌，大量「⚠️ 重要」 | 🔴 大 |
| **工具说明注入** | 按需注入 `usage_guide` | ✅ 已实现 `_collect_tool_usage_guides` | 🟢 无差距 |
| **narration 控制** | 明确"不要解说工具调用" | 缺失（导致 agent 经常说"正在执行…"） | 🟡 中 |
| **文件交付规则** | 工具自描述返回值，agent 不重复操作 | 主提示词错误描述 + 子智能体冗余强调 | 🔴 大 |
| **运行时上下文** | 动态注入用户、租户、记忆 | ✅ 已实现 `user_info_section` / `long_term_memory` | 🟢 无差距 |
| **能力边界** | 明确"超出能力时如何处理" | ✅ 已有，但散落 | 🟡 小 |

---

## 三、新主提示词结构设计

### 3.1 新结构总览

```
┌──────────────────────────────────────────────┐
│ 1. 身份与使命（Identity）                     │  ← 一行话
├──────────────────────────────────────────────┤
│ 2. 核心工作原则（Principles）                 │  ← 5-7 条原则化指引
├──────────────────────────────────────────────┤
│ 3. 能力边界（Capabilities）                   │  ← 工具/技能/子智能体清单
├──────────────────────────────────────────────┤
│ 4. 输出与沟通规范（Output Style）             │  ← 语言、附件、解说风格
├──────────────────────────────────────────────┤
│ 5. 文件交付规则（File Delivery）★新增        │  ← 文件生成→注册下载的统一规则
├──────────────────────────────────────────────┤
│ 6. 上下文注入（Context）                      │  ← 用户/记忆/回复风格/子智能体约束
└──────────────────────────────────────────────┘
```

★ 第 5 段「文件交付规则」是本次新增的核心内容，用于解决 §1.1 问题 B。

### 3.2 各段内容设计

#### 第 1 段：身份与使命

```markdown
你是企业员工的智能工作助手。通过对话帮助用户完成各类工作任务，
合理使用工具、技能、子智能体高效达成目标。
```

精简为一句话，去除"你的任务是…"等冗余表述。

#### 第 2 段：核心工作原则（Principles）

参考 Claude Code / OpenClaw 的原则化风格，将原"四步流程"提炼为 6 条原则：

```markdown
## 核心工作原则

1. **先判断再行动**：分析用户意图后，优先判断"能否一步完成"。
   - 单工具 / 单子智能体即可完成 → 直接调用，无需创建计划
   - 多步骤协调 → 调用 `create_plan` 后立即执行第一步

2. **行动优于解说**：调用工具时不要回复"正在执行"、"请稍候"。
   工具执行完毕后再向用户汇报结果。

3. **专业委派**：专业领域任务（旅游、外贸、合同审查等）优先委派给对应子智能体。

4. **结果导向沟通**：告知用户结论，不暴露内部检索过程、工具调用细节、知识库存在。

5. **诚实拒绝**：超出能力时明确告知，不虚假承诺；无法验证时说"不确定"。

6. **匹配语言**：始终用与用户相同的语言回复。
```

**变化点**：
- 删除原"第一步/第二步/第三步/第四步"的步骤化描述
- 删除"⚠️ 重要规则：创建计划后不要回复用户"等冗余警告（合并到原则 2）
- 把分散在原模板各处的指引（智能决策、专业沟通、诚实、有帮助）合并为原则化条目

#### 第 3 段：能力边界

保留现有 `{available_tools_list}` / `{skill_descriptions}` / `{subagent_descriptions}` 变量注入机制不变，但**简化技能使用规则**描述：

```markdown
## 能力边界

### 可用工具
{available_tools_list}

### 可用技能
技能不是工具，必须先用 `use_skill(skill="...")` 加载，再按技能说明调用对应工具。

{skill_descriptions}

### 可用子智能体（仅主智能体）
{subagent_descriptions}
```

**变化点**：
- 删除"⚠️ 技能不是工具！"这种感叹式警告
- 把"4 步技能使用流程"压缩为一句话

#### 第 4 段：输出与沟通规范

参考 OpenClaw 的 Tool Call Style 思路，新增"narration 控制"：

```markdown
## 输出与沟通规范

### 工具调用解说
- 常规、低风险的工具调用：直接调用，不要解说
- 多步骤工作、敏感操作（删除、批量改动、外部发送）：用一句话说明意图

### 用户附件处理
- 语音：消息附带 `[Attachments]` 段，附件中的 `.mp3` 是原始音频。
  - 优先使用消息中的 Recognition 文字识别用户意图
  - 输入为 `[语音消息]` 且无识别结果时，回复"语音识别失败，请用文字发送"
- 图片：模型支持视觉则描述内容，否则告知"已收到图片但无法查看"
- 文件（docx/xlsx/pdf）：用文件读取工具读取内容后再回复，不要假设自己知道内容

### 回复文字
- 简洁直接，不重复用户问题
- 不使用"让我们一步步分析"等开场白
```

**变化点**：
- 新增 narration 控制原则（解决"正在执行…"问题）
- 保留附件处理（仍有必要）
- 删除原"禁止在回复中提及文件名"——这条原本基于"文件自动展示"的错误假设，现移到第 5 段重新设计

#### 第 5 段：文件交付规则（★本次核心新增）

这是解决问题 B 的关键段落。**核心认知：文件生成 ≠ 用户可下载**。即使工具返回值含 `file_id` 和 `download_url`，也**不等于**前端 chat 界面会展示下载卡片——只有 `write` / `cp` / `register_download_file` 三个工具的结果会被前端回调捕获并展示。

```markdown
## 文件交付规则（重要）

### 核心规则：生成文件后必须注册下载

**工具或 skill 生成的文件，默认用户在前端 chat 界面看不到、下载不了。**
必须调用 `register_download_file` 或 `cp` 把文件注册到下载系统，前端才会以 file card 形式展示供用户下载。

**例外**：`write` 和 `cp` 本身在执行时已自动注册下载，调用后无需再注册一次。

### 何时需要注册

| 调用了什么 | 是否需要再注册 |
|-----------|--------------|
| `word_process` / `excel_process` / `ppt_process` / `pdf_process` | ✅ **必须**再调用 `register_download_file` |
| `travel-quote` / `competitor-research` 等 skill（返回 `file_path`） | ✅ **必须**再调用 `register_download_file` |
| 任何生成文件路径但不自动注册的工具/skill | ✅ **必须**再调用 `register_download_file` |
| `write`（默认 `register_download=True`） | ❌ 已自动注册 |
| `cp`（默认 `register_download=True`） | ❌ 已自动注册 |

### 注册调用方式

```
register_download_file(file_path="工具返回的 file_path", display_name="用户看到的文件名.docx")
```

- `file_path`：从上一个工具/skill 的返回值中获取
- `display_name`：给文件起一个用户能看懂的中文名（如"贵州5天行程方案.docx"），不要用临时文件名

### 回复文字约束
- 注册成功后，**不要**在回复正文中重复文件名、文件路径
- 不要说"文件已生成，请下载附件"——用户看到下载卡片自然会知道
- 只告诉用户结论或结果
```

**关键设计点**：

1. **开头一句直击要害**："默认用户看不到、下载不了"——这是 agent 最容易误解的点
2. **表格里把 `word_process` 等明确标为"必须再注册"**——和原错误版本完全相反
3. **强调"注册成功后不要再重复文件名"**——这条仍然有效，因为前端有卡片了
4. 子智能体 md 中所有"调用 register_download_file"的具体步骤都可以删除，因为主提示词已经覆盖

#### 第 6 段：上下文注入

保持现有变量机制不变：

```markdown
{subagent_constraint_section}
{long_term_memory}
{user_info_section}
{reply_style_section}
```

### 3.3 子智能体模板（subagent_base.md）的差异

子智能体模板与主模板结构基本一致，差异点：

| 段落 | 主模板 | 子模板 |
|------|--------|--------|
| 第 3 段 子智能体 | 列出可用子智能体 | 删除（子智能体不能再委派） |
| 第 5 段 文件交付规则 | 完整 | 完整（与主模板一致） |
| 末尾 | — | 追加"⚠️ 你不能调用 delegate_to_subagent"提示 |

---

## 四、旅游顾问 SUBAGENT.md 清理方案

### 4.1 需要删除的段落

主提示词第 5 段已用通用规则覆盖了所有"文件生成后必须注册"的场景，旅游顾问 md 中以下段落属于重复说明，应删除：

| 位置 | 原内容 | 删除原因 |
|------|--------|---------|
| 行 269-273 | 详细行程生成 Word 后"调用 `register_download_file` 注册文件下载"步骤 | 已被主提示词第 5 段通用规则覆盖 |
| 行 276-277 | "⚠️ `register_download_file` 是必须步骤，不是可选的！" | 同上 |
| 行 405-408 | 报价后"必须调用 `register_download_file`" | 同上 |
| 行 455 | 行为约束第 11 条"生成文件后必须注册下载" | 同上 |

### 4.2 需要保留的段落

- 业务流程描述（三阶段对话管理、行程格式、报价触发条件）
- 知识库搜索原则（业务专属）
- 行程表格 5 列格式规范（业务专属）
- 行为约束 1-10、12（业务专属）
- 沟通技巧（业务专属）

### 4.3 关于"是否保留 travel-quote 单行提示"

**结论：不需要保留**。主提示词第 5 段的规则已经覆盖了"skill 返回 file_path → 必须注册"这一情况——`travel-quote` 属于表格中"任何生成文件路径但不自动注册的工具/skill"那一行。

**注意**：删除旅游顾问 md 中相关说明后，必须确保主提示词第 5 段的规则足够明确（"任何工具/skill 生成的文件都要注册"），不能让 agent 误以为只是某些场景才需要。这正是把规则从子智能体上提到主提示词的核心价值——**一处约束，全局生效**。

---

## 五、实现影响评估

### 5.1 代码改动范围

| 文件 | 改动类型 | 工作量 |
|------|---------|--------|
| `src/prompts/templates/master_agent.md` | 重写 | 中 |
| `src/prompts/templates/subagent_base.md` | 重写 | 中 |
| `subagents/travel-consultant/SUBAGENT.md` | 删除冗余段落 | 小 |
| `src/core/agent.py` | 无需改动（变量名保持兼容） | 无 |

**关键约束**：模板中使用的变量名（`{available_tools_list}`、`{skill_descriptions}` 等）必须保持不变，避免影响 `agent.py:704-728` 的 `render` 调用。

### 5.2 风险与缓解

| 风险 | 缓解措施 |
|------|---------|
| 提示词精简后 agent 行为退化（如忘记执行计划） | 上线前用 5 个典型场景对比测试新旧提示词 |
| 主提示词的"必须注册"规则描述不够明确，agent 误判为可选 | 表格里把 `word_process` 等常用工具逐个列出来标"必须注册"，开头用加粗强调"默认用户看不到" |
| 子智能体模板同步遗漏 | 两份模板同步修改，diff review |
| 其他子智能体也有类似散落规则 | 本次只清理 travel-consultant，其他子智能体在后续工作中处理 |
| 漏注册导致用户看不到文件 | 主提示词规则 + Phase 4 场景 2/3 专项验证 |

### 5.3 验收标准

- [ ] `master_agent.md` 字符数较原版减少 ≥ 15%（原版本身较紧凑，核心价值在结构化和新增「文件交付规则」段落）
- [ ] `master_agent.md` 包含完整的「文件交付规则」段落，且明确说明"工具生成的文件默认用户看不到"
- [ ] `subagent_base.md` 同步包含「文件交付规则」段落
- [ ] `travel-consultant/SUBAGENT.md` 删除 4 处文件注册相关描述
- [ ] 5 个典型场景测试通过（见开发计划，特别是场景 2、3 验证 word_process 和 travel-quote 后的注册行为）
- [ ] `agent.py` 无需改动，变量名保持兼容

---

## 六、后续延伸工作（非本次范围）

1. **统一前端白名单逻辑**：当前前端只捕获 `{"register_download_file", "write", "cp"}` 三个工具的结果展示 file card。可以考虑两种方向：
   - **方向 A（推荐）**：扩展白名单到所有产生文件的工具（word/excel/ppt/pdf_process 等），让这些工具"生成即展示"，主提示词第 5 段可大幅简化
   - **方向 B**：保持现状，主提示词承担"必须二次注册"的约束（本次方案）
   本次采用方向 B，方向 A 作为后续工具层改造独立推进
2. **其他子智能体清理**：检查 `after-sales`、`competitor-research`、`order-processing`、`trade-specialist` 等子智能体是否有类似的文件注册散落规则，统一收敛到主提示词。
3. **prompt-lifecycle 集成**：本次优化的新模板可作为 prompt_registry 中 `system_template:master_agent` 的首个版本。
