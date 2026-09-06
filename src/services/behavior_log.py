"""用户行为审计日志写入模块

设计文档：docs/system/user-behavior-audit-log-design.md
- 表：user_behavior_logs（系统表，deploy/init-postgres.sql）
- 记录范围：登录成功/失败/登出/改密/发验证码/改资料/渠道绑定（Phase 1），
  管理后台 CRUD（Phase 2，走 audit_action 装饰器），普通用户动作（Phase 3）
- 核心原则：写入失败只记日志，绝不抛出影响业务主流程

用法（登录等需要区分成功/失败分支的端点用显式调用）：

    from src.services.behavior_log import record_behavior, record_behavior_sync
    from src.saas.models.enums import BehaviorAction
    await record_behavior(request, BehaviorAction.LOGIN, user_id=..., detail={...})

用法（管理后台 CRUD 端点用装饰器，Phase 2 批量挂）：

    from src.services.behavior_log import audit_action
    @router.post("/tenants")
    @audit_action(BehaviorAction.CREATE, BehaviorResourceType.TENANT)   # 必须挂在 @router 之下
    async def create_tenant(request: Request, ...): ...
"""

import asyncio
import functools
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Request
from loguru import logger
from pydantic import BaseModel

from src.db.database import get_db_connection
from src.saas.models.enums import BehaviorAction, BehaviorDeviceType, BehaviorEntry

# detail 敏感键过滤词表（与 sanitize_error_info 的敏感模式口径保持一致）
SENSITIVE_DETAIL_KEYS = {
    "password", "new_password", "old_password", "token", "api_key", "secret", "code",
}

# error_msg 截断长度
ERROR_MSG_MAX_LEN = 500
# UA 截断长度（与表列宽一致）
USER_AGENT_MAX_LEN = 512


# ============== detail 敏感信息过滤 ==============

def sanitize_detail(detail: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """递归过滤 detail 中的敏感键（不修改原 dict，返回新 dict）

    敏感键：password / new_password / old_password / token / api_key / secret / code
    键名匹配不区分大小写（如 Password、API_KEY 也会被过滤）
    """
    if not detail:
        return {}
    return _sanitize_value(detail)


def _sanitize_value(value: Any) -> Any:
    """递归过滤：dict 过滤敏感键，list 逐项处理，其他类型原样返回"""
    if isinstance(value, dict):
        result = {}
        for key, val in value.items():
            if str(key).lower() in SENSITIVE_DETAIL_KEYS:
                result[str(key)] = "***"
            else:
                result[str(key)] = _sanitize_value(val)
        return result
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item) for item in value]
    return value


# ============== 设备类型解析（自写规则，不引重依赖）==============

# 受控词表（设计文档 §5.4）：写入 device_info 的合法值。
# 匹配顺序：先判 App 内嵌（微信），再判 OS；钉钉/飞书内嵌后续按需扩展。
DEVICE_VOCABULARY = {
    "windows_pc", "mac_pc", "linux_pc",
    "android_mobile", "ios_mobile", "harmony_mobile",
    "android_wechat", "ios_wechat",
    "unknown",
}

# device_info -> device_type（粗分）归并规则
_DEVICE_TYPE_SUFFIX_MAP = {
    "pc": BehaviorDeviceType.PC.value,
    "mobile": BehaviorDeviceType.MOBILE.value,
    "wechat": BehaviorDeviceType.MOBILE.value,
}


def _device_type_of(device_info: str) -> str:
    """由细分词表值归并粗分 device_type：*_pc -> pc，*_mobile/*_wechat -> mobile"""
    if device_info == "unknown":
        return BehaviorDeviceType.UNKNOWN.value
    suffix = device_info.rsplit("_", 1)[-1]
    return _DEVICE_TYPE_SUFFIX_MAP.get(suffix, BehaviorDeviceType.UNKNOWN.value)


def _ua_contains_os(ua_lower: str) -> Optional[str]:
    """从 UA 中识别 OS（内嵌 App 判断之后调用），返回词表值或 None"""
    # 鸿蒙优先于安卓：鸿蒙浏览器 UA 常带 Android 兼容串（如 HUAWEI/ArkWeb）
    if "harmony" in ua_lower or "hmos" in ua_lower:
        return "harmony_mobile"
    if "android" in ua_lower:
        return "android_mobile"
    if "iphone" in ua_lower or "ipad" in ua_lower or "ipod" in ua_lower:
        return "ios_mobile"
    if "windows" in ua_lower:
        return "windows_pc"
    if "mac os" in ua_lower or "macintosh" in ua_lower:
        return "mac_pc"
    # linux 放最后：android UA 也含 linux，已被上面分支拦截
    if "linux" in ua_lower or "x11" in ua_lower:
        return "linux_pc"
    return None


def parse_device(user_agent: str) -> Tuple[str, str]:
    """解析 User-Agent，返回 (device_info 细分词表值, device_type 粗分值)

    匹配顺序（设计文档 §5.4）：先判 App 内嵌（MicroMessenger 等），再判 OS。
    安卓微信 UA 同时含 Mobile 和 MicroMessenger，若先判 OS 会误归为 android_mobile。
    解析结果随事件写入 device_info 冻结，规则日后升级不影响历史口径。
    """
    if not user_agent:
        return ("unknown", BehaviorDeviceType.UNKNOWN.value)
    ua_lower = user_agent.lower()

    # 1. 先判 App 内嵌
    if "micromessenger" in ua_lower:
        # 微信内嵌：按 OS 细分为 android_wechat / ios_wechat
        if "android" in ua_lower or "harmony" in ua_lower or "hmos" in ua_lower:
            device_info = "android_wechat"
        elif "iphone" in ua_lower or "ipad" in ua_lower or "ipod" in ua_lower:
            device_info = "ios_wechat"
        else:
            # 词表外不造词，归 unknown
            device_info = "unknown"
        return (device_info, _device_type_of(device_info))

    # 2. 再判 OS
    device_info = _ua_contains_os(ua_lower)
    if device_info is None:
        return ("unknown", BehaviorDeviceType.UNKNOWN.value)
    return (device_info, _device_type_of(device_info))


# ============== 请求上下文提取 ==============

def _get_client_ip(request: Optional[Request]) -> Optional[str]:
    """取客户端 IP：X-Forwarded-For 首段（生产在 nginx 后）-> request.client.host"""
    if request is None:
        return None
    try:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            first = xff.split(",")[0].strip()
            if first:
                return first[:45]
        client = getattr(request, "client", None)
        if client and getattr(client, "host", None):
            return client.host[:45]
    except Exception:
        pass
    return None


def _get_user_agent(request: Optional[Request]) -> Optional[str]:
    """取原始 UA（截断 512，与表列宽一致）；渠道回调无浏览器上下文时为 None"""
    if request is None:
        return None
    try:
        ua = request.headers.get("user-agent")
        if ua:
            return ua[:USER_AGENT_MAX_LEN]
    except Exception:
        pass
    return None


def _get_token_id(request: Optional[Request]) -> Optional[str]:
    """取当前 token 指纹：SHA256(Authorization 中的 token) 前 8 位，不存原文"""
    if request is None:
        return None
    try:
        auth_header = request.headers.get("authorization") or ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            if token:
                return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]
    except Exception:
        pass
    return None


def _get_request_id() -> Optional[str]:
    """取 obs 追踪的 trace_id（仅对话链路有；取不到返回 None，不影响调用方）

    trace_id 挂在 SessionRecordService 的 trace_collector 上（agent.py SSE handler
    内创建的局部实例），非对话上下文（登录/管理后台等）恒为 None。
    """
    try:
        from src.services.session_record import SessionRecordManager
        record = SessionRecordManager.get_current_record()
        collector = getattr(record, "trace_collector", None)
        trace_id = getattr(collector, "trace_id", None)
        return trace_id if trace_id else None
    except Exception:
        return None


def _resolve_tenant_id(request: Optional[Request], override: Optional[str]) -> Optional[str]:
    """tenant_id：显式传参优先，否则从 request.state（TenantContextMiddleware 注入）读取"""
    if override:
        return override
    if request is None:
        return None
    return getattr(request.state, "tenant_id", None) or None


def _resolve_user_id(request: Optional[Request], override: Optional[str]) -> Optional[str]:
    """user_id：显式传参优先（登录失败时用户身份未知则不传），否则从 request.state 读取"""
    if override:
        return override
    if request is None:
        return None
    return getattr(request.state, "user_id", None) or None


# ============== DB 写入 ==============

def _insert_sync(row: Dict[str, Any]) -> None:
    """同步执行 INSERT（调用方负责用 asyncio.to_thread 包裹，假异步规范）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO user_behavior_logs (
                tenant_id, user_id, user_role, action, resource_type, resource_id,
                resource_name, detail, client_ip, user_agent, success, error_msg,
                entry, login_method, channel, channel_user_id, token_id, request_id,
                http_method, path, device_type, device_info
            ) VALUES (
                %(tenant_id)s, %(user_id)s, %(user_role)s, %(action)s, %(resource_type)s, %(resource_id)s,
                %(resource_name)s, %(detail)s, %(client_ip)s, %(user_agent)s, %(success)s, %(error_msg)s,
                %(entry)s, %(login_method)s, %(channel)s, %(channel_user_id)s, %(token_id)s, %(request_id)s,
                %(http_method)s, %(path)s, %(device_type)s, %(device_info)s
            )
            """,
            row,
        )
        conn.commit()


def _truncate_error_msg(error_msg: Optional[str]) -> Optional[str]:
    """error_msg 截断 500 字符"""
    if error_msg is None:
        return None
    error_msg = str(error_msg)
    return error_msg[:ERROR_MSG_MAX_LEN] if len(error_msg) > ERROR_MSG_MAX_LEN else error_msg


def _build_row(
    request: Optional[Request],
    action: str,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    resource_name: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    user_role: Optional[str] = None,
    tenant_id: Optional[str] = None,
    success: bool = True,
    error_msg: Optional[str] = None,
    entry: Optional[str] = None,
    login_method: Optional[str] = None,
    channel: Optional[str] = None,
    channel_user_id: Optional[str] = None,
    http_method: Optional[str] = None,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    """组装单条行为日志行（渠道事件各字段语义见设计文档 §5.6）"""
    # 入口：显式传参优先；未传时按请求上下文推断（有 request 即 web，渠道事件必须显式传 channel）
    resolved_entry = entry or (BehaviorEntry.WEB.value if request is not None else None)

    # 设备解析：渠道回调无浏览器上下文，UA 为空则设备为 unknown
    ua = _get_user_agent(request)
    device_info, device_type = parse_device(ua)

    detail_json = None
    sanitized = sanitize_detail(detail)
    if sanitized:
        try:
            detail_json = json.dumps(sanitized, ensure_ascii=False)
        except (TypeError, ValueError):
            detail_json = json.dumps({"detail": "unserializable"}, ensure_ascii=False)

    return {
        "tenant_id": _resolve_tenant_id(request, tenant_id),
        "user_id": _resolve_user_id(request, user_id),
        "user_role": user_role or (getattr(request.state, "user_role", None) if request is not None else None),
        "action": str(getattr(action, "value", action)),
        "resource_type": resource_type,
        "resource_id": resource_id,
        "resource_name": resource_name,
        "detail": detail_json,
        "client_ip": _get_client_ip(request),
        "user_agent": ua,
        "success": success,
        "error_msg": _truncate_error_msg(error_msg),
        "entry": resolved_entry,
        "login_method": login_method,
        "channel": channel,
        "channel_user_id": channel_user_id,
        "token_id": _get_token_id(request),
        "request_id": _get_request_id(),
        "http_method": http_method,
        "path": path,
        "device_type": device_type,
        "device_info": device_info,
    }


def _log_write_failure(action: str, e: Exception) -> None:
    """写入失败的统一处理：只记错误日志，绝不抛出影响业务"""
    logger.opt(exception=True).error(f"后端日志：用户行为审计日志写入失败 action={action}: {e}")


async def record_behavior(
    request: Optional[Request],
    action: str,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    resource_name: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    user_role: Optional[str] = None,
    tenant_id: Optional[str] = None,
    success: bool = True,
    error_msg: Optional[str] = None,
    entry: Optional[str] = None,
    login_method: Optional[str] = None,
    channel: Optional[str] = None,
    channel_user_id: Optional[str] = None,
    http_method: Optional[str] = None,
    path: Optional[str] = None,
) -> None:
    """记录一条用户行为审计日志（异步上下文用）

    tenant_id / user_id / user_role 默认从 request.state 读取（TenantContextMiddleware
    已注入），登录/渠道事件可显式覆盖。IP 取 X-Forwarded-For 首段。
    写入失败仅记日志，绝不抛出影响业务主流程。
    """
    try:
        row = _build_row(
            request, action,
            resource_type=resource_type, resource_id=resource_id,
            resource_name=resource_name, detail=detail,
            user_id=user_id, user_role=user_role, tenant_id=tenant_id,
            success=success, error_msg=error_msg, entry=entry,
            login_method=login_method, channel=channel, channel_user_id=channel_user_id,
            http_method=http_method, path=path,
        )
        # INSERT 是同步阻塞调用，必须 to_thread 包裹（假异步规范）
        await asyncio.to_thread(_insert_sync, row)
    except Exception as e:
        _log_write_failure(str(getattr(action, "value", action)), e)


def record_behavior_sync(
    request: Optional[Request],
    action: str,
    **kwargs,
) -> None:
    """记录一条用户行为审计日志（同步上下文用，如渠道回调线程、命令行脚本）

    参数同 record_behavior；在当前线程直接同步执行 INSERT。
    写入失败仅记日志，绝不抛出影响业务主流程。
    """
    try:
        row = _build_row(request, action, **kwargs)
        _insert_sync(row)
    except Exception as e:
        _log_write_failure(str(getattr(action, "value", action)), e)


# ============== 路由装饰器（Phase 2 管理后台 CRUD 批量挂点用）==============

def _find_request(*fn_args, **fn_kwargs) -> Optional[Request]:
    """从位置/关键字参数中定位 Request

    - fastapi.Request 实例优先（isinstance 精确匹配）；
    - 关键字参数也必须 isinstance 校验：FastAPI 全 kwargs 传参，且部分端点的
      Pydantic body 参数名恰好叫 request（如 tenant_migration.py），直接
      kwargs.get("request") 会误拿 body；
    - 形状兜底（method/url 鸭子类型）：扫描位置与关键字参数值，排除 Pydantic
      BaseModel（body 模型可能恰好定义 method/url 字段，防止误伤）。
    """
    request = next((a for a in fn_args if isinstance(a, Request)), None)
    if request is None:
        request = next((a for a in fn_kwargs.values() if isinstance(a, Request)), None)
    if request is None:
        candidates = list(fn_args) + list(fn_kwargs.values())
        request = next(
            (
                a for a in candidates
                if not isinstance(a, BaseModel)
                and hasattr(a, "method") and hasattr(a, "url")
            ),
            None,
        )
    return request


def _find_body_candidates(fn_args: tuple, fn_kwargs: dict) -> List[Any]:
    """收集可能的请求体候选（dict 或 Pydantic BaseModel 实例），按参数顺序"""
    return [
        a for a in (list(fn_args) + list(fn_kwargs.values()))
        if isinstance(a, BaseModel) or isinstance(a, dict)
    ]


def _extract_resource_name(name_arg: str, fn_args: tuple, fn_kwargs: dict) -> Optional[Any]:
    """从请求体候选中提取名称字段（body 参数名可能是 body/req/data 等任意名字）

    - dict：键存在即取（值可为 None）；
    - Pydantic 模型：字段存在即取（值可为 None），字段不存在才继续找下一个候选；
    - 全部候选都没有该字段时返回 None。
    """
    for body in _find_body_candidates(fn_args, fn_kwargs):
        if isinstance(body, dict):
            if name_arg in body:
                return body[name_arg]
        else:
            model_fields = getattr(type(body), "model_fields", None) or {}
            if name_arg in model_fields:
                return getattr(body, name_arg, None)
    return None


def _extract_update_fields(
    action: Any, fn_args: tuple, fn_kwargs: dict
) -> Optional[Dict[str, Any]]:
    """UPDATE 类操作时，从 Pydantic body 模型提取请求携带的字段名列表（不含值）

    设计文档 §5.3「至少记变更字段名列表」：detail={"fields": [...]}。
    用 model_dump(exclude_unset=True) 只记请求实际携带的字段（PATCH 语义）；
    仅字段名，不含字段值，无敏感信息泄露风险。非 UPDATE 或无 Pydantic body 时不记。
    """
    if getattr(action, "value", action) != BehaviorAction.UPDATE.value:
        return None
    for body in _find_body_candidates(fn_args, fn_kwargs):
        if not isinstance(body, BaseModel):
            continue
        try:
            fields = list(body.model_dump(exclude_unset=True).keys())
        except Exception:
            # 兜底：拿不到 exclude_unset 集合时退化为模型全部字段名
            fields = list(getattr(type(body), "model_fields", {}).keys())
        if fields:
            return {"fields": fields}
    return None


def audit_action(
    action: str,
    resource_type: str,
    id_arg: Optional[str] = None,
    name_arg: Optional[str] = None,
):
    """装饰 FastAPI 路由函数，自动记录行为审计日志

    - 业务函数成功返回后记 success=True；抛异常时记 success=False + error_msg 后原样 re-raise
    - 业务级失败也记失败：返回 dict 且 result["success"] is False 时记 success=False +
      error_msg（取 error 或 message 字段）；无 success 键的 dict 不受影响
    - resource_id：id_arg 指定路径/查询参数名（如 "tenant_id"），未指定时不取
    - resource_name：name_arg 指定请求体中的名称字段（如 "company_name"），从任意名字的
      Pydantic body / dict body 中提取，避免为记日志额外查库
    - UPDATE 操作自动记 detail={"fields": [...]}（请求体实际携带的字段名列表，不含值）
    - 同步 def 路由自动经 asyncio.to_thread 执行，不阻塞事件循环（backend_dev.md 假异步规范）
    - http_method / path 从 request 自动取，零成本精确定位端点
    - 登录/登出/改密码等不适用本装饰器（需在成功与失败分支分别记录、user 身份特殊），用显式调用
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            request = _find_request(*args, **kwargs)

            # 提取 resource_id（路径/查询参数）与 resource_name（请求体名称字段）
            resource_id = kwargs.get(id_arg) if id_arg else None
            resource_name = _extract_resource_name(name_arg, args, kwargs) if name_arg else None
            update_fields = _extract_update_fields(action, args, kwargs)

            http_method = request.method if request is not None else None
            path = request.url.path if request is not None else None

            try:
                if asyncio.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    # 同步 def 路由：to_thread 包裹执行，避免阻塞事件循环
                    result = await asyncio.to_thread(func, *args, **kwargs)
            except Exception as e:
                await record_behavior(
                    request, action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    resource_name=resource_name,
                    success=False,
                    error_msg=str(e),
                    detail=update_fields,
                    http_method=http_method,
                    path=path,
                )
                raise

            # 业务级失败判定：显式返回 {"success": False} 视为失败（误伤防护：无 success 键不触发）
            success = True
            error_msg = None
            if isinstance(result, dict) and result.get("success") is False:
                success = False
                error_msg = result.get("error") or result.get("message")

            await record_behavior(
                request, action,
                resource_type=resource_type,
                resource_id=resource_id,
                resource_name=resource_name,
                success=success,
                error_msg=error_msg,
                detail=update_fields,
                http_method=http_method,
                path=path,
            )
            return result
        return wrapper
    return decorator
