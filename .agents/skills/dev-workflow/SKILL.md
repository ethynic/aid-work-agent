---
name: dev-workflow
description: 三智能体开发流程（开发→测试→CodeReview→提交）。当用户要求开发新功能、Phase 开发、多文件改动、或提到"走开发流程"/"三智能体"/"开发+测试+codereview"时使用。确保提交前代码经过独立测试和审查，避免服务器因 build/启动错误挂掉。
metadata:
  author: aid-work-agent
  version: "1.0.0"
argument-hint: <开发任务描述>
---

# 三智能体开发流程

针对非平凡的代码开发任务（新功能、Phase 开发、重构、多文件 bug 修复），按本流程串行执行三个独立智能体，确保代码质量和上线安全。

> 完整规范见 [.claude/rules/dev_workflow.md](../../../.claude/rules/dev_workflow.md)。本 skill 是该规范的可触发版本。

## 何时使用

- ✅ 新功能 / Phase 开发 / 触及启动链路（main.py、scheduler、配置）的改动
- ✅ 多文件改动（>2 个文件）
- ✅ 用户明确要求"走流程"/"三智能体"/"开发+测试+codereview"
- ⚡ 单行修复 / typo / 纯文档：直接改，不走流程
- ⚡ 紧急 hotfix：直接改+自测+提交，事后补 CR

## 执行步骤

### 步骤 0：准备
1. 读取相关设计文档和现有代码，理解需求和既有模式。
2. 若需求不明确或有多种实现方式，用 AskUserQuestion 澄清后再开工。
3. 建立任务 todo list。

### 步骤 1：开发智能体
用 Agent 工具（subagent_type: general-purpose）启动开发智能体，prompt 自包含：
- 仓库路径、背景、要实现什么
- 关键技术事实（已验证的列名、模式、入口点等，避免智能体重复探索）
- 约束：遵循既有代码风格（中文注释）、配置 settings.py+config.yaml 同步、写单测、自测全绿
- 约束：**不提交、不 push**
- 要求报告：改动文件、测试结果、遇到的问题

### 步骤 2：测试智能体（开发完成且自测通过后启动）
用 Agent 工具启动测试智能体，prompt 自包含：
- 告知开发智能体改了什么文件、相关测试在哪
- 要求：跑新测试 + 回归（改动模块全量 + 相邻模块）+ **启动安全检查**
- 启动安全检查清单：
  - 后端语法：`python -c "import ast; ast.parse(open('<file>',encoding='utf-8').read())"`
  - 后端 import：`python -c "from <module> import <新符号>"`
  - 配置加载：`python -c "from src.config.settings import create_settings; s=create_settings(); print(...)"`
  - 前端：`cd frontend && npm run build`（若有前端改动）
- 要求：修复明显问题（import 错、SQL 错、签名不匹配、断言弱化）；预先存在/环境问题记录不修
- 约束：**不提交、不 push**
- 要求报告：测试命令+通过/失败数、修复了什么、最终结论（是否全绿+启动安全）

### 步骤 3：CodeReview 智能体（测试全绿且启动安全后启动）
用 Agent 工具启动 CR 智能体，prompt 自包含：
- 告知改动文件列表
- 审查重点（按严重度）：正确性 bug > 资源/安全（SQL注入/连接泄漏）> 并发/异步 > 启动安全（import 破坏）> 配置一致性 > 测试质量
- 要求：`git diff` 看全部 + 读新文件全文；按 P0/P1/P2/nit 列发现
- 要求：P0/P1 必修（自己修+重跑测试）；P2/nit 只报告
- 约束：**不提交、不 push**
- 要求报告：按严重度排序的发现（标 FIXED/REPORTED-ONLY）、最终结论（是否可安全提交）

### 步骤 4：提交前验证（主控者亲自做，不委托智能体）
三个智能体都通过后，主控者自己核验关键结论：
1. **import/build 终检**（自己跑一遍，不盲信报告）：
   - 后端关键启动路径 import
   - 前端 build（若有前端改动）
2. **fetch 最新远程**：`git fetch origin`，若有新提交先 stash + 合并 + 解决冲突。
3. **暂存相关文件**：`git add <仅本次任务相关文件>`，排除无关 untracked 文件。
4. **提交**：中文 commit message，说明改动 + 测试结果 + 流程通过情况。
5. **推送**：`git push origin master`。

> Git 规范见 [AGENTS.md](../../../AGENTS.md)：不自动提交，仅用户明确说"提交代码"才提交。

## 关键原则

1. **串行不并行**：后一个智能体等前一个完成才启动。
2. **独立判断**：每个智能体不复用前者的结论，自己验证。
3. **启动安全优先**：触及 main.py/scheduler/配置的改动，提交前必须验证服务器能启动。
4. **主控者兜底**：智能体报告后，主控者亲自核验测试数和 import，不能盲信。
5. **不提交不 push**：三个智能体都只改代码，提交和推送由主控者在最后一步统一做。

## 输出

每步完成后向用户简要汇报：当前在哪一步、结果如何、下一步是什么。全部完成后给出完整总结。
