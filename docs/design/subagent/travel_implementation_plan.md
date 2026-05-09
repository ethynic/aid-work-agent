# 旅游子智能体重构实施计划

> 版本: v1.0 | 创建: 2026-05-09 | 状态: ✅ 全部完成

## Context

根据 `docs/design/subagent/travel_subagent_design.md`（v5.1），重构旅游咨询子智能体。核心变更：

1. **3 个 skill 合并为 1 个 `quote-generate`** — trip-planner（规划指导→SUBAGENT.md）、quote-generator + quote-export（查库+计算+导出→合并为一个 skill）
2. **10 张定价数据表** — 从知识库检索改为数据库结构化查询
3. **extra.md 租户定制** — 人设/风格/策略/模板路径从硬编码改为租户级配置文件
4. **后台 CRUD API** — 前端管理页面维护定价数据

## 实施阶段

---

### Phase 0: extra.md 租户定制基础 — ✅ 完成

- [x] **Task 0.1**: AgentRouter + Factory + main.py 传递 tenant_id
- [x] **Task 0.2**: Agent._build_system_prompt() 加载 extra.md（新增 `_load_extra_md()` 方法）
- [x] **Task 0.3**: 创建 `storage/subagents/travel-consultant/` 目录
- [x] **Task 0.4**: `src/api/subagent_extra.py` — GET/PUT/DELETE extra.md API

**修改文件**：
- `src/core/agent_router.py` — get_agent() 新增 tenant_id 参数
- `src/subagents/factory.py` — create_standalone_subagent() 新增 tenant_id 参数
- `src/core/agent.py` — 新增 `_load_extra_md()` 方法，`_build_system_prompt()` 中追加 extra.md
- `src/main.py` — 两处调用点提前提取 tenant_id 并传入
- `src/api/subagent_extra.py` — 新建

---

### Phase 1: 数据库表 — ✅ 完成

- [x] **Task 1.1**: `deploy/init-postgres.sql` + `deploy/db_update.sql` 添加 10 张表
- [x] **Task 1.2**: `src/api/travel_quote.py` — 通用 CRUD API（含区域、车辆、景点、门票、酒店、房型、餐标、导游、费用、淡旺季）

**修改文件**：
- `deploy/init-postgres.sql` — 追加 10 张 `bs_travel_quote_*` 建表语句
- `deploy/db_update.sql` — 追加增量变更（带日期注释）
- `src/api/travel_quote.py` — 新建

---

### Phase 2: SUBAGENT.md 重写 — ✅ 完成

- [x] **Task 2.1**: 重写为通用旅游顾问（v3.0.0）
  - skills.allowed 改为 `[quote-generate, paddleocr-doc-parsing]`
  - 移除硬编码人设/风格（"小旅"等）
  - 保留通用对话流程（阶段 1-6）和沟通技巧
  - 更新报价功能说明为单个 quote-generate skill 调用
  - 新增完整的参数收集清单

**修改文件**：
- `subagents/travel-consultant/SUBAGENT.md` — 完整重写

---

### Phase 3: 统一 quote-generate Skill — ✅ 完成

- [x] **Task 3.1**: 创建 `src/skills/quote-generate/SKILL.md`
- [x] **Task 3.2**: `src/skills/quote-generate/scripts/generate.py` — 核心脚本（~600行）
  - init_tables() 10 张表自动建表
  - 区域展开查询、季节判断
  - 车型推荐算法、排房计算、门票按票种计算
  - 餐饮、导游、其他费用计算
  - Excel 模板导出（支持 {{变量}} + {{#items}} 行复制）
  - 无模板时降级为内置简单格式
- [x] **Task 3.3**: `src/skills/quote-generate/templates/default.xlsx` — 默认模板

**新建文件**：
- `src/skills/quote-generate/SKILL.md`
- `src/skills/quote-generate/scripts/generate.py`
- `src/skills/quote-generate/templates/default.xlsx`
- `src/skills/quote-generate/templates/create_default_template.py`（模板生成辅助脚本）

---

### Phase 4: 清理旧 Skill — ✅ 完成

- [x] **Task 4.1**: 删除 `src/skills/trip-planner/`、`quote-generator/`、`quote-export/`

**删除文件**（通过 git rm）：
- `src/skills/trip-planner/SKILL.md`
- `src/skills/quote-generator/SKILL.md`
- `src/skills/quote-generator/scripts/calculate.py`
- `src/skills/quote-export/SKILL.md`
- `src/skills/quote-export/scripts/export_xlsx.py`
