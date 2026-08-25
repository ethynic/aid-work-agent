# 审查：邮件工具整体梳理（优化 / 新增 / 删除）

> 2026-08-19。背景：邮件是项目最早实现的工具，先于开发规范存在；新场景（#63 邮件→附件→Excel ETL）要求邮件侧达标。本文为全量审查结论。

## 1. 邮件相关的全部资产

| 资产 | 位置 | 状态 |
|---|---|---|
| 3 个工具：email_send / email_read / email_list_folders | `src/tools/email/email_tool.py`（687 行单文件） | 已注册（agent.py:436-438），user_id 运行时注入凭据 |
| 凭据模型 + 加密存储 | `src/db/email_credential.py` + `user_email_settings` 表 | 密码 Fernet 加密、API 返回掩码，合规 |
| 绑定/查询/删除/测试邮件 API | `src/api/email_settings.py` | 保存前发测试邮件验证 SMTP，设计合理 |
| 前端绑定入口 | `frontend/web/components/SettingsDialog.vue` | 存在 |
| 测试 | tests/test_email_tool.py(5) + unit/tools(9) + integration(7) + e2e(1, 无凭证 skip) | mock 为主，覆盖 happy path |
| 调度/轮询/邮件渠道 | — | 不存在（当前按需拉取模式，够用，不建议建轮询） |

## 2. 问题清单（按严重级，含证据）

### P0 —— bug 与稳定性风险（必须修）

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| 1 | **事件循环阻塞**：imaplib/smtplib 同步调用直接写在 `async def execute` 里 | email_tool.py:320-507（read）、161-236（send）；对照规范实践：word 工具已用 `asyncio.to_thread`（md_to_word.py:118） | 收 N 封邮件期间整个 worker 卡死，多租户并发互相拖累 |
| 2 | **软删除后重绑丢配置**：upsert 的 DELETE 分支 `return True` 提前返回，新配置未插入 | email_credential.py:61-69（"恢复已删除邮箱配置"注释下的早退） | 用户解绑再绑定后永远"未绑定邮箱" |
| 3 | **无超时**：IMAP/SMTP 连接与操作均未设 timeout | email_tool.py:348-364（IMAP4_SSL 无 timeout 参数） | 网络挂起 → 请求永久挂起 |
| 4 | **连接泄漏**：`mail.close()/logout()` 不在 finally | email_tool.py:489-490（正常路径才关闭） | 异常路径 IMAP 连接不释放，服务器连接数耗尽 |
| 5 | **过滤时整封拉取**：本地过滤 from/subject 时对 `limit×3` 封邮件逐封 `BODY.PEEK[]`（整封原始字节含附件） | email_tool.py:412-418 + 425 | 流量/延迟放大 3 倍以上；应先 `BODY.PEEK[HEADER]` 过滤、命中再拉全信 |

### P1 —— 规范违反与体验缺陷

| # | 问题 | 证据 | 修法 |
|---|---|---|---|
| 6 | 错误返回裸 `str(e)` | email_tool.py:255、504-506 | 接 `sanitize_error`（`src/tools/_helpers.py` 白名单机制） |
| 7 | 调试残留：打印 IMAP 原始响应 + "普通搜索模式作为对比"整段死代码（每次多一次全量 search） | email_tool.py:397-405 | 删除 |
| 8 | HTML 邮件正文为空：`_get_email_body` 只认 text/plain | email_tool.py:530-559 | 加 text/html 分支（strip 标签简版即可），企业邮件大量 HTML-only |
| 9 | body_preview 硬编码 200 字 | email_tool.py:477 | 参数化（默认 500） |
| 10 | 硬编码邮箱写死中文文件夹语义假设、folder 解析靠字符串切分 | email_tool.py:299-318 | 低优先级，够用不动 |

### 删除/合并候选（2026-08-19 用户确认：三合一）

- **"普通搜索对比"死代码段**（#7 内）→ 直接删。
- **三个工具合并为一个 `email_process`（action 分发），EmailListFoldersTool 取消独立形态**：
  - `action: send | read | download_attachments`，参数随 action 区分；folders 信息改为 read 返回的 `folders` 字段 + folder 参数报错时附可用列表提示。
  - **关键差异：不建内部 LLM 路由器**（区别于 excel_process）——邮件三动作语义泾渭分明，按 action 参数确定性分发，零路由 token/延迟；正好规避测算发现的每次路由 ~0.75 积分成本。
  - 收益：系统提示词工具 schema 3 份→1 份（每轮 agent 循环的固定输入 token 下降）；新附件下载能力作为 action 自然并入，不再是第 4 个工具。
  - 迁移：一次性改测试引用（tests 4 个文件）、subagent 允许工具清单、提示词工具名。
- 生产日志里的原始 IMAP 响应打印 → 删。

### 新增（新场景必需，#63 已定 D21~D24，并入 email_process 的 read/download_attachments action）

- read 返回附件元信息（filename/content_type/size，含 RFC2231 中文文件名解码）
- download_attachments 落盘（uid + 附件名过滤 + 目标目录，25MB 上限）
- `since` 日期过滤（IMAP 服务器端 SINCE）
- UID 水位幂等（skill 侧记录，工具不用改）

### 可选（本场景不需要，不排期）

- 发送带附件（MIMEMultipart 结构已具备，加参数即得）
- IMAP 凭据与 SMTP 凭据分离（现共用一对，主流邮箱够用）

## 3. 建议行动（三步走）

1. **第一步·修 bug + 合规 + 三合一（预计 1~2 天）**：P0 #1~#5 + P1 #6~#9 + 工具合并为 `email_process`（action 确定性分发）。改动：`asyncio.to_thread` 包裹、连接 timeout、finally 关闭、两段式 fetch（HEADER 先行）、`sanitize_error`、删调试代码、HTML 正文 fallback、preview 参数化。回归测试补：HTML 正文、两段式过滤、upsert 重绑恢复（#2 专项）、三合一后 action 分发。
2. **第二步·新场景能力（随 #63 P0 一起做）**：D21~D24 附件四件套，直接落进 email_process 的 read/download_attachments action。
3. **第三步·结构化（顺手）**：拆分 `email_lib.py`（IMAP/SMTP 连接与解析纯函数）+ 工具层薄壳，与 excel 目录结构对齐，便于单测。

> 拆分建议随第一步一起做更划算：反正要动 687 行的单文件，直接按新结构落，避免改两遍。
