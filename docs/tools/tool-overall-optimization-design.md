# 工具总体优化梳理设计

> 本文档是**跨工具层面的优化梳理登记文档**，区别于单工具设计文档。每个优化项给出「现状 → 问题 → 方案 → 影响范围 → 待办」，方案经评审后再进入开发流程。
>
> 与 `docs/tools/tool-input-contract-redesign.md`（文件生成类工具入参契约）的关系：那份文档聚焦 Word/PDF/Excel/PPT 的入参拆分；本文档承载**工具形态、职责边界、注册策略**等更宏观的梳理。

## 索引

| # | 优化项 | 状态 | 类型 |
|---|--------|------|------|
| 1 | `upload_to_remote` 工具直接删除 | 📋 待开发 | 工具移除 |
| 2 | 工具输入/输出 Token 效率审查与统一规范 | 📋 待开发 | 跨工具 token 治理 |

---

## #1 `upload_to_remote` 工具直接删除

### 现状

`upload_to_remote` 是面向 LLM 的 function calling 工具，用于把本地文件上传到 SMB/FTP 服务器。

- **工具实现**：`src/tools/file/upload_to_remote.py`
  - `UploadToRemoteInput`（行 17）：8 个必填/选填入参
  - `SMBUploader`（行 30）/ `FTPUploader`（行 127）：实际上传逻辑
  - `UploadToRemoteTool`（行 224，`name="upload_to_remote"`）
- **注册**：`src/core/agent.py`
  - 行 316：`from src.tools.file.upload_to_remote import UploadToRemoteTool`
  - 行 335：`self.tool_registry.register(UploadToRemoteTool())`
  - 均在 `_register_builtin_tools()`（行 306 起）内

### 问题

1. **凭证暴露给 LLM**：`server_host / server_port / username / password / domain` 五个连接参数全部作为工具入参交给 LLM 填写。LLM 既不应、也通常无法正确填写这类敏感凭证——要么用户每次在对话里贴明文密码，要么 LLM 编造，二者都不可接受（违反项目「敏感信息必须加密，不以明文返回给用户」的安全原则）。
2. **返回值泄漏明文密码**：错误路径 `debug = kwargs`（`upload_to_remote.py:296`）把含 `username`/`password` 的参数原样塞进返回值，进入对话上下文。安全 + token 双重问题（详见 #2 审查问题 C）。
3. **description 与实现不一致**：description 声称「凭据未配置时返回凭据配置链接」，但 `execute()` 中并无此逻辑，会误导 LLM。
4. **无测试覆盖**：`tests/` 全局搜索零命中，删除不影响任何测试。
5. **无前端联动**：前端工具列表由后端 Tool 类自描述动态渲染，前端无任何 `upload_to_remote` 硬编码（已全局搜索确认：`upload_to_remote` / `uploadToRemote` / `upload-to-remote` / `UploadToRemote` 均零命中）。

### 方案：直接删除工具

从 ToolRegistry 移除并删除实现文件，**不替换为 skill、不保留远程上传能力**。如未来确有 SMB/FTP 上传需求，再以 skill 形态（凭证走 `.env`、逻辑走 `scripts/`）单独立项新建，不复用旧实现。

### 影响范围

#### 后端改动（开发时执行，本阶段仅登记）

| 操作 | 位置 |
|------|------|
| 删除工具实现文件 | `src/tools/file/upload_to_remote.py`（整文件） |
| 删除 import | `src/core/agent.py:316` |
| 删除 register | `src/core/agent.py:335` |

> `src/tools/file/__init__.py` 的 `__all__` 本就未导出 `UploadToRemoteTool`，无需改动。

#### 前端

**无需改动**。前端无硬编码引用，工具列表由后端动态驱动。从 ToolRegistry 移除后，前端自动不再展示该工具。

#### 文档处理（开发时执行）

| 操作 | 文件 |
|------|------|
| 标注废弃 | `docs/tools/remote-upload-feature.md`（旧工具专属设计文档） |
| 更新引用说明 | `docs/tools/IMPLEMENTATION_SUMMARY.md`（行 48/51/66/127/128/134/195/208/284 多处提及） |
| 更新引用说明 | `docs/system/file_usage.md:211` |
| 更新引用说明 | `docs/infrastructure/storage/storage_path_refactoring_plan.md`（行 203/206/259/267） |

> 建议处理方式：旧 `remote-upload-feature.md` 顶部加「⚠️ 工具已删除」标注而非直接删除，保留历史。其余文档的相关段落移除或更新描述。

#### 测试

现状无任何测试覆盖（`tests/` 全局搜索零命中）。删除后无需新增测试。

### 验证清单（开发时用）

- [ ] 全局搜索 `upload_to_remote` 确认源码中已无残留（仅日志/历史文档归档除外）
- [ ] 从后端移除后，前端工具列表不再出现该工具

### 非目标

- 不保留任何远程上传能力（SMB/FTP 上传逻辑 `SMBUploader`/`FTPUploader` 随工具一并删除）
- 未来如需远程上传，以 skill 形态新建，不复用旧实现

---

## #2 工具输入/输出 Token 效率审查与统一规范

### 背景

LLM 每轮对话都会消耗两类工具 token：① **工具 schema**（`description` + 参数 Field description + `usage_guide`）注入 system prompt，全对话常驻；② **`execute()` 返回值**进入 tool_result，逐轮累积。本次对全部 27 个业务工具做了一次性审查，定位浪费并给出统一规范。

> **正向标杆**：`x_to_image`（description ~80 字、返回纯元信息、不回塞内容）、`ppt_process`（错误用 `_format_user_error` 白名单脱敏、参数用 `Literal` 枚举校验）、`analyze_data`（内部 trace 走单独持久化、不进返回值、table preview 限 10 行）。新工具应参照这三个。

### 五类共性问题（按严重度排序）

#### 问题 A：返回值回塞大块内容 / 全文（最高风险）

工具返回值把外部大响应、文档全文、表格正文、知识库 chunk 整体灌入 LLM 上下文。这是**最昂贵的浪费**——单次调用即可产生上万 token，且逐轮累积无法回收。

| 工具 | 问题 | 位置 |
|------|------|------|
| **http_api** | JSON 响应 `data` 字段**零大小限制**（text 有 5000 字符截断、error 有 500 字符截断，唯独 JSON 无防护） | `src/tools/network/http_api.py:273` |
| **browser_automation** | `done` 时回传整页 markdown（上限 5 万字符），且 `result` 与 `message` **完全重复存两份**；`steps` 完整回传最长 30 步历史 | `orchestrator.py:490-495,768,781` |
| **paddleocr_doc_parsing** | 同一份 OCR 结果以 `texts`(数组) + `full_text`(拼接串) + `result`(原始API响应) **三重复制**塞回 | `src/tools/ocr/ocr_tool.py:297-303` |
| **pdf_process** | read/ocr/pdf_to_md 回塞全文；`read_tables` 回塞完整表格数组 | `src/tools/pdf/pdf_process_tool.py:371-383` |
| **word_process** | `word_to_md` 回塞完整 markdown；diff 回塞完整 diff_text+changes+summary | `src/tools/word/word_process_tool.py:432-444` |
| **excel_process** | `to_md` 回塞完整表格 markdown | `src/tools/excel/excel_process_tool.py:355-370` |
| **knowledge_base_search** | 每条返回**完整 chunk 文本（不截断）** + 完整 metadata dict 原样透传；默认 top_k=10 | `src/tools/knowledge/knowledge_base_tool.py:143,146` |
| **attraction_search** / **hotel_search** | `project_table` / `price_table` **不截断**裸传；默认 top_k=**20** | `attraction_search_tool.py:145` / `hotel_search_tool.py:191` |

**统一规范建议**：
- 所有文本型返回字段统一加字符上限（建议：单字段 ≤ 2000 字符，全文类 ≤ 5000 字符，超出时返回截断 + `truncated: true` 标记，参照 `x_to_image` 的做法）。
- 知识库类默认 `top_k` 下调到 5；`*_table` 字段必须截断。
- `http_api` 的 JSON 响应补齐截断（与 text 一视同仁）。
- `browser_automation` 删除 `message=result` 重复；`result` 加截断。
- `paddleocr_doc_parsing` 删除冗余的 `result` 原始响应字段，保留 `full_text`（已截断）即可。
- 读取类工具（word/excel/pdf 的 to_md/read）回塞全文的设计应改为「返回行数/页数元信息 + 可选 preview（前 N 行），全文走文件路径让 LLM 按需 `read`」。

#### 问题 B：`description` 把 system prompt 教程塞进 schema（中高风险）

`description` 应是「一句话工具卡片」，但被当成 system prompt 使用手册来写。后果：① description 每轮注入，固定烧 token；② 与参数 Field description 内容重复，双重消耗。

| 工具 | description 长度 | 塞了什么 |
|------|------|------|
| **pdf_process** | ~1100 字符（最长） | 9 种操作菜单罗列 + cp 注册说明 |
| **excel_process** | ~1050 字符 | 触发规则 + 「不要调用本工具」负面清单 + cp 注册 |
| **ppt_process** | ~1050 字符 | cp 注册 + **嵌套完整 cp 调用示例代码** |
| **word_process** | ~900 字符 | 触发规则 + cp 注册（占近 1/3） |
| **transfer_to_human** | ~200 字符 | 适用/不适用场景 + 调用后行为指引（4 条），而 `usage_guide` 刻意留空 |
| **read** | ~19 行 | 3 种读取模式教程 + 大文件建议 + SKILL_ROOT 注释 |
| **write/edit/cp** | ~20 行 | 调用示例 + 参数说明（与 Field description 逐条重复） |
| **create_scheduled_task** | 含「必须先向用户确认」「禁止自行猜测」强制指令 | 指令污染 |
| **browser_automation** | ~400 字符（含 3 个 task 示例） | 适用场景 + 使用方式 + 注意事项 |
| **email_read** | （短，但参数描述） | `limit`/`unseen_only` 的 Field description 混入调参 few-shot |

**统一规范建议**：
- `description` 限一句话功能说明（建议 ≤ 80 字符），仅说明「做什么」+「何时触发」。
- 所有「如何调用」「参数怎么填」「不要怎么用」「调用后要 cp 注册」等教程**统一收归 `usage_guide`**（仍是常驻注入，但集中管理、避免与 schema 重复）。
- Field description 只描述字段本身（类型/含义/默认值），**禁止写调参示例、few-shot、业务背景**。
- cp 注册提醒是跨工具复用的高频内容，建议提取为统一的 BaseTool 提示片段或固化进相关工具 usage_guide，而非在每个 description 里重复一遍。

#### 问题 C：错误路径泄漏 `debug` 字段 / traceback（含安全风险）

多个工具在错误返回里塞 `debug` 字段，最严重的是 **`upload_to_remote` 的 `debug: kwargs` 会把明文密码塞进返回值**进入对话上下文（安全问题，随 #1 工具删除一并解决）。

| 工具 | 问题 | 位置 |
|------|------|------|
| **upload_to_remote** | `debug = kwargs`，含 username/password 明文 | `upload_to_remote.py:296` |
| **paddleocr_doc_parsing** | 多处错误返回 `debug` 字段，含内部错误细节 | `ocr_tool.py:222,239,270,281,293` |
| **create_scheduled_task** | 错误路径大量 `debug` 字段（部分截断 500 字符） | `scheduled_task_tool.py:128-204` |
| **manage_scheduled_task** | `debug = str(e)` 可能泄漏异常细节 | `scheduled_task_tool.py:323-325` |

**统一规范建议**：
- **全局禁止** `execute()` 返回值携带 `debug` 字段给 LLM。需要诊断信息走结构化日志（`logger`），不进 tool_result。
- 错误返回统一为 `{"success": False, "error": "<脱敏后的一句话>"}`，参照 `ppt_process._format_user_error` 白名单脱敏机制。
- 禁止返回 `str(e)` 原始异常、`traceback` 字符串、`kwargs` 回显。

#### 问题 D：成功返回 echo 输入参数 / 冗余 message（低风险但普遍）

成功路径把 LLM 刚传的参数原样回读，或返回与结构化字段信息重叠的 `message` 话术。

| 工具 | echo 的输入 / 冗余字段 |
|------|------|
| **email_send** | `details` echo 回 to/cc/subject |
| **ai_call** | echo 回 phone/lead_id/call_purpose + `mock` debug 标记 |
| **browser_automation** | echo 回 `task` 输入 |
| **cp** | `resolved_source` 重复 source_file_path |
| **content_generate** | echo 回 language/content_type + 固定 `message` |
| **create/manage_scheduled_task** | `message` 与已有字段拼接重复 |
| **web_search** | `limit` 参数定义了却从不消费（死参数）；`message` 中文话术 |
| **email_list_folders** | `original_name`（modified UTF-7）debug 级字段对 LLM 无用 |

**统一规范建议**：
- 成功返回只带 LLM 无法从调用本身得知的**新信息**（生成的路径、行数、count、id、状态）。禁止 echo 输入参数。
- 删除纯话术 `message`，或仅保留真正承载新信息的 message。
- `web_search` 删除未使用的 `limit` 死参数。
- `ai_call` 的 `mock` 标记应走日志而非返回值。

#### 问题 E：条件必填参数全标 Optional（低风险，影响 schema 准确性）

`edit` 工具的 9 个参数全是 Optional，但实际取决于 `mode` 有运行时条件必填项（靠 execute 内手动校验报错）。LLM 每次都要猜该传哪些，schema 信息密度低。

**统一规范建议**：条件必填场景优先用 Pydantic `model_validator`（参照 `ppt_process` 的 `PptProcessInput` 做法和 `Literal` 枚举校验），让 schema 自描述参数约束，减少运行时报错往返。

### 全工具风险速查表

| 工具 | 风险 | 首要问题 |
|------|------|------|
| **http_api** | 🔴高 | JSON 响应无大小限制，外部大响应整体灌入 |
| **browser_automation** | 🔴高 | result=message 双份；整页 markdown(≤5万字符)；steps 全历史；description 最长 |
| **paddleocr_doc_parsing** | 🔴高 | 三重复制（texts+full_text+result）+ debug 字段 |
| **pdf_process** | 🔴高 | description 最长；read/ocr/to_md 回塞全文；透传 OCR 全文 |
| **knowledge_base_search** | 🔴高 | chunk 文本不截断 + metadata 整体透传 |
| **attraction_search** | 🔴高 | project_table 不截断 × top_k=20 |
| **hotel_search** | 🔴高 | price_table 不截断 × top_k=20 |
| **word_process** | 🟠中高 | description 教程化；word_to_md/diff 回塞全文 |
| **excel_process** | 🟠中高 | description 含负面清单；to_md 回塞表格 |
| **upload_to_remote** | 🔴高 | debug 回显明文密码（随 #1 工具删除一并解决） |
| **transfer_to_human** | 🔴高 | description 塞场景教程；hint 引导文字；usage_guide 留空 |
| **create_scheduled_task** | 🟠中 | description 指令污染；错误 debug 字段；message 重复 |
| **manage_scheduled_task** | 🟠中 | list 返回 message+tasks 双重数据；错误 debug |
| **read** | 🟠中 | description 教程化 + Field desc 重复 |
| **write** | 🟠中 | description 与 usage_guide 大量重复（~40 行） |
| **edit** | 🟠中 | description 教程化；条件必填参数全 Optional |
| **cp** | 🟠中 | description 重复 schema 参数说明；visible 塞业务背景 |
| **email_send** | 🟠中 | details echo 输入参数 |
| **email_read** | 🟠中 | limit/unseen_only Field description 混入 few-shot |
| **ai_call** | 🟠中 | echo 3 个输入参数 + mock 标记 |
| **web_search** | 🟡低中 | limit 死参数；message 中文话术 |
| **email_list_folders** | 🟡低 | original_name debug 字段；message 重复 |
| **content_generate** | 🟡低 | prompt 参数 description 教程化；usage_guide 示例偏长 |
| **ppt_process** | 🟡低 | description 偏长但返回值干净；错误处理是正面标杆 |
| **analyze_data** | 🟢优 | trace 走单独持久化；meta 全小字段；preview 限 10 行 |
| **upload_data_file** | 🟢优 | 仅返回元信息，不 echo 数据 |
| **x_to_image** | 🟢优 | description 简短、返回纯元信息、不回塞内容 |

### 统一规范（新工具 + 重构旧工具共同遵循）

| 维度 | 规范 |
|------|------|
| `description` | 一句话功能 + 触发场景，≤ 80 字符。禁止教程/示例/指令 |
| Field description | 只描述字段本身（类型/含义/默认/枚举值）。禁止 few-shot/业务背景 |
| `usage_guide` | 集中放调用教程、cp 注册提醒、负面清单。仍常驻注入但避免与 schema 重复 |
| 文本返回字段 | 统一字符上限（单字段 ≤2000，全文类 ≤5000），超出返回截断 + `truncated:true` |
| 错误返回 | 仅 `{"success":False,"error":"<脱敏一句话>"}`。**禁止 debug/traceback/kwargs** |
| 成功返回 | 只带新信息（路径/行数/count/id/状态）。**禁止 echo 输入参数**、禁止纯话术 message |
| 参数约束 | 条件必填用 `model_validator` + `Literal` 枚举，让 schema 自描述 |

### 实施建议

按「影响 × 成本」分批，不在本轮开发：

- **P0（安全 + 高频黑洞，优先）**：upload_to_remote（随 #1 删除）、http_api JSON 截断、browser_automation 删重复+截断、paddleocr 删三重复制。
- **P1（搜索类默认值 + 截断）**：knowledge_base_search / attraction_search / hotel_search 的 top_k 与 table 截断。
- **P2（description/usage_guide 文案治理）**：文档四件套、transfer_to_human、read/write/edit/cp、create_scheduled_task 的 description 瘦身 + 教程迁移。
- **P3（低风险清理）**：echo 输入、debug 字段、死参数、条件必填参数。

### 非目标

- 不改变工具的核心功能语义（read 还是读文件、http_api 还是调 API）。
- 截断阈值的具体数值（2000/5000）待联调时按实际场景校准。
- 本轮只做审查与规范登记，不做代码改动。
