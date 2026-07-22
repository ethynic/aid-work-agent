"""
节省时间估算

参考 M365 Copilot Dashboard 方法：节省时间 = Σ(每条对话的预估手工耗时 - 实际 AI 处理耗时)
- 预估手工耗时 = f(工具调用类型)
- 实际 AI 处理耗时 = duration_ms / 1000 / 60（分钟）

系数可在 configs/config.yaml 配置（report.time_saver_coefficients），便于按客户反馈调整。
"""

from typing import Any, Dict, List

# 默认手工耗时系数（分钟），按工具名 / 调用类型映射
# 初期保守，避免数字虚高反噬可信度
DEFAULT_COEFFICIENTS = {
    "send_email": 8,        # 邮件起草
    "search_documents": 5,  # 文档检索
    "create_document": 15,  # 文档生成
    "export_data": 10,      # 数据导出
    "query_customer": 5,    # 客户查询
    "default": 3,           # 通用问答 / 其他
}


def estimate_saved_minutes(
    records: List[Dict[str, Any]],
    coefficients: Dict[str, int] = None,
) -> float:
    """估算节省时间（分钟）

    Args:
        records: chat_records 列表，每条需含 duration_ms、execution_details
        coefficients: 工具耗时系数覆盖（None 用默认）

    Returns:
        节省时间（分钟，保留 1 位小数）；不能为负
    """
    coeffs = coefficients or DEFAULT_COEFFICIENTS
    total_saved = 0.0

    for rec in records:
        # 实际 AI 处理耗时（分钟）
        duration_ms = rec.get("duration_ms") or 0
        actual_minutes = duration_ms / 1000 / 60

        # 预估手工耗时：取该轮对话中调用过的工具的最大系数
        # 若无工具调用，按 default 系数
        execution_details = rec.get("execution_details")
        if isinstance(execution_details, str):
            import json
            try:
                execution_details = json.loads(execution_details)
            except (json.JSONDecodeError, TypeError):
                execution_details = {}
        if not isinstance(execution_details, dict):
            execution_details = {}

        tool_executions = execution_details.get("tool_executions") or []
        if tool_executions:
            tool_names = [t.get("tool_name", "") for t in tool_executions if isinstance(t, dict)]
            estimated_minutes = max(
                (coeffs.get(name, coeffs["default"]) for name in tool_names),
                default=coeffs["default"],
            )
        else:
            estimated_minutes = coeffs["default"]

        saved = estimated_minutes - actual_minutes
        # 单条不能为负（AI 慢于手工的情况不扣分）
        if saved > 0:
            total_saved += saved

    return round(total_saved, 1)
