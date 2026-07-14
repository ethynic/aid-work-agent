"""
SaaS 领域枚举值定义

所有 SaaS 相关表字段的枚举值统一在此定义。
包括：租户状态、订阅状态、支付状态、智能体实例状态等。

使用说明：
- 后端 Pydantic 模型引用：from src.saas.models import TenantStatus
- 数据库默认值引用：TenantStatus.ACTIVE.value
"""

from enum import Enum


# ============== 租户状态 ==============

class TenantStatus(str, Enum):
    """
    租户状态枚举

    数据库存储：TEXT
    - active      = 正常
    - suspended  = 停用
    - deactivated = 已删除
    """
    ACTIVE = "active"       # 正常
    SUSPENDED = "suspended"    # 停用
    DEACTIVATED = "deactivated"  # 已删除

    @property
    def display_name(self) -> str:
        """用户友好的显示名称"""
        mapping = {
            self.ACTIVE: "正常",
            self.SUSPENDED: "停用",
            self.DEACTIVATED: "已删除",
        }
        return mapping.get(self, "未知")


# ============== 订阅状态 ==============

class SubscriptionStatus(str, Enum):
    """
    订阅状态枚举

    数据库存储：TEXT
    - active    = 活跃
    - expired   = 已过期
    - cancelled = 已取消
    """
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"

    @classmethod
    def all_values(cls) -> list[str]:
        return [cls.ACTIVE.value, cls.EXPIRED.value, cls.CANCELLED.value]

    @property
    def display_name(self) -> str:
        mapping = {
            self.ACTIVE: "活跃",
            self.EXPIRED: "已过期",
            self.CANCELLED: "已取消",
        }
        return mapping.get(self, "未知")


# ============== 支付状态 ==============

class PaymentStatus(str, Enum):
    """
    支付状态枚举

    数据库存储：TEXT
    - pending  = 待支付
    - paid     = 已支付
    - refunded = 已退款
    """
    PENDING = "pending"
    PAID = "paid"
    REFUNDED = "refunded"

    @classmethod
    def all_values(cls) -> list[str]:
        return [cls.PENDING.value, cls.PAID.value, cls.REFUNDED.value]

    @property
    def display_name(self) -> str:
        mapping = {
            self.PENDING: "待支付",
            self.PAID: "已支付",
            self.REFUNDED: "已退款",
        }
        return mapping.get(self, "未知")


# ============== 智能体实例状态 ==============

class AgentInstanceStatus(str, Enum):
    """
    智能体实例状态枚举

    数据库存储：TEXT
    - idle = 空闲可用（无用户使用）
    - busy = 忙碌中（有用户正在使用）

    注意：只保留并发控制状态，运行状态由instance_manager在内存中管理
    """
    IDLE = "idle"
    BUSY = "busy"

    @classmethod
    def all_values(cls) -> list[str]:
        return [cls.IDLE.value, cls.BUSY.value]

    @property
    def display_name(self) -> str:
        mapping = {
            self.IDLE: "空闲",
            self.BUSY: "忙碌",
        }
        return mapping.get(self, "未知")


# ============== 用户状态 ==============

class UserStatus(str, Enum):
    """
    用户状态枚举

    数据库存储：TEXT (users.status)
    - active      = 正常
    - suspended   = 停用
    - deactivated = 已注销
    """
    ACTIVE = "active"         # 正常
    SUSPENDED = "suspended"    # 停用
    DEACTIVATED = "deactivated"  # 已注销

    @property
    def display_name(self) -> str:
        """用户友好的显示名称"""
        mapping = {
            self.ACTIVE: "正常",
            self.SUSPENDED: "停用",
            self.DEACTIVATED: "已注销",
        }
        return mapping.get(self, "未知")


# ============== 用户来源 ==============

class UserSource(str, Enum):
    """
    用户来源枚举

    数据库存储：TEXT
    - NULL / 空字符串 = 内部用户（管理员创建）
    - wecom_kf = 企业微信客服
    """
    WECOM_KF = "wecom_kf"

    @classmethod
    def all_values(cls) -> list[str]:
        return [cls.WECOM_KF.value]

    @property
    def display_name(self) -> str:
        mapping = {
            self.WECOM_KF: "企业微信客服",
        }
        return mapping.get(self, "未知")


# ============== 排队状态 ==============

class QueueStatus(str, Enum):
    """
    智能体实例排队状态枚举

    数据库存储：TEXT (agent_instance_queue.status)
    - waiting   = 排队中
    - ready     = 已到号
    - expired   = 过期
    - cancelled = 已取消（包括用户主动取消和系统自动放弃）
    - abandoned = 已放弃（系统自动取消，归并为 cancelled 显示）
    """
    WAITING = "waiting"
    READY = "ready"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"

    @classmethod
    def all_values(cls) -> list[str]:
        return [cls.WAITING.value, cls.READY.value, cls.EXPIRED.value, cls.CANCELLED.value, cls.ABANDONED.value]

    @property
    def display_name(self) -> str:
        mapping = {
            self.WAITING: "排队中",
            self.READY: "已到号",
            self.EXPIRED: "过期",
            self.CANCELLED: "已取消",
            self.ABANDONED: "已取消",  # 归并为 cancelled 显示
        }
        return mapping.get(self, "未知")


# ============== 上下文压缩摘要状态 ==============

class ContextSummaryStatus(str, Enum):
    """上下文压缩摘要状态枚举（v3.2.1 P1-2）。

    数据库存储：TEXT (chat_context_summaries.status)
    - active     = 当前生效的摘要（同一 session+source_type 同时只允许一条）
    - superseded = 已被更新的 active 摘要替代
    - rolled_back= 已被运维回滚（消息 compacted 标记已清除）

    见 src/saas/api/context_compression_routes.py + src/memory/mid_term.py。
    """
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ROLLED_BACK = "rolled_back"

    @classmethod
    def all_values(cls) -> list[str]:
        return [cls.ACTIVE.value, cls.SUPERSEDED.value, cls.ROLLED_BACK.value]

    @property
    def display_name(self) -> str:
        mapping = {
            self.ACTIVE: "生效中",
            self.SUPERSEDED: "已替代",
            self.ROLLED_BACK: "已回滚",
        }
        return mapping.get(self, "未知")
