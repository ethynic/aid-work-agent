"""
SaaS 订阅计费 API

路由：/api/saas/billing/*
- 订单列表
- 支付回调
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from src.saas.api.tenant_auth import require_admin
from src.saas.services.payment import PaymentService
from src.config.settings import settings

router = APIRouter(prefix="/api/saas/billing", tags=["SaaS 订阅计费"])


# ============== 请求模型 ==============

class PayCallbackRequest(BaseModel):
    order_id: str = Field(..., description="订单 ID")
    transaction_id: str = Field(..., description="第三方交易号")


# ============== API 端点 ==============

@router.get("/orders")
async def list_orders(request: Request):
    """获取支付订单列表"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    orders = PaymentService.list_orders(admin["tenant_id"])
    return {"success": True, "orders": orders}


@router.post("/payment_callback")
async def payment_callback(body: PayCallbackRequest):
    """
    支付回调

    微信/支付宝异步通知调用此接口。
    当前为简化版本，直接传入 order_id + transaction_id。
    """
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    success = PaymentService.handle_callback(body.order_id, body.transaction_id)
    if success:
        return {"success": True, "message": "支付处理成功"}
    return {"success": False, "message": "支付处理失败（订单不存在或已处理）"}
