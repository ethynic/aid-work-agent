"""
Redis 缓存管理 API

提供平台管理员可视化查看/删除 Redis 缓存键值的能力，替代 redis-cli 手敲命令。
仅平台管理员可访问。

设计文档：docs/system/design-redis-cache-admin.md
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Request, HTTPException
from loguru import logger

from src.api.auth import get_current_user
from src.core.cache_utils import CacheKeys
from src.core.redis_client import redis_client
from src.saas.permissions.checker import is_platform_admin

router = APIRouter(prefix="/api/admin/redis", tags=["平台Redis管理"])


# ============== 工具函数 ==============

def _require_platform_admin(request: Request) -> dict:
    """校验平台管理员权限，失败抛 403。返回 user dict。"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    if not is_platform_admin(user):
        logger.warning(f"[RedisAdmin] 非平台管理员访问: {user.get('user_id')}")
        raise HTTPException(status_code=403, detail="仅平台管理员可访问")
    return user


def _registered_prefixes() -> set:
    """从 CacheKeys 类提取所有已登记的前缀字符串"""
    return {
        v for k, v in vars(CacheKeys).items()
        if not k.startswith("_") and isinstance(v, str)
    }


def _strip_key_prefix(key: str) -> str:
    """去掉 REDIS_KEY_PREFIX 前缀，返回业务前缀部分

    例：key_prefix="aid-local"，key="aid-local:token:abc123" → "token:abc123"
    """
    prefix = redis_client._key_prefix
    if prefix and key.startswith(prefix + ":"):
        return key[len(prefix) + 1:]
    return key


def _get_first_segment(stripped_key: str) -> str:
    """获取去掉 key_prefix 后的第一个段（业务前缀）

    例：stripped="token:abc123" → "token"
    """
    return stripped_key.split(":", 1)[0] if ":" in stripped_key else stripped_key


def _is_bare_key(key: str, registered: set) -> bool:
    """判断键是否为裸键（不在 CacheKeys 登记前缀）"""
    stripped = _strip_key_prefix(key)
    first_seg = _get_first_segment(stripped)
    return first_seg not in registered


def _build_match_pattern(prefix: Optional[str], search: Optional[str]) -> str:
    """构造 SCAN 的 MATCH 模式

    - prefix 非空：精确匹配前缀段，避免 "token" 误匹配 "token_usage"
      拼出 {kp}:{prefix}:*{search}* 形式（kp 为空时省略）
    - prefix 为空：匹配当前应用所有键
      拼出 {kp}:*{search}* 形式（kp 为空时退化为 *{search}*）
    """
    if prefix:
        # make_key 返回 "{kp}:{prefix}:" 或 "{prefix}:"，结尾带冒号
        full_prefix = redis_client.make_key(prefix, "")
        if search:
            return f"{full_prefix}*{search}*"
        return f"{full_prefix}*"
    # prefix 为空：匹配当前应用所有键（用冒号锚定，避免误匹配同前缀字符串）
    kp = redis_client._key_prefix
    base = f"{kp}:" if kp else ""
    if search:
        return f"{base}*{search}*"
    return f"{base}*" if base else "*"


def _format_size(size: Optional[int]) -> str:
    """字节数转可读单位（B/KB/MB）"""
    if size is None:
        return "-"
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.2f}MB"


def _format_ttl(ttl: int) -> Dict[str, Any]:
    """TTL 转可读结构"""
    if ttl == -1:
        return {"value": -1, "label": "永不过期", "level": "warning"}
    if ttl == -2:
        return {"value": -2, "label": "已过期", "level": "danger"}
    return {"value": ttl, "label": f"{ttl}s", "level": "normal"}


def _truncate_value(value: Any, max_len: int = 500) -> Any:
    """大值截断展示（管理页用，避免返回超大 JSON）"""
    if isinstance(value, str) and len(value) > max_len:
        return value[:max_len] + f"...[truncated {len(value) - max_len} chars]"
    return value


def _serialize_value(value: Any) -> Any:
    """序列化 Redis 值为 JSON 可响应结构"""
    # TODO: 敏感值脱敏（参考 design-redis-cache-admin.md §3.5）
    # Phase 1 不实现脱敏，值原样返回
    return _truncate_value(value)


def _serialize_members(members: Any) -> Any:
    """序列化 hash/list/set/zset 成员"""
    # TODO: 敏感值脱敏（参考 design-redis-cache-admin.md §3.5）
    # Phase 1 不实现脱敏，值原样返回
    if isinstance(members, dict):
        return {str(k): _truncate_value(v) for k, v in members.items()}
    if isinstance(members, list):
        return [_truncate_value(m) for m in members]
    return members


# ============== API 端点 ==============

@router.get("/overview")
async def get_overview(request: Request):
    """概览：键总数、已登记前缀数、裸键数、Redis 连接状态

    Phase 1 不缓存结果（Phase 2 增强），每次全量 SCAN。
    """
    user = _require_platform_admin(request)

    try:
        registered = _registered_prefixes()
        registered_count = 0
        bare_count = 0
        total_scanned = 0

        # 全量 SCAN 统计（复用 _build_match_pattern，避免 key_prefix 非空时拼出 "kp::*" 不匹配真实键）
        cursor = 0
        full_match = _build_match_pattern(None, None)
        while True:
            cursor, batch = redis_client.scan(cursor, full_match, count=500)
            for k in batch:
                total_scanned += 1
                if _is_bare_key(k, registered):
                    bare_count += 1
                else:
                    registered_count += 1
            if cursor == 0:
                break

        return {
            "success": True,
            "total_keys": total_scanned,
            "registered_keys": registered_count,
            "bare_keys": bare_count,
            "redis_connected": redis_client.connected,
            "fallback_active": not redis_client.connected,
            "key_prefix": redis_client._key_prefix,
        }
    except Exception as e:
        logger.opt(exception=True).error(f"[RedisAdmin] overview 失败: {e}")
        return {
            "success": False,
            "error": "获取概览失败",
            "debug": str(e),
            "total_keys": 0,
            "registered_keys": 0,
            "bare_keys": 0,
            "redis_connected": redis_client.connected,
            "fallback_active": not redis_client.connected,
        }


@router.get("/keys")
async def list_keys(
    request: Request,
    prefix: Optional[str] = Query(None, description="CacheKeys 前缀过滤，如 'token'、'user'；'__bare__' 表示裸键"),
    search: Optional[str] = Query(None, description="键名子串搜索"),
    cursor: int = Query(0, description="SCAN 游标，首次传 0"),
    limit: int = Query(200, ge=1, le=1000, description="单次返回上限"),
):
    """键列表（支持前缀过滤、子串搜索、游标分页）"""
    user = _require_platform_admin(request)

    try:
        registered = _registered_prefixes()
        # 特殊标记：__bare__ 表示查看裸键
        is_bare_view = prefix == "__bare__"
        actual_prefix = None if is_bare_view else prefix

        match = _build_match_pattern(actual_prefix, search)

        # 累积 SCAN 直到达到 limit 或游标归零
        accumulated: List[str] = []
        next_cursor = cursor
        scan_rounds = 0
        max_rounds = 50  # 安全上限，避免无限循环
        while len(accumulated) < limit and scan_rounds < max_rounds:
            next_cursor, batch = redis_client.scan(next_cursor, match, count=100)
            accumulated.extend(batch)
            scan_rounds += 1
            if next_cursor == 0:
                break

        # 裸键视图：在内存中过滤掉已登记前缀的键
        if is_bare_view:
            accumulated = [k for k in accumulated if _is_bare_key(k, registered)]

        # 去重（SCAN 可能返回重复键）
        seen = set()
        unique_keys = []
        for k in accumulated:
            if k not in seen:
                seen.add(k)
                unique_keys.append(k)

        # 截断到 limit
        truncated = unique_keys[:limit]
        has_more = (next_cursor != 0) or (len(unique_keys) > limit)

        # 批量查询每个键的 type / ttl / size
        items = []
        for key in truncated:
            items.append({
                "key": key,
                "type": redis_client.type(key),
                "ttl": redis_client.ttl(key),
                "size": redis_client.memory_usage(key),
                "size_label": _format_size(redis_client.memory_usage(key)),
                "ttl_info": _format_ttl(redis_client.ttl(key)),
                "is_bare": _is_bare_key(key, registered),
            })

        return {
            "success": True,
            "items": items,
            "next_cursor": next_cursor if has_more else 0,
            "has_more": has_more,
            "filter": {
                "prefix": prefix,
                "search": search,
                "is_bare_view": is_bare_view,
            },
        }
    except Exception as e:
        logger.opt(exception=True).error(f"[RedisAdmin] list_keys 失败: {e}")
        return {
            "success": False,
            "error": "获取键列表失败",
            "debug": str(e),
            "items": [],
            "next_cursor": 0,
            "has_more": False,
        }


@router.get("/keys/{key:path}")
async def get_key_detail(request: Request, key: str):
    """单键详情（按 Redis 类型分支返回 value / members）"""
    user = _require_platform_admin(request)

    try:
        key_type = redis_client.type(key)
        ttl = redis_client.ttl(key)
        size = redis_client.memory_usage(key)

        if key_type == "none":
            return {
                "success": False,
                "message": "键不存在",
                "key": key,
            }

        value: Any = None
        members: Any = None
        truncated = False
        member_count = 0

        if key_type == "string":
            value = redis_client.get(key)
        elif key_type == "hash":
            members = redis_client.hgetall(key)
            member_count = len(members) if members else 0
        elif key_type == "list":
            length = redis_client.llen(key)
            member_count = length
            end = min(length - 1, 199) if length > 0 else 0
            members = redis_client.lrange(key, 0, end)
            truncated = length > 200
        elif key_type == "set":
            members = redis_client.smembers(key)
            member_count = len(members) if members else 0
            # SET 类型无法精准分页，超过 200 截断
            if member_count > 200:
                members = members[:200]
                truncated = True
        elif key_type == "zset":
            length = redis_client.zcard(key)
            member_count = length
            end = min(length - 1, 199) if length > 0 else 0
            raw_members = redis_client.zrange(key, 0, end)
            # zrange 只返回 member，需要单独查 score（管理页 Phase 1 简化：只展示 member 列表）
            members = raw_members
            truncated = length > 200
        else:
            return {
                "success": False,
                "message": f"暂不支持查看类型: {key_type}",
                "key": key,
                "type": key_type,
            }

        return {
            "success": True,
            "key": key,
            "type": key_type,
            "ttl": ttl,
            "ttl_info": _format_ttl(ttl),
            "size": size,
            "size_label": _format_size(size),
            "value": _serialize_value(value) if key_type == "string" else None,
            "members": _serialize_members(members) if key_type != "string" else None,
            "member_count": member_count,
            "truncated": truncated,
            "is_bare": _is_bare_key(key, _registered_prefixes()),
        }
    except Exception as e:
        logger.opt(exception=True).error(f"[RedisAdmin] get_key_detail 失败 [{key}]: {e}")
        return {
            "success": False,
            "error": "获取键详情失败",
            "debug": str(e),
            "key": key,
        }


@router.delete("/keys/{key:path}")
async def delete_key(request: Request, key: str):
    """删除单键（含审计日志）"""
    user = _require_platform_admin(request)

    try:
        existed = redis_client.exists(key)
        if not existed:
            raise HTTPException(status_code=404, detail="键不存在")

        redis_client.delete(key)

        logger.info(f"[RedisAdmin] 删除键 {key} by {user.get('user_id')}")

        return {
            "success": True,
            "deleted_key": key,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"[RedisAdmin] delete_key 失败 [{key}]: {e}")
        return {
            "success": False,
            "error": "删除键失败",
            "debug": str(e),
        }
