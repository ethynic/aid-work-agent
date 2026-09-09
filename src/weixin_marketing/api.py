"""微信营销自动化 Web API（R46 API 契约，前缀 /api/weixin-marketing）

- 统一 auth（get_current_user）+ 租户中间件（request.state.tenant_id）；
  user_id/tenant_id 只取认证上下文，不信请求体/查询参数；
- 响应沿项目 envelope：成功 ``{"success": True, "data": ...}``；错误 JSONResponse
  ``{"success": False, "error": 中文说明, "code": 稳定码, "field_errors": [...],
  "debug": 脱敏排查信息}``（backend_dev.md：错误响应必须带脱敏 debug 字段）；
- 稳定码矩阵：404 NOT_FOUND（跨租户/不存在/非属主统一，不泄露存在性）、
  409 CONFLICT（版本 CAS / 状态机冲突）、409 RETRY_EVIDENCE_REQUIRED（R52 未知
  效果重试缺 resolve 人工决定）、409 IDEMPOTENCY_PAYLOAD_CONFLICT /
  IDEMPOTENCY_IN_PROGRESS（幂等键）、422 VALIDATION_FAILED（语义错+field_errors）、
  429 QUOTA_EXCEEDED（预留映射——配额实际在底座许可事务以 QUOTA_EXCEEDED 执行，
  本 API 当前无服务层触发点）、503 ADAPTER_UNREGISTERED（enabled=false 门控）；
- Idempotency-Key（R46：scope=(tenant,user,route,key)，模块级 DB 表实现）：
  同 key 同 payload 幂等重放原响应（含状态码），同 key 异 payload 409，无 key 正常执行；
  仅「建资源 / 发布 / 手动 run」类 POST 接入（create/publish/run）。
  payload 判定 = 请求路径 + 请求体规范化 JSON 的 digest（CR-P1-1：路径并入
  digest，同 key 同 body 打不同资源一律 409，绝不跨资源重放）；等价 JSON
  键序差异经 sort_keys 归一；R51 同事务：业务写入与幂等完成记录（_Idempotency-
  Finalizer.write_on）经服务层同一次 commit 持久化——「业务已提交而响应未保存」
  不可达，失败整体回滚后同 key 同 payload 干净重试；崩溃残留的 pending 占位超
  TTL（10 分钟）且 digest 匹配才可接管（digest 不符 → 409，不覆盖执行）；
  手动 run 提供 key 时 request_id 恒取该 key（跨重试稳定，触发键同源去重）；
- async 端点内同步服务调用一律 asyncio.to_thread（backend_dev.md 假异步规范）。
"""

import asyncio
import hashlib
import json
import threading
import uuid as _uuid
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Type, TypeVar

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, ValidationError

from src.api.auth import get_current_user
from src.desktop_automation import attempts as da_attempts
from src.desktop_automation.constants import RUN_TERMINAL_STATES
from src.utils import sanitize_error_info
from src.weixin_marketing.constants import AUTOMATION_STATUSES
from src.weixin_marketing.service import (
    ConfigurationError,
    ConflictError,
    NotFoundError,
    RetryEvidenceRequiredError,
    TenantNotAllowedError,
    WeixinMarketingService,
    WeixinValidationError,
)

router = APIRouter(prefix="/api/weixin-marketing", tags=["weixin-marketing"])

# ==================== 稳定错误码 ====================

CODE_NOT_FOUND = "NOT_FOUND"
CODE_CONFLICT = "CONFLICT"
CODE_TENANT_NOT_ALLOWED = "TENANT_NOT_ALLOWED"
CODE_RETRY_EVIDENCE_REQUIRED = "RETRY_EVIDENCE_REQUIRED"
CODE_VALIDATION_FAILED = "VALIDATION_FAILED"
CODE_QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
CODE_ADAPTER_UNREGISTERED = "ADAPTER_UNREGISTERED"
CODE_INTERNAL_ERROR = "INTERNAL_ERROR"
CODE_IDEMPOTENCY_PAYLOAD_CONFLICT = "IDEMPOTENCY_PAYLOAD_CONFLICT"
CODE_IDEMPOTENCY_IN_PROGRESS = "IDEMPOTENCY_IN_PROGRESS"
CODE_IDEMPOTENCY_KEY_INVALID = "IDEMPOTENCY_KEY_INVALID"

TRIGGER_TYPES = ("once", "interval", "calendar", "event")

# run 状态全集（desktop_automation.constants：非终态 + §5.4 聚合终态）
RUN_STATES = ("pending", "running", "waiting_device", *RUN_TERMINAL_STATES)

# 幂等 scope 中的 route 标识（路径模板，不含具体资源 ID——scope 按 R46 为
# (tenant,user,route,key)；「同 payload」的判定 = request.url.path + body 规范化
# JSON 的 digest（CR-P1-1），故同 key 打到不同资源必 digest 不一致 → 409）
_ROUTE_CREATE = "POST /automations"
_ROUTE_PUBLISH = "POST /automations/{automation_id}/publish"
_ROUTE_RUN = "POST /automations/{automation_id}/run"

_IDEMPOTENCY_KEY_MIN = 8
_IDEMPOTENCY_KEY_MAX = 200
# 崩溃残留 pending 占位的废弃阈值（秒）——超时后同 key 请求可接管（CR-P1-3）
_IDEMPOTENCY_PENDING_TTL_SECONDS = 600

_M = TypeVar("_M", bound=BaseModel)


# ==================== 幂等键存储（模块级 DB 实现）====================

# 参照 desktop_agent_turn_requests / desktop_remote_tool_invocations 既有模式：
# UNIQUE 仲裁并发 + ON CONFLICT DO NOTHING + request_digest 异 payload 检测。
# 表 DDL 由本模块幂等自建（API 文件内建表先例：src/api/recruiting_operator.py）；
# 总工程师可另行同步进 deploy/init-postgres.sql（见交接说明）。
_IDEMPOTENCY_DDL = """
CREATE TABLE IF NOT EXISTS weixin_marketing_idempotency_keys (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    route TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    response_json JSONB,
    status_code INTEGER,
    status TEXT DEFAULT 'pending' NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE (tenant_id, user_id, route, idempotency_key)
)
"""

_idempotency_table_lock = threading.Lock()
_idempotency_table_ready = False


def ensure_idempotency_table() -> None:
    """幂等建幂等键表（进程内单次；并发 CREATE IF NOT EXISTS 冲突时复查存在性）"""
    global _idempotency_table_ready
    if _idempotency_table_ready:
        return
    with _idempotency_table_lock:
        if _idempotency_table_ready:
            return
        from src.db.database import get_db_connection

        try:
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute(_IDEMPOTENCY_DDL)
                conn.commit()
        except Exception:
            # 多 worker 并发建表的 pg_type 竞态窗口：复查存在性，仍在则放行
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' "
                    "AND table_name='weixin_marketing_idempotency_keys'"
                )
                if cur.fetchone() is None:
                    raise
                conn.commit()
        _idempotency_table_ready = True


def _digest_payload(payload: Dict[str, Any]) -> str:
    """规范化请求体摘要（sort_keys：等价 JSON 不同键序不误判异 payload）"""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _idempotency_reserve(
    tenant_id: str, user_id: str, route: str, key: str, digest: str
) -> Dict[str, Any]:
    """占位（同 desktop_agent reserve 语义）：新键→execute；既有键→digest/状态判定"""
    from src.db.database import get_db_connection

    ensure_idempotency_table()
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO weixin_marketing_idempotency_keys
                (tenant_id, user_id, route, idempotency_key, request_digest)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, user_id, route, idempotency_key) DO NOTHING
            RETURNING id
            """,
            (tenant_id, user_id, route, key, digest),
        )
        if cur.fetchone() is not None:
            conn.commit()
            return {"action": "execute"}
        # CR-P1-3：TTL 兜底——崩溃/闪断残留的 pending 占位（response_json 仍 NULL）
        # 超过阈值视为废弃可接管；R51：接管 UPDATE 带 digest 匹配（AND request_digest=%s）
        # ——同 digest 才接管执行，digest 不符（同 key 异 payload 的残留）不得覆盖执行，
        # 落入下方 SELECT 判定为 conflict（409 PAYLOAD_CONFLICT）
        cur.execute(
            f"""
            UPDATE weixin_marketing_idempotency_keys
            SET created_at = NOW(), updated_at = NOW()
            WHERE tenant_id = %s AND user_id = %s AND route = %s AND idempotency_key = %s
              AND request_digest = %s
              AND status = 'pending' AND response_json IS NULL
              AND created_at < NOW() - INTERVAL '{_IDEMPOTENCY_PENDING_TTL_SECONDS} seconds'
            """,
            (tenant_id, user_id, route, key, digest),
        )
        if cur.rowcount == 1:
            conn.commit()
            return {"action": "execute"}
        cur.execute(
            """
            SELECT request_digest, response_json, status_code, status
            FROM weixin_marketing_idempotency_keys
            WHERE tenant_id = %s AND user_id = %s AND route = %s AND idempotency_key = %s
            """,
            (tenant_id, user_id, route, key),
        )
        row = cur.fetchone()
        conn.commit()
    if row is None:
        # 并发删除窗口（不可达路径）：按首次执行
        return {"action": "execute"}
    if str(row["request_digest"]) != digest:
        return {"action": "conflict"}
    if row["status"] == "pending" or row["response_json"] is None:
        return {"action": "in_progress"}
    return {
        "action": "replay",
        "status_code": int(row["status_code"] or 200),
        "body": row["response_json"],
    }


class _IdempotencyFinalizer:
    """幂等完成写入器（R51 同事务方案选型：写入器注入）。

    服务层在业务事务内、conn.commit() 前调用 write_on(cursor, ...)——幂等行的
    response/status 完成写入与业务写入经同一次 commit 持久化：
    - 注入失败（崩溃/异常）→ 连接上下文统一 rollback → 业务与幂等记录一并回滚，
      「业务已提交而响应未保存」状态在该设计下不可达；
    - 恢复路径：同 key 重试命中 completed 行 → 直接重放原响应（原资源不再重复创建）。
    """

    __slots__ = ("tenant_id", "user_id", "route", "key", "digest")

    def __init__(self, tenant_id: str, user_id: str, route: str, key: str, digest: str):
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.route = route
        self.key = key
        self.digest = digest

    def write_on(self, cursor, *, status_code: int, data: Any) -> None:
        from psycopg2.extras import Json

        cursor.execute(
            """
            UPDATE weixin_marketing_idempotency_keys
            SET response_json = %s, status_code = %s, status = 'completed', updated_at = NOW()
            WHERE tenant_id = %s AND user_id = %s AND route = %s AND idempotency_key = %s
              AND request_digest = %s AND status = 'pending'
            """,
            (
                Json({"success": True, "data": _jsonable(data)}),
                status_code,
                self.tenant_id, self.user_id, self.route, self.key, self.digest,
            ),
        )
        if cursor.rowcount != 1:
            # 占位被并发接管/清理（新鲜占位理论不可达）：中止业务事务（调用方抛出回滚）
            raise RuntimeError(
                f"幂等占位失配（route={self.route} key 已非 pending 或 digest 不符），中止业务事务"
            )


def _idempotency_abandon(
    tenant_id: str, user_id: str, route: str, key: str, digest: str
) -> None:
    """执行失败释放占位（同 key 同 payload 可重试；desktop_agent abandon 语义）"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            DELETE FROM weixin_marketing_idempotency_keys
            WHERE tenant_id = %s AND user_id = %s AND route = %s AND idempotency_key = %s
              AND request_digest = %s AND status = 'pending' AND response_json IS NULL
            """,
            (tenant_id, user_id, route, key, digest),
        )
        conn.commit()


class _HandlerFailure(Exception):
    """handler 内已映射好的失败响应（幂等占位需 abandon）"""

    def __init__(self, status_code: int, body: Dict[str, Any]):
        super().__init__(body.get("error") or status_code)
        self.status_code = status_code
        self.body = body


# ==================== 通用工具 ====================


def _jsonable(value: Any) -> Any:
    """datetime/UUID → JSON 可序列化形态（递归）"""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, _uuid.UUID):
        return str(value)
    return value


def _ok(data: Any, status_code: int = 200) -> Tuple[int, Dict[str, Any]]:
    return status_code, {"success": True, "data": _jsonable(data)}


def _error_body(
    code: str, message: str, *, field_errors: Optional[List[Dict[str, str]]] = None,
    debug: Optional[str] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "success": False,
        "error": message,
        "code": code,
        "field_errors": field_errors or [],
    }
    if debug is not None:
        body["debug"] = debug
    return body


def _error_json(status_code: int, code: str, message: str, *,
                field_errors: Optional[List[Dict[str, str]]] = None,
                debug: Optional[str] = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=_error_body(code, message, field_errors=field_errors, debug=debug),
    )


def _failure_from_service(exc: Exception) -> _HandlerFailure:
    """服务层异常 → 稳定码/状态码（服务层消息为中文说明，可直出）"""
    message = str(exc)
    if isinstance(exc, NotFoundError):
        # 跨租户/不存在/非属主统一 404（猜别人的 ID 不区分存在性）
        return _HandlerFailure(
            404, _error_body(CODE_NOT_FOUND, "资源不存在或无权访问", debug=message)
        )
    if isinstance(exc, WeixinValidationError):
        return _HandlerFailure(
            422,
            _error_body(
                CODE_VALIDATION_FAILED, message,
                field_errors=[{"field": "", "message": message}], debug=message,
            ),
        )
    if isinstance(exc, TenantNotAllowedError):
        # CR-P1-2：allowlist 非空且租户不在列（409，与 dispatch 域门控语义对齐）
        return _HandlerFailure(
            409, _error_body(CODE_TENANT_NOT_ALLOWED, message, debug=message)
        )
    if isinstance(exc, RetryEvidenceRequiredError):
        # R52：未知效果重试缺人工证据（409 RETRY_EVIDENCE_REQUIRED）
        return _HandlerFailure(
            409, _error_body(CODE_RETRY_EVIDENCE_REQUIRED, message, debug=message)
        )
    if isinstance(exc, ConflictError):
        return _HandlerFailure(
            409, _error_body(CODE_CONFLICT, message, debug=message)
        )
    if isinstance(exc, ConfigurationError):
        return _HandlerFailure(
            503, _error_body(CODE_ADAPTER_UNREGISTERED, message, debug=message)
        )
    return _HandlerFailure(
        500,
        _error_body(
            CODE_INTERNAL_ERROR, "操作失败，请稍后重试",
            debug=sanitize_error_info(message),
        ),
    )


async def _current_user_and_tenant(request: Request):
    """统一认证 + 租户上下文（照 desktop_automation/api.py 模式）"""
    user = await asyncio.to_thread(get_current_user, request)
    if not user:
        raise HTTPException(status_code=401, detail={"error": "未登录或登录已过期"})
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=400, detail={"error": "缺少租户上下文（tenant_id）"})
    user_id = user.get("user_id") or user.get("id")
    if not user_id:
        raise HTTPException(status_code=403, detail={"error": "无法确定用户身份"})
    return str(tenant_id), str(user_id)


def _valid_uuid(value: str) -> bool:
    try:
        _uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


async def _parse_body(request: Request) -> Dict[str, Any]:
    raw = await request.body()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _HandlerFailure(
            422, _error_body(
                CODE_VALIDATION_FAILED, "请求体不是合法 JSON",
                field_errors=[{"field": "", "message": "请求体不是合法 JSON"}],
                debug=sanitize_error_info(str(exc)),
            )
        ) from exc
    if not isinstance(data, dict):
        raise _HandlerFailure(
            422, _error_body(
                CODE_VALIDATION_FAILED, "请求体必须是 JSON 对象",
                field_errors=[{"field": "", "message": "请求体必须是 JSON 对象"}],
            )
        )
    return data


def _validate(model_cls: Type[_M], data: Dict[str, Any]) -> _M:
    """手动校验（保证 422 也是项目 envelope + field_errors，而非 FastAPI 默认体）"""
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        field_errors = [
            {
                "field": ".".join(str(part) for part in err.get("loc", ())),
                "message": str(err.get("msg", "")),
            }
            for err in exc.errors()
        ]
        raise _HandlerFailure(
            422,
            _error_body(
                CODE_VALIDATION_FAILED, "请求参数校验失败",
                field_errors=field_errors, debug=sanitize_error_info(str(exc)),
            ),
        ) from exc


def _check_idempotency_key(key: Optional[str]) -> Optional[JSONResponse]:
    if key is None:
        return None
    if not (_IDEMPOTENCY_KEY_MIN <= len(key) <= _IDEMPOTENCY_KEY_MAX):
        return _error_json(
            422, CODE_IDEMPOTENCY_KEY_INVALID,
            f"Idempotency-Key 长度须在 {_IDEMPOTENCY_KEY_MIN}-{_IDEMPOTENCY_KEY_MAX} 之间",
        )
    return None


def _parse_if_match(value: Optional[str]) -> Optional[int]:
    """If-Match: '3' / W/"3" / 3 → int；不可解析返回 None（由调用方 422）"""
    if value is None:
        return None
    stripped = value.strip().removeprefix("W/").strip().strip('"').strip()
    try:
        return int(stripped)
    except ValueError:
        return None


def _enrich_latest_attempts(tenant_id: str, detail: Dict[str, Any]) -> Dict[str, Any]:
    """run 详情补逐条最新 attempt 摘要（证据引用/evidence_ref、safe_to_retry）——
    只读组合底座公开函数，不复制服务层 SQL。

    safe_to_retry 语义（P2-4）：**仅 True 表示底座判定可安全重试**；
    None（机器未判定）与 False 均表示不可重试——调用方不得把 None 当作可重试。"""
    for delivery in detail.get("deliveries", []):
        delivery_id = str(delivery.get("id"))
        attempts = da_attempts.list_delivery_attempts(delivery_id, tenant_id)
        latest = attempts[-1] if attempts else None
        delivery["latest_attempt"] = (
            {
                "attempt_id": str(latest["id"]),
                "attempt_no": latest.get("attempt_no"),
                "effect": latest.get("effect"),
                "phase": latest.get("phase"),
                "safe_to_retry": latest.get("safe_to_retry"),
                "evidence_ref": latest.get("evidence_ref"),
            }
            if latest
            else None
        )
    return detail


_service = WeixinMarketingService()


# ==================== automations CRUD ====================


@router.get("/automations")
async def list_automations(
    request: Request,
    keyword: Optional[str] = None,
    status: Optional[str] = None,
    trigger_type: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    tenant_id, user_id = await _current_user_and_tenant(request)
    field_errors: List[Dict[str, str]] = []
    if status is not None and status not in AUTOMATION_STATUSES:
        field_errors.append({"field": "status", "message": f"非法状态: {status}"})
    if trigger_type is not None and trigger_type not in TRIGGER_TYPES:
        field_errors.append({"field": "trigger_type", "message": f"非法触发类型: {trigger_type}"})
    if page < 1:
        field_errors.append({"field": "page", "message": "page 须 >= 1"})
    if not (1 <= page_size <= 100):
        field_errors.append({"field": "page_size", "message": "page_size 须在 1-100 之间"})
    if field_errors:
        return _error_json(
            422, CODE_VALIDATION_FAILED, "查询参数校验失败", field_errors=field_errors
        )
    try:
        result = await asyncio.to_thread(
            _service.list_automations,
            tenant_id, user_id,
            keyword=keyword, status=status, trigger_type=trigger_type,
            page=page, page_size=page_size,
        )
        return JSONResponse(content={"success": True, "data": _jsonable(result)})
    except Exception as e:
        return _internal_error("查询自动化列表失败", e)


@router.post("/automations")
async def create_automation(
    request: Request,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    from src.weixin_marketing.models import AutomationCreateInput

    tenant_id, user_id = await _current_user_and_tenant(request)
    key_error = _check_idempotency_key(idempotency_key)
    if key_error:
        return key_error

    async def handler(
        payload: AutomationCreateInput, finalizer: Optional[_IdempotencyFinalizer]
    ) -> Tuple[int, Dict[str, Any]]:
        result = await _call_service(
            _service.create_automation, tenant_id, user_id, payload,
            idempotency=finalizer,
        )
        return _ok(result)

    return await _execute_idempotent_payload(
        idempotency_key, tenant_id, user_id, _ROUTE_CREATE, request,
        AutomationCreateInput, handler, action_label="创建自动化",
    )


async def _execute_idempotent_payload(
    idempotency_key: Optional[str],
    tenant_id: str,
    user_id: str,
    route: str,
    request: Request,
    model_cls: Type[_M],
    handler: Callable[[_M, Optional[_IdempotencyFinalizer]], Awaitable[Tuple[int, Dict[str, Any]]]],
    action_label: str,
) -> JSONResponse:
    """幂等编排（payload 惰性版，R51 同事务）：先读原始 body 做 digest，再在 handler 内校验。

    digest = 规范化 JSON({"path": request.url.path, "body": 原始 body})（CR-P1-1：
    路径并入 digest——同 key 同 body 打不同资源必 409，绝不跨资源重放）；
    用原始 body 而非模型 dump——校验失败时模型不存在，且原始 body 已足够区分
    「同 key 异 payload」（等价 JSON 键序差异经 sort_keys 归一）。

    R51：execute 分支构造 _IdempotencyFinalizer 传给 handler→服务层——业务写入与
    幂等完成记录同一事务提交（成功即已持久化，无事后补写）；失败/异常路径占位
    释放（abandon），残留兜底由 TTL 接管（digest 匹配）。
    """
    if not idempotency_key:
        try:
            body = await _parse_body(request)
            payload = _validate(model_cls, body)
            status_code, response = await handler(payload, None)
        except _HandlerFailure as failure:
            return JSONResponse(status_code=failure.status_code, content=failure.body)
        except Exception as e:
            return _internal_error(f"{action_label}失败", e)
        return JSONResponse(status_code=status_code, content=response)

    raw = await request.body()
    digest_source: Dict[str, Any] = {}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                digest_source = parsed
        except json.JSONDecodeError:
            digest_source = {}
    digest = _digest_payload({"path": request.url.path, "body": digest_source})
    try:
        reserve = await asyncio.to_thread(
            _idempotency_reserve, tenant_id, user_id, route, idempotency_key, digest
        )
    except Exception as e:
        # P2-6：占位读/接管 DB 故障走统一 envelope（不裸抛 FastAPI 默认 500 体）
        return _internal_error(f"{action_label}失败", e)
    action = reserve.get("action")
    if action == "replay":
        return JSONResponse(status_code=reserve["status_code"], content=reserve["body"])
    if action == "conflict":
        return _error_json(
            409, CODE_IDEMPOTENCY_PAYLOAD_CONFLICT,
            "同 Idempotency-Key 已用于不同请求体或路径，拒绝执行",
        )
    if action == "in_progress":
        return _error_json(
            409, CODE_IDEMPOTENCY_IN_PROGRESS,
            "同 Idempotency-Key 的请求正在处理中，请稍后重试",
        )
    finalizer = _IdempotencyFinalizer(tenant_id, user_id, route, idempotency_key, digest)
    try:
        body = await _parse_body(request)
        payload = _validate(model_cls, body)
        status_code, response = await handler(payload, finalizer)
    except _HandlerFailure as failure:
        # 业务 4xx/5xx 结论是权威事实：占位清理失败不影响结论表达
        # （残留 pending 行由 TTL 接管兜底，CR-P1-3）
        await _abandon_safely(tenant_id, user_id, route, idempotency_key, digest)
        return JSONResponse(status_code=failure.status_code, content=failure.body)
    except Exception as e:
        # 未知异常：业务与幂等记录已经同事务回滚（R51），释放占位后同 key 同 payload 可重试
        await _abandon_safely(tenant_id, user_id, route, idempotency_key, digest)
        return _internal_error(f"{action_label}失败", e)
    # 成功路径：幂等完成记录已在服务层事务内与业务写入一并提交（R51），
    # 此处响应即已持久化的原结果——重试同 key 同 payload 将直接重放本响应
    return JSONResponse(status_code=status_code, content=response)


async def _abandon_safely(
    tenant_id: str, user_id: str, route: str, key: str, digest: str
) -> None:
    """CR-P1-3：占位释放的 DB 闪断兜底——失败仅告警不裸抛（残留行由 TTL 接管）"""
    try:
        await asyncio.to_thread(
            _idempotency_abandon, tenant_id, user_id, route, key, digest
        )
    except Exception as e:
        logger.opt(exception=True).warning(
            f"后端日志：weixin_marketing 幂等占位释放失败 route={route}: {e}"
        )


async def _guarded_service(action: str, exc: Exception) -> JSONResponse:
    """服务异常统一兜底：服务层错误类型→稳定码；未知异常→500+脱敏 debug+堆栈日志"""
    if isinstance(exc, _HandlerFailure):
        return JSONResponse(status_code=exc.status_code, content=exc.body)
    if isinstance(exc, (NotFoundError, ConflictError, WeixinValidationError, ConfigurationError)):
        failure = _failure_from_service(exc)
        return JSONResponse(status_code=failure.status_code, content=failure.body)
    return _internal_error(f"{action}失败", exc)


async def _call_service(func: Callable, /, *args, **kwargs):
    """幂等 handler 内的服务调用：服务层错误类型 → _HandlerFailure（触发 abandon）"""
    try:
        return await asyncio.to_thread(func, *args, **kwargs)
    except (NotFoundError, ConflictError, WeixinValidationError, ConfigurationError) as e:
        raise _failure_from_service(e) from e


def _internal_error(message: str, exc: Exception) -> JSONResponse:
    logger.opt(exception=True).error(f"后端日志：weixin_marketing API 失败: {exc}")
    return _error_json(
        500, CODE_INTERNAL_ERROR, f"{message}，请稍后重试",
        debug=sanitize_error_info(str(exc)),
    )


@router.get("/automations/{automation_id}")
async def get_automation(automation_id: str, request: Request):
    tenant_id, user_id = await _current_user_and_tenant(request)
    if not _valid_uuid(automation_id):
        return _error_json(404, CODE_NOT_FOUND, "自动化任务不存在或无权访问")
    try:
        result = await asyncio.to_thread(
            _service.get_automation_detail, tenant_id, automation_id, user_id
        )
        return JSONResponse(content={"success": True, "data": _jsonable(result)})
    except Exception as e:
        return await _guarded_service("查询", e)


@router.put("/automations/{automation_id}/draft")
async def update_draft(
    automation_id: str,
    request: Request,
    if_match: Optional[str] = Header(None, alias="If-Match"),
):
    """草稿编辑（If-Match/version CAS，冲突 409）。

    版本以 body.expected_version 为权威（服务层契约）；If-Match 头为等价别名，
    同时提供且不一致 → 422（防两条版本信道打架）。
    """
    tenant_id, user_id = await _current_user_and_tenant(request)
    if not _valid_uuid(automation_id):
        return _error_json(404, CODE_NOT_FOUND, "自动化任务不存在或无权访问")
    try:
        from src.weixin_marketing.models import DraftUpdateInput

        body = await _parse_body(request)
        payload = _validate(DraftUpdateInput, body)
        if if_match is not None:
            header_version = _parse_if_match(if_match)
            if header_version is None:
                return _error_json(
                    422, CODE_VALIDATION_FAILED, "If-Match 头不可解析",
                    field_errors=[{"field": "If-Match", "message": "If-Match 须为版本号（如 \"3\"）"}],
                )
            if header_version != payload.expected_version:
                return _error_json(
                    422, CODE_VALIDATION_FAILED,
                    f"If-Match({header_version}) 与 expected_version({payload.expected_version}) 不一致",
                    field_errors=[{"field": "If-Match", "message": "与 body.expected_version 不一致"}],
                )
        result = await asyncio.to_thread(
            _service.update_draft, tenant_id, automation_id, user_id, payload
        )
        return JSONResponse(content={"success": True, "data": _jsonable(result)})
    except _HandlerFailure as failure:
        return JSONResponse(status_code=failure.status_code, content=failure.body)
    except (NotFoundError, ConflictError, WeixinValidationError, ConfigurationError) as e:
        return await _guarded_service("更新草稿", e)
    except Exception as e:
        return _internal_error("更新草稿失败", e)


# ==================== validate / publish / 状态迁移 ====================


@router.post("/automations/{automation_id}/validate")
async def validate_automation(automation_id: str, request: Request):
    """静态校验 + 未来 5 次触发预览（无任何发送副作用）"""
    tenant_id, user_id = await _current_user_and_tenant(request)
    if not _valid_uuid(automation_id):
        return _error_json(404, CODE_NOT_FOUND, "自动化任务不存在或无权访问")
    try:
        result = await asyncio.to_thread(
            _service.validate_automation, tenant_id, automation_id, user_id
        )
        return JSONResponse(content={"success": True, "data": _jsonable(result)})
    except (NotFoundError, ConflictError, WeixinValidationError, ConfigurationError) as e:
        return await _guarded_service("校验", e)
    except Exception as e:
        return _internal_error("校验失败", e)


@router.post("/automations/{automation_id}/publish")
async def publish_automation(
    automation_id: str,
    request: Request,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    from src.weixin_marketing.models import PublishInput

    tenant_id, user_id = await _current_user_and_tenant(request)
    if not _valid_uuid(automation_id):
        return _error_json(404, CODE_NOT_FOUND, "自动化任务不存在或无权访问")
    key_error = _check_idempotency_key(idempotency_key)
    if key_error:
        return key_error

    async def handler(
        payload: PublishInput, finalizer: Optional[_IdempotencyFinalizer]
    ) -> Tuple[int, Dict[str, Any]]:
        result = await _call_service(
            _service.publish, tenant_id, automation_id, user_id, payload,
            idempotency=finalizer,
        )
        return _ok(result)

    return await _execute_idempotent_payload(
        idempotency_key, tenant_id, user_id, _ROUTE_PUBLISH, request,
        PublishInput, handler, action_label="发布",
    )


async def _versioned_transition(
    automation_id: str,
    request: Request,
    service_method: str,
    action_label: str,
):
    tenant_id, user_id = await _current_user_and_tenant(request)
    if not _valid_uuid(automation_id):
        return _error_json(404, CODE_NOT_FOUND, "自动化任务不存在或无权访问")
    try:
        from src.weixin_marketing.models import VersionedActionInput

        body = await _parse_body(request)
        payload = _validate(VersionedActionInput, body)
        method = getattr(_service, service_method)
        result = await asyncio.to_thread(
            method, tenant_id, automation_id, user_id, payload
        )
        return JSONResponse(content={"success": True, "data": _jsonable(result)})
    except _HandlerFailure as failure:
        return JSONResponse(status_code=failure.status_code, content=failure.body)
    except (NotFoundError, ConflictError, WeixinValidationError, ConfigurationError) as e:
        return await _guarded_service(action_label, e)
    except Exception as e:
        return _internal_error(f"{action_label}失败", e)


@router.post("/automations/{automation_id}/pause")
async def pause_automation(automation_id: str, request: Request):
    return await _versioned_transition(automation_id, request, "pause", "暂停")


@router.post("/automations/{automation_id}/resume")
async def resume_automation(automation_id: str, request: Request):
    return await _versioned_transition(automation_id, request, "resume", "恢复")


@router.post("/automations/{automation_id}/archive")
async def archive_automation(automation_id: str, request: Request):
    return await _versioned_transition(automation_id, request, "archive", "归档")


# ==================== 手动 run ====================


class _RunRequest(BaseModel):
    """手动 run 请求体：request_id 可选（服务层 manual 键幂等去重）"""

    model_config = {"extra": "forbid"}
    request_id: Optional[str] = None


@router.post("/automations/{automation_id}/run", status_code=202)
async def run_automation(
    automation_id: str,
    request: Request,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """手动触发（202 语义）：occurrence 与 run 在接纳事务内一并落库，
    202 响应的 run_id 恒非空（deliveries 由执行驱动 tick 编译）。"""
    tenant_id, user_id = await _current_user_and_tenant(request)
    if not _valid_uuid(automation_id):
        return _error_json(404, CODE_NOT_FOUND, "自动化任务不存在或无权访问")
    key_error = _check_idempotency_key(idempotency_key)
    if key_error:
        return key_error

    async def handler(
        payload: _RunRequest, finalizer: Optional[_IdempotencyFinalizer]
    ) -> Tuple[int, Dict[str, Any]]:
        # R51：提供 Idempotency-Key 时 request_id 恒取该 key（跨重试稳定——
        # manual 触发键与幂等键同源去重，重试不产生第二个 occurrence）
        if idempotency_key:
            request_id = idempotency_key
        else:
            request_id = (payload.request_id or "").strip() or f"api-{_uuid.uuid4().hex}"
        result = await _call_service(
            _service.manual_run, tenant_id, automation_id, user_id,
            request_id=request_id,
            idempotency=finalizer,
        )
        return _ok(result, status_code=202)

    return await _execute_idempotent_payload(
        idempotency_key, tenant_id, user_id, _ROUTE_RUN, request,
        _RunRequest, handler, action_label="触发手动运行",
    )


# ==================== runs 查询 / 取消 ====================


@router.get("/runs")
async def list_runs(
    request: Request,
    automation_id: Optional[str] = None,
    state: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    tenant_id, user_id = await _current_user_and_tenant(request)
    field_errors: List[Dict[str, str]] = []
    if automation_id is not None and not _valid_uuid(automation_id):
        field_errors.append({"field": "automation_id", "message": "非法 UUID"})
    if state is not None and state not in RUN_STATES:
        field_errors.append({"field": "state", "message": f"非法运行状态: {state}"})
    if page < 1:
        field_errors.append({"field": "page", "message": "page 须 >= 1"})
    if not (1 <= page_size <= 100):
        field_errors.append({"field": "page_size", "message": "page_size 须在 1-100 之间"})
    if field_errors:
        return _error_json(422, CODE_VALIDATION_FAILED, "查询参数校验失败", field_errors=field_errors)
    try:
        result = await asyncio.to_thread(
            _service.list_runs, tenant_id, user_id,
            automation_id=automation_id, state=state, page=page, page_size=page_size,
        )
        return JSONResponse(content={"success": True, "data": _jsonable(result)})
    except Exception as e:
        return _internal_error("查询运行记录失败", e)


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request):
    """run 详情：逐条 delivery 脱敏摘要（仅引用/hash/状态，无正文）+ 证据引用
    （latest_attempt.safe_to_retry 仅 True 表示可重试，None/False 均不可）"""
    tenant_id, user_id = await _current_user_and_tenant(request)
    if not _valid_uuid(run_id):
        return _error_json(404, CODE_NOT_FOUND, "运行记录不存在或无权访问")
    try:
        detail = await asyncio.to_thread(
            _service.get_run_detail, tenant_id, run_id, user_id
        )
        detail = await asyncio.to_thread(_enrich_latest_attempts, tenant_id, detail)
        return JSONResponse(content={"success": True, "data": _jsonable(detail)})
    except (NotFoundError, ConflictError, WeixinValidationError, ConfigurationError) as e:
        return await _guarded_service("查询运行详情", e)
    except Exception as e:
        return _internal_error("查询运行详情失败", e)


@router.post("/runs/{run_id}/cancel", status_code=202)
async def cancel_run(run_id: str, request: Request):
    """请求停止尚未提交条目（202 语义）：已提交条目照实回收（§5.4 partial）"""
    tenant_id, user_id = await _current_user_and_tenant(request)
    if not _valid_uuid(run_id):
        return _error_json(404, CODE_NOT_FOUND, "运行记录不存在或无权访问")
    try:
        result = await asyncio.to_thread(
            _service.cancel_run, tenant_id, run_id, user_id
        )
        return JSONResponse(
            status_code=202,
            content={"success": True, "data": _jsonable(result)},
        )
    except (NotFoundError, ConflictError, WeixinValidationError, ConfigurationError) as e:
        return await _guarded_service("取消运行", e)
    except Exception as e:
        return _internal_error("取消运行失败", e)


# ==================== deliveries resolve / retry ====================


@router.post("/deliveries/{delivery_id}/resolve")
async def resolve_delivery(delivery_id: str, request: Request):
    """人工结论 + 证据说明（独立审计留痕；不覆盖机器判定）"""
    tenant_id, user_id = await _current_user_and_tenant(request)
    if not _valid_uuid(delivery_id):
        return _error_json(404, CODE_NOT_FOUND, "投递记录不存在或无权访问")
    try:
        from src.weixin_marketing.models import DeliveryResolveInput

        body = await _parse_body(request)
        payload = _validate(DeliveryResolveInput, body)
        result = await asyncio.to_thread(
            _service.resolve_delivery, tenant_id, delivery_id, user_id, payload
        )
        return JSONResponse(content={"success": True, "data": _jsonable(result)})
    except _HandlerFailure as failure:
        return JSONResponse(status_code=failure.status_code, content=failure.body)
    except (NotFoundError, ConflictError, WeixinValidationError, ConfigurationError) as e:
        return await _guarded_service("人工结论", e)
    except Exception as e:
        return _internal_error("人工结论失败", e)


@router.post("/deliveries/{delivery_id}/retry")
async def retry_delivery(delivery_id: str, request: Request):
    """人工重试（R45）：需显式 confirm=true；经 predecessor_attempt_id 建新 attempt"""
    tenant_id, user_id = await _current_user_and_tenant(request)
    if not _valid_uuid(delivery_id):
        return _error_json(404, CODE_NOT_FOUND, "投递记录不存在或无权访问")
    try:
        from src.weixin_marketing.models import DeliveryRetryInput

        body = await _parse_body(request)
        payload = _validate(DeliveryRetryInput, body)
        result = await asyncio.to_thread(
            _service.retry_delivery, tenant_id, delivery_id, user_id,
            confirm=bool(payload.confirm),
        )
        return JSONResponse(content={"success": True, "data": _jsonable(result)})
    except _HandlerFailure as failure:
        return JSONResponse(status_code=failure.status_code, content=failure.body)
    except (NotFoundError, ConflictError, WeixinValidationError, ConfigurationError) as e:
        return await _guarded_service("人工重试", e)
    except Exception as e:
        return _internal_error("人工重试失败", e)
