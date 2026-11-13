"""重工具服务 HTTP 面（重容器入口，设计 §4.1/§4.3/§8）。

端点：
- POST /internal/heavy-tools/execute   同步长调用（服务 token 认证 + 路径 allowlist + 信号量）
- POST /internal/heavy-tools/cancel    协作取消（设置标志；标志在 handler 启动前检查，
                                        已进入执行的重负载段为 openpyxl/pandas/云 API
                                        等原生调用，无法中断，会跑完但产物按
                                        「远程操作一律写新产物文件」不变量不与他人竞态）
- GET  /internal/heavy-tools/health    免认证 liveness（仅返回非敏感计数）

语义：
- 信号量满不排队，直接结构化 busy（429，§8）——代理侧不自动重试，防烧积分；
- 进行中 job 以 tool_call_id 登记；重复提交先协作取消旧 job 再接新（§4.3 恢复重放）；
  孤儿 job 与新请求的产物竞态由「远程操作一律写新产物文件」不变量消除；
- 调用方消失后的登记 TTL 清扫（按各 job 实际超时 + JOB_TTL_GRACE_SECONDS 派生，§4.3）；
- 执行超时只取消等待：重负载线程继续运行，信号量槽位移交后台任务等线程真正
  结束后才释放（防止实际并发超过 HEAVY_TOOLS_CONCURRENCY 击穿重容器内存上限）；
- 每次 execute 落 tlog（主题「重工具服务」）+ 结构化日志（tool_call_id、耗时、
  VmHWM 峰值内存采样），供验收与排障（§4.1 审计）。

容器约束：本进程无 DB 连接、无 LLM 凭证；路径 allowlist 是权限判定留在轻容器
之外的廉价纵深防御（§4.1）。命令行入口：
    python -m src.services.heavy_tool_service.server --host 0.0.0.0 --port 8093
"""

import argparse
import asyncio
import hmac
import os
import threading
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger

from . import config, handlers

app = FastAPI(title="heavy-tools", docs_url=None, redoc_url=None, openapi_url=None)

# 进行中 job 登记（key = tool_call_id）；跨请求共享状态仅存在于单进程 uvicorn
# 容器内（§4.3 内存登记 + TTL 清扫，MVP 无持久化）
_jobs: Dict[str, Dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_semaphore: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(config.heavy_tools_concurrency())
    return _semaphore


def _peak_memory_mb() -> float:
    """VmHWM（进程峰值 RSS，MiB）：重容器内存验收采样（§10 验收 2）。"""
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                if line.startswith("VmHWM:"):
                    return round(int(line.split()[1]) / 1024.0, 1)
    except Exception:
        pass
    return 0.0


def _sweep_jobs() -> int:
    """TTL 清扫：登记超过「其实际超时 + JOB_TTL_GRACE_SECONDS」的孤儿 job 移除（§4.3）。

    阈值按各 job 登记时记录的 timeout_seconds 派生而非固定常量：paddleocr 等
    合法任务可运行 900s，固定短 TTL 会把仍在运行中的 job 误当孤儿移除。
    """
    now = time.monotonic()
    removed = 0
    with _jobs_lock:
        for job_id in [key for key, job in _jobs.items()
                       if now - job["started_at"] > job["ttl_seconds"]]:
            _jobs.pop(job_id, None)
            removed += 1
    return removed


async def _sweeper() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            _sweep_jobs()
        except Exception as error:  # noqa: BLE001 - 清扫失败不阻断服务
            logger.debug("heavy-tools job 清扫失败: {}", error)


@app.on_event("startup")
async def _start_sweeper() -> None:
    asyncio.get_running_loop().create_task(_sweeper())


def _authorized(request: Request) -> bool:
    token = config.service_token()
    if not token:
        return False  # fail-closed：未配置服务凭据拒绝全部执行请求
    header = request.headers.get("authorization") or ""
    if not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[len("Bearer "):].strip(), token)


def _register_job(tool_call_id: str,
                  timeout_seconds: Optional[int] = None) -> Dict[str, Any]:
    """登记 job；同 id 重复提交（恢复重放撞车）先协作取消旧 job 再 upsert（§4.3）。

    返回登记条目本身（非 cancel_event）：execute 收尾时只在「登记条目仍是自己」
    时才 pop——同 id 撞车时旧 job 的 finally 不能误删新 job 的登记，否则新 job
    在剩余运行期内对 cancel / 重复提交防护全部不可见。
    """
    entry = {
        "cancel_event": threading.Event(),
        "started_at": time.monotonic(),
        # 孤儿回收阈值 = 本次请求实际超时 + 清扫余量（覆盖 60s 扫描间隔与抖动）
        "ttl_seconds": (int(timeout_seconds or config.DEFAULT_TIMEOUT_SECONDS)
                        + config.JOB_TTL_GRACE_SECONDS),
    }
    with _jobs_lock:
        previous = _jobs.get(tool_call_id)
        if previous is not None:
            previous["cancel_event"].set()
            logger.warning("heavy-tools 同 tool_call_id 重复提交，已协作取消旧 job: {}",
                           tool_call_id)
        _jobs[tool_call_id] = entry
    return entry


def _unregister_job(tool_call_id: str, job_entry: Dict[str, Any]) -> None:
    """收尾移除登记；仅当条目仍是自己时才 pop。

    同 id 撞车（恢复重放）时登记已被新 job 覆盖，旧 job 的收尾不得误删
    新 job 的登记，否则新 job 在剩余运行期内对 cancel / 重复提交防护不可见。
    """
    with _jobs_lock:
        if _jobs.get(tool_call_id) is job_entry:
            _jobs.pop(tool_call_id, None)


async def _drain_orphan(future, semaphore: asyncio.Semaphore, tool_call_id: str) -> None:
    """超时孤儿线程收尾：等线程真正结束后才释放信号量槽位。

    超时只取消了 wait_for 的等待，重负载线程在线程池中继续运行；若此时立即
    释放信号量，新请求被接纳后实际并发会超过 HEAVY_TOOLS_CONCURRENCY，
    多个病理级大文件任务同时跑可击穿重容器内存上限（OOM kill 整容器）。
    """
    try:
        await future
    except Exception:  # noqa: BLE001 - 孤儿线程结果/异常一律丢弃，仅记日志
        logger.debug("heavy-tools 孤儿线程异常结束（结果丢弃） tcid={}", tool_call_id)
    logger.info("heavy-tools 孤儿线程结束，释放信号量槽位 tcid={}", tool_call_id)
    semaphore.release()


@app.post("/internal/heavy-tools/execute")
async def execute(request: Request):
    if not _authorized(request):
        return JSONResponse(status_code=401, content={
            "success": False, "error": "重工具服务认证失败"})
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={
            "success": False, "error": "请求体必须是 JSON"})

    tool = payload.get("tool") or ""
    handler = payload.get("handler") or None
    params = payload.get("params") or {}
    context = payload.get("context") or {}
    try:
        timeout_seconds = int(payload.get("timeout_seconds") or config.DEFAULT_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        timeout_seconds = config.DEFAULT_TIMEOUT_SECONDS
    if not isinstance(params, dict) or not isinstance(context, dict):
        return JSONResponse(status_code=400, content={
            "success": False, "error": "params/context 必须是对象"})

    # 路径入参服务端校验（§4.1）：声明的路径字段必须为绝对路径且落在共享卷内
    violation = handlers.assert_paths_allowed(
        handlers.collect_request_paths(params, context),
        config.allowed_path_roots())
    if violation:
        return JSONResponse(status_code=400, content={
            "success": False, "error": violation})

    tool_call_id = (context.get("tool_call_id") or
                    f"heavy_{uuid.uuid4().hex[:12]}")
    if _semaphore is not None and _semaphore.locked():
        # busy：不排队直接拒绝（§8）——排队等待 = 占用轻容器执行体，无意义
        logger.info("heavy-tools busy 拒绝 tool={} handler={}", tool, handler)
        return JSONResponse(status_code=429, content={
            "success": False, "error_code": "busy",
            "error": "重工具服务繁忙，请稍后重试"})

    job_entry = _register_job(tool_call_id, timeout_seconds)
    cancel_event = job_entry["cancel_event"]
    started = time.monotonic()
    memory_before = _peak_memory_mb()
    from src.core.temp_logger import tlog
    tlog("重工具服务", "execute 开始 tool={tool} handler={handler} tcid={tcid}",
         tool=tool, handler=handler, tcid=tool_call_id)
    logger.info("heavy-tools execute tool={} handler={} tcid={}", tool, handler, tool_call_id)

    from src.tools.context import ToolExecutionContext, tool_execution_scope

    # 信号量手动获取（不用 async with）：超时路径要把释放权移交给孤儿收尾任务，
    # 等重负载线程真正结束后才归还槽位（防止实际并发超限击穿内存上限）
    semaphore = _get_semaphore()
    await semaphore.acquire()
    release_owned = True
    try:
        # 协作取消检查点：等待信号量期间被同 id 新提交取消的旧 job，直接放弃执行
        if cancel_event.is_set():
            return {"success": False,
                    "error": "任务已取消（同 tool_call_id 的新提交已接管）"}
        # 远程段零 LLM / 零知识清单：llm_gateway 与 knowledge_search_inventory
        # 不进 payload 也不构造（§4.2 可序列化核对）；租户身份字段仅用于日志
        # 归因与产物目录定位，不做权限判定（§7 权限判定留在轻容器装配边界）
        context_obj = ToolExecutionContext(
            tenant_id=context.get("tenant_id"),
            user_id=context.get("user_id"),
            session_id=context.get("session_id"),
            channel=context.get("channel"),
            subagent_id=context.get("subagent_id"),
            chat_record_id=context.get("chat_record_id"),
            agent_execution_id=context.get("agent_execution_id"),
            tool_call_id=tool_call_id,
            profile_id=context.get("profile_id"),
            instance_id=context.get("instance_id"),
            skill_workdir=context.get("skill_workdir"),
        )
        with tool_execution_scope(context_obj):
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(
                None,
                lambda: handlers.dispatch(tool, handler, params, cancel_event),
            )
            try:
                result = await asyncio.wait_for(asyncio.shield(future),
                                                timeout=timeout_seconds)
            except asyncio.TimeoutError:
                # 超时只取消等待：线程无法强杀，重负载继续在后台跑完。
                # 信号量释放权移交孤儿收尾任务；登记条目在 finally 正常移除
                # （孤儿线程不再接受 cancel / 重复提交，结果按超时口径丢弃）。
                release_owned = False
                loop.create_task(_drain_orphan(future, semaphore, tool_call_id))
                duration = round(time.monotonic() - started, 1)
                tlog("重工具服务",
                     "execute 超时 tool={tool} handler={handler} tcid={tcid} 耗时={d}s",
                     tool=tool, handler=handler, tcid=tool_call_id, d=duration, level="ERROR")
                logger.error("heavy-tools execute timeout tool={} handler={} tcid={} {}s",
                             tool, handler, tool_call_id, duration)
                return {"success": False,
                        "error": f"重工具执行超时（>{timeout_seconds}s），处理未完成"}
            except asyncio.CancelledError:
                # 服务关闭等外部取消：线程同样无法强杀，移交孤儿收尾后再传播
                release_owned = False
                loop.create_task(_drain_orphan(future, semaphore, tool_call_id))
                raise
    except Exception as error:  # noqa: BLE001 - 顶层兜底，错误响应过滤由日志承担
        logger.opt(exception=True).error(
            "heavy-tools execute failed tool={} handler={} tcid={}", tool, handler, tool_call_id)
        return JSONResponse(status_code=500, content={
            "success": False, "error": f"重工具服务内部错误: {type(error).__name__}"})
    finally:
        if release_owned:
            semaphore.release()
        _unregister_job(tool_call_id, job_entry)

    result = result if isinstance(result, dict) else {"success": False, "error": "重工具返回非对象结果"}
    duration = round(time.monotonic() - started, 1)
    tlog("重工具服务",
         "execute 完成 tool={tool} handler={handler} tcid={tcid} success={ok} 耗时={d}s peak_mem={mem}MB",
         tool=tool, handler=handler, tcid=tool_call_id, ok=result.get("success"),
         d=duration, mem=_peak_memory_mb())
    logger.info("heavy-tools done tool={} handler={} tcid={} success={} duration={}s "
                "peak_rss={}MB(before={})",
                tool, handler, tool_call_id, result.get("success"), duration,
                _peak_memory_mb(), memory_before)
    return {"success": bool(result.get("success")), "result": result}


@app.post("/internal/heavy-tools/cancel")
async def cancel(request: Request):
    if not _authorized(request):
        return JSONResponse(status_code=401, content={
            "success": False, "error": "重工具服务认证失败"})
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={
            "success": False, "error": "请求体必须是 JSON"})
    tool_call_id = payload.get("tool_call_id") or ""
    with _jobs_lock:
        job = _jobs.get(tool_call_id)
        if job is not None:
            job["cancel_event"].set()
            found = True
        else:
            found = False
    # 取消语义（协作式）：标志在 handler 启动前检查（execute 取得信号量后、
    # dispatch 入口及各 handler 执行前置点）；已进入执行的重负载段为原生调用，
    # 无法中断，会跑完——产物竞态由「远程操作一律写新产物文件」不变量消除。
    # 尽力而为：未找到登记（已完成/已清扫/跨副本）也返回成功，调用方不据此重试
    logger.info("heavy-tools cancel tool_call_id={} found={}", tool_call_id, found)
    return {"success": True, "found": found}


@app.get("/internal/heavy-tools/health")
async def health():
    """免认证 liveness：仅非敏感计数，供 compose healthcheck 与主 API 探测。"""
    with _jobs_lock:
        running = len(_jobs)
    return {
        "status": "ok",
        "running_jobs": running,
        "busy": running >= config.heavy_tools_concurrency(),
        "concurrency": config.heavy_tools_concurrency(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the heavy tool service")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=config.DEFAULT_PORT)
    arguments = parser.parse_args()
    if not config.service_token():
        # fail-closed 前置告警：无凭据时 execute 全拒，尽早暴露配置缺失
        logger.error("heavy-tools 启动缺少 AGENT_RUNNER_WEB_SERVICE_TOKEN，"
                     "execute 请求将全部拒绝（fail-closed）")
    import uvicorn

    uvicorn.run(app, host=arguments.host, port=arguments.port, log_config=None)


if __name__ == "__main__":
    main()
