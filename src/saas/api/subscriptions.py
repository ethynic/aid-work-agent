"""
SaaS 订阅计费 API

路由：/api/saas/billing/*
- 套餐列表
- 订阅管理（创建、列表、查看用量）
- 支付流程（创建订单、发起支付、回调）
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from loguru import logger

from src.saas.api.tenant_auth import require_admin
from src.saas.db.subscription_db import SubscriptionDB
from src.saas.models.subscription import AVAILABLE_PLANS
from src.saas.services.billing import (
    create_subscription_for_tenant,
    get_subscription_usage,
)
from src.saas.services.payment import PaymentService

router = APIRouter(prefix="/api/saas/billing", tags=["SaaS 订阅计费"])


# ============== 请求模型 ==============

class CreateSubscriptionRequest(BaseModel):
    plan: str = Field("basic", description="套餐名称：basic/standard/premium")
    billing_cycle: str = Field("monthly", description="计费周期：monthly/yearly")
    subagent_type: Optional[str] = Field(None, description="关联的子智能体类型")


class PayOrderRequest(BaseModel):
    payment_method: str = Field("wechat", description="支付方式：wechat/alipay")


class PayCallbackRequest(BaseModel):
    order_id: str = Field(..., description="订单 ID")
    transaction_id: str = Field(..., description="第三方交易号")


# ============== API 端点 ==============

@router.get("/plans")
async def list_plans():
    """获取可用套餐列表"""
    plans = []
    for name, plan in AVAILABLE_PLANS.items():
        plans.append({
            "name": plan.name,
            "display_name": plan.display_name,
            "price": plan.price,
            "token_quota": plan.token_quota,
            "max_instances": plan.max_instances,
            "max_users": plan.max_users,
        })
    return {"success": True, "plans": plans}


@router.get("/subscriptions")
async def list_subscriptions(request: Request):
    """获取当前租户的订阅列表"""
    admin = require_admin(request)
    subs = SubscriptionDB.list_by_tenant(admin["tenant_id"])
    return {"success": True, "subscriptions": subs}


@router.post("/subscriptions")
async def create_subscription(request: Request, body: CreateSubscriptionRequest):
    """购买新订阅（创建订阅 + 创建支付订单）"""
    admin = require_admin(request)

    # 1. 验证套餐
    plan = AVAILABLE_PLANS.get(body.plan)
    if not plan:
        raise HTTPException(status_code=400, detail=f"无效的套餐: {body.plan}")

    # 2. 创建订阅（状态为 pending，支付成功后激活）
    sub = create_subscription_for_tenant(
        tenant_id=admin["tenant_id"],
        plan_name=body.plan,
        billing_cycle=body.billing_cycle,
        subagent_type=body.subagent_type,
    )
    if not sub:
        raise HTTPException(status_code=500, detail="创建订阅失败")

    # 3. 设置为待支付
    SubscriptionDB.update_status(sub["subscription_id"], status="pending", payment_status="pending")

    # 4. 创建支付订单
    amount = sub["unit_price"]
    order = PaymentService.create_order(
        tenant_id=admin["tenant_id"],
        subscription_id=sub["subscription_id"],
        amount=amount,
        payment_method=body.billing_cycle,  # 临时用 billing_cycle 字段
    )

    logger.info(
        f"Subscription created: {sub['subscription_id']} plan={body.plan}, "
        f"order={order['order_id'] if order else 'N/A'}"
    )

    return {
        "success": True,
        "subscription": sub,
        "order": {
            "order_id": order["order_id"],
            "amount": order["amount"],
            "payment_status": order["payment_status"],
        } if order else None,
    }


@router.get("/orders")
async def list_orders(request: Request):
    """获取支付订单列表"""
    admin = require_admin(request)
    orders = PaymentService.list_orders(admin["tenant_id"])
    return {"success": True, "orders": orders}


@router.post("/pay/{order_id}")
async def initiate_payment(order_id: str, request: Request, body: PayOrderRequest):
    """发起支付（返回支付链接）"""
    admin = require_admin(request)

    order = PaymentService.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")

    if order["tenant_id"] != admin["tenant_id"]:
        raise HTTPException(status_code=403, detail="无权操作此订单")

    if order["payment_status"] != "pending":
        raise HTTPException(status_code=400, detail=f"订单状态为 {order['payment_status']}，不可支付")

    # 更新支付方式
    from src.db.database import get_db_connection
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE payment_orders SET payment_method = ?, updated_at = CURRENT_TIMESTAMP WHERE order_id = ?",
            (body.payment_method, order_id),
        )
        conn.commit()

    # 生成支付链接
    payment_url = PaymentService.create_payment_url(order_id)

    return {
        "success": True,
        "order_id": order_id,
        "amount": order["amount"],
        "payment_method": body.payment_method,
        "payment_url": payment_url,
    }


@router.post("/pay/callback")
async def payment_callback(body: PayCallbackRequest):
    """
    支付回调

    微信/支付宝异步通知调用此接口。
    当前为简化版本，直接传入 order_id + transaction_id。
    """
    success = PaymentService.handle_callback(body.order_id, body.transaction_id)
    if success:
        return {"success": True, "message": "支付处理成功"}
    return {"success": False, "message": "支付处理失败（订单不存在或已处理）"}


@router.get("/usage")
async def get_usage(request: Request):
    """获取当前租户的 token 用量"""
    admin = require_admin(request)

    subs = SubscriptionDB.list_by_tenant(admin["tenant_id"], status="active")
    if not subs:
        return {"success": True, "usage": []}

    usage_list = []
    for sub in subs:
        usage = get_subscription_usage(sub["subscription_id"])
        if usage:
            usage_list.append(usage)

    return {"success": True, "usage": usage_list}
