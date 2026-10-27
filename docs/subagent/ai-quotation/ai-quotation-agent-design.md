# AI报价智能体设计（quotation-pricing 技能移植 + ai-quotation 子智能体）

> 日期：2026-10-27
> 来源：workbuddy 第三方技能包 `quotation-pricing` v1.0.0（用户在 workbuddy 系统中探索沉淀）
> 整合流程：遵循 [.claude/rules/skill_integration.md](../../../.claude/rules/skill_integration.md)

## 1. 背景与目标

已有子智能体「建筑行业方案清单生成器」（`subagents/building-supply-chain/`）能把设计规范 PDF 转成「室内材料报价清单」Excel（`spec-to-quotation-list-2.0.0` 技能），但清单生成后单价列留空、需人工询价填写。

workbuddy 的 `quotation-pricing` 技能补上了下游环节：承接报价清单 Excel，按知识库（产品价目表/资料库）匹配产品与价格填入单价，产出带来源标注与替换留痕的已报价文件。

**目标**：移植该技能，并新建子智能体「AI报价」（`subagents/ai-quotation/`），同时挂载上游生成技能与下游填价技能，形成「spec PDF → 清单 → 知识库报价」端到端闭环；也支持用户直接上传外部同类清单只走填价流程。

## 2. 来源技能包盘点

```
quotation-pricing/
├── SKILL.md                 # 分阶段流程：解析 → 确认范围 → KB匹配 → 缺项建议价 → 回填 → 复核
├── references/kb_search.md  # 知识库检索与规格一致性判定指南（workbuddy 工具口径）
├── references/workflow.md   # 流程参考
├── sample/                  # HappyRoom 示例（原清单 + priced 产物 + items_extracted.json），68K
└── scripts/
    ├── parse_quotation.py   # 清单 xlsx → items_extracted.json（155 行）
    ├── fill_quotation.py    # JSON 回填 Excel：I 列单价、M/N 列来源说明、替换差异 sheet（141 行）
    ├── review.py            # 范围内逐项复核，输出 review_report.json（77 行）
    └── _make_sample.py      # 生成示例用，不移植
```

设计评价（正面）：
- 脚本只做确定性解析/回填/校验，智能环节（检索、规格判定、建议价）留给 agent 对话完成——与本项目「技能=领域知识包」架构完全匹配
- 两个确认门（先定范围后填价、缺项建议价需用户同意）、来源强制标注（`知识库-KB` / `知识库-KB(替换)` / `AI建议参考价`）、替换必须留痕、review 末道闸门——符合本项目「不确定时诚实承认」原则
- 仅依赖 openpyxl，无第三方特殊依赖

## 3. 关键决策一：spec-to-quotation-list-2.0.0 列序对齐上游

### 3.1 问题

上游脚本假定主表列序（其 output_format 口径）：

```
A 编号 / B 描述 / C:D 区域(合并) / E 数量 / F 单位 / G 品牌 / H 图片 / I 单价 / J 合价 / K 数量依据 / L 依据
```

我方 `generate_quotation_lists.py` 实际写入：

```
A 编号 / B 产品类别(简称) / C 描述 / D 空间 / E~L 同上
```

B/C/D 三列语义错位。若不改，上游 `parse_quotation.py` 会把描述拆到类别列、把整段描述当成区域。

### 3.2 决策：改我方生成器对齐上游列序

理由：
1. `output_format.md` 写的本来就是上游列序——生成器当初为「B 列类别便于筛选」偏离了文档但未同步文档，**对齐即修复文档/代码不一致**
2. `validate.py` / `merge_items.py` 均不校验 xlsx 列位（针对 items.json），不受影响
3. `spec-to-quotation-list-2.0.0` 无现有单测，无回归包袱；无租户实际使用
4. 列序与上游一致后，quotation-pricing 三脚本可**原样移植零适配**；后续上游增量合并零摩擦（符合 skill_integration.md「默认保持与上游兼容」）
5. 损失仅「B 列类别简称筛选」——类别信息本就由 `■` 分区行承载

### 3.3 生成器改动点（`scripts/generate_quotation_lists.py`，局部）

1. `HEADERS`：B → `Description\n材料/产品名称与规格`；C:D 合并为 `Area / Location\n区域`
2. `WIDTHS`：B 加宽至 ≈46，C:D 合计 ≈18
3. 数据行写入：desc 移到 B 列；区域合并写入 C:D；删除「类别简称入 B 列」；E~L（数量/单位/品牌/图片/单价/合价公式）全部不动
4. ◆ 空间抬头 / ■ 类别分区 / 小计 / 合计行的合并写法不变——上游 `parse_quotation.py` 行过滤逻辑（`is_item_row` 要求 A、B 同有值 + `^■` 正则 + total 正则）对这几种行天然兼容（◆/小计行 B 列为空被排除，■ 行被正则排除，已逐一核对）

## 4. 关键决策二：技能移植设计

### 4.1 目录结构 `src/skills/quotation-pricing-1.0.0/`

```
SKILL.md                     # 重写（见 4.2）
references/kb_search.md      # 重写（workbuddy 工具口径 → 我方工具口径）
references/workflow.md       # 上游内容适配后保留
scripts/parse_quotation.py   # 原样移植
scripts/fill_quotation.py    # 原样移植
scripts/review.py            # 原样移植
sample/                      # 保留（roundtrip 单测夹具 + 使用示例）
```

`_make_sample.py` 不移植。

### 4.2 SKILL.md 重写要点

- **定位**：承接 `spec-to-quotation-list-2.0.0` 产出的报价清单（也兼容外部同类列序清单），按知识库填价；版本 1.0.0
- **frontmatter 声明** `requires_tools: [knowledge_base_search, knowledge_file_search]`——加载时经 `SkillLoader` / `apply_skill_requirements` 自动补全工具勾选
- **工具口径替换**：
  - workbuddy `knowledge_search` → 我方 `knowledge_base_search`（语义检索）
  - 新增：价目表类资料优先 `knowledge_file_search` 按文件名定位（价目表/产品目录文档），再 `knowledge_base_search` 语义检索规格匹配
  - `AskUserQuestion` → 我方 `ask_user`（两个确认门均 `allow_skip=false`；交互方式参照 building-supply-chain SUBAGENT.md 既有用法）
  - Excel 回传 → `cp` 工具 `register_download=True`，输出 `<原名>_priced.xlsx` 不覆盖原文件
- **检索边界**：检索范围由工具层收口（栏目授权 `resolve_category_scope`、跨租户共享 `load_shared_ranges` 已在 `knowledge_base_tool` / `knowledge_file_search` 内实现），技能层不越界、不自行拼租户条件
- **一致性判定强化**（针对上游薄弱点，价格错配直接变成错误报价）：
  - KB 命中多条候选时，`kb_match` 必须记录来源文档名；关键规格（尺寸/材质/颜色/品牌/型号）任一不确定即降级 `source="待确认"`，不得自动按替换成交
  - 「替换与差异 Replacements」sheet 交付前必须经用户过目确认
- **币种约束**：清单与知识库价目币种不一致时不得直接填，标 `待确认`
- **v1 不嵌图片**：`replacement_image` 留文字差异说明。原因：知识库图片经 ImageRegistry 链路（file_id/下载 URL/租户目录），与技能脚本临时目录模型不匹配，v1 避免路径与权限复杂度；后续有需求再接 ImageRegistry

### 4.3 kb_search.md 重写要点

- 来源 A：我方知识库（`knowledge_base_search` / `knowledge_file_search`），检索词 = 条目「中文名/英文名 + 关键规格」；知识库检索要求租户已把价目表/产品资料入库（knowledge 栏目）
- 来源 B：用户会话内上传的本地价目表（Excel/CSV）——存于 `$SKILL_TMP_DIR`，脚本/openpyxl 读取建内存索引，精确优先、未命中再走知识库语义检索
- 保留上游「关键规格判定表 + 来源标注规范」章节（口径不变：`知识库-KB` / `知识库-KB(替换)` / `AI建议参考价` / `未报价` / 我方新增 `待确认`）

## 5. 子智能体设计：AI报价（subagents/ai-quotation/）

- **name**: AI报价；目录名 `ai-quotation`（路由 `/chat/ai-quotation`）；version 1.0.0；author system
- **skills.allowed**: `spec-to-quotation-list` + `quotation-pricing`
- **capabilities**: `spec_pdf_parsing` / `quotation_list_generation` / `kb_price_matching`
- **triggers**: file_patterns `*.pdf` `*.xlsx`；keywords 报价单 / 报价填写 / 填价 / 套价 / 材料清单 / FF&E / 知识库报价
- **tools**: inherit true
- **context**: `max_input_tokens: 32000`、`max_output_tokens: 8000`、`max_iterations: 60`（参照 building-supply-chain 大项目实践）
- system_prompt 写在 body（不进 YAML frontmatter，规避 loader `---` 分割陷阱），三段：
  1. spec PDF 转清单：走 spec-to-quotation-list 流程（两个结构确认门）
  2. 清单填价：走 quotation-pricing 流程（范围确认门 → KB 匹配 → 缺项建议价确认门 → 回填 → 复核）
  3. 边界声明：价格以知识库为唯一可信来源；AI 建议价必须标注来源且声明「非成交价」；不编造规格与数量；替换必须留痕并经用户确认
- 不注册业务页面（无 bs_ 表，产物为 Excel 交付，走 cp 回传）

## 6. 白名单、计费与安全分析

| 项 | 结论 |
|---|---|
| 技能白名单 | 子智能体运行时读管理后台/DB `subagent_definitions.skills.allowed`，配 1 处即可；主智能体白名单不动 |
| LLM 计费 | 技能脚本零 LLM 调用；KB 检索 embedding 计费在 `knowledge_base_tool` 工具内部已有（billing_audit §3.6 工具内统一计费）；建议价环节是 agent 主循环对话，走通路①。**无新增计费缺口** |
| 租户隔离 | 检索走既有工具收口（栏目授权 + 共享范围）；价目表等资料天然是租户私有知识库内容；脚本产物在会话临时目录，交付经 cp 登记到租户 conversation 场景 |
| 敏感信息 | 价格与产品规格非敏感类；无凭证类占位符需求 |

## 7. 测试方案

`tests/unit/test_quotation_pricing_skill.py`：

1. **roundtrip 全链路**：用上游 `sample/HappyRoom_家具软装.xlsx`（上游列序）→ parse（断言 B 描述拆分、C:D 区域、E~L 抽取正确）→ 手工填 JSON（含替换项）→ fill（断言 I/M/N 回填、Replacements sheet 生成、输出 `_priced.xlsx`）→ review（断言复核统计）
2. **生成器列序回归**：修改后的 `generate_quotation_lists.py` 用最小 items.json 冒烟生成 xlsx，再喂 parse 断言抽取正确（两技能衔接验证）
3. **非法输入**：无表头文件、空 JSON、`in_scope` 全 false、unit_price 非数值、文件缺失
4. **注册发现**：SkillRegistry 加载 quotation-pricing、SKILL.md frontmatter 解析、requires_tools 声明可被 `apply_skill_requirements` 补全

## 8. 实施顺序

1. 改 `generate_quotation_lists.py` 对齐上游列序 + 冒烟验证（步骤 2 的前提）
2. 移植 quotation-pricing 三脚本（原样）+ 重写 SKILL.md / kb_search.md / workflow.md
3. 建 `subagents/ai-quotation/SUBAGENT.md`（AI报价）
4. 单测 + 容器内 CLI 冒烟（parse sample → 填 JSON → fill → review 全链路）
5. 登记 `docs/ideas.md`（本条目）

风险等级：**常规**——纯新增技能与子智能体 + 生成器局部列序调整，不碰计费/调度/租户隔离核心代码。流程：开发 + 一个独立验证智能体（跑单测 + 审 diff）。

## 9. 已知限制与后续方向

- v1 替换差异不嵌图片（见 4.2）
- 知识库价格质量取决于租户价目资料入库质量；未入库时大量条目会落入「AI 建议参考价」或「待确认」，属预期行为
- 建议价由大模型估算，SKILL.md 强制标注「非成交价，需复核」，不与知识库价混淆
- 后续可选：价目表 Excel 结构化入库（独立 source_type 栏目 + 表级名单）、替换项图片经 ImageRegistry 嵌入、报价历史版本留痕

---

## 开发进度

### 2026-10-27 开发完成（待提交部署）

按第 8 节顺序全部完成，独立验证智能体（测试 + CodeReview）通过，无 P0/P1：

1. **列序对齐**：`generate_quotation_lists.py` HEADERS/WIDTHS/表头 C6:D6 合并/数据行 desc→B、区域逐行 merge C:D；SKILL.md、output_format.md（列宽同步）、docstring 旧列序残留已清。容器内冒烟：最小 items.json 生成 → 上游 parse_quotation.py 抽取全对（code/area/描述/数量/单位/品牌、行号定位）。
2. **技能移植**：`src/skills/quotation-pricing-1.0.0/` 三脚本原样移植，SKILL.md / kb_search.md / workflow.md 按第 4 节要点重写，sample 保留作夹具。
3. **子智能体**：`subagents/ai-quotation/SUBAGENT.md` 创建，SubagentLoader 加载验证（skills.allowed 两技能、body 作为 system_prompt）。
4. **测试**：`tests/unit/test_quotation_pricing_skill.py` 11 例全绿（注册发现 / requires_tools 声明 / sample roundtrip 全链路 / 生成器列序回归 / 未报价复核 / 非法输入）；回归 `tests/unit/skills + test_ai_lead_gen_skill.py` 262 例全绿；全量技能注册 30 个加载正常。
5. **CR 修复**：SKILL.md:118 与生成器 docstring「D 列」旧口径、output_format.md 列宽与代码不一致（既有偏差）均已修。

已知差异（无害）：上游 sample 表头 C6:D6 未合并而我方生成器合并——parse 不依赖表头合并，不影响兼容。

**待办**：提交部署后真机验收（真实 LLM 对话端到端：转清单 → 填价 → 替换确认门；知识库价目资料入库质量观察）。

