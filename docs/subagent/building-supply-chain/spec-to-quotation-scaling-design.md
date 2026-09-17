# spec-to-quotation-list 大项目扩容设计（轮数、分批编条目、图片体积）

> 关联：建筑供应链智能体（`subagents/building-supply-chain/`）+ spec-to-quotation-list 技能（`src/skills/spec-to-quotation-list-1.0.0/`）。技能引入与端到端验证已于 2026-09-17 完成（`docs/ideas.md` 条目 20260917-1130）。
>
> 状态：📋 设计完成，待开发

## 1. 背景与问题

冒烟验证（3 条目）通过后，真实项目存在三个规模瓶颈，单改任何一项都不够：

| # | 瓶颈 | 现状 | 后果 |
|---|------|------|------|
| 1 | 智能体循环轮数硬编码 20 | `src/core/agent.py:2458`（master）、`agent.py:3507`（子智能体）均为 `max_iterations = 20` | 几百条目的规范书编不完，中途被截断 |
| 2 | 条目编写的 token 消耗 | LLM 在每轮内联写 items.json 片段；几百条目 + 每轮重发历史消息（SKILL.md + references ~470 行 + probe 输出） | 上下文在 32000 tokens 处先于轮数撞墙；token 成本线性膨胀 |
| 3 | xlsx 体积超限 | `generate_quotation_lists.py` 抠图 dpi=100/130、jpg_quality=80/82；validate 以 20MB 告警 | 多业态 × 每条目嵌图，几百条目必然超 20MB，Excel 打开缓慢 |

**目标**：支持单次会话完成 300~500 条目、10+ 拆分文件的真实规范书转换，产物单文件体积可控（目标 ≤15MB/文件）。

## 2. 方案总览

三项联动改造 + 一条运营层缓解：

```
轮数可配置（支撑长流程）
  -> 分批编条目 + 合并（压住 token 增长）
  -> 图片体积预算自动降质（压住产物体积）
```

| 项 | 改动位置 | 改动量 |
|---|---------|--------|
| 2.1 轮数可配置 | `src/core/agent.py` + `src/models/subagent.py` + SUBAGENT.md | 小 |
| 2.2 分批编条目 | 技能 SKILL.md + 子智能体提示词 + 合并脚本（新增小工具） | 中 |
| 2.3 图片体积预算 | `generate_quotation_lists.py` + validate 联动 + SKILL.md | 中 |

## 3. 详细设计

### 3.1 轮数可配置

**原则**：默认值不变（20），只对明确声明的子智能体放开，避免影响存量子智能体与主智能体的行为和成本。

- `SubagentConfig`（`src/models/subagent.py`）frontmatter `context` 段新增可选键 `max_iterations`（int，缺省 20，上限 100）。
- `src/core/agent.py` 子智能体循环处（:3507）改为：
  ```python
  max_iterations = getattr(self.subagent_config.context, "max_iterations", None) or 20
  ```
  master 循环（:2458）保持 20 不动（主智能体不该跑长流程，长任务归子智能体）。
- `subagents/building-supply-chain/SUBAGENT.md` 的 `context` 段加 `max_iterations: 60`。
- 加载层校验：非 int 或 <1 时告警并回退 20；>100 时钳制到 100（防误配把成本放大）。

**成本提示**：轮数只是上限，不改变实际消耗；但配置了大轮数的子智能体单次任务可能跑到 40~60 轮，token 成本需在计费审计时关注（新增调用量走既有 agent 主循环计费通路①，无新增计费缺口）。

### 3.2 分批编条目 + 合并

**核心思路**：把「一次性编完 items.json」改为「按业态/户型分批，每批一个小 JSON，最后确定性合并」。LLM 每轮只关注一个拆分文件的条目（10~25 条），上下文压力被摊平。

- **SKILL.md 流程改造**（第 3 步「编条目」）：步骤 1 完成拆分裁决后，逐文件循环「probe 关键页 -> 编该文件 items 分片 -> 落盘 `$OUT/parts/NN_<业态>.json`」。每个分片沿用现有 schema，但顶层只需 `files: [一个文件对象]` + 该文件用到的 `parameters`。
- **新增合并小脚本** `scripts/merge_items.py`（约 80 行，纯 stdlib）：
  - 输入 `--parts-dir $OUT/parts/ --out $OUT/items.json`
  - 合并规则：`files` 数组顺序拼接；`parameters` 同名 key 后写覆盖前写并在 remark 追加「(合并自 NN)」；`precise_images`/`page_renderings` 字典浅合并；`project` 取第一个非空
  - 冲突检测：同名 `code` 跨分片出现时报错退出（非 0），提示修改分片
- **子智能体提示词**固化分批纪律：每轮编完一批立即落盘并简报进度（条目数/累计条目数），不让 LLM 在上下文里长期持有已完成分片的内容。
- **20 轮预算分配参考**（60 轮上限时）：analyze + probe 普查 3~5 轮；每拆分文件 3~6 轮（probe 定位页 + 编分片 + 修正）；合并 + generate + validate + 交付 4~6 轮。

### 3.3 图片体积预算自动降质

**原则**：询价单场景 72~100 dpi 完全够用（Excel 里图片显示宽度仅 ~80px），体积优先于清晰度。

- `generate_quotation_lists.py` 增加参数：
  - `--dpi`（默认维持 100/130 现状不动，向后兼容）
  - `--jpg-quality`（默认 80/82）
  - `--size-budget-mb`（默认不启用；启用时如 `--size-budget-mb 15`）
- **预算执行逻辑**（生成完所有文件后）：
  1. 按文件实测体积排序，超预算文件进入降质队列
  2. 每轮降质：dpi 与 quality 各乘 0.8（130->104->83->66，floor 60），仅重嵌该文件图片（复用现有 `self.cache` 反向失效）
  3. 单文件最多降质 4 轮，仍超限则在 stdout 告警并把该文件标记为「图片已压缩至最低质量」
- `validate.py` 联动：`--max-mb` 检测到超限时，输出提示改为「建议用 `--size-budget-mb <值>` 重新生成」；SKILL.md 第 6 步流程改为「validate 报体积超限 -> 带 `--size-budget-mb` 重跑 generate -> 复检」，不再依赖人工判断降 dpi。
- 明确不做的方案：图片外链化（需求方要求图片列随文件发供应商，外链会失效且跨租户不可达）。

## 4. 风险与边界

| 风险 | 缓解 |
|------|------|
| 单次会话耗时过长（40~60 轮 × 每轮 LLM 调用） | 子智能体进度事件每轮推送（现有 send_progress 机制），用户可中途取消；取消后已完成分片仍在 `$OUT/parts/`，续跑只需补编未完成分片 |
| LLM 分片间条目重复/编号冲突 | merge_items.py 的 code 冲突硬校验，报错即修 |
| 降质后图片不可辨识 | jpg_quality floor 60 + dpi floor 60，且 validate 保留人工复核项「抽查图片可读性」 |
| 大轮数被其他子智能体误配 | 上限 100 钳制 + 加载告警日志 |
| 参数同名跨分片语义不一致 | 编制纪律写进提示词：全局参数（客房总数等）只在第一个分片定义，分片内不得重定义 |

## 5. 实施清单

1. `src/models/subagent.py`：`context.max_iterations` 字段解析 + 校验
2. `src/core/agent.py:3507`：子智能体循环读取配置
3. `src/skills/spec-to-quotation-list-1.0.0/scripts/merge_items.py`：新增合并脚本
4. `src/skills/spec-to-quotation-list-1.0.0/scripts/generate_quotation_lists.py`：`--dpi/--jpg-quality/--size-budget-mb` + 预算降质循环
5. `src/skills/spec-to-quotation-list-1.0.0/scripts/validate.py`：超限提示文案联动
6. `src/skills/spec-to-quotation-list-1.0.0/SKILL.md`：分批流程 + 体积预算流程改写
7. `subagents/building-supply-chain/SUBAGENT.md`：`max_iterations: 60` + 分批纪律提示词
8. 测试：`tests/unit/` 新增 merge_items 合并/冲突用例；agent 轮数配置解析用例

## 6. 验证方案

- 单测：merge_items.py 合并正确性（files 拼接、parameters 覆盖、code 冲突退出）；max_iterations 解析（缺省 20 / 配置 60 / 越界钳制）
- 集成：用样例 PDF 构造 30+ 条目多分片输入，跑 merge -> generate -> validate 全链路，确认合并产物通过 validate 且生成文件体积 ≤ 预算
- 容器内回归：现有 3 条目冒烟路径不受影响（不带新参数时行为与旧版一致）

## 7. 运营层缓解（不改代码）

超大项目（>500 条目或 >15 拆分文件）可拆多次会话：「先生成客房与公共区文件」，验收后再「生成餐饮与材料标准文件」。分片落盘机制（3.2）保证跨会话续作不需要重编已完成部分。
