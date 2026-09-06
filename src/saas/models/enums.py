"""
SaaS 领域枚举值定义

所有 SaaS 相关表字段的枚举值统一在此定义。
包括：租户状态、订阅状态、支付状态等。

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


# ============== 用户角色 ==============

class UserRole(str, Enum):
    """
    用户角色枚举

    数据库存储：TEXT
    - platform_admin = 平台管理员
    - tenant_admin   = 租户管理员
    - tenant_user    = 租户用户
    """
    PLATFORM_ADMIN = "platform_admin"
    TENANT_ADMIN = "tenant_admin"
    TENANT_USER = "tenant_user"

    @classmethod
    def all_values(cls) -> list[str]:
        return [cls.PLATFORM_ADMIN.value, cls.TENANT_ADMIN.value, cls.TENANT_USER.value]

    @property
    def display_name(self) -> str:
        mapping = {
            self.PLATFORM_ADMIN: "平台管理员",
            self.TENANT_ADMIN: "租户管理员",
            self.TENANT_USER: "用户",
        }
        return mapping.get(self, "未知")


# ============== 用户来源 ==============

class UserSource(str, Enum):
    """
    用户来源枚举

    数据库存储：TEXT
    - NULL / 空字符串 = 内部用户（管理员创建）
    - wecom_kf = 企微客服
    - wecom_personal_rpa = 企微RPA
    """
    WECOM_KF = "wecom_kf"
    WECOM_PERSONAL_RPA = "wecom_personal_rpa"

    @classmethod
    def all_values(cls) -> list[str]:
        return [cls.WECOM_KF.value, cls.WECOM_PERSONAL_RPA.value]

    @property
    def display_name(self) -> str:
        mapping = {
            self.WECOM_KF: "企微客服",
            self.WECOM_PERSONAL_RPA: "企微RPA",
        }
        return mapping.get(self, "未知")


# ============== 套餐计划 ==============

class PlanType(str, Enum):
    """
    套餐类型枚举

    数据库存储：TEXT
    - basic    = 基础版
    - standard = 标准版
    - premium  = 旗舰版
    """
    BASIC = "basic"
    STANDARD = "standard"
    PREMIUM = "premium"

    @classmethod
    def all_values(cls) -> list[str]:
        return [cls.BASIC.value, cls.STANDARD.value, cls.PREMIUM.value]

    @property
    def display_name(self) -> str:
        mapping = {
            self.BASIC: "基础版",
            self.STANDARD: "标准版",
            self.PREMIUM: "旗舰版",
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


# ============== 用户行为审计日志 ==============

class BehaviorAction(str, Enum):
    """
    用户行为审计日志 - 行为类型枚举

    数据库存储：TEXT (user_behavior_logs.action)
    详见 docs/system/user-behavior-audit-log-design.md §4
    """
    LOGIN = "login"                      # 登录成功
    LOGIN_FAILED = "login_failed"        # 登录失败
    LOGOUT = "logout"                    # 登出
    PASSWORD_CHANGE = "password_change"  # 修改密码
    VERIFY_CODE_SENT = "verify_code_sent"  # 发送验证码（防爆破观测）
    PROFILE_UPDATE = "profile_update"    # 修改资料
    CHANNEL_BIND = "channel_bind"        # 渠道账号绑定（entry=channel）
    CHANNEL_UNBIND = "channel_unbind"    # 渠道账号解绑（entry=channel）
    CREATE = "create"                    # 创建资源（Phase 2/3）
    UPDATE = "update"                    # 更新资源（Phase 2/3）
    DELETE = "delete"                    # 删除资源（Phase 2/3）
    BATCH_DELETE = "batch_delete"        # 批量删除（Phase 3）
    EXPORT = "export"                    # 导出（Phase 2/3）

    @classmethod
    def all_values(cls) -> list[str]:
        return [m.value for m in cls]

    @property
    def display_name(self) -> str:
        mapping = {
            self.LOGIN: "登录成功",
            self.LOGIN_FAILED: "登录失败",
            self.LOGOUT: "登出",
            self.PASSWORD_CHANGE: "修改密码",
            self.VERIFY_CODE_SENT: "发送验证码",
            self.PROFILE_UPDATE: "修改资料",
            self.CHANNEL_BIND: "渠道账号绑定",
            self.CHANNEL_UNBIND: "渠道账号解绑",
            self.CREATE: "创建",
            self.UPDATE: "更新",
            self.DELETE: "删除",
            self.BATCH_DELETE: "批量删除",
            self.EXPORT: "导出",
        }
        return mapping.get(self, "未知")


class BehaviorEntry(str, Enum):
    """
    用户行为审计日志 - 请求入口枚举

    数据库存储：TEXT (user_behavior_logs.entry)
    """
    WEB = "web"          # web 前端发起
    API = "api"          # 脚本/第三方直接调 API
    CHANNEL = "channel"  # 渠道回调（无用户侧 IP/UA）

    @classmethod
    def all_values(cls) -> list[str]:
        return [m.value for m in cls]

    @property
    def display_name(self) -> str:
        mapping = {
            self.WEB: "Web 前端",
            self.API: "API 直调",
            self.CHANNEL: "渠道回调",
        }
        return mapping.get(self, "未知")


class BehaviorResourceType(str, Enum):
    """
    用户行为审计日志 - 资源类型枚举

    数据库存储：TEXT (user_behavior_logs.resource_type)
    """
    TENANT = "tenant"              # 租户
    TENANT_USER = "tenant_user"    # 租户用户
    SUBAGENT = "subagent"          # 数字员工定义
    PROMPT = "prompt"              # 提示词
    SESSION = "session"            # 会话
    KNOWLEDGE_DOC = "knowledge_doc"  # 知识库文档
    CONFIG = "config"              # 配置
    BILLING = "billing"            # 计费
    ACCOUNT = "account"            # 自己的账号（改密码/改资料）
    ACTIVATION_CODE = "activation_code"      # 激活码（协会客户端）
    CLIENT_BINDING = "client_binding"        # 客户端绑定（协会客户端激活/管理）
    RPA_CLIENT = "rpa_client"                # RPA客户端（个人微信 RPA）
    CHANNEL_ACCOUNT = "channel_account"      # 客服账号（企微客服等渠道账号）
    EXTERNAL_CUSTOMER = "external_customer"  # 外部客户（线索/外部联系人）
    REPLY_STYLE = "reply_style"              # 回复风格
    SKILL = "skill"                          # 租户技能（自定义 Skill）
    ERROR_LOG = "error_log"                  # 错误日志（管理后台处理动作）
    KNOWLEDGE_SHARE = "knowledge_share"      # 知识库授权（租户间共享）

    @classmethod
    def all_values(cls) -> list[str]:
        return [m.value for m in cls]

    @property
    def display_name(self) -> str:
        mapping = {
            self.TENANT: "租户",
            self.TENANT_USER: "租户用户",
            self.SUBAGENT: "数字员工",
            self.PROMPT: "提示词",
            self.SESSION: "会话",
            self.KNOWLEDGE_DOC: "知识库文档",
            self.CONFIG: "配置",
            self.BILLING: "计费",
            self.ACCOUNT: "账号",
            self.ACTIVATION_CODE: "激活码",
            self.CLIENT_BINDING: "客户端绑定",
            self.RPA_CLIENT: "RPA客户端",
            self.CHANNEL_ACCOUNT: "客服账号",
            self.EXTERNAL_CUSTOMER: "外部客户",
            self.REPLY_STYLE: "回复风格",
            self.SKILL: "租户技能",
            self.ERROR_LOG: "错误日志",
            self.KNOWLEDGE_SHARE: "知识库授权",
        }
        return mapping.get(self, "未知")


class BehaviorDeviceType(str, Enum):
    """
    用户行为审计日志 - 粗分设备类型枚举

    数据库存储：TEXT (user_behavior_logs.device_type)
    细分设备快照（device_info）使用受控词表，见
    docs/system/user-behavior-audit-log-design.md §5.4，不进正式枚举
    """
    PC = "pc"
    MOBILE = "mobile"
    TABLET = "tablet"
    UNKNOWN = "unknown"

    @classmethod
    def all_values(cls) -> list[str]:
        return [m.value for m in cls]

    @property
    def display_name(self) -> str:
        mapping = {
            self.PC: "PC",
            self.MOBILE: "移动端",
            self.TABLET: "平板",
            self.UNKNOWN: "未知",
        }
        return mapping.get(self, "未知")


# ============== chat_records.source_type ==============

class ChatRecordSourceType(str, Enum):
    """chat_records.source_type 枚举值。

    数据库存储：TEXT (chat_records.source_type)
    取值含义：
    - chat                  = Web 端对话
    - wecom                 = 企业微信应用消息
    - wecom_kf              = 企业微信客服
    - wecom_personal_rpa    = 企微个人号 RPA
    - dingtalk              = 钉钉
    - feishu                = 飞书
    - report_personal       = 个人日报生成（LLM 摘要调用）
    - report_team           = 团队日报生成（LLM 摘要调用）
    - report_personal_weekly= 个人周报生成
    - report_team_weekly    = 团队周报生成
    - report_personal_monthly = 个人月报生成
    - report_team_monthly   = 团队月报生成
    - video_gen             = 视频创作生成（按秒计费，video-agent 主链路）
    - video_prompt          = 视频提示词 LLM 调用（qwen-vl 等，按 token 计费，
                              用户可能多次调整提示词后放弃创建视频，独立计费）
    - knowledge_embedding   = 知识库文档向量化/摘要（离线处理，用户上传文档触发，
                              非 dialog 内调用，独立 chat_records）
    - background_llm        = background_runner 后台 LLM 调用（长期记忆摘要、
                              工作成果复盘、上下文压缩扫描等定时任务）

    report_* 系列由 src/reports/generator.py 写入，用于在用量页区分
    报告类 LLM 调用与普通对话调用。
    详见 docs/research/ai-agent-experience-daily-report-research.md §4.7.5
    """
    CHAT = "chat"
    WECOM = "wecom"
    WECOM_KF = "wecom_kf"
    WECOM_PERSONAL_RPA = "wecom_personal_rpa"
    DINGTALK = "dingtalk"
    FEISHU = "feishu"
    REPORT_PERSONAL = "report_personal"
    REPORT_TEAM = "report_team"
    REPORT_PERSONAL_WEEKLY = "report_personal_weekly"
    REPORT_TEAM_WEEKLY = "report_team_weekly"
    REPORT_PERSONAL_MONTHLY = "report_personal_monthly"
    REPORT_TEAM_MONTHLY = "report_team_monthly"
    VIDEO_GEN = "video_gen"
    VIDEO_PROMPT = "video_prompt"
    KNOWLEDGE_EMBEDDING = "knowledge_embedding"
    BACKGROUND_LLM = "background_llm"

    @classmethod
    def all_values(cls) -> list[str]:
        return [m.value for m in cls]

    @classmethod
    def report_values(cls) -> list[str]:
        """报告类 source_type（用于用量页过滤、计费聚合）"""
        return [
            cls.REPORT_PERSONAL.value,
            cls.REPORT_TEAM.value,
            cls.REPORT_PERSONAL_WEEKLY.value,
            cls.REPORT_TEAM_WEEKLY.value,
            cls.REPORT_PERSONAL_MONTHLY.value,
            cls.REPORT_TEAM_MONTHLY.value,
        ]

    @classmethod
    def is_report(cls, value: str) -> bool:
        return value in cls.report_values()

    @property
    def display_name(self) -> str:
        mapping = {
            self.CHAT: "Web 对话",
            self.WECOM: "企业微信",
            self.WECOM_KF: "企微客服",
            self.WECOM_PERSONAL_RPA: "企微个人号",
            self.DINGTALK: "钉钉",
            self.FEISHU: "飞书",
            self.REPORT_PERSONAL: "个人日报",
            self.REPORT_TEAM: "团队日报",
            self.REPORT_PERSONAL_WEEKLY: "个人周报",
            self.REPORT_TEAM_WEEKLY: "团队周报",
            self.REPORT_PERSONAL_MONTHLY: "个人月报",
            self.REPORT_TEAM_MONTHLY: "团队月报",
            self.VIDEO_GEN: "视频生成",
            self.VIDEO_PROMPT: "视频提示词",
            self.KNOWLEDGE_EMBEDDING: "知识库向量化",
            self.BACKGROUND_LLM: "后台 LLM 任务",
        }
        return mapping.get(self, "未知")
