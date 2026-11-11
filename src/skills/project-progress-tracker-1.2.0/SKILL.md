---
name: project-progress-tracker
description: >
  多项目进度归集、AI 研判与可视化看板技能。把用户上传的任意格式项目汇报文件（周报/月报/会议纪要/
  验收单/邮件/聊天记录/Excel 台账/PDF/Word/PPT/图片）或知识库里的项目进度资料，统一抽取成
  结构化证据，由 AI 判定每个项目为「超时 / 正常 / 延误」并生成总体情况总结，最终产出一份
  离线可打开的单文件项目看板 HTML（含图表、筛选、详情、录入）；看板录入的新汇报/沟通记录
  经导入脚本回流后重研判、重构建，形成增量闭环。适用于项目组合管理、PMO 周报、多项目
  健康度巡检、交付进度汇报、客户项目跟踪等场景。内置虚构建材集团（瓷砖/岩板）8 项目演示
  数据生成器（make_demo.py），首次体验可直接生成。触发词：项目进度、进度看板、项目看板、
  多项目跟踪、项目状态、超时延误、PMO、项目周报汇总、project dashboard、progress tracker。
metadata:
  version: "1.2.0"
  author: aid-work-agent
requires_tools:
  - read
  - cp
  - image_read
  - knowledge_base_search
  - knowledge_file_search
---

# 多项目进度归集与 AI 看板

## 一、技能定位

解决一个具体的问题：**同一个客户/组织底下同时跑着多个项目，进度信息散落在几十份格式各异的汇报文件和聊天记录里，没人能一眼说清哪些正常、哪些快延误、哪些已经超时，更说不清整体怎么样。**

本技能把这些散落材料收敛成一条流水线：

```
上传文件 / 知识库文档
   -> 统一抽取（任意格式 -> 带来源的文本证据）
   -> AI 研判（每个项目三态 + 依据 + 风险 + 下一步；全局总结）
   -> projects.json（唯一数据真源）
   -> 单文件看板 HTML（离线可开，含图表、筛选、详情、录入）
   -> 新录入的汇报/沟通记录回流 -> 重跑 -> 看板刷新
```

### 边界（不要使用本技能）

- 单个项目的任务级排期管理、日程提醒 -> 走通用对话/待办能力，本技能只做**项目组合层**的进度研判。
- 通用数据分析图表（与项目进度无关的表格统计）-> 用数据分析工具。
- 本技能的看板是**离线单文件 HTML 快照**，不是常驻在线服务；需要持续在线看板时另行评估。

## 二、运行环境注意（必读）

- 每次 `skill_execute` 调用都在独立的临时工作目录中执行，调用间不共享 cwd。**所有脚本必须显式传 `--root "$SKILL_TMP_DIR/ppt-<任务标识>"`**（下文记为 `$PPT_ROOT`）：
  - 同一任务的多次调用使用同一个 `$PPT_ROOT`，中间产物（extracted.json / analysis_packet.md / projects.json / inbox 归档）全部落在里面，持久不清理，支持增量闭环。
  - 技能目录运行时只读，绝不把 `--root` 指向技能目录。
  - 判定规则、看板模板等只读资源脚本自动从技能目录读取（`--skill-root` 保持默认即可，无需传）。
- **研判结论（projects.json）落盘方式**：通过 `skill_execute` 的 `files` 参数携带
  `{"projects.json": "<base64>"}`，命令里先 `cp projects.json "$PPT_ROOT/data/projects.json"`
  再执行后续脚本（files 落在工作目录 cwd，需复制到 `$PPT_ROOT`）。
- 用户上传文件的路径来自会话上传信息（冒号后面是实际上传路径），**始终使用实际上传路径**，不要用原始文件名拼路径。上传文件用 `scripts/stage_inputs.py` 归组到 `$PPT_ROOT/data/sources/<项目名>/`。
- 看板构建完成后用 `cp` 工具**一次性批量交付** `dashboard/index.html`（及需要回传给用户的报告）；聊天回复只给简短结论，不粘贴正文。
- **扫描版（图片型）PDF / 截图类沟通记录**：抽取脚本只能提取元信息（标记 `needs_vision`）。此类文件先用 `image_read` 视觉读取关键内容，再把读出的文本作为 kb 文档落盘（`stage_inputs.py --kb`），不要凭空补全。
- 本地桥接服务 `serve.py` 仅适用于本机桌面场景（如 agent-desktop 本地调试）；对话式交付下用户浏览器无法访问服务端 127.0.0.1，**增量闭环一律走 `ingest_inbox.py` 离线链路**。

## 三、Inputs（数据来源，三选一或混合）

| 来源 | 形态 | 处理方式 |
|---|---|---|
| A 会话上传 | 用户上传的项目周报/月报/会议纪要/验收单/邮件/聊天记录/Excel 台账/PDF/Word/PPT/图片/zip | `stage_inputs.py --project <项目名> --file <上传路径>` 归组；支持 .txt/.md/.csv/.eml/.html/.docx/.xlsx/.pptx/.pdf/图片/.zip（zip 自动展开只读遍历） |
| B 知识库 | 用户说资料在知识库/资料库里 | `knowledge_base_search`（语义）/ `knowledge_file_search`（按文件名定位）检索，检索正文经 `stage_inputs.py --kb --title <标题>` 落盘为 kb_*.md |
| C 混合 | 两处都有 | 合并后去重：同项目同名文件以时间新的为准 |

项目名取子目录名/归组名；归不上组的散文件放 `_未归类/` 并在总结里提示。检索不到时如实记录 `evidence_gaps`，不虚构。知识库检索关键词模板见 `references/knowledge-sources.md`。

## 四、Workflow

### 第 1 步 · 收集与归组

- 来源 A：按会话上传清单逐个 `stage_inputs.py --file`；文件名含项目关键词的自动推断归组，推断不了的问用户（不要猜）。
- 来源 B：按 `references/knowledge-sources.md` 的关键词模板交叉检索，每个项目三组关键词；检索正文落盘 `kb_*.md`。
- 明确判定基准日 `--as-of`（默认今天）。

### 第 2 步 · 抽取证据

```bash
python scripts/extract_sources.py --root "$PPT_ROOT" --as-of 2026-11-11
```

输出 `$PPT_ROOT/data/extracted.json`（每文件一条：项目名、文件名、资料类型、日期、正文、字符数、解析方式）与 `logs/extract_report.md`。零必装依赖：Office/PDF 有第三方库则增强解析，缺失走内置 zip+XML/文本兜底。`needs_vision` 项回填见「运行环境注意」。

### 第 3 步 · AI 研判（packet 模式，对话内默认且唯一路径）

```bash
python scripts/analyze.py --root "$PPT_ROOT" --as-of 2026-11-11 --mode packet
```

生成 `$PPT_ROOT/data/analysis_packet.md`：按项目分组的证据 + 判定规则 + 输出格式要求。**执行体阅读该包，逐项目产出结论**（先读 `references/status-rules.md` 与 `references/data-schema.md`），然后经 `skill_execute` `files` 参数把 `projects.json` 落到 `$PPT_ROOT/data/`。

研判必须做到：

1. 抽取计划时间线（开始/截止/里程碑）与实际进度，算出 `progress` 与 `planned_progress`。
2. 按 `status-rules.md` 判三态，写 `status_reason`，并附支撑结论的原文片段（`sources[].excerpt` 必须是证据原文，不改写）。
3. 提炼 `highlights` / `risks` / `next_actions`，各 2~4 条，具体、可执行，不写套话。
4. 汇总全局：`totals`（脚本会重算）、`overall_status`、`executive_summary`（3~5 句：整体健康度、最该先处理的 2~3 件事、跨项目共性风险）、`top_risks`、`recommendations`。

> **mode=api 说明**：`analyze.py --mode api` 会直连外部 OpenAI 兼容端点（密钥只从环境变量 `PPT_LLM_API_KEY` 读取）。该模式绕过我方模型网关、不经过对话计费，**仅限本机离线批量场景**；对话内研判一律走 packet 模式（研判由主循环大模型完成，正常计费）。不要在服务端配置 `PPT_LLM_API_KEY`。

### 第 4 步 · 构建看板

```bash
python scripts/build_dashboard.py --root "$PPT_ROOT"
```

把 `projects.json` 注入模板生成 `$PPT_ROOT/dashboard/index.html`（内嵌数据，**双击即可离线打开**）。构建时校验必填字段与三态取值，缺字段直接报错而不是渲染空白卡片；重算后的 `totals` / `days_remaining` 回写 `data/projects.json`。校验失败必须修复 projects.json 后重跑，不得绕过。

### 第 5 步 · 交付

用 `cp` 工具一次性批量交付 `dashboard/index.html`。回复里只给简短结论：共几个项目、三态各几个、整体判断、最关键的 2~3 个风险、看板附件；详细内容全部留在看板里，不把大段正文粘回聊天窗口。

### 第 6 步 · 增量更新（离线闭环）

看板上每个项目都能录入新汇报文本/沟通记录。**新记录不会自己改变项目状态**，必须走完本链才生效：

1. 用户在看板点「导出待分析包」得到 `inbox.json`，重新上传到对话。
2. 运行 `ingest_inbox.py --root "$PPT_ROOT" --inbox <上传路径> --as-of <基准日> --chain`：自动把每条记录按项目写进 `data/sources/<项目名>/inbox_*.md`、归档原包、串联抽取与分析（packet 模式）。
3. 执行体阅读更新后的 `analysis_packet.md` 研判，按第 3 步方式合并进 `projects.json`（沿用原 `id` 对齐历史，`status_history` 追加），再跑第 4 步刷新看板并重新交付。
4. 浏览器无法传出的二进制附件，脚本会在 `logs/inbox_ingest.md` 列出「需人工补入的文件」——请用户把原文件直接上传到对话，再走第 1 步归组。

历史证据全部保留，判定变化写入 `status_history[]`，看板详情面板会画出完成率变化曲线。

### 首次体验（演示数据）

```bash
python scripts/make_demo.py --root "$PPT_ROOT" --as-of 2026-11-11
python scripts/build_dashboard.py --root "$PPT_ROOT"
```

生成虚构建材集团（华瓷建材集团，瓷砖/岩板）8 个演示项目与 9 类样例源文件（3 正常 / 3 延误 / 2 超时），可直接构建看板体验。演示数据带 `demo: true` 标识，接入真实资料后整体替换，不允许把演示结论混进真实汇报。

## 五、Critical Rules（铁律）

1. **不编造进度**。每个项目的状态、完成率、日期、风险，都必须能回溯到 `sources[]` 里的具体文件与原文片段；材料不足时写 `confidence` 低值 + `evidence_gaps`，把结论标为「待补充材料」，绝不猜一个好看的数字。
2. **三态定义唯一**，以 `references/status-rules.md` 为准：**超时 = 已越过计划节点仍未完成；延误 = 尚未到期但进度已落后 / 存在实质阻塞；正常 = 进度符合计划且无重大风险**。超时与延误不是同义词，不许混用。
3. **依据可追溯**。AI 结论里每一条 `summary` / `risks[]` / `next_actions[]` 都要能对应到证据摘录；看板详情面板会展示这些摘录和出处文件名。
4. **原始资料只读**。绝不改写、重命名、删除用户上传的文件，也不改动知识库原文。所有产物只写进 `$PPT_ROOT`。
5. **数据真源只有一个**：`$PPT_ROOT/data/projects.json`。看板 HTML 由它构建生成，不允许手工改 HTML 里的数字来「修」看板。
6. **密钥与计费**。不索要、不读取、不落盘任何明文密钥。对话内研判一律 packet 模式；`--mode api` 与 `serve.py` 仅限本机离线场景，服务端不得配置 `PPT_LLM_API_KEY`（见第 3 步说明）。
7. **演示数据与真实数据隔离**。演示项目带 `demo: true`，接入真实资料后整体替换；行业信号词见 `references/status-rules.md` 第三节，换行业只需替换素材，判定规则不变。

## 六、Resources

### references/

- `status-rules.md` — **三态判定规则（权威口径）**：定义、判定优先级、阻塞信号词（含行业化词表）、完成率口径、置信度、全局结论口径（第 3 步必读）。
- `data-schema.md` — `projects.json` 字段规范与校验规则（第 3 步产出前必读）。
- `knowledge-sources.md` — 知识库检索关键词模板、落盘规范、去重与冲突处理（来源 B 使用）。

### scripts/（均通过 skill_execute 调用）

- `stage_inputs.py` — **上传文件/知识库正文归组**（我方新增）：`--project <项目名> --file <上传路径>` 复制归组；`--kb --title <标题> --text-file <正文路径>` 落盘 kb_*.md。
- `extract_sources.py` — **多格式抽取器**：`data/sources/` -> `data/extracted.json` + 抽取报告；纯机械不做判断。
- `analyze.py` — **研判包生成**：证据包 -> `data/analysis_packet.md`（packet 模式）；`--mode api` 为本机离线自动研判备用（见铁律 6）。
- `build_dashboard.py` — **看板构建**：`projects.json` + 模板 -> 单文件离线看板，含字段校验与 totals 重算回写。
- `ingest_inbox.py` — **增量导入**：看板导出的 inbox.json -> 按项目落盘 -> `--chain` 串联抽取/分析。
- `make_demo.py` — 生成建材行业演示数据与 9 类样例源文件（仅首次体验用）。
- `serve.py` — 本地桥接服务（仅本机桌面场景；对话式交付不使用）。
- `selftest.js` — Node 看板渲染自检（无需浏览器，开发验证用）。

### assets/

- `dashboard.template.html` — 看板模板（占位符 `__DASHBOARD_DATA__`），由 build_dashboard.py 注入数据。

## 版本

- v1.2.0 · 2026-11-11 · 自 workbuddy 上游 v1.2.0 移植：运行时适配（`--root` 数据目录与只读技能目录分离、新增 `stage_inputs.py` 适配会话上传归组与知识库检索落盘、projects.json 经 files 参数落盘、增量闭环改离线 ingest 链路、演示数据改为 make_demo.py 按需生成）；知识库通道改接 knowledge_base_search / knowledge_file_search。
- 上游 v1.2.0 · 2026-11-10 · 演示数据整体改为建材集团场景；v1.1.0 新增 serve.py 一键重分析；v1.0.0 首版。
