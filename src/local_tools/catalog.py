"""受信 Provider manifest 注册表

MVP 静态内置 boss-recruiting。Runtime 上报的 capabilities_json 含 provider_id，
claim 时校验 invocation.tool_name 在该 Provider 批准的 tools 内。
schema_digest 强校验留 M0.5 接 LLM schema 时实现。
"""

from typing import Any, Dict, List, Optional

TRUSTED_PROVIDERS: Dict[str, Dict[str, Any]] = {
    "boss-recruiting": {
        "provider_id": "ai.aidwork.boss-recruiting",
        "min_provider_version": "1.0.0",
        "execution_target": "local_required",
        "tools": [
            "boss_filter",
            "boss_filter_options",
            "boss_send_to",
            "boss_send_current",
            "boss_clear_filter",
            "boss_goto",
            "boss_greet",
            "boss_accept_resume",
            "boss_reject_current",
            "boss_interview_demo",
            "boss_resume_detail",
            "boss_resume_batch",
        ],
    }
}


def get_provider(provider_key: str) -> Optional[Dict[str, Any]]:
    """按 provider key（如 'boss-recruiting'）取受信 Provider 配置"""
    return TRUSTED_PROVIDERS.get(provider_key)


def get_provider_key_for_device(capabilities: Optional[Dict[str, Any]]) -> Optional[str]:
    """从设备 capabilities_json 解析 provider key

    capabilities_json 约定含 provider_id（如 'ai.aidwork.boss-recruiting'），
    与 TRUSTED_PROVIDERS 各条目的 provider_id 匹配。
    """
    if not capabilities:
        return None
    provider_id = capabilities.get("provider_id")
    if not provider_id:
        return None
    for key, provider in TRUSTED_PROVIDERS.items():
        if provider["provider_id"] == provider_id:
            return key
    return None


def allowed_tools(provider_key: str) -> List[str]:
    provider = TRUSTED_PROVIDERS.get(provider_key)
    return list(provider["tools"]) if provider else []


def is_tool_allowed(provider_key: Optional[str], tool_name: str) -> bool:
    """校验 tool_name 在该 Provider 批准的 tools 内"""
    if not provider_key:
        return False
    return tool_name in allowed_tools(provider_key)
