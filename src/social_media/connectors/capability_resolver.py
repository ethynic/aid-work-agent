"""统一能力解析与调用前校验。

从账号持久化的 ``capabilities_json`` 解析该账号已支持的能力，并在调用平台能力
（发布、回查、拉报表、web 操作等）前做前置门禁：不支持即抛 ``CapabilityNotSupported``，
避免调用层收到意料中的不支持错误。

三模块（内容管理 / 广告管理 / 巡检商机）共用，替代原先内联在 ``SocialMediaService``
里的 ``_account_capability_values``。
"""

from __future__ import annotations

import json
from typing import Any

from src.social_media.connectors.base import CapabilityNotSupported
from src.social_media.enums import PlatformCapability


class CapabilityResolver:
    """账号能力解析器：解析 + 查询 + 强制校验。"""

    @staticmethod
    def resolve(account: dict[str, Any]) -> set[str]:
        """解析账号 ``capabilities_json.supported`` → 能力值字符串集合。

        ``capabilities_json`` 可能是 JSON 字符串（psycopg2 text 列）或已解析 dict
        （jsonb 列），二者都兼容；缺失时返回空集合。
        """
        raw = account.get("capabilities_json") or {}
        if isinstance(raw, str):
            raw = json.loads(raw)
        return set(raw.get("supported") or [])

    @staticmethod
    def supports(account: dict[str, Any], capability: PlatformCapability) -> bool:
        """账号是否支持某能力（用于 service 层带领域语义的判断）。"""
        return capability.value in CapabilityResolver.resolve(account)

    @staticmethod
    def require(account: dict[str, Any], capability: PlatformCapability) -> None:
        """调用平台能力前的强制门禁：不支持则抛 ``CapabilityNotSupported``。

        供发布执行器（S1）、广告动作执行器、巡检 web 操作连接器等在真正调用连接器方法
        前统一校验，把「声明支持但实现缺失」的矛盾挡在调用前。
        """
        if not CapabilityResolver.supports(account, capability):
            raise CapabilityNotSupported(capability.value)
