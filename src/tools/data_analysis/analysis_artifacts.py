"""
分析产物注册表 — 同会话多次 analyze_data 之间的中间产物复用索引

背景（2026-09-20 392 积分事故，docs/incidents/analysis-agent-cost-392-credits-incident.md）：
每次 analyze_data 新建 AnalysisAgent，DataAnalyzer._store 虽已把中间变量持久化为
`storage/tenants/{tid}/temp/analysis_output_{uuid}.csv`，但新子代理不知道它们存在，导致同一批原始表
被重复「检索 → 加载 → 逐表加工 → 合并」（每次 ~200 万 prompt tokens）。

本模块在租户 temp 目录维护会话级 JSON 注册表：
  storage/tenants/{tid}/temp/analysis_registry_{session_id}.json
条目绑定唯一 CSV 文件名，记录 var/描述/行列信息/直接上游 lineage/完整原始表来源集合（source_table_ids），
子代理启动时注入清单 + load_output 按名加载，即可跳过整段重复的数据加工链路；
跨调用复用资格以「已登记 + source_table_ids 完整 + 每张来源表当前均可用」判定
（范围校验在 analysis_agent 侧批量执行，本模块不查库）。

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
# 注入子代理 user message 的最大条目数（2026-10 改进3：10 -> 50，与 REGISTRY_LIMIT
# 对齐——导出/后续调用需看到全部产物才能复用，此前 10 条上限导致后续调用看不到
# 早期宽表产物而重新 load_table + 逐表加工）
INJECT_LIMIT = 50
# 单条目记录的直接上游引用数上限（控制 lineage 体积，仅优化提示）
UPSTREAM_LIMIT = 20
# 完整原始表来源集合上限（设计 §18.7）：去重后超过该值 → source_table_ids=null
# （来源不全，不得保存截断集合后仍当授权依据），与 UPSTREAM_LIMIT/REGISTRY_LIMIT
# 职责独立，不新增配置项
SOURCE_TABLE_IDS_LIMIT = 200
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


def artifact_csv_path(tenant_id: str, csv_file: str) -> Optional[str]:
    """解析登记绑定的 CSV 文件名；无绑定的旧登记不猜测变量路径。"""
    if not tenant_id or not isinstance(csv_file, str) or not re.fullmatch(
        r"analysis_output_[0-9a-f]{32}\.csv", csv_file
    ):
        return None
    from src.core.storage import get_tenant_storage_dir

    temp_dir = os.path.realpath(get_tenant_storage_dir(tenant_id, "temp"))
    path = os.path.join(temp_dir, csv_file)
    return path if os.path.realpath(path).startswith(temp_dir + os.sep) else None


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
    csv_file: Optional[str] = None,
    description: str = "",
    method: str = "",
    rows: int = 0,
    columns: Optional[List[str]] = None,
    upstreams: Optional[List[str]] = None,
    source_table_ids: Optional[Optional[List[str]]] = None,
) -> bool:
    """写入/更新一条产物记录（同 var 覆盖）。失败仅告警，返回 False。

    upstreams（改进3 lineage，2026-10）：本产物直接引用的数据源（原始表 table_id /
    上游产物 var / 文件路径），仅作溯源展示，与复用资格判定独立。

    source_table_ids（设计 §18.7）：完整原始表 doc_id 去重集合，是跨调用复用资格
    的判定依据。None=来源不全/未知（保存为 null 且持续传播）；非空集合去重后超过
    SOURCE_TABLE_IDS_LIMIT 同样按 null 保存——不保存截断集合再当授权依据。
    旧条目缺该字段与 null 同义（不复用，提示重算）。
    """
    path = _registry_path(tenant_id, session_id)
    if not path or not is_valid_var_name(var):
        return False

    normalized_sources: Optional[List[str]] = None
    if source_table_ids is not None:
        seen: set = set()
        deduped: List[str] = []
        for tid in source_table_ids:
            tid = str(tid)
            if tid and tid not in seen:
                seen.add(tid)
                deduped.append(tid)
        # 超上限按来源不全处理：宁可放弃复用优化，不保存不完整授权证据
        normalized_sources = deduped if deduped and len(deduped) <= SOURCE_TABLE_IDS_LIMIT else None

    try:
        entries: List[Dict] = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                entries = [e for e in data if isinstance(e, dict)]

        entry = {
            "var": var,
            "csv_file": csv_file,
            "description": str(description or "")[:200],
            "method": str(method or ""),
            "rows": int(rows or 0),
            "columns": [str(c) for c in (columns or [])][:COLUMNS_PREVIEW_LIMIT],
            "upstreams": [str(u)[:64] for u in (upstreams or []) if u][:UPSTREAM_LIMIT],
            "source_table_ids": (
                [str(t) for t in normalized_sources] if normalized_sources else None
            ),
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
            if os.path.exists(artifact_csv_path(tenant_id, e.get("csv_file")) or "")
        ]
        return list(reversed(entries[-limit:])) if limit else list(reversed(entries))
    except Exception as e:
        logger.warning(f"[analysis_artifacts] 注册表读取失败（不影响分析）: {e}")
        return []


def find_equivalent_artifacts(
    tenant_id: str, session_id: str, table_id: str,
    entries: Optional[List[Dict]] = None,
) -> List[Dict]:
    """返回上游闭包包含 table_id 的产物条目（时间倒序）。

    改进3（2026-10）load_table 拦截的判定入口：产物直接引用 table_id 算命中；
    引用上游产物 var 则沿 lineage 传递（B 由 A 加工而来、A 来自表 X，则 B 的
    闭包含 X）。任何异常都返回空列表（拦截是优化不是正确性依赖，失败放行）。

    entries 允许调用方传入已过滤的候选条目（如「已登记 + 来源表当前均可用」的
    可复用集合）——本模块只读注册表文件，不做数据库范围校验；传 None 时读全部
    已登记条目（旧调用方兼容语义，运行时拦截必须传入过滤后集合）。
    """
    if entries is None:
        entries = load_artifacts(tenant_id, session_id, limit=0)
    if not entries or not table_id:
        return []

    by_var = {e["var"]: e for e in entries}

    def _closure_contains(entry: Dict, seen: set) -> bool:
        if entry["var"] in seen:
            return False
        seen.add(entry["var"])
        for ref in entry.get("upstreams") or []:
            if str(ref) == str(table_id):
                return True
            upstream_entry = by_var.get(str(ref))
            if upstream_entry and _closure_contains(upstream_entry, seen):
                return True
        return False

    try:
        return [e for e in entries if _closure_contains(e, set())]
    except Exception as e:
        logger.warning(f"[analysis_artifacts] 等价产物查询失败（放行不拦截）: {e}")
        return []
