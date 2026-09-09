"""desktop_automation Web 用户 API（R8）

GET /api/desktop-automation/deliveries/{id}：授权查看中立 operation 状态与证据引用。
- 统一 auth（get_current_user）+ 租户中间件（request.state.tenant_id）；
- 场景适配器控制可见范围（visible_to_user 可选方法；未实现时默认任务属主本人）；
- 跨租户/无权一律 404（不区分存在性，防探测）。
"""

import asyncio
import uuid as _uuid
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from src.api.auth import get_current_user
from src.desktop_automation import attempts as attempts_module
from src.desktop_automation import deliveries as deliveries_module
from src.desktop_automation.adapters import TrustedAdapterRegistry
from src.utils import sanitize_error_info

router = APIRouter(prefix="/api/desktop-automation", tags=["desktop-automation"])


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


async def _current_user_and_tenant(request: Request):
    user = await asyncio.to_thread(get_current_user, request)
    if not user:
        raise HTTPException(status_code=401, detail={"error": "未登录或登录已过期"})
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=400, detail={"error": "缺少租户上下文（tenant_id）"})
    return user, tenant_id


def _visible_to_user(delivery: Dict[str, Any], user: Dict[str, Any]) -> bool:
    """场景适配器控制可见范围：adapter.visible_to_user(delivery, user_id, role) 可选方法；
    未实现时默认仅任务属主本人可见（保守 fail-closed）。
    纯内存判定约定（P2-6）：适配器实现不得在此做 DB/IO——视图已带租户过滤与行数据，
    场景侧只基于内存中的 delivery/user 字段裁决。"""
    adapter = TrustedAdapterRegistry.get(delivery.get("scenario_key") or "")
    if adapter is not None and hasattr(adapter, "visible_to_user"):
        return bool(
            adapter.visible_to_user(  # type: ignore[attr-defined]
                delivery=delivery, user_id=user.get("user_id"), role=user.get("role")
            )
        )
    return delivery.get("user_id") == user.get("user_id")


def _valid_uuid(value: str) -> bool:
    """路径参数 UUID 形态预检：非法形态直接按 404 处理（P2-6）"""
    try:
        _uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


@router.get("/deliveries/{delivery_id}")
async def get_delivery_view(delivery_id: str, request: Request):
    """中立 delivery 状态视图（不返回 payload 内容，只有引用/摘要与证据引用）"""
    user, tenant_id = await _current_user_and_tenant(request)
    if not _valid_uuid(delivery_id):
        raise HTTPException(status_code=404, detail={"error": "delivery 不存在或无权查看"})
    try:
        delivery = await asyncio.to_thread(
            deliveries_module.get_delivery, delivery_id, tenant_id
        )
        if delivery is None or not _visible_to_user(delivery, user):
            # 跨租户/不存在/无权统一 404，不泄露存在性
            raise HTTPException(status_code=404, detail={"error": "delivery 不存在或无权查看"})

        attempts = await asyncio.to_thread(
            attempts_module.list_delivery_attempts, delivery_id, tenant_id
        )
        latest_attempt = attempts[-1] if attempts else None
        return {
            "success": True,
            "delivery": {
                "delivery_id": str(delivery["id"]),
                "run_id": str(delivery["run_id"]),
                "scenario_key": delivery.get("scenario_key"),
                "position": delivery.get("position"),
                "operation": delivery.get("operation"),
                "provider_key": delivery.get("provider_key"),
                "target_ref": delivery.get("target_ref"),
                "target_version": delivery.get("target_version"),
                "payload_ref": delivery.get("payload_ref"),
                "payload_hash": delivery.get("payload_hash"),
                "state": delivery.get("state"),
                "effect": delivery.get("effect"),
                "phase": delivery.get("phase"),
                "created_at": _iso(delivery.get("created_at")),
                "finished_at": _iso(delivery.get("finished_at")),
            },
            "latest_attempt": (
                {
                    "attempt_id": str(latest_attempt["id"]),
                    "attempt_no": latest_attempt.get("attempt_no"),
                    "effect": latest_attempt.get("effect"),
                    "phase": latest_attempt.get("phase"),
                    "safe_to_retry": latest_attempt.get("safe_to_retry"),
                    "evidence_ref": latest_attempt.get("evidence_ref"),
                    "created_at": _iso(latest_attempt.get("created_at")),
                    "finished_at": _iso(latest_attempt.get("finished_at")),
                }
                if latest_attempt
                else None
            ),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(
            f"后端日志：查询 desktop_automation delivery 失败 id={delivery_id}: {e}"
        )
        raise HTTPException(
            status_code=500,
            detail={"error": "查询失败，请稍后重试", "debug": sanitize_error_info(str(e))},
        )
