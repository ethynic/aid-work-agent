"""巡检商机模块枚举定义

遵循 database_dev.md / backend_dev.md「枚举值定义规范」：
- 数据库字段使用 TEXT，枚举值 = 数据库存储值
- 业务代码必须以本文件枚举类为准，禁止硬编码字符串
"""

from enum import Enum


class LeadStatus(str, Enum):
    """商机状态机取值。

    状态流转（见 state_machine.ALLOWED_TRANSITIONS）：
        new → contacted
        contacted → qualified | invalid
        qualified → converted
        invalid / converted → 终态
    """

    NEW = "new"                # 新商机，待接触
    CONTACTED = "contacted"    # 已接触（评论/私信/外呼等任一动作完成）
    QUALIFIED = "qualified"    # 已确认有效需求，待转化
    INVALID = "invalid"        # 无效（不相关/低质量/风险/重复等），终态
    CONVERTED = "converted"    # 已转化（成交/进入 CRM），终态


class LeadSourceType(str, Enum):
    """商机来源类型（区分进入商机池的入口动作）。"""

    RADAR = "radar"                          # 需求雷达巡视捕获
    CONTENT_INTERACTION = "content_interaction"  # 我方内容下的互动（评论/点赞/收藏）
    OUTREACH_REPLY = "outreach_reply"        # 我方主动接触后的对方回复


class InteractionType(str, Enum):
    """商机互动/跟进记录类型。"""

    NOTE = "note"            # 销售手记
    CALL = "call"            # 电话
    EMAIL = "email"          # 邮件
    DM = "dm"                # 平台私信
    COMMENT = "comment"      # 平台评论
    VISIT = "visit"          # 上门拜访
    WECHAT = "wechat"        # 微信沟通
    OTHER = "other"          # 其他


class OutreachActionType(str, Enum):
    """我方接触动作类型（区别于互动记录：这里是「我方主动发起、需审核」的接触动作审计）。"""

    COMMENT = "comment"   # 在对方内容下评论
    DM = "dm"             # 发私信
    POST = "post"         # 主动发布内容（@对方或针对对方需求）


class OutreachExecutionStatus(str, Enum):
    """我方接触动作执行状态。"""

    DRAFT = "draft"              # 草稿（话术已生成，未提交审核）
    PENDING_REVIEW = "pending_review"  # 待人工审核
    EXECUTED = "executed"        # 已执行（平台已受理 ≠ 已生效）
    FAILED = "failed"            # 执行失败
    CANCELLED = "cancelled"      # 取消
