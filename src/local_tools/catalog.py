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
            # boss_interview_notify 为云端企微群通知（proxy_tool 覆写 execute 不建 invocation、不碰设备），
            # 列入仅为与 LOCAL_PROXY_TOOL_NAMES 全集一致；运行时侧 manifestVerifier 不含它
            "boss_interview_notify",
            "boss_list_jobs",
            "boss_select_job",
            # boss_read_chat / boss_open_chat 为沟通页只读能力（读消息流/未读清单、切会话），
            # 2026-08-27 随 boss-cli 0.2.4 新增，设备执行、非云端混合模式
            "boss_read_chat",
            "boss_open_chat",
            # boss_overlay_inspect / boss_overlay_dismiss 为弹层自愈原语（2026-08-31），
            # 仅供云端自愈编排内部调用，不进 SUBAGENT 白名单（agent 不可见）
            "boss_overlay_inspect",
            "boss_overlay_dismiss",
            # boss_jobs_list 为云端查询（proxy_tool 覆写 execute 不建 invocation、不碰设备），
            # 列入仅为与 LOCAL_PROXY_TOOL_NAMES 全集一致；运行时侧 manifestVerifier 不含它
            "boss_jobs_list",
            "boss_resume_detail",
            "boss_resume_batch",
        ],
    },
    # 微信 Provider（P3-A1）：与 Runtime src/providers.ts 的 weixin manifest（5 工具）
    # 对齐，但**不含 weixin_message_send（v1 写）**——v1 写不经底座许可链路；
    # weixin_message_send_v2 为底座 v2 统一操作名（真 v2 capability 随 P0 交付前，
    # Runtime 侧 PROTOCOL_NOT_SUPPORTED 门控拒绝，写路径安全不依赖本清单）。
    # 4 个只读工具（probe/chat_search/history_read/unread_list）供搜索/核验/预检
    # 经 local_tool 队列下发，读链路走旧 /result 回传。
    "weixin": {
        "provider_id": "ai.aidwork.weixin",
        "min_provider_version": "1.0.0",
        "execution_target": "local_required",
        "tools": [
            "weixin_probe",
            "weixin_name_resolve",
            "weixin_chat_search",
            "weixin_history_read",
            "weixin_unread_list",
            "weixin_message_send_v2",
        ],
    },
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


def get_provider_keys_for_device(capabilities: Optional[Dict[str, Any]]) -> List[str]:
    """设备 capabilities 覆盖的全部受信 provider key（多 Provider claim 过滤用）。

    解析优先序（总工程师契约补充 #2，与 P1-B Runtime capabilities 上报对齐）：
    1. capabilities.providers 数组条目直接命中 TRUSTED_PROVIDERS 的 key（Runtime 上报
       provider_key 字符串，如 'boss-recruiting'/'weixin'）；
    2. capabilities.provider_manifests 各 value 的 provider_id 映射回 key；
    3. 旧 capabilities.provider_id 字段映射（旧 Runtime 兼容，boss-only 现状）。
    去重后返回。行 provider_key 非 NULL 的 invocation 只派给覆盖该 key 的设备。
    """
    keys: List[str] = []
    if not capabilities:
        return keys

    def _add(key: Optional[str]) -> None:
        if key and key in TRUSTED_PROVIDERS and key not in keys:
            keys.append(key)

    # (1) providers 数组：provider_key 直接命中
    for entry in capabilities.get("providers") or []:
        if isinstance(entry, str):
            _add(entry)
        elif isinstance(entry, dict):
            _add(entry.get("provider_key") or entry.get("provider_id"))
    # (2) provider_manifests：provider_id 映射回 key
    manifests = capabilities.get("provider_manifests")
    if isinstance(manifests, dict):
        pid_to_key = {
            provider["provider_id"]: key for key, provider in TRUSTED_PROVIDERS.items()
        }
        for manifest in manifests.values():
            if isinstance(manifest, dict):
                _add(pid_to_key.get(manifest.get("provider_id")))
    # (3) 旧 provider_id 兜底
    pid = capabilities.get("provider_id")
    if pid:
        for key, provider in TRUSTED_PROVIDERS.items():
            if provider["provider_id"] == pid:
                _add(key)
    return keys


def allowed_tools(provider_key: str) -> List[str]:
    provider = TRUSTED_PROVIDERS.get(provider_key)
    return list(provider["tools"]) if provider else []


def is_tool_allowed(provider_key: Optional[str], tool_name: str) -> bool:
    """校验 tool_name 在该 Provider 批准的 tools 内"""
    if not provider_key:
        return False
    return tool_name in allowed_tools(provider_key)
