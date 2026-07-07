# 工具总体优化开发计划

> 配套设计文档：[`tool-overall-optimization-design.md`](./tool-overall-optimization-design.md)（含现状、五类问题分析、统一规范）。
>
> 本计划以**「先立标准 → 试点验证 → 按使用频率铺开」**为推进顺序，每个工具/工具组独立成 Phase，便于跟踪与回滚。
>
> **重要约束**：每个 Phase 走项目三智能体开发流程（开发 → 测试 → CodeReview）。改造完成不等于上线——试点 Phase（Phase 2）设置 token 对比验收 gate，达标后才铺开后续批次。

## 排序原则

1. **先删后改**：先把待删除工具清掉，减少干扰。
2. **先立标准**：在动任何工具前，先落地统一规范（作为后续所有改造和新工具的依据）。
3. **试点先行**：`http_api` + `pdf_process` 最早做，用 token 对比 + 人工抽检验证规范有效，再铺开。
4. **闭环优先**：Phase 2 试点只做了截断（信息黑洞），Phase 3 立即补齐「落盘 + read/grep 回读」闭环 + 新增 grep 工具 + 文件工具对齐 Claude Code（offset 1-based、replace_all）。这是基础设施级改造，优先于其他高频工具的纯 token 优化。
5. **按使用频率排优先级**（高频优先，低频末尾）：
   - **高频梯队**：文件四件套（read/write/edit/cp）+ grep、knowledge_base_search、content_generate、email 三件套、word/excel_process
   - **中频梯队**：attraction/hotel_search、web_search、analyze_data/upload_data_file
   - **低频梯队（放最后）**：browser_automation、create/manage_scheduled_task、ai_call、transfer_to_human、ppt_process（paddleocr 已在 Phase 2 提前完成）
6. **依赖就近**：`paddleocr_doc_parsing` 被 `pdf_process.ocr` 内部调用，已在 Phase 2 提前完成。

## 全局里程碑

| 里程碑 | 内容 | 验收 gate |
|--------|------|-----------|
| M0 清理 | Phase 0 | upload_to_remote 从代码库彻底移除 ✅ |
| M1 规范 | Phase 1 | 工具开发规范文档落地 + BaseTool 公共能力可用 ✅ |
| M2 试点 | Phase 2 | http_api + pdf_process + paddleocr 改造完成，token 对比达标 ✅ |
| M2.5 闭环 | Phase 3a-3b | **落盘闭环基建 + 回填 Phase 2 落盘 + 新增 grep**，agent 能 read/grep 回读被截断内容 |
| M3 高频 | Phase 3c-7 | 文件工具对齐 Claude Code（offset 1-based、replace_all）+ 高频梯队全部符合规范 |
| M4 中频 | Phase 8-10 | 中频梯队全部符合规范 |
| M5 低频 | Phase 11-15 | 低频梯队全部符合规范，整体收尾（原 Phase 15 paddleocr 已并入 Phase 2） |

---

## Phase 0 — 前置清理：删除 `upload_to_remote`

**对应设计**：#1

**改动点**：
- 删除 `src/tools/file/upload_to_remote.py`（整文件）
- 删除 `src/core/agent.py:316` import + `agent.py:335` register
- 文档处理：`docs/tools/remote-upload-feature.md` 顶部加废弃标注；`docs/tools/IMPLEMENTATION_SUMMARY.md`、`docs/system/file_usage.md`、`docs/infrastructure/storage/storage_path_refactoring_plan.md` 相关段落更新

**验收**：
- 全局搜索 `upload_to_remote` 源码无残留（日志/归档文档除外）
- 服务正常启动，工具列表不再出现该工具
- 无前端改动（已确认零硬编码）

---

## Phase 1 — 建立工具标准规范（立标准）

**目标**：把设计文档 #2 的统一规范落地为**可执行的开发规范**，并沉淀为 BaseTool 层公共能力。后续所有改造（Phase 2+）和新工具开发**必须遵循**。

**产出物**：

1. **工具开发规范文档** `docs/tools/tool-development-spec.md`（新工具 checklist + 旧工具重构依据），固化以下契约：
   - `description`：一句话功能 + 触发场景，≤ 80 字符，禁止教程/示例/指令
   - Field description：只描述字段本身（类型/含义/默认/枚举值），禁止 few-shot/业务背景
   - `usage_guide`：集中承载调用教程、cp 注册提醒、负面清单；与 schema 不重复
   - 文本返回字段：统一字符上限（单字段 ≤ 2000、全文类 ≤ 5000），超出返回截断 + `truncated: true`
   - 错误返回：仅 `{"success": False, "error": "<脱敏一句话>"}`，**禁止 debug/traceback/kwargs**
   - 成功返回：只带新信息（路径/行数/count/id/状态），**禁止 echo 输入参数**、禁止纯话术 message
   - 参数约束：条件必填用 `model_validator` + `Literal` 枚举，让 schema 自描述

2. **BaseTool 公共能力**（在 `src/tools/base.py` 或新建 `src/tools/_helpers.py`）：
   - `truncate_text(text, limit, key)` 统一截断助手（自动加 `truncated` 标记）
   - `sanitize_error(e)` 统一错误脱敏（参照 `ppt_process._format_user_error` 白名单机制，提取为公共方法）
   - 约定枚举类型优先用 `Literal`

**验收**：
- 规范文档评审通过
- 公共 helper 单测覆盖（截断边界、脱敏白名单）
- 以 `x_to_image` / `ppt_process` / `analyze_data` 三个正面标杆作为规范参照样例写入文档

> 说明：本 Phase 不改任何具体工具的行为，只立规矩、造工具。是后续所有 Phase 的前置依赖。

---

## Phase 2 — 试点验证：`http_api` + `pdf_process`（最早实验）

**目标**：选这两个工具最早做，原因——`http_api` 的 JSON 响应零截断是**最典型的 token 黑洞**，`pdf_process` 的 description 最长（1100 字符）且回塞全文，二者能充分检验规范在「返回截断」「description 瘦身」两个核心场景的有效性。**达标后才铺开后续批次。**

### 2a. `http_api`
- 返回值 `data`（JSON 响应）补齐字符截断（与 text 一视同仁，复用 Phase 1 helper）
- schema `headers` description 去除与 usage_guide 重复的 Bearer 示例
- usage_guide 精简（凭据替换说明去重，约 200 token 常驻成本压减）

### 2b. `pdf_process`
- `description` 从 1100 字符瘦身到 ≤ 80 字符，9 种操作菜单与 cp 注册说明迁移到 usage_guide
- `read` / `read_tables` / `ocr` / `pdf_to_md` 返回值改为「元信息 + preview（前 N 行/页）+ 文件路径」，不再回塞全文；全文让 LLM 按需 `read`
- `read_tables` 的完整 tables 数组加截断

### ⚠️ 依赖说明（paddleocr）
`pdf_process` 的 `ocr` 操作**内部调用 `paddleocr_doc_parsing`**，后者三重复制（texts+full_text+result）的返回会透传进 pdf_process 返回值。**建议把 `paddleocr_doc_parsing` 纳入本 Phase 一并改造**（或紧随其后插入一个 Phase 2c），否则 pdf_process 的 ocr 路径仍带超额 token。原计划 paddleocr 因低频排在 Phase 15，此处标注——**是否提前由你决定**。

### 验收 gate（token 对比 + 人工抽检）
- **token 对比**：改造前后跑同一批代表性请求（http_api 用真实大 JSON 响应 case、pdf_process 用长文档读取/ocr/转 md case），对比 system prompt token 与 tool_result token，记录降幅
- **人工抽检**：验证 LLM 在新返回结构下决策未退化（能正确判断是否截断、能按 next_hint 续读、不重复请求全文）
- 达标 → 进入 M3；不达标 → 回炉调整规范，不盲目铺开

---

## Phase 3 — 高频：文件四件套对齐 Claude Code + 新增 grep（含落盘闭环基建）

**最核心工具，每次文件操作必用，放高频首位。本 Phase 升级为「对齐 Claude Code 水准」，不只是 token 优化。**

> 设计文档：[`file-tools-claude-code-parity-design.md`](./file-tools-claude-code-parity-design.md)（offset/replace_all）、[`large-content-retrieval-design.md`](./large-content-retrieval-design.md)（grep + 落盘闭环）

### 3a. 落盘闭环基建（新增 `_spill.py` + grep 工具）
- 新增 `src/tools/_spill.py`：`spill_large_content()` 落盘管理器（>5000 字符才落盘到临时目录）
- 新增 `src/tools/file/grep_tool.py`：grep 工具（调 ripgrep 二进制，正则+行号+上下文+glob+output_mode）
- `src/core/agent.py` 注册 grep 工具
- `Dockerfile` 补装 ripgrep
- 单测：`tests/unit/tools/test_spill.py`、`test_grep_tool.py`
- **此步先行**，因为 3b 的 http_api 落盘改造依赖 `_spill.py`

### 3b. 回填 Phase 2 试点：补落盘闭环
Phase 2 只截断没落盘，本步回填：
- `http_api._parse_response`：大响应调 `spill_large_content` 落盘，返回 file_path + truncated
- `paddleocr.full_text`、`pdf_process._merge_results` 截断字段：同上
- **解决 Phase 2 的「信息黑洞」问题**——agent 现在能用 read/grep 回读被截断内容

### 3c. read/edit 对齐 Claude Code（breaking change）
- **offset 改 1-based**（read + edit replace_lines）：所见行号即所填 offset
- read：description 瘦身（教程迁 usage_guide）、next_hint 文案改 1-based
- edit：description 瘦身、`replace_string` 新增 `replace_all` 参数、replace_lines offset 改 1-based
- **全局搜索 `src/skills/`、`.agents/skills/` 适配 SKILL.md 中的 0-based offset 示例**
- edit 9 个条件必填参数改用 `model_validator` + `Literal`

### 3d. write/cp token 优化
- write：description（~20 行）与 usage_guide（~19 行）去重合并；`generate_prompt` Field description 去教程化
- cp：description 参数说明（与 Field description 逐条重复）删除；`visible` Field description 去业务背景；返回值 `resolved_source` 去 echo 冗余

**验收**：
- 落盘闭环：大响应落盘 + agent 能 read/grep 回读（端到端跑通一次 http_api 大响应→grep→read 流程）
- offset 1-based：read `offset=1` 读第 1 行，全局 skill 无 0-based 残留
- replace_all：批量替换生效，默认行为不变（向后兼容）
- 符合 token 规范 + 文件工具测试全绿 + 抽检文件读写编辑流程正常

---

## Phase 4 — 高频：`knowledge_base_search`

- 每条返回的 `text`（完整 chunk，不截断）加字符截断
- `metadata` 改为按需提取单字段（doc_title 等有用项），不再整体透传
- `top_k` 默认值从 10 下调到 5

**验收**：符合规范 + 检索结果质量人工抽检（命中未因 top_k 下调而显著变差）。

---

## Phase 5 — 高频：`content_generate`

- `prompt` 参数 description 内嵌的 5 要素教学说明迁移到 usage_guide（或精简）
- usage_guide 中 ~15 行完整邮件示例 prompt 精简（每轮常驻注入，压减固定成本）
- 返回值 echo 的 `language`/`content_type` + 固定 `message` 去冗余

**验收**：符合规范 + 生成质量抽检未退化。

---

## Phase 6 — 高频：email 三件套 `email_send` / `email_read` / `email_list_folders`

- **email_read**：`limit` / `unseen_only` 的 Field description few-shot（"查询近期邮件时建议…"、"用户问…时设 True"）迁移到 usage_guide
- **email_send**：返回值 `details` 去 echo（to/cc/subject 回显）
- **email_list_folders**：返回值去 `original_name`（modified UTF-7 debug 字段，对 LLM 无用）；`message` 与 `total_folders` 去重

**验收**：符合规范 + 邮件收发/列表流程抽检正常。

---

## Phase 7 — 高频：`word_process` + `excel_process`

- 两者 `description`（word ~900、excel ~1050 字符）瘦身到 ≤ 80 字符；触发规则、excel 负面清单、cp 注册迁移到 usage_guide
- `word_to_md` / `excel.to_md` 返回值不再回塞全文 markdown，改为元信息 + preview + 文件路径
- word 的 `diff` 返回值（diff_text + changes + summary）加截断
- `content_type` 枚举改用 `Literal`

**验收**：符合规范 + 文档读写转换流程抽检正常。

---

## Phase 8 — 中频：`attraction_search` + `hotel_search`

- `project_table` / `price_table`（不截断裸传）加字符截断
- `top_k` 默认值从 20 下调到 5-8
- 复用 Phase 4 的截断/精简模式

**验收**：符合规范 + 旅游顾问子智能体报价流程抽检正常。

---

## Phase 9 — 中频：`web_search`

- 删除未使用的 `limit` 死参数（与 `max_results` 语义重叠，从不消费）
- `message` 中文话术精简或移除

**验收**：符合规范 + 搜索结果抽检正常。

---

## Phase 10 — 中频：`analyze_data` + `upload_data_file`

审查为低风险（已是正面标杆），本 Phase 主要是**对照规范确认符合性**，仅做轻微调整：
- 确认 `analysis_meta`、table artifact preview 符合截断规范
- `upload_data_file` 的 `message` 与表名去轻微冗余

**验收**：符合规范确认通过。

---

## Phase 11 — 低频：`browser_automation`

> 频率低但 token 风险🔴高，放低频梯队但仍需认真改。

- 删除返回值 `message = result` 的**完全重复**（整页 markdown ≤ 5 万字符双份占用）
- `result`（done 时整页 markdown）加截断
- `steps` 完整历史（最长 30 步）精简或截断
- 返回值 echo 的 `task` 输入字段去除
- description（~400 字符，含 3 个 task 示例）瘦身，示例迁移到 usage_guide
- `ask_user` 状态注入的 `instruction` 引导文字审视必要性

**验收**：符合规范 + 浏览器自动化任务抽检正常。

---

## Phase 12 — 低频：`create_scheduled_task` + `manage_scheduled_task`

> 用户明确要求放后面。

- **create_scheduled_task**：description 末尾「必须先向用户确认」「禁止自行猜测」指令污染审视（属 system prompt 内容）；错误路径大量 `debug` 字段全部移除（走日志）；返回值 `message` 与已有字段去重；`task_prompt` Field description 去示例化
- **manage_scheduled_task**：list action 返回 `message`（格式化文本）+ `tasks`（完整数组）**双重数据**二选一；错误 `debug = str(e)` 移除

**验收**：符合规范 + 定时任务创建/管理流程抽检正常。

---

## Phase 13 — 低频：`ai_call`

- 返回值 echo 的 3 个输入参数（phone/lead_id/call_purpose）去除
- `mock` debug 标记移到日志，不进返回值

**验收**：符合规范。

---

## Phase 14 — 低频：`transfer_to_human`

- description（~200 字符，适用/不适用场景 + 调用后指引 4 条）迁移到 usage_guide（当前刻意留空，设计自相矛盾）
- 各失败分支的 `hint` 引导文字审视：保留对纠错必要的，去纯话术

**验收**：符合规范 + 转人工流程抽检正常。

---

## Phase 15 — 低频：`ppt_process`

> `paddleocr_doc_parsing` 已在 Phase 2 提前完成，原 Phase 15 取消，本 Phase 顺延。
> ppt_process 返回值已干净（正面标杆之一），本 Phase 主要是 description 瘦身。

- description（~1050 字符，含嵌套 cp 调用示例代码）瘦身，cp 注册迁移到 usage_guide
- 复用其 `_format_user_error` 脱敏机制作为规范参照（已在 Phase 1 提取为公共能力）

**验收**：符合规范。

---

## 时间预估（粗略）

| 批次 | Phase | 预估 |
|------|-------|------|
| M0-M1 清理+规范 | 0-1 | 1-1.5 天 |
| M2 试点 | 2 | 1-2 天（含 token 对比验收） |
| M3 高频 | 3-7 | 3-4 天 |
| M4 中频 | 8-10 | 1-1.5 天 |
| M5 低频 | 11-16 | 2-3 天 |

> 每个 Phase 含三智能体流程（开发 → 测试 → CodeReview），预估含来回。实际依改造复杂度浮动。

## 风险与回滚

- **每个工具独立 Phase**：一旦某工具改造引入回归，可单独回滚，不影响其他。
- **试点 gate 是硬门槛**：token 对比不达标不铺开，避免错误模式扩散到 16 个工具。
- **截断阈值需联调校准**：规范里的 2000/5000 字符是初值，试点阶段据实调整后写进规范。
- **文档同步**：每个 Phase 完成后更新 `docs/ideas.md` #34 条目状态，并在 design 文档速查表标注该工具已优化。
