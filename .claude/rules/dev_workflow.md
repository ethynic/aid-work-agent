# 开发流程规范：三智能体开发流程（开发 → 测试 → CodeReview）

> 适用：所有非平凡的代码开发任务（新功能、Phase 开发、重构、bug 修复涉及多文件）。
> 简单的单行修复、文档修改、typo 不需要走完整流程。
> 本规范是 ZCode / Claude Code / Codex 三种工具**共同遵循**的开发流程，确保代码质量可控、上线安全。

---

## 0. 为什么要这个流程

代码直接提交后服务器会自动更新，**提交即上线**。一旦提交的代码有 build 错误或启动错误，服务器直接挂掉。本流程通过三个独立智能体的串行校验，把问题拦截在提交前：

- **开发智能体**：写代码，自测通过
- **测试智能体**：独立运行测试 + 回归，修复明显问题（不复用开发智能体的判断）
- **CodeReview 智能体**：独立审查，修复必要问题（不复用前两者的判断）

三个智能体**串行执行，后一个等前一个完成才启动**，每个都做独立判断，避免"自己写的自己测"的盲区。

---

## 1. 流程总览

```
开发智能体（实现 + 自测）
    │  完成
    ▼
测试智能体（独立测试 + 回归 + 修复明显问题）
    │  测试全绿
    ▼
CodeReview 智能体（独立审查 + 修复必要问题）
    │  CR 通过
    ▼
提交前验证（import/build 安全检查）
    │  通过
    ▼
fetch + commit + push
```

**关键原则**：后一个智能体拿到的是前一个的产出，但必须**独立验证**，不能假设前一步是对的。

---

## 2. 智能体 1：开发

### 职责
实现需求/设计文档对应的代码，写对应单测，自测通过。

### 要求
1. 先读相关设计文档和现有代码，复用既有模式（不要重复造轮子）。
2. 遵循项目既有代码风格（中文注释、命名规范、错误处理范式）。
3. 配置变更要 `settings.py` 和 `configs/config.yaml` **同步**（字段名一致）。
4. 写单测，覆盖核心路径（命中/未命中/异常/边界）。
5. 自测：`./scripts/dev_test.sh <相关测试> -p no:cacheprovider -q` 必须全绿（脚本自动探测环境，容器在跑走 `docker exec aid-agent-api`，否则走宿主机 `python`，详见 §8）。
6. **不提交、不 push**。完成后报告：改动文件、测试结果、遇到的问题。

### 交接给测试智能体
明确告知：实现了什么、改了哪些文件、相关测试在哪、已知的风险点。

---

## 3. 智能体 2：测试

### 启动时机
**开发智能体完成且自测通过后**才启动。

### 职责
独立运行测试，验证质量，修复**明显的**问题。

### 要求
1. 跑新功能的测试：`./scripts/dev_test.sh <新测试文件> -p no:cacheprovider -q -v`
2. **回归测试**：跑改动模块的全量测试 + 相邻模块，确认没破坏既有功能。
3. **启动安全检查**（关键，避免服务器挂）：
   - `./scripts/dev_test.sh` 自动走容器/宿主机环境，下面命令同样需要走对应环境（见 §8）：
     - 语法检查：`docker exec aid-agent-api python -c "import ast; ast.parse(open('<改动文件>',encoding='utf-8').read())"`（宿主机环境去掉 `docker exec aid-agent-api` 前缀）
     - import 检查：`docker exec aid-agent-api python -c "from <改动模块> import <新增符号>"`
     - 涉及配置：`docker exec aid-agent-api python -c "from src.config.settings import create_settings; s=create_settings(); print(...)"`
   - 涉及前端：`cd frontend && npm run build` 必须 0 错误
4. 自己读一遍实现，检查明显 bug（列名拼错、异常未隔离、参数未参数化等）。
5. 修复**明显的**问题（import 错误、SQL 错误、签名不匹配、测试断言被弱化）。修复要最小化、外科手术式。
6. **预先存在/环境的**问题（缺可选依赖、e2e 需真实服务）记录但不修。
7. 修复后**重跑**相关测试确认绿。
8. **不提交、不 push**。报告：测试命令 + 通过/失败数、修复了什么、未修的及原因、最终结论（是否测试全绿 + 启动安全）。

---

## 4. 智能体 3：CodeReview

### 启动时机
**测试智能体确认全绿 + 启动安全后**才启动。

### 职责
独立审查代码，修复**必要的**问题。

### 审查重点（按严重度）
1. **正确性 bug**（P0/P1）：逻辑错误、SQL 错误、列名/属性错、签名不匹配。
2. **资源/安全**：SQL 注入（f-string 拼值）、连接泄漏（缺 commit/close）、异常未处理致崩溃。
3. **并发/异步**：异步路径里的阻塞调用、竞态、事件循环误用。
4. **启动安全**（P0）：import 是否破坏 main.py 启动链路、是否有循环 import、lazy import 是否正确。
5. **配置一致性**：yaml 和 settings.py 字段名/默认值是否一致。
6. **测试质量**：测试是否真的断言了正确行为，还是 mock 过度变成空测。

### 要求
1. 先 `git diff` 看全部改动 + 读新文件全文。
2. 按严重度（P0 > P1 > P2 > nit）列出发现，每条给 file:line + 问题 + 修复建议。
3. **P0/P1 必修**：自己修复，修完重跑测试确认绿。
4. P2/nit 只报告不修（除非 trivial）。
5. 严格但公平，不编造问题。
6. **不提交、不 push**。报告：按严重度排序的发现（标 FIXED 或 REPORTED-ONLY）、修复了什么、最终结论（是否可安全提交）。

---

## 5. 提交前验证（主控者执行，非智能体）

三个智能体都通过后，**主控者**（即接收用户指令的那个 agent）自己做最终验证：

1. **import/build 终检**（自己跑一遍，不依赖智能体报告，环境走法见 §8）：
   - 后端：`docker exec aid-agent-api python -c "from src.scheduler.manager import ..."` 等关键启动路径 import（宿主机环境去掉 `docker exec aid-agent-api` 前缀）
   - 前端：`cd frontend && npm run build`（若有前端改动）
2. **fetch 最新远程**：`git fetch origin`，若有新提交需先合并解决冲突。
3. **暂存相关文件**：排除无关的 untracked 文件（只提交本次任务的改动）。
4. **提交**：中文 commit message，说明改动 + 测试结果 + 流程通过情况。
5. **推送**：`git push origin master`。

> 见 [AGENTS.md](../../AGENTS.md) Git 提交规范：不自动提交，仅用户明确说"提交代码"才提交；提交前 fetch、检查冲突、push。

---

## 6. 何时可以简化流程

| 任务类型 | 流程 |
|---------|------|
| 新功能 / Phase 开发 / 多文件改动 | ✅ 完整三智能体流程 |
| 单文件 bug 修复（含测试） | 🔧 开发 + 测试两步（CR 可选） |
| 单行修复 / typo / 纯文档 | ⚡ 直接改，自测即可 |
| 紧急 hotfix | ⚡ 直接改 + 自测 + 提交，事后补 CR |

**判断标准**：改动是否触及启动链路（main.py、scheduler、配置加载）或多个模块？是→完整流程。

---

## 7. 智能体调用的技术约束

- 三个智能体用 `Agent` 工具（subagent_type: `general-purpose`）**串行**调用，不要并行。
- 每个智能体的 prompt 必须**自包含**：告知仓库路径、背景、改了什么文件、要做什么、约束（不提交不 push）、报告格式。
- 智能体返回后，主控者**核验关键结论**（如测试通过数、import 是否真的 OK），不能盲信报告。
- 涉及"提交代码可能导致服务器更新"的场景，主控者必须亲自做 import/build 终检。

---

## 8. 本机执行环境（容器 vs 宿主机）

项目依赖（PostgreSQL 连接池、Redis 降级、skill 加载链路、loguru 等）在团队成员间有两种部署：

| 环境 | 部署方式 | 识别方法 |
|------|---------|---------|
| 容器环境 | 所有依赖在 `aid-agent-api` docker 容器内 | `docker ps` 能看到 `aid-agent-api` |
| 宿主机环境 | 依赖直接装在宿主机 python | `docker ps` 无 `aid-agent-api` |

**统一入口**：`./scripts/dev_test.sh <pytest 参数>` 自动探测环境——容器在跑走 `docker exec aid-agent-api python -m pytest`，否则走宿主机 `python -m pytest`。

**命令前缀规则**：

| 命令类型 | 容器环境 | 宿主机环境 |
|---------|---------|-----------|
| pytest | `./scripts/dev_test.sh ...` | `./scripts/dev_test.sh ...`（脚本自动降级） |
| python -c | `docker exec aid-agent-api python -c "..."` | `python -c "..."` |
| 前端 build | `cd frontend && npm run build`（两端相同） | 同左 |

**智能体约定**：
- pytest 命令一律走 `./scripts/dev_test.sh`，不要直接写 `python -m pytest` 或 `docker exec ... pytest`。
- `python -c` 类命令在容器环境下加 `docker exec aid-agent-api` 前缀，宿主机环境直接 `python -c`。智能体首次执行前可先跑 `docker ps | grep aid-agent-api` 探测一次，后续命令统一前缀。

