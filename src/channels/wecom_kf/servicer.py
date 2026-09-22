"""企微客服员工（接待人员）姓名反查

userid -> 企微通讯录真实姓名 的统一入口，供三处复用：
- channel_routes._persist_kf_servicer_message（员工消息落库时填 metadata.servicer_name）
- transfer_to_human（转人工成功后回写线索 servicer_name）
- external_push_human._resolve_transfer_info（推送时兜底，后台 runner 无 api_client，
  经 ChannelFactory 按租户渠道配置现取）

查询结果写 Redis 缓存（wecom_kf_servicer_name:{tenant_id}:{userid}，TTL 1 天，
键语义见 CacheKeys.WECOM_KF_SERVICER_NAME），失败返回空串不阻塞调用方主流程。
"""
from typing import Any, Optional

from loguru import logger

from src.core.cache_utils import CacheKeys
from src.core.redis_client import redis_client

# 企微通讯录成员姓名极少变更，1 天足够
_SERVICER_NAME_TTL = 86400


async def resolve_servicer_name(
    tenant_id: str, servicer_userid: str, api_client: Any = None
) -> str:
    """查询企微侧员工真实姓名（Redis 缓存 userid->name，TTL 1 天）。

    Args:
        tenant_id: 租户 ID（缓存键隔离 + 无 api_client 时按租户配置现取客户端）
        servicer_userid: 企微成员 userid（大小写敏感）
        api_client: WeComKfApiClient 实例；为空时经 ChannelFactory 从租户
            wecom_kf 渠道配置现取（后台 runner 场景），取不到则放弃查询

    Returns:
        真实姓名；查询失败/无姓名返回空串，由调用方降级（不阻塞主流程）
    """
    if not servicer_userid:
        return ""

    cache_key = f"{CacheKeys.WECOM_KF_SERVICER_NAME}:{tenant_id}:{servicer_userid}"
    try:
        cached = redis_client.get(cache_key)
        if cached is not None:
            return str(cached)
    except Exception as e:
        logger.debug(f"[wecom_kf] 员工姓名缓存读取失败: {e}")

    if api_client is None:
        try:
            from src.saas.services.channel_factory import ChannelFactory

            adapter, _, _ = await ChannelFactory.create_from_tenant_config(
                tenant_id, "wecom_kf"
            )
            api_client = getattr(adapter, "api_client", None) if adapter else None
        except Exception as e:
            logger.debug(f"[wecom_kf] 员工姓名查询渠道客户端获取失败: tenant={tenant_id}, error={e}")
            return ""
        if api_client is None:
            return ""

    try:
        result = await api_client.get_user(servicer_userid)
    except Exception as e:
        logger.debug(f"[wecom_kf] 员工姓名查询失败: userid={servicer_userid}, error={e}")
        return ""

    if not isinstance(result, dict) or result.get("errcode", 0) != 0:
        return ""
    name = str(result.get("name", "") or "")
    if name:
        try:
            redis_client.set(cache_key, name, ex=_SERVICER_NAME_TTL)
        except Exception as e:
            logger.debug(f"[wecom_kf] 员工姓名缓存写入失败: {e}")
    return name
