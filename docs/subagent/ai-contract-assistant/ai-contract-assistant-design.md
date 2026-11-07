# AI合同助手（ai-contract-assistant）设计说明

> 2026-11-07 首次移植 workbuddy 技能包 contract-generator，按 skill_integration.md 流程整合。本文档记录移植决策与适配点，供后续增量合并对照。

## 定位

子智能体「AI合同助手」（`subagents/ai-contract-assistant/`，目录名 `ai-contract-assistant`），按租户自有合同模板起草、生成合同文稿。四步固定工作流（技能铁律）：

1. 匹配模板 → 2. 运行时分析模板得字段清单（analysis.json，按模板 hash 缓存）→ 3. 单轮问答收集 → 4. stdin 一次回填 + 报告自检（fill_report.json）。

**边界**：合同审批走 contract-approval、合同核验走 eas-contract-verify、Excel 报价清单走 spec-to-quotation-list；不提供法律咨询。

## 资产

| 资产 | 来源 | 说明 |
|------|------|------|
| `src/skills/contract-generator-1.0.0/` | 上游 ext_package/contract-generator 整体拷贝 + SKILL.md 适配 | scripts 5 个上游脚本未改动；新增我方胶水 `scripts/fetch_file.py` |
| `subagents/ai-contract-assistant/SUBAGENT.md` | 新写 | skills.allowed: [contract-generator]；tools.additional: [knowledge_base_search, knowledge_file_search] |

## 我方补丁清单（下轮合并上游时逐项核对）

| 补丁 | 三选一结论（2026-11-07 首次移植） |
|------|------|
| SKILL.md frontmatter（metadata.version/author、dependencies、requires_tools） | 我方层，上游不涉及，保留 |
| 「运行环境注意」节（skill_execute 独立临时 cwd、产物走 `$SKILL_TMP_DIR/contract_work/<客户简称>/` 绝对路径） | 我方层，保留 |
| 工作目录/缓存位置：`$SKILL_TMP_DIR/contract_work/_analysis_cache/` | 我方层，保留 |
| 模板四渠道节（数字员工模板文件 file_id / 知识库文档 / 会话上传 / 平台内置 assets/templates/）+ fetch_file.py 用法 | 我方层，保留 |
| `knowledge_search` → `knowledge_base_search`、`present_files` → `cp` 工具 | 我方层，保留 |
| `scripts/fetch_file.py`（file_id 经 resolve_uploaded_file_path 解析；--src fail-closed 限 storage/tenants/ 下） | 我方胶水，合并时保留我方版本 |

## 模板获取（用户已确认口径）

优先级：① 数字员工「模板文件」（提示词「### 可用模板文件」file_id）→ ② 知识库文档（knowledge_file_search 返回 file_path）→ ③ 用户会话上传 .docx → ④ 技能包 `assets/templates/`（平台内置，初始为空）。渠道 ①②③ 先经 fetch_file.py 归位到 `$OUT/templates/` 再交给 prep.py。

## 取价

`knowledge_base_search` 检索租户知识库报价清单为唯一权威价格来源；价格库 CSV（用户会话上传）仅兜底；模板示例价绝不作为定价依据（技能铁律）。

## 计费

技能脚本纯 python-docx 处理，无 LLM/Embedding/ASR 调用点，无计费审计项；工作流中的分析由 Agent 主循环自身完成（已按 billing_audit.md 核对）。

## 验证状态

- 单测 6 用例全绿（`tests/unit/test_contract_generator.py`：注册发现、requires_tools、prep→analysis→fill 端到端（大写/合计/清单 3 条/缓存入桶）、二次 prep 缓存命中、fetch_file 租户路径放行 + 越界拒绝 + 缺参拒绝）
- 容器加载验证：SkillRegistry 加载 contract-generator v1.0.0（scripts 含 fetch_file.py）；SubagentRegistry 加载 ai-contract-assistant，白名单/工具声明正确
- **缺口（待部署真机）**：真实租户模板全链路对话验收；knowledge_base_search 真实取价；file_id 真实归位

## 已知限制

- `$SKILL_TMP_DIR`（storage/tmp）为进程级共享目录，合同中间产物按 `<客户简称>` 子目录隔离；跨租户隔离依赖 skill_execute 现有边界（与 spec-to-quotation-list 同口径），未新增风险面。
- 上游「把模板放进 assets/templates/」仍保留但降级为渠道④；租户级模板管理复用现有「我的数字员工-模板文件」机制，未新增上传入口。
