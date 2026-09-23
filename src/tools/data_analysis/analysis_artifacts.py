"""
分析产物注册表 — 同会话多次 analyze_data 之间的中间产物复用索引

背景（2026-09-20 392 积分事故，docs/incidents/analysis-agent-cost-392-credits-incident.md）：
每次 analyze_data 新建 AnalysisAgent，DataAnalyzer._store 虽已把中间变量持久化为
`storage/tenants/{tid}/temp/{var}.csv`，但新子代理不知道它们存在，导致同一批原始表
被重复「检索 → 加载 → 逐表加工 → 合并」（每次 ~200 万 prompt tokens）。

本模块在租户 temp 目录维护会话级 JSON 注册表：
  storage/tenants/{tid}/temp/analysis_registry_{session_id}.json
条目记录 var/描述/行列信息，子代理启动时注入清单 + load_output 按名加载，
即可跳过整段重复的数据加工链路。

设计约束：
- 注册表读写全部容错（失败仅 log warning，绝不影响分析主流程）；
- session_id 为空时不读不写：无法界定复用范围，宁可不复用；
- 会话内 analyze_data 串行执行，无并发写竞争；不同会话文件隔离。
"""

import json
import os
import re
import uuid
from typing import Dict, List, Optional

from loguru import logger

# 单会话注册表条目上限（FIFO 淘汰最早条目）
REGISTRY_LIMIT = 50
# 注入子代理 user message 的最大条目数
INJECT_LIMIT = 10
# 列名清单截断长度（控制注入消息体积）
COLUMNS_PREVIEW_LIMIT = 15
# 合法产物变量名：字母/数字/下划线/中文/连字符（排除 . / \ 等路径成分，防穿越）
VAR_NAME_PATTERN = re.compile(r"[\w\-]{1,64}", re.UNICODE)
# 合法会话 ID（session_id 来自 LLM 可控参数，必须校验后才能拼路径）
SESSION_ID_PATTERN = re.compile(r"[\w\-]{1,128}", re.UNICODE)

_REGISTRY_FILENAME_PREFIX = "analysis_registry_"


def _registry_path(tenant_id: str, session_id: str) -> Optional[str]:
    """注册表文件路径；tenant/session 缺失或非法时返回 None（不启用复用）。

    session_id 经 analyze_data 参数暴露给 LLM（可被提示注入控制），
    未校验直接拼路径可穿越租户 temp 目录实现跨租户读写——
    因此与 load_output 同款双重防线：格式校验 + realpath 包含检查。
    """
    if not tenant_id or not session_id:
        return None
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        logger.warning(f"[analysis_artifacts] 非法 session_id，跳过产物注册表: {session_id!r}")
        return None

    from src.core.storage import get_tenant_storage_dir

    temp_dir = get_tenant_storage_dir(tenant_id, "temp")
    path = os.path.join(temp_dir, f"{_REGISTRY_FILENAME_PREFIX}{session_id}.json")
    # realpath 包含检查：拦截一切逃逸租户 temp 目录的构造（含符号链接外指）
    if not os.path.realpath(path).startswith(os.path.realpath(temp_dir) + os.sep):
        logger.warning(f"[analysis_artifacts] 注册表路径越界，拒绝: {session_id!r}")
        return None
    return path


def artifact_csv_path(tenant_id: str, var: str) -> Optional[str]:
    """产物 CSV 路径（租户 temp 目录下 {var}.csv）；与 DataAnalyzer._store 落盘路径一致。"""
    if not tenant_id or not var:
        return None
    from src.core.storage import get_tenant_storage_dir

    return os.path.join(get_tenant_storage_dir(tenant_id, "temp"), f"{var}.csv")


def is_valid_var_name(var: str) -> bool:
    """产物变量名合法性校验（load_output 入口防线，防路径穿越）。

    fullmatch 语义：re 的 `$` 锚允许尾随换行（"a\n" 会匹配 ^...$），
    产生含换行的文件名，必须整串匹配。
    """
    return bool(var) and bool(VAR_NAME_PATTERN.fullmatch(var))


def record_artifact(
    tenant_id: str,
    session_id: str,
    var: str,
    *,
    description: str = "",
    method: str = "",
    rows: int = 0,
    columns: Optional[List[str]] = None,
) -> bool:
    """写入/更新一条产物记录（同 var 覆盖）。失败仅告警，返回 False。"""
    path = _registry_path(tenant_id, session_id)
    if not path or not is_valid_var_name(var):
        return False

    try:
        entries: List[Dict] = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                entries = [e for e in data if isinstance(e, dict)]

        entry = {
            "var": var,
            "description": str(description or "")[:200],
            "method": str(method or ""),
            "rows": int(rows or 0),
            "columns": [str(c) for c in (columns or [])][:COLUMNS_PREVIEW_LIMIT],
        }
        # 同 var 覆盖更新，保持原位置；新条目追加到末尾（时间序）
        entries = [e for e in entries if e.get("var") != var]
        entries.append(entry)
        if len(entries) > REGISTRY_LIMIT:
            entries = entries[-REGISTRY_LIMIT:]

        os.makedirs(os.path.dirname(path), exist_ok=True)
        # 唯一临时名：同会话串行假设被打破（如并行工具调用）时也不互相覆盖半成品
        tmp_path = f"{path}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False)
        os.replace(tmp_path, path)
        return True
    except Exception as e:
        logger.warning(f"[analysis_artifacts] 注册表写入失败（不影响分析）: {e}")
        return False


def load_artifacts(
    tenant_id: str, session_id: str, limit: int = INJECT_LIMIT
) -> List[Dict]:
    """读取本会话最近注册的产物条目（时间倒序，已剔除 CSV 已丢失的条目）。"""
    path = _registry_path(tenant_id, session_id)
    if not path or not os.path.exists(path):
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        entries = [
            e for e in data
            if isinstance(e, dict) and e.get("var") and is_valid_var_name(str(e["var"]))
        ]
        # 产物文件被清理的条目不注入（避免子代理 load_output 必然失败）
        entries = [
            e for e in entries
            if os.path.exists(artifact_csv_path(tenant_id, e["var"]) or "")
        ]
        return list(reversed(entries[-limit:])) if limit else list(reversed(entries))
    except Exception as e:
        logger.warning(f"[analysis_artifacts] 注册表读取失败（不影响分析）: {e}")
        return []
