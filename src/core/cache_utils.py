#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缓存工具模块

提供统一的 Redis 缓存封装，包括：
1. 通用缓存装饰器
2. 批量缓存失效函数
3. 缓存 Key 命名规范
"""

from functools import wraps
from typing import Any, Callable, Optional

from loguru import logger

from src.core.redis_client import redis_client


# ============== Key 前缀规范 ==============

class CacheKeys:
    """缓存 Key 前缀定义"""
    TOKEN = "token"                    # token:{token}
    USER = "user"                      # user:{user_id}
    USER_SESSIONS = "user_sessions"    # user_sessions:{user_id}:{tenant}:{page}:{size}
    SESSION = "session"                # session:{session_id}
    SESSION_MSGS = "session_msgs"      # session_msgs:{session_id}:{limit}
    TENANT = "tenant"                  # tenant:{tenant_id}
    TENANT_CODE = "tenant_code"        # tenant_code:{code}
    TENANT_STATS = "tenant_stats"      # tenant_stats:{tenant_id}
    TENANT_SUB_COUNT = "tenant_sub_cnt"  # tenant_sub_cnt:{tenant_id}
    AGENT_QUOTA = "agent_quota"        # agent_quota:{tenant_id}:{agent_id}
    USER_AGENTS = "user_agents"        # user_agents:{user_id}
    PLATFORM_USAGE = "platform_usage"  # platform_usage:{month}
    TENANT_USAGE = "tenant_usage"      # tenant_usage:{tenant_id}:{month}:{page}
    TENANT_USG_SUM = "tenant_usage_sum"  # tenant_usage_sum:{tenant_id}:{month}
    TOKEN_USAGE = "token_usage"        # token_usage:{tenant_id}:{start}:{end}:{gb}
    AGENT_INSTANCE = "agent_inst"      # agent_inst:{instance_id}
    TENANT_INST = "tenant_inst"        # tenant_inst:{tenant_id}
    CHANNEL_SESSION = "ch_session"     # ch_session:{channel_type}:{user_id}
    DOCS_LIST = "docs_list"            # docs_list:{tenant}:{user}:{limit}:{offset}
    DOCS_COUNT = "docs_count"          # docs_count:{tenant}:{user}
    PROMPT_CONTENT = "prompt_content"  # prompt_content:{prompt_id}:{version}
    PROMPT_LABEL = "prompt_label"      # prompt_label:{prompt_id}:{label}
    PROMPT_REGISTRY = "prompt_reg"       # prompt_reg:{tenant_id}:{scope}:{scope_id}
    PROMPT_SECTIONS = "prompt_sections"  # prompt_sections:{agent_id} → {section_key: content} dict
    CHANNEL_RATE_LIMIT = "ch_rate_limit"  # ch_rate_limit:{channel_type}:{user_id}（ZSET，滑动窗口）
    RECALL_PENDING = "recall_pending"     # recall_pending:{session_id}（SET，缓存"处理中被撤回的 msgid"，落库时补打 is_recalled）
    COMPRESSION_METRICS = "comp_metrics"  # comp_metrics:{kind} 上下文压缩指标（Phase 7 §7.2）
    SCHEDULER_LOCK = "sched_task_lock"    # sched_task_lock:manager（全局分布式锁，多 worker 唯一启动调度器）
    WECOM_RPA_SELF_ECHO_ESCAPE = "wecom_rpa:self_echo_escape"  # :{tenant_id}:{account_id}（ZSET，危险 echo 五分钟窗口）
    BROWSER_RUN = "browser_run"              # browser_run:{tenant_id}:{run_id}
    BROWSER_OWNER = "browser_owner"          # browser_owner:{tenant_id}:{run_id}
    BROWSER_CONTROL = "browser_control"      # browser_control:{tenant_id}:{run_id}
    # Phase 3+ 预登记命名，不代表当前阶段已提供相应功能。
    BROWSER_VIEW_TICKET = "browser_view_ticket"
    BROWSER_WEB_PRESENCE = "browser_web_presence"
    BROWSER_LAUNCH_TICKET = "browser_launch_ticket"
    BROWSER_DESKTOP_RUNTIME_SESSION = "browser_desktop_runtime_session"
    BROWSER_ASSISTANCE = "browser_assistance"
    AGENT_TOOL_SUSPENSION = "agent_tool_suspension"
    AGENT_SESSION_SUSPENSION = "agent_session_suspension"
    BROWSER_RESUME_JOBS = "browser_resume_jobs"
    AGENT_CONTINUATION_EVENTS = "agent_continuation_events"
    # video-agent：精修模式提示词草稿缓存（draft_only=True 时写入，draft_only=False 时优先读取）
    # video_prompt_draft:{session_id} -> PromptResult 序列化 dict（含 business_prompt/craft_prompt/model_params）
    VIDEO_PROMPT_DRAFT = "video_prompt_draft"
    # 文件资产 / 企微客服 / 独立会话状态（登记前缀，消除管理后台"裸键"告警）
    UPLOADED_FILE = "uploaded_file"        # uploaded_file:{file_id}（文件/图片元数据）
    SUBAGENT_GREETING = "subagent_greeting"  # subagent_greeting:{agent_id}（数字员工空态摘要+快捷按钮，LLM 生成后缓存）
    WECOM_KF = "wecom_kf"                  # wecom_kf:{corp_id}:{key}（企微客服，key 必须带企业维度）
    STANDALONE_AGENT = "standalone_agent"  # standalone_agent:{session_id}:{agent_id}
    # pre-sales-api 委托登录 client_token：pre_sales_client_token:{tenant_id}:{assignee_phone}
    # （外部系统委托人 token，有效期 1 天，缓存 TTL 23h 留 buffer；Code=-99 时 force_refresh 强刷）
    PRE_SALES_CLIENT_TOKEN = "pre_sales_client_token"
    # recap 任务幂等：recap_task:{tenant_id}:{task_name}:{round_message_id}
    # （每轮问答结束后的沉淀任务防重入/防回调重放，TTL 24h；docs/subagent/recap-mechanism-design.md）
    RECAP_TASK_DEDUP = "recap_task"


# ============== 通用缓存函数 ==============

def get_cached(key_prefix: str, *key_parts: str) -> Optional[Any]:
    """从 Redis 读取缓存数据

    Args:
        key_prefix: 缓存前缀，建议使用 CacheKeys 中的值
        key_parts: 缓存 key 组成部分

    Returns:
        缓存数据，不存在返回 None
    """
    key = redis_client.make_key(key_prefix, ":".join(str(p) for p in key_parts))
    return redis_client.get(key)


def set_cached(key_prefix: str, *key_parts: str, value: Any, ttl: int) -> None:
    """写入 Redis 缓存

    Args:
        key_prefix: 缓存前缀
        key_parts: 缓存 key 组成部分
        value: 要缓存的数据
        ttl: 过期时间（秒）
    """
    key = redis_client.make_key(key_prefix, ":".join(str(p) for p in key_parts))
    redis_client.set(key, value, ex=ttl)


def delete_cached(key_prefix: str, *key_parts: str) -> bool:
    """删除 Redis 缓存

    Args:
        key_prefix: 缓存前缀
        key_parts: 缓存 key 组成部分

    Returns:
        是否删除成功
    """
    key = redis_client.make_key(key_prefix, ":".join(str(p) for p in key_parts))
    return redis_client.delete(key)


def delete_cached_pattern(key_prefix: str, *key_parts: str) -> int:
    """按模式删除缓存（使用 Redis keys 命令，慎用）

    Args:
        key_prefix: 缓存前缀
        key_parts: 缓存 key 组成部分（最后一部分可为 '' 表示匹配所有后缀）

    Returns:
        删除的 key 数量
    """
    prefix = redis_client.make_key(key_prefix, ":".join(str(p) for p in key_parts))
    pattern = f"{prefix}*"
    keys_found = redis_client.keys(pattern)
    count = 0
    for k in keys_found:
        if redis_client.delete(k):
            count += 1
    return count


# ============== 缓存装饰器 ==============

def cached(key_prefix: str, ttl: int = 600):
    """通用缓存装饰器

    自动以函数参数生成缓存 key，优先返回缓存数据。

    Args:
        key_prefix: 缓存前缀
        ttl: 过期时间（秒）

    Usage:
        @cached(CacheKeys.USER, ttl=600)
        def get_user(user_id: str) -> dict:
            ...
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 排除 self/cls 第一个参数，用剩余参数生成 key
            key_parts = []
            for i, a in enumerate(args):
                if i == 0 and hasattr(a, "__class__") and not isinstance(a, (str, int, float, bool)):
                    continue
                key_parts.append(str(a))
            for v in kwargs.values():
                key_parts.append(str(v))

            if key_parts:
                cached_value = get_cached(key_prefix, *key_parts)
                if cached_value is not None:
                    return cached_value

            result = func(*args, **kwargs)

            if result is not None and key_parts:
                set_cached(key_prefix, *key_parts, value=result, ttl=ttl)

            return result
        return wrapper
    return decorator


# ============== 批量失效函数 ==============

def invalidate_user_cache(user_id: str) -> int:
    """清除用户相关所有缓存

    Args:
        user_id: 用户 ID

    Returns:
        清除的缓存数量
    """
    count = 0
    count += 1 if delete_cached(CacheKeys.USER, user_id) else 0
    count += 1 if delete_cached(CacheKeys.USER_AGENTS, user_id) else 0
    count += delete_cached_pattern(CacheKeys.USER_SESSIONS, user_id, "")
    logger.debug(f"后端日志：清除用户缓存 user_id={user_id}, 共 {count} 条")
    return count


def invalidate_tenant_cache(tenant_id: str) -> int:
    """清除租户相关所有缓存

    Args:
        tenant_id: 租户 ID

    Returns:
        清除的缓存数量
    """
    count = 0
    count += 1 if delete_cached(CacheKeys.TENANT, tenant_id) else 0
    count += 1 if delete_cached(CacheKeys.TENANT_STATS, tenant_id) else 0
    count += 1 if delete_cached(CacheKeys.TENANT_SUB_COUNT, tenant_id) else 0
    count += 1 if delete_cached(CacheKeys.TENANT_INST, tenant_id) else 0
    count += delete_cached_pattern(CacheKeys.AGENT_QUOTA, tenant_id, "")
    count += delete_cached_pattern(CacheKeys.TENANT_USAGE, tenant_id, "")
    count += delete_cached_pattern(CacheKeys.TENANT_USG_SUM, tenant_id, "")
    count += delete_cached_pattern(CacheKeys.TOKEN_USAGE, tenant_id, "")
    logger.debug(f"后端日志：清除租户缓存 tenant_id={tenant_id}, 共 {count} 条")
    return count


def invalidate_session_cache(session_id: str) -> int:
    """清除会话相关缓存

    Args:
        session_id: 会话 ID

    Returns:
        清除的缓存数量
    """
    count = 0
    count += 1 if delete_cached(CacheKeys.SESSION, session_id) else 0
    count += delete_cached_pattern(CacheKeys.SESSION_MSGS, session_id, "")
    return count
