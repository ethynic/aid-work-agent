# 每周例行巡检规范（周四 11:30）

> **目的**：每周固定巡检计费遗漏、租户存储违规、error/warning 日志、tool_error 终态失败、tlog 残留五类问题，输出报告并建立逐项处理闭环。
>
> **触发方式**：Claude Code 持久定时任务（cron `30 11 * * 4`，durable）。仅当 Claude Code 处于运行且空闲状态时触发；错过时可在会话中直接说「执行每周巡检」按本规范手动执行。
>
> **报告落盘**：`docs/audit/weekly-YYYY-MM-DD.md`（取执行当日日期）。报告中的问题项按 P0/P1/P2 分级，逐项处理时引用报告编号，处理完在报告中勾销。

---

## 0. 环境与访问速查

| 环境 | 服务器 | SSH | 容器 | 宿主机日志 | 主库 | 日志库 |
|------|--------|-----|------|-----------|------|--------|
| 生产 | 129.211.65.243 | `ssh -p 10167 ubuntu@129.211.65.243`（密钥 `deploy/ssh_key`） | aid-agent-api / aid-postgres | `/var/www/agent/log` | `aid_work_agent` | `aid_work_logs` |
| 测试 | 124.222.3.254 | `ssh ubuntu@124.222.3.254` | aid-agent-api2 / aid-postgres | `/var/www/agent2/log` | `aid_work_agent2` | `aid_work_logs2` |

- psql 统一经容器执行：`sudo docker exec aid-postgres psql -U aid_user -d <库名> -c "<SQL>"`（两台服务器 postgres 容器同名）
- 测试库公网 5433 可直连；生产库走 SSH 隧道（见 [postgres.md](../deploy/postgres.md) §4）
- 详细路径与排障见 [服务器部署现状.md](../deploy/服务器部署现状.md) §11
- **安全红线**：巡检以只读为主；连接串/密码/令牌不得写入报告；发现可疑入侵迹象先报告用户，不自行处置

---

## 1. 计费遗漏增量自检（本地代码，只读）

依据 [billing_audit.md](../../.claude/rules/billing_audit.md) §3 checklist 做**增量**核对：

1. 读上一份 `docs/audit/weekly-*.md`，取出其中的 LLM/Embedding/ASR/视频/短信调用点清单。
2. 重新执行 §3.1 / §3.2 / §3.3 / §3.4 / §3.8 的 grep 扫描，与上次清单 diff，找出**新增调用点**。
3. 对每个新增调用点读上下文（前后 50 行），按 §3.5「已计费五条件」逐点核对；不满足任一条件的记为缺口。
4. 工具类改动（§3.6 入口分离）与同步/异步问题（§3.7）只核对本周 git log 涉及的文件。
5. 报告输出：新增调用点数、缺口清单（含文件:行号、场景、建议修复模式 §4.x）、无缺口时明确写「本周无新增计费缺口」。

## 2. 租户附件存储违规检查（两环境）

依据 [backend_dev.md](../../.claude/rules/backend_dev.md)「租户附件存储规范」。对每台服务器执行（只读）：

```bash
# 散落在 storage/ 根目录的文件（应只存在 tenants/ 等已知顶层目录）
ssh <srv> "find /var/www/agent{,2}/storage -maxdepth 1 -type f 2>/dev/null"

# 旧路径 uploads/ 近 7 天的新写入（新代码禁止写 uploads/）
ssh <srv> "find /var/www/agent{,2}/storage/uploads -type f -mtime -7 2>/dev/null | head -50"

# 租户目录下跳过场景子目录直接存放的文件（深度 2 出现文件即违规）
ssh <srv> "find /var/www/agent{,2}/storage/tenants -mindepth 2 -maxdepth 2 -type f 2>/dev/null | head -50"

# 未知顶层目录
ssh <srv> "ls /var/www/agent/storage /var/www/agent2/storage"
```

- 逐条核对命中项：是否属于 `tenants/{tid}/{scene}/` 合法结构（scene ∈ conversation/knowledge/export/report/memory/avatar/temp/…）；`documents.file_path` 等登记值与磁盘路径抽样比对（按环境连主库 `SELECT file_path FROM documents ORDER BY doc_id DESC LIMIT 20`）。
- 历史遗留（旧 uploads 存量）记为「已知遗留」不算新违规；**只记录，不删除**，删除动作需用户确认。

## 3. 近 7 天 error / warning 日志收集（两环境）

1. 主库 `log_error` 表（仅 ERROR，含堆栈）：

```sql
SELECT module, error_type, left(message, 120) AS message, count(*) AS cnt, max(timestamp) AS last_seen
FROM log_error
WHERE timestamp > NOW() - INTERVAL '7 days' AND status = 'unprocessed'
GROUP BY 1, 2, 3 ORDER BY cnt DESC LIMIT 50;
```

2. 宿主机主日志近 7 天 WARNING/ERROR 主题分布（按周轮转文件取本周份）：

```bash
ssh <srv> "grep -hE '(ERROR|WARNING)' /var/www/agent{,2}/log/aid-work-agent.log* 2>/dev/null \
  | grep -oE '(ERROR|WARNING)[^:]{0,80}' | sort | uniq -c | sort -rn | head -30"
```

3. 汇总去重聚类，报告中列出：错误主题、次数、最后出现时间、影响面（租户/渠道）、初步定性。已在本周或既往巡检处理过的主题标注「已知」，避免重复立项。

## 4. tool_error 终态失败 trace 排查（两环境日志库）

筛选条件：带 `tool_error` 标签、非 running、且满足「反复发生 ≥3 次 / 消耗大量 token（单 trace total_tokens > 5000）/ 最终任务失败」任一条。

```sql
-- 聚类：按错误聚合，定位反复发生 + token 消耗
SELECT left(COALESCE(error_code, error_message), 150) AS err, count(*) AS cnt,
       sum(total_tokens) AS total_tokens, max(created_at) AS last_seen
FROM obs_traces
WHERE created_at > NOW() - INTERVAL '7 days'
  AND tags @> '["tool_error"]' AND status <> 'running'
GROUP BY 1 ORDER BY total_tokens DESC, cnt DESC LIMIT 30;

-- 明细：top token 消耗的单个 trace
SELECT trace_id, session_id, tenant_id, created_at, total_tokens,
       left(COALESCE(error_code, error_message), 200) AS err
FROM obs_traces
WHERE created_at > NOW() - INTERVAL '7 days'
  AND tags @> '["tool_error"]' AND status <> 'running'
ORDER BY total_tokens DESC LIMIT 30;

-- 失败工具分布
SELECT name, count(*) AS cnt FROM obs_spans
WHERE success = false AND start_time > NOW() - INTERVAL '7 days'
GROUP BY 1 ORDER BY cnt DESC LIMIT 20;
```

- 对命中 trace 必要时按 [服务器部署现状.md](../deploy/服务器部署现状.md) §11.3 用 trace_id 下钻 obs_spans 定位失败工具与根因。
- 报告输出：每类问题一行（错误摘要、次数、总 token、涉及 trace_id 样例、初步根因、建议动作），标注与既往巡检重复的已知项。

## 5. tlog 残留清理（本地代码，可修改）

依据 [backend_dev.md](../../.claude/rules/backend_dev.md)「临时主题日志」：tlog 仅用于临时调试，bug 修复后必须删除。判定标准：**调用 tlog 的源文件最近一次 git 修改距今 ≥ 14 天**，视为临时日志已完成任务。

```bash
# 1. 列出所有 tlog 调用所在文件
grep -rln "tlog(" src/ --include="*.py"

# 2. 每个文件的最后修改时间
git log -1 --format="%ci %h" -- <file>
```

处理规则：

1. 距今 ≥ 14 天的文件 → 候选清理；< 14 天的保留并在报告中列明（含最后修改日期）。
2. 候选文件逐个删除 tlog 调用行及 `from src.core.temp_logger import tlog` import；**不得改动任何业务逻辑**。
3. 清理后运行受影响模块的定向测试（`./scripts/dev_test.sh <相关测试> -p no:cacheprovider -q`）；无法确定测试范围时至少做 import 检查。
4. 改动停留在工作区**不提交**，报告列出清理文件清单与验证结果，由用户决定何时提交。
5. 交叉核对两环境 `log/temp/` 实际产生的主题文件（`ssh <srv> "ls -la /var/www/agent{,2}/log/temp/"`），生产仍在高频写入但本地已判定可清理的主题，在报告中单独标注提醒。

---

## 6. 报告模板

```markdown
# 每周巡检报告 YYYY-MM-DD

执行方式：定时任务 / 手动 | 执行人：Claude Code

## 1. 计费遗漏自检
- 新增调用点：N 处（缺口 M 处）
| 调用点 | 场景 | 五条件核对 | 修复建议 |
|---|---|---|---|

## 2. 租户存储违规
| 服务器 | 路径 | 问题 | 定性（新违规/已知遗留） |
|---|---|---|---|

## 3. error/warning 日志（近 7 天）
| 主题 | 次数 | 最后出现 | 环境 | 定性 | 状态 |
|---|---|---|---|---|---|

## 4. tool_error 终态失败
| 错误摘要 | 次数 | 总 token | trace 样例 | 初步根因 | 状态 |
|---|---|---|---|---|---|

## 5. tlog 清理
- 已清理：N 个文件（清单 + 验证结果）
- 保留（<14 天）：清单
- 生产仍在写入的已清理主题：提醒

## 待处理清单（P0/P1/P2）
- [ ] ...
```

报告头部「状态」列沿用既往报告口径：`新发现` / `已知-观察中` / `处理中` / `已处理(日期)`。
