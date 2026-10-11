"""防滥用规则目录（代码内置，新增规则 = 发版）。

Phase 1 仅含硬规则 credit_budget；no_file_generation（工具封禁 denylist）
与软规则（no_creation / no_batch_enumeration）分别在 Phase 2 / Phase 3 加入。
"""
from typing import Any, Dict, List, Optional, Tuple

# credit_budget 预算统计只统计会话执行类记录（设计 §5.2），
# 排除 admin_llm_ops / report_* / video_* / knowledge_embedding / background_llm
# 等管理后台与后台任务记录——管理员操作与离线任务不计入用户预算。
SESSION_SOURCE_TYPES: Tuple[str, ...] = (
    "chat",
    "wecom",
    "wecom_kf",
    "wecom_personal_rpa",
    "dingtalk",
    "feishu",
)

RULE_CATALOG: List[Dict[str, Any]] = [
    {
        "rule_id": "credit_budget",
        "name": "时间窗积分预算",
        "type": "hard",
        "description": (
            "按滚动窗口统计用户的会话积分消耗，达到告警线记事件并通知租户管理员，"
            "达到硬限线后（拦截模式）请求入口直接拦截。具体阈值建议先用观察模式跑真实数据校准。"
        ),
        "params_schema": [
            {
                "key": "period",
                "label": "统计窗口",
                "type": "enum",
                "options": ["hour", "day"],
                "default": "day",
                "required": True,
            },
            {
                "key": "warn_threshold",
                "label": "告警线（积分）",
                "type": "number",
                "min": 0,
                "required": False,
                "description": "达到该值记事件并通知；不填则只看硬限线",
            },
            {
                "key": "hard_threshold",
                "label": "硬限线（积分）",
                "type": "number",
                "min": 0,
                "required": True,
                "description": "拦截模式下达到该值拦截请求",
            },
        ],
        "default_params": {"period": "day"},
    },
]

_RULE_INDEX = {rule["rule_id"]: rule for rule in RULE_CATALOG}


def get_rule(rule_id: str) -> Optional[Dict[str, Any]]:
    return _RULE_INDEX.get(rule_id)


def validate_policy_params(rule_id: str, params: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    """对照 presets 的参数 schema 校验绑定参数，非法返回 (False, 错误信息)"""
    rule = get_rule(rule_id)
    if rule is None:
        return False, f"未知规则: {rule_id}"
    params = params or {}
    schema = {item["key"]: item for item in rule["params_schema"]}
    for item in rule["params_schema"]:
        if item.get("required") and item["key"] not in params:
            return False, f"规则 {rule_id} 缺少必填参数: {item['key']}"
    for key, value in params.items():
        item = schema.get(key)
        if item is None:
            return False, f"规则 {rule_id} 不支持参数: {key}"
        if item["type"] == "enum" and value not in item["options"]:
            return False, f"规则 {rule_id} 参数 {key} 取值非法: {value}"
        if item["type"] == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return False, f"规则 {rule_id} 参数 {key} 必须为数字"
            if value < item.get("min", 0):
                return False, f"规则 {rule_id} 参数 {key} 不能小于 {item.get('min', 0)}"
    warn = params.get("warn_threshold")
    hard = params.get("hard_threshold")
    if warn is not None and hard is not None and warn > hard:
        return False, f"规则 {rule_id} 告警线不能大于硬限线"
    return True, ""
