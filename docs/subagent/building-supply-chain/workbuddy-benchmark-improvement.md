# spec-to-quotation-list WorkBuddy 对标改进

> 背景：同一输入（Hotel101 Global Design Specs Book 85 页 PDF），
> WorkBuddy 18 分钟、60+ 次 LLM 调用完成 8 份报价清单 289 条条目；
> 本系统 agent 走子智能体 20 轮即达上限中断（trace `tr_a63c834ebbe241c7`，
> 测试环境 254）。本文记录对标分析结论与已回写技能的改进，
> WorkBuddy 产物与过程代码留存于 `/mnt/c/Users/Ric/WorkBuddy/建筑供应链/`。

## 1. 失败根因（2026-09-17 已修）

| 根因 | 修复 |
|------|------|
| Web 独立模式子智能体聊天走 `Agent.process_message`，迭代上限硬编码 20，`SUBAGENT.md` 的 `context.max_iterations: 60` 不生效 | `src/core/agent.py` 改为 `self.subagent_config.get_max_iterations()`，与 `delegate_to_subagent` 委托路径一致 |
| LLM 用 12 轮逐段调 `probe_pdf.py` 探查 85 页，3 轮分 3 段读 595 行脚本源码，`items.json` 一字未写即耗尽轮数 | SKILL.md 流程 §3 增加硬约束：probe 一次调用传多个页段（全书 1~2 次探完）；禁止 read 脚本源码 |

## 2. WorkBuddy 工作流对标（关键差异）

WorkBuddy 复用的正是本技能脚本（`~/.workbuddy/skills/spec-to-quotation-list/`），
差异在工作流模式：

| 关键做法 | 本技能当时的差异 | 状态 |
|---------|----------------|------|
| 第一步 dump 全文（74KB fulltext.txt 一次进上下文），写条目不回查 | 12 轮 probe 分段探查弥补"上下文没有全文" | P2，未实施（probe 批量化后已缓解） |
| 条目以 Python 代码为载体（3 个 45~57KB 的 `it_*.py`，内嵌 it/G/F helper，一个文件覆盖 1~3 个拆分文件） | skill_execute 命令内联 JSON 分片，每轮仅 10~25 条 | P2，未实施 |
| 合并时统一自检：qty_rule 引用的参数名必须存在 | merge_items.py 只查编号冲突 | ✅ 已回写（见 §3.4） |
| 取图"宁缺勿错"：自动兜底图每文件每图限 1 次 | 自动兜底无去重 | ✅ 已回写（见 §3.1） |

轮数/成本参照：旧 trace 20 轮烧 102 万 tokens（每轮约 5 万，大头为重复 system prompt + 历史）；
按新模式估全流程 25~35 轮、约 1.5~2M tokens。WorkBuddy 的
**289 条 / 43 组 / 已算量 92% / 嵌图 140 张** 可作为该测试案例的重测 benchmark。

## 3. 技能改进（2026-09-17 已回写 `src/skills/spec-to-quotation-list-1.0.0/`）

来源：WorkBuddy memory（`.workbuddy/memory/2026-09-17.md`）标注的实战回写，
核对确认本仓库副本全部缺失后移植。

### 3.1 兜底图去重 — `scripts/generate_quotation_lists.py`

`build_file` 增加 `used_auto_imgs` 集合：自动兜底（标题匹配 / 章节效果图 / 页面最大图 /
文件兜底）取得的图每文件限用 1 次，重复时留空（宁缺勿错）；条目显式指定图
（`img` / `precise_images`）不受限。否则做法类/系统类文件会出现几十行贴同一张空间效果图。

### 3.2 留空占位写法与 xref=0 坑 — `references/items_json_schema.md`

- 强制留空占位：`"img": ["x", 页号, -1]`（不存在的 xref，取图静默留空），用于
  "确定不配图但要阻止自动兜底"的条目。
- **占位 xref 禁止用 0**：部分 PDF 第 1 页真实存在 xref=0 的图片对象，占位会静默失效。

### 3.3 validate.py 目录页崩溃修复 — `scripts/validate.py`

症状：遇到目录页等非报价清单工作簿，表头单元格是数字时 `need in (h or "")` 抛
`TypeError`（`h or ""` 返回 int）。修复：

- 表头统一 `str()` 转字符串后再比较；
- 缺「参数 Parameters」页时记录 issue 并跳过逐行校验（提前 return，含完整统计键）。

### 3.4 参数引用自检 — `scripts/merge_items.py`

合并后校验每条 `qty_rule`：`area` 模式取 `param`、`expr` 模式取 `{参数名}`，
引用的参数名必须存在于合并后的 `parameters`，否则列出前 20 处（定位到条目 code）
并退出非 0。避免生成后才发现公式是死引用。

### 3.5 任务台账（跨会话续作手柄）— `SKILL.md` 流程 §6

WorkBuddy memory 的交付台账结构：交付统计、拆分方案、**关键参数及推导依据**、
**待填参数清单**、待办事项。借鉴为 SKILL.md 强制收尾步骤：交付前必须产出
`$OUT/summary.md` 并 cp 注册下载。用户后续"外墙面积确定了重算 06 文件"时，
新会话凭台账即可续作，参数推导依据不再只活在当次对话里。

## 4. 端到端实测（2026-09-17，本地容器 + deepseek-v4-flash）

实测脚本：`scripts/e2e_spec2quote_test.py`（容器内运行，逐轮打印 tokens/工具调用，
命中上限自动「继续」续跑，结束对生成目录跑 validate.py）。

| 指标 | 本系统实测 | WorkBuddy 参照 |
|------|-----------|---------------|
| LLM 调用（=迭代轮数） | **39 轮**（上限 60，续跑 0 次） | 60+ 次（口径不同，含轻量子调用） |
| 总耗时 | **约 12 分钟** | 18 分钟 |
| 工具调用 | 62 次 | — |
| tokens | input 518 万（**99% 缓存命中**，未缓存仅 5.8 万）/ output 14.6 万 | — |
| 产出 | 9 文件 + summary.md 台账，182 条 / 已算量 75% / 嵌图 117 | 8 文件 + 目录页，289 条 / 92% / 140 图 |

**结论：60 轮上限够用（余量约 35%），轮数不是瓶颈。**

实测发现并修复的真瓶颈：

1. **输出上限失配**（已修）：`configs/config.yaml` `llm.model_max_tokens` 在模型改名
   `deepseek-v4-flash` -> `deepseek-flash` 后只保留新名 key；`.env` 仍用旧名时匹配不到，
   回退全局默认 16384 -> 长输出截断、工具调用 JSON 不完整、agent 循环**静默退出**。
   已补旧名别名 `deepseek-v4-flash: 393216`。教训：按模型名索引的映射表改名时要同时
   保留新旧两个 key。
2. **截断静默退出**（P2 待修）：LLM 输出被截断（finish_reason=length）且工具调用解析
   失败时，循环按"空最终回复"正常结束，用户端无任何提示。应检测 finish_reason 并
   提示续跑。

质量差距（182/75% vs 289/92%）：条目覆盖偏少、09 材料标准文件几乎全空量（WorkBuddy
同文件也不算量，可比口径下差距仍存）。改善方向即 §5 的 P2 项（全文优先、Python 分片）。

## 5. 后续建议（P2，未实施）

1. **全文优先模式**：`analyze_pdf.py` 后若全书文本 ≤ 100KB，一次 dump 全文进上下文，
   probe 仅保留 `--all` 图片普查 + 定点 `--search`（2~3 轮）。
2. **Python 分片模式**：条目分片从 JSON 内联改为每轮写一个 `parts/it_x.py`
   （内嵌 helper，一个分片覆盖 1~3 个拆分文件、50~100 条），转义安全、单轮 payload 大 5~10 倍。
3. 超时兜底文案改中文 + 已完成步骤简报 + "发送『继续』续跑"（配合 `_continuation_tool_result`）。
