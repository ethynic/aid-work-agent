"""
支付集成服务

微信支付/支付宝对接骨架、订单管理。
当前为 Mock 实现，预留真实支付接口。
"""

import uuid
from datetime import datetime
from typing import Optional, Dict, Any

from loguru import logger

from src.db.database import get_db_connection, get_db_placeholder
from src.saas.db.subscription_db import SubscriptionDB


class PaymentService:
    """支付服务"""

    @staticmethod
    def create_order(
        tenant_id: str,
        subscription_id: Optional[str] = None,
        amount: float = 0,
        payment_method: str = "wechat",
    ) -> Optional[Dict[str, Any]]:
        """
        创建支付订单

        Args:
            tenant_id: 租户 ID
            subscription_id: 关联的订阅 ID
            amount: 金额（元）
            payment_method: 支付方式（wechat/alipay）

        Returns:
            订单信息 dict
        """
        order_id = f"pay_{uuid.uuid4().hex[:12]}"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO payment_orders
                        (order_id, tenant_id, subscription_id, amount, payment_method, payment_status)
                    VALUES (?, ?, ?, ?, ?, 'pending')
                """, (order_id, tenant_id, subscription_id, amount, payment_method))
                conn.commit()

                cursor.execute("SELECT * FROM payment_orders WHERE order_id = ?", (order_id,))
                row = cursor.fetchone()
                logger.info(f"Payment order created: {order_id}, amount={amount}, method={payment_method}")
                return dict(row) if row else None
            except Exception as e:
                logger.error(f"Failed to create payment order: {e}")
                return None

    @staticmethod
    def get_order(order_id: str) -> Optional[Dict[str, Any]]:
        """获取订单信息"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM payment_orders WHERE order_id = ?", (order_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def list_orders(tenant_id: str, limit: int = 20) -> list:
        """列出租户的支付订单"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            placeholder = get_db_placeholder()
            # PostgreSQL 不支持 LIMIT ?，需要直接拼接
            cursor.execute(
                f"SELECT * FROM payment_orders WHERE tenant_id = {placeholder} ORDER BY created_at DESC LIMIT {limit}",
                (tenant_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def create_payment_url(order_id: str) -> Optional[str]:
        """
        生成支付链接

        当前为 Mock 实现，返回模拟支付页面 URL。
        真实实现需要对接微信支付/支付宝 SDK。
        """
        order = PaymentService.get_order(order_id)
        if not order:
            return None

        # Mock: 返回模拟支付 URL
        payment_method = order["payment_method"]
        if payment_method == "wechat":
            # TODO: 调用微信支付统一下单 API
            return f"https://pay.example.com/wechat?order={order_id}&amount={order['amount']}"
        elif payment_method == "alipay":
            # TODO: 调用支付宝统一下单 API
            return f"https://pay.example.com/alipay?order={order_id}&amount={order['amount']}"

        return None

    @staticmethod
    def handle_callback(order_id: str, transaction_id: str) -> bool:
        """
        支付回调处理

        支付成功后调用：更新订单状态、激活订阅。

        Args:
            order_id: 订单 ID
            transaction_id: 第三方交易号

        Returns:
            是否处理成功
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 1. 更新订单状态
            cursor.execute("""
                UPDATE payment_orders
                SET payment_status = 'paid', transaction_id = ?, paid_at = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE order_id = ? AND payment_status = 'pending'
            """, (transaction_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), order_id))

            if cursor.rowcount == 0:
                logger.warning(f"Payment callback: order {order_id} not found or already paid")
                return False

            # 2. 查询关联订阅（在同一连接中）
            cursor.execute("SELECT subscription_id FROM payment_orders WHERE order_id = ?", (order_id,))
            row = cursor.fetchone()
            subscription_id = row["subscription_id"] if row else None

            # 3. 激活关联的订阅（在同一连接中）
            if subscription_id:
                cursor.execute("""
                    UPDATE subscriptions
                    SET status = 'active', payment_status = 'paid', updated_at = CURRENT_TIMESTAMP
                    WHERE subscription_id = ?
                """, (subscription_id,))
                logger.info(f"Subscription activated: {subscription_id} via order {order_id}")

            conn.commit()
            return True
