# 日志统一改造计划：标准库 logging 收口到 loguru

> 创建：2026-10-13
> 状态：✅ 已执行（Phase 1/2 完成，待部署观察主日志量变化）
> 来源：防滥用中心 `/api/saas/abuse/usage` 500 排查（2026-10-13）中发现的日志盲区问题

## 1. 背景与问题

### 1.1 规则与现状的矛盾

`.claude/rules/backend_dev.md` 已明确：本项目后端统一使用 `loguru` 作为日志库，禁止使用标准库 `logging`。但规则落地时只约束了新增代码，存量代码未做一次性迁移。扫描（2026-10-13）确认 `src/` 仍有 **14 个文件**在用标准库 `logging`。

### 1.2 这些日志现在基本是丢的

`src/config/logging.py` 的 `setup_logging()` 只配置了 loguru 的 sink，**没有**：
- `logging.basicConfig(...)`（标准 logging 无任何 handler）
- InterceptHandler（把标准 logging 转发进 loguru）

后果：

| 级别 | 现状 |
|------|------|
| INFO / DEBUG | 标准库 `logging` 无 handler 时直接丢弃，**完全不可见** |
| WARNING 及以上 | 走 `logging.lastResort` 裸输出到 stderr（即 docker logs），不进 loguru 主日志 / `log/error_*.log` / `log_error` 表 |

这与「未捕获异常 500 不进错误日志」是同一类根因的另一面（该问题已单独修复：`src/main.py` 增加全局兜底异常处理器，走 loguru 记录）。

### 1.3 受影响的真实场景

- `src/knowledge/vector_db/vector_db.py`：向量库操作的 warning/error 不进错误日志库
- `src/session_tasks/decisions.py`（21 处调用）：会话任务决策日志全部不可见
- `src/services/overlay_heal_service.py`：覆盖修复服务的异常告警不可见
- 第三方库（uvicorn、apscheduler、psycopg2 等）的 WARNING+ 日志同样只进 docker logs，排查问题时必须登服务器看容器 stdout，无法在「错误日志」页面追溯

## 2. 改造目标

1. **兜底拦截**：标准 logging（含第三方库）统一转发进 loguru，一处改动全部生效
2. **存量清理**：`src/` 内 14 个文件全部去掉标准库 `logging`，规则真正落地
3. 完成后 `src/` 内 `import logging` 清零（`logging` 标准库仅允许出现在 `src/config/logging.py` 的 InterceptHandler 实现内）

## 3. 方案设计

### 3.1 Phase 1：InterceptHandler 兜底拦截

在 `src/config/logging.py` 中新增 InterceptHandler，并在 `setup_logging()` 内注册到 root logger：

```python
import logging
from loguru import logger

class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        # 深度回溯到标准 logging 的真实调用帧，绕过 logging 内部帧
        frame, depth = logging.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())

def _install_intercept_handler() -> None:
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
```

要点：

- `logging.basicConfig(..., force=True)`：清掉 root 上可能存在的旧 handler，幂等可重复调用
- `level=0`：全量接收，由 loguru 侧的 level 过滤（主日志 INFO / 错误文件 ERROR）决定去留
- `record.exc_info` 透传给 `logger.opt(exception=...)`：WARNING+ 的堆栈能正确进入错误文件与 `log_error` 表（满足 error_log_sink 采集条件）
- `depth` 回溯：让 `{name}:{function}:{line}` 显示真实调用点而不是 logging 内部帧

**降噪考虑**：uvicorn access log 已在 nginx 层关闭，但 uvicorn/gunicorn 的 INFO 启动横幅、apscheduler 调度日志会因此进主日志。如噪音过大，在 InterceptHandler 内对个别 logger 名（如 `uvicorn.access`、`apscheduler.executors.default`）做降级或过滤，先上线观察再定。

> 执行补充（2026-10-13，CodeReview P1 采纳）：httpx/openai 每次出站请求都打 INFO（项目 LLM 网关/渠道调用密集，噪音可预见），已在 `_install_intercept_handler` 内预置降噪——`httpx` / `httpcore` / `openai` / `urllib3` / `apscheduler.executors.default` 五个库 logger 压到 WARNING，其余库保持 level=0 放行观察。

**注册位置**：`setup_logging()` 末尾（`register_error_log_sink()` 之后），`src/main.py` 与 `src/background_runner.py` 两个入口都会自动生效，无需改入口代码。

**注意 enqueue 语义**：InterceptHandler 在标准 logging 的调用线程内同步执行，进 loguru 后由 enqueue 队列串行写文件，与现有 sink 的多 worker 安全机制一致。

### 3.2 Phase 2：存量 14 文件迁移

| 文件 | logger 调用数 | 处理 |
|------|--------------|------|
| `src/session_tasks/decisions.py` | 21 | `import logging` + `getLogger` 改 `from loguru import logger`；调用处 `logger.warning(..., exc_info=True)` 等标准库写法改 `logger.opt(exception=True).warning(...)` |
| `src/skills/eas-contract-verify-1.0.0/scripts/eas_contract_verify.py` | 8 | 同上 |
| `src/skills/eas-contract-verify-1.0.0/scripts/parse_contract_pdf.py` | 7 | 同上 |
| `src/services/overlay_heal_service.py` | 4 | 同上 |
| `src/knowledge/vector_db/vector_db.py` | 3 | 同上 |
| `src/tenant_custom/hongtao_shop/fetcher.py` | 1 | 同上 |
| `src/skills/paddleocr-doc-parsing-2.0.4/scripts/lib.py` | 1 | 同上 |
| `src/session_tasks/texts.py` | 1 | 同上 |
| `src/session_tasks/service.py` | 1 | 同上 |
| `src/boss_conversation/init_tables.py` | 0 | 仅删 `import logging` + `getLogger` 行 |
| `src/weixin_conversation/init_tables.py` | 0 | 仅删 import |
| `src/wechat_mp/db.py` | 0 | 仅删 import |
| `src/session_tasks/init_tables.py` | 0 | 仅删 import |
| `src/services/content_sync/db.py` | 0 | 仅删 import |

迁移注意：

- 标准库 `%s` 占位符风格 `logger.info("msg %s", arg)` 若存在，需改为 f-string（loguru 不做 % 延迟格式化）；扫描时未见此风格，迁移时留意
- `logger.exception(...)`（标准库写法）改 `logger.opt(exception=True).error(...)`，与 backend_dev.md 规范一致
- skill 脚本（`eas_contract_verify.py` / `parse_contract_pdf.py`）可能在脱离 src 环境独立运行，loguru 为项目必装依赖，直接改即可；若脚本有 standalone 诉求，改后确认 `from loguru import logger` 可用
- 迁移后这批日志的 `{name}:{function}:{line}` 会变为 loguru 格式，`log_error` 表的 `module` 字段来源不变（sink 读 loguru record），无兼容问题

### 3.3 完成后防回归

- `.claude/rules/backend_dev.md` 日志规范一节补一句：`src/` 内禁止 `import logging`，唯一例外 `src/config/logging.py` 的 InterceptHandler
- 可选：CI / pre-commit 用 `grep -rn "^import logging" src/ --include="*.py" | grep -v config/logging.py` 做检查

## 4. 验证方案

按风险分级，本改动触及**启动链路 + 日志基础设施**，属高风险任务（三智能体流程）：

1. **Phase 1 验证**
   - 单测：构造 `logging.getLogger("test").error(...)` + exc_info，断言进入 loguru（可用 logger.add(sink_list) 捕获）
   - 容器内启动检查：`docker exec aid-agent-api python -c "from src.main import app"`，确认 `logging.basicConfig` 不与既有配置冲突
   - 实测：触发一个第三方库 warning（如人为让 apscheduler 打日志），确认进 `log/agent/aid-work-agent_*.log`；触发 ERROR 确认进 `error_*.log`，且 `WRITE_LOG_ERROR=true` 环境下进 `log_error` 表
2. **Phase 2 验证**
   - `grep -rn "import logging" src/ --include="*.py"` 仅剩 `src/config/logging.py`
   - 定向跑受影响模块测试：`./scripts/dev_test.sh tests/unit/ -p no:cacheprovider -q`（session_tasks / vector_db 相关）
   - `grep -rn "exc_info\|logger\.\(warning\|error\)" src/session_tasks/decisions.py` 确认无标准库残留写法
3. **回归观察**：部署后 1-2 天观察主日志量变化（第三方 INFO 日志涌入可能放大日志量），必要时做 logger 名降噪

## 5. 执行顺序与工作量预估

| 步骤 | 内容 | 预估 |
|------|------|------|
| 1 | Phase 1：InterceptHandler + 单测 | 0.5h |
| 2 | Phase 2：14 文件迁移（9 个有调用 + 5 个仅删 import） | 1-1.5h |
| 3 | 验证（含容器内启动检查 + 定向测试） | 0.5h |
| 4 | 规则回写 backend_dev.md + 本文档状态更新 | 0.2h |

## 6. 风险与回滚

| 风险 | 缓解 |
|------|------|
| 第三方 INFO 日志涌入放大主日志量 | level=0 全量接收后由 loguru sink 过滤；噪音大时按 logger 名降噪；log_retention 15 天清理兜底 |
| InterceptHandler 在 fork/gunicorn 多 worker 下的重复注册 | `basicConfig(force=True)` 幂等；setup_logging 每 worker 各调一次，各 worker 独立 handler 实例，无共享状态 |
| skill 脚本独立运行环境无 loguru | loguru 为项目依赖，风险极低；如确有 standalone 场景再单独评估 |
| 回滚 | Phase 1 删 InterceptHandler 注册即回滚；Phase 2 为逐文件独立改动，git revert 单文件即可 |
