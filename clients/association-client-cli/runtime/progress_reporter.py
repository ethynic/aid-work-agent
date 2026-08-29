"""
NDJSON 进度上报 —— stdout 输出换行分隔的 JSON 事件，供 Electron 客户端实时解析。

事件类型（设计文档 §3.2.3 / §9.1）：
    start / progress / billing / log / error / complete / stopped
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# 手机号脱敏正则
_MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


def _redact_mobiles(text: str) -> str:
    """手机号脱敏：1xx****xxxx"""
    if not text:
        return text
    return _MOBILE_RE.sub(
        lambda m: f"{m.group()[:3]}****{m.group()[-4:]}", text
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit(event: dict) -> None:
    """输出一行 NDJSON。"""
    # 所有输出做手机号脱敏 + Path→str 安全转换
    for key, val in list(event.items()):
        if isinstance(val, Path):
            event[key] = str(val)
        elif isinstance(val, str):
            event[key] = _redact_mobiles(val)
        elif isinstance(val, dict):
            event[key] = {
                k: str(v) if isinstance(v, Path) else (_redact_mobiles(v) if isinstance(v, str) else v)
                for k, v in val.items()
            }
    sys.stdout.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()

    # tee：本地完整日志 + 遥测缓冲（均吞异常，绝不影响采集）
    try:
        from runtime import run_log
        run_log.append_event(event)
    except Exception:
        pass
    try:
        from runtime import telemetry
        telemetry.record_event(event)
    except Exception:
        pass


def emit_start(session_id: str, associations: list[str], server_url: str = "") -> None:
    _emit({
        "event": "start",
        "session_id": session_id,
        "associations": associations,
        "server_url": server_url,
        "timestamp": _now(),
    })


def emit_progress(
    association: str,
    step: str,
    status: str,
    progress: int = 0,
    message: str = "",
) -> None:
    _emit({
        "event": "progress",
        "association": association,
        "step": step,
        "status": status,  # running / success / failed
        "progress": progress,
        "message": message,
        "timestamp": _now(),
    })


def emit_billing(
    association: str,
    stage: str,
    raw_credit_cost: float,
    credit_cost: float,
    balance_after: Optional[float],
    model: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> None:
    _emit({
        "event": "billing",
        "association": association,
        "stage": stage,
        "raw_credit_cost": raw_credit_cost,
        "credit_cost": credit_cost,
        "balance_after": balance_after,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "timestamp": _now(),
    })


def emit_log(level: str, message: str, association: str = "", **extra) -> None:
    _emit({
        "event": "log",
        "level": level,  # INFO / WARNING / ERROR
        "association": association,
        "message": message,
        "timestamp": _now(),
        **extra,
    })


def emit_error(
    association: str,
    error_code: str,
    message: str,
    stage: str = "",
    session_fatal: bool = False,
) -> None:
    _emit({
        "event": "error",
        "association": association,
        "error_code": error_code,
        "message": message,
        "stage": stage,
        "session_fatal": session_fatal,
        "timestamp": _now(),
    })


def emit_complete(
    session_id: str,
    total_consumed: float,
    output: str = "",
    summary: Optional[dict] = None,
) -> None:
    _emit({
        "event": "complete",
        "session_id": session_id,
        "total_consumed": total_consumed,
        "output": output,
        "summary": summary or {},
        "timestamp": _now(),
    })


def emit_stopped(
    session_id: str,
    completed: list[str],
    failed: list[str],
    remaining: list[str],
    output: str = "",
) -> None:
    """用户停止后的结果汇报：已完成/失败/未处理名单 + 部分结果 Excel 路径。"""
    _emit({
        "event": "stopped",
        "session_id": session_id,
        "completed": completed,
        "failed": failed,
        "remaining": remaining,
        "output": output,
        "timestamp": _now(),
    })


class CliProgressReporter:
    """适配 AssociationBatchEnricher 的 progress_reporter 回调。

    enricher 调用 progress_reporter(message) 时，我们解析消息文本推断当前协会名和步骤，
    输出 progress 事件。
    """

    def __init__(self):
        self.current_association: str = ""
        self.total_consumed: float = 0.0
        self.gateway = None  # 由 main.py 注入，用于同步 current_association

    def report_final(self, association: str, status: str) -> None:
        """enricher 每协会处理完成后的最终状态回调（success/failed）。

        让客户端进度图标实时从 ● 变成 ✓/✗（此前 __call__ 永远发 running，
        图标永远停在运行中状态）。
        """
        emit_progress(
            association=association,
            step="batch",
            status=status,
            message="处理完成" if status == "success" else "处理失败",
        )

    def __call__(self, message: str) -> None:
        """enricher 进度回调（message 格式 [协会名] 正在...）。"""
        # 解析 [协会名] 前缀
        if message.startswith("[") and "]" in message:
            end = message.index("]")
            self.current_association = message[1:end]
            action = message[end + 1:].strip()
            # 同步到 gateway（让 billing 事件带上 association）
            if self.gateway:
                self.gateway.current_association = self.current_association
                # 从 action 推断 stage。注意措辞必须与 enricher 实际消息对齐：
                # 第2步消息是「正在打开官网采集：...」（官网采集），曾因判断词
                # 写成「采集官网」（词序反了）永不匹配，官网链路全部 LLM 计费
                # 行被错标 search_profile（真机 2026-08-26 对账发现）
                if "基础信息" in action:
                    self.gateway.current_stage = "search_profile"
                elif "官网采集" in action or "采集官网" in action:
                    self.gateway.current_stage = "official_profile"
                elif "微信搜索" in action:
                    self.gateway.current_stage = "wechat_search_leader"
                elif "微信检索" in action or "手机号" in action:
                    self.gateway.current_stage = "wechat_mobile"
        else:
            action = message

        # stderr 也输出一份（便于直接命令行查看）
        print(_redact_mobiles(message), file=sys.stderr, flush=True)

        # 推断步骤
        step = "unknown"
        if "基础信息" in action or "search" in action.lower():
            step = "search_profile"
        elif "采集官网" in action or "官网" in action:
            step = "official_profile"
        elif "微信搜索" in action:
            step = "wechat_search_leader"
        elif "微信检索" in action or "微信" in action or "手机号" in action:
            step = "wechat_mobile"

        emit_progress(
            association=self.current_association,
            step=step,
            status="running",
            message=action,
        )
