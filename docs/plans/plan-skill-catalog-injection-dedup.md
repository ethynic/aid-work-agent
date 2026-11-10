# 技能目录重复注入去重实施计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | use_skill 工具定义去名单 + 单测 | ✅ 完成（2026-11-10） | 新增 2 用例置于 `tests/unit/test_skill_registry_tool_definition.py`（integration 层宿主机模式因 DB 守卫整模块 skip，unit 层真实执行）：`./scripts/dev_test.sh tests/unit/test_skill_registry_tool_definition.py tests/unit/tools/test_tool_control_set.py tests/unit/tools/test_tool_assembly.py -p no:cacheprovider -q` 20 通过 / 0 跳过；既有 integration/test_skill_flow.py 保持原样 |
| Phase 2 | 独立验证（测试 + CR） | 📋 待开发 | — |

> 日期：2026-11-10；状态：Phase 1 开发+自测完成，待独立验证。改动小，设计与计划合并本文档，无独立设计文档。

## 背景与问题

每次对话请求发给大模型两部分：系统提示词 + 工具清单（function calling 的 tools 参数）。技能名单当前**同时**写入两处：

1. 系统提示 `{skill_descriptions}`：`src/prompts/templates/subagent_base.md:18`（SUBAGENT/STANDALONE 模式）、`master_agent.md:21`（MASTER 模式）
2. `use_skill` 工具 schema description：`SkillRegistry.get_skill_tool_definition()`（`src/core/skill_registry.py:606-632`），经 `ToolControlSet.definitions()`（`src/tools/control_set.py:81`）对所有角色无条件挂载

两处同源（同一个 `_skills` 白名单过滤后的 dict，`skill_registry.py:508-518` vs `:607-610`），内容一致，无授权语义差异。生产 STANDALONE 模式每次会话固定白付一份名单 token，且名单任何变化导致两处提示缓存同时失效。

**范围决策（2026-11-10 与用户确认）**：子智能体目录 4 处重复为 MASTER 专属（`master_agent.md` 模板 3 处 + 委派工具 schema 1 处），当前无客户使用 MASTER 模式，本轮不修；将来统一智能体模式（单类智能体 + 交互原语，另立任务）时自然消亡。

## 设计方案

**决策：技能名单只保留系统提示一处；`use_skill` 工具 description 去掉名单，只留使用规则。**

理由：

- 系统提示"### 可用技能"段本就包裹使用规则（"技能不是工具，首次使用需 `use_skill` 加载操作指南"），技能选型的决策上下文完整，名单放在那里信息密度最高
- 两处同源同过滤，去掉 schema 侧不影响授权语义（`use_skill` 执行时仍有 `is_allowed` 校验兜底，`use_skill_tool.py:62-63`）
- 名单从 schema 移除后，`use_skill` 工具定义变为稳定文本，对提供商侧提示缓存友好

### 改动点

`src/core/skill_registry.py` `get_skill_tool_definition()`：

- description **保留**：技能定位说明（操作手册，加载后按指引决定下一步）、使用流程三步（`use_skill` → 按指南调 `skill_execute` / `content_generate` / `web_search` 等 → 完成后直接最终回复）、"不要跳过步骤，严格按指南执行"注意事项
- description **删除**：末尾"可用技能：\n{skill_list}"及 `skill_list` 构建（607-610 行）
- **保留**空注册表 fallback（返回"暂无可用技能"），语义不变

顺手项（同主题死代码，CR 时确认）：`SkillExecutor.get_skill_descriptions()`（`src/core/skill_executor.py:203-209`）全仓无调用方，删除。

### 明确不改动

- 系统提示模板两个文件的 `{skill_descriptions}` 注入（名单的唯一保留地）
- `use_skill_tool.py:52` 缺参错误返回中的 `available_skills`、`skill_executor.py:209` 同类运行时引导——仅在出错时出现，非每轮固定成本
- MASTER 专属子智能体目录重复（见范围决策）

## 实施计划

### Phase 1：工具定义去名单 + 单测

1. 按改动点修改 `get_skill_tool_definition()`；删除 `SkillExecutor.get_skill_descriptions()` 死代码。
2. 单测调整：
   - 新增断言：definition 的 description 不包含任何已加载技能名（用 fixtures 技能名），且仍包含使用流程关键词（如"操作指南"）
   - 补空注册表用例：fallback 仍返回"暂无可用技能"
   - 现有 `tests/integration/test_skill_flow.py::test_skill_tool_definition` 只断言 name，不受影响；`tests/unit/tools/test_tool_control_set.py` 等使用桩实现，不受影响
3. 运行 `./scripts/dev_test.sh tests/integration/test_skill_flow.py tests/unit/tools/test_tool_control_set.py tests/unit/tools/test_tool_assembly.py -p no:cacheprovider -q`（容器须运行本次工作区代码）。

### Phase 2：独立验证（测试 + CR）

一个独立验证智能体完成（常规风险分级：开发+自测 → 一个独立验证）：

- 测试：复跑 Phase 1 相关测试，确认绿；grep 全仓确认无其他代码依赖 schema description 中的名单（重点：模板/提示词中是否存在"见工具说明中的名单"类反向引用——已核对 `subagent_base.md:16`、`master_agent.md:19` 的用法说明均指向系统提示名单，无此类引用）
- CR 只读审查：对照本文档改动点，确认无名单残留进 schema、无越范围改动、死代码删除无隐藏调用方（含动态 getattr 形式）

### 验证清单

- 相关单测绿（可核验命令与退出状态）
- 容器内渲染冒烟（可选）：构造 STANDALONE 会话，检查 system prompt 与 tools 参数，技能名单仅系统提示出现一次；不打印配置凭据
- **诚实声明验证缺口**：模型行为（技能选型是否退化）无法在本环境验证，需真机观察；理论上名单仍在系统提示，选型依据未变

### 风险与回滚

- 风险低：删除的是模型可见冗余文本，不触碰授权、执行、计费路径；改动单点
- 回滚：还原 `get_skill_tool_definition()` 一处即可

### 登记与提交

- 开发后先更新本计划「开发进度」，再同步 `docs/ideas.md` 索引行；完成后移入 `docs/ideas_finished.md`
- master 禁止直接提交，实施时先开分支；无明确"提交代码"授权不提交（提交说明关联本文档）
