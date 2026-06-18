# 主智能体系统提示词优化 — 开发计划

> 关联设计：[docs/system/prompt/agent-system-prompt-optimization-design.md](../docs/system/prompt/agent-system-prompt-optimization-design.md)
> 反向关联：[docs/infrastructure/prompt-lifecycle-design.md](../docs/infrastructure/prompt-lifecycle-design.md)

## 阶段总览

| 阶段 | 任务 | 状态 | 预估 |
|------|------|------|------|
| Phase 1 | 重写 `master_agent.md` | 📋 待开发 | 1h |
| Phase 2 | 重写 `subagent_base.md` | 📋 待开发 | 0.5h |
| Phase 3 | 清理 `travel-consultant/SUBAGENT.md` | 📋 待开发 | 0.5h |
| Phase 4 | 场景验证 | 📋 待开发 | 1h |

---

## Phase 1：重写 master_agent.md

### 任务清单

- [ ] **1.1** 备份当前 `src/prompts/templates/master_agent.md`（git 历史已有，无需手动备份）
- [ ] **1.2** 按设计文档 §3.2 重写，分 6 段：
  - 第 1 段：身份与使命
  - 第 2 段：核心工作原则（6 条原则）
  - 第 3 段：能力边界（保留变量 `{available_tools_list}` / `{skill_descriptions}` / `{subagent_descriptions}`）
  - 第 4 段：输出与沟通规范（新增 narration 控制）
  - 第 5 段：文件交付规则（★核心新增）
  - 第 6 段：上下文注入（保留变量 `{subagent_constraint_section}` / `{long_term_memory}` / `{user_info_section}` / `{reply_style_section}`）
- [ ] **1.3** 保留 `{subagent_matching_hint}` / `{tool_usage_guides}` / `{delegation_guide}` 变量
- [ ] **1.4** 验证：渲染后字符数减少 ≥ 30%

### 关键约束

- 变量名必须与 `agent.py:706-717` 完全一致
- 不能新增/删除变量（避免改 Python 代码）
- 模板使用 `str.format_map` 替换，`{{` / `}}` 转义要保留

### 验收

- [ ] 渲染后的系统提示词包含「文件交付规则」段落
- [ ] 渲染后的系统提示词包含「核心工作原则」6 条
- [ ] 字符数较原版减少 ≥ 30%

---

## Phase 2：重写 subagent_base.md

### 任务清单

- [ ] **2.1** 按设计文档 §3.3 重写，与 master_agent.md 结构对齐
- [ ] **2.2** 差异点：
  - 删除「可用子智能体」段落
  - 末尾保留"⚠️ 你不能调用 delegate_to_subagent"
- [ ] **2.3** 文件交付规则段落与主模板**完全一致**（复制粘贴）

### 验收

- [ ] 渲染后的子智能体提示词包含「文件交付规则」段落
- [ ] 不包含子智能体相关内容

---

## Phase 3：清理 travel-consultant/SUBAGENT.md

### 任务清单

- [ ] **3.1** 删除行 269-273（详细行程生成 Word 后的注册步骤）——已被主提示词第 5 段覆盖
- [ ] **3.2** 删除行 276-277（`register_download_file` 是必须步骤的强调）
- [ ] **3.3** 删除行 405-408（报价后必须调用 `register_download_file`）
- [ ] **3.4** 删除行为约束第 11 条（行 455 附近）
- [ ] **3.5** 不保留 travel-quote 单行提示（主提示词规则已覆盖，详见设计文档 §4.3）

### 修改示例

**修改前**（详细行程第四步，行 269-277）：
```markdown
3. **调用 `register_download_file` 注册文件下载**：
   ```
   register_download_file(file_path="word_process返回的file_path", display_name="XX行程方案.docx")
   ```
4. 告诉客户 Word 已生成

⚠️ **严格规则**：必须先在对话中展示详细行程 → 客户确认 → 再生成 Word...
⚠️ **`register_download_file` 是必须步骤，不是可选的！**...
```

**修改后**：
```markdown
3. 调用 `word_process` 生成 Word
4. 告诉客户 Word 已生成

⚠️ **严格规则**：必须先在对话中展示详细行程 → 客户确认 → 再生成 Word...
```

**说明**：注册下载的步骤由主提示词第 5 段通用规则约束（"任何工具/skill 生成文件后必须调用 register_download_file"），子智能体不再重复说明。

### 验收

- [ ] 文件中 `register_download_file` 残留为 0
- [ ] 业务流程描述（三阶段、行程格式、报价触发）保持完整
- [ ] 不再包含 `register_download_file` 的具体调用步骤（主提示词已覆盖）
- [ ] 保留"行程未变不重复报价"业务逻辑（工具名改为 cp）

---

## Phase 4：场景验证

### 测试场景

启动开发服务器后，用以下 5 个场景对比新旧提示词的 agent 行为：

| # | 场景 | 预期行为 | 验证点 |
|---|------|---------|--------|
| 1 | 用户上传 Excel 让分析 | 直接调用分析工具，不说"正在执行" | narration 控制 |
| 2 | 用户让生成一份会议纪要 Word | 调用 `word_process` 后**必须**再调用 `register_download_file`，用户才能看到下载卡片 | 文件交付规则（word_process 不在前端白名单） |
| 3 | 旅游顾问流程：客户确认行程 → 生成 Word → 报价 | `word_process` 后调用 `register_download_file`；`travel-quote` 后也调用 `register_download_file` | 文件交付规则（统一行为） |
| 4 | 用户让委派外贸智能体 | 直接 `delegate_to_subagent`，不创建计划 | 核心原则 1、3 |
| 5 | 用户问一个完全超出能力的问题 | 明确告知无法完成，提供替代方案 | 核心原则 5 |
| 6（关键回归） | 调用 `write` 或 `cp` 生成文件 | **不**再重复调用 `register_download_file`（这两个工具已自动注册） | 文件交付规则（例外项） |

### 验收

- [ ] 5 个场景全部通过
- [ ] 无回归（其他正常功能不受影响）

---

## 上线检查清单

- [ ] Phase 1-4 全部完成
- [ ] 在 `docs/ideas.md` 中将本条目状态更新为 ✅
- [ ] 在 `docs/ideas_finished.md` 中登记
- [ ] git commit + push（不自动，等用户确认）
