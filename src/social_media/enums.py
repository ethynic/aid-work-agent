from enum import Enum


class PlatformCapability(str, Enum):
    # 内容能力（已实现）
    ACCOUNT_OAUTH = "account_oauth"
    ACCOUNT_CREDENTIALS = "account_credentials"
    REMOTE_ASSET_LIST = "remote_asset_list"
    REMOTE_DRAFT = "remote_draft"
    API_PUBLISH = "api_publish"
    ASSISTED_PUBLISH = "assisted_publish"
    SCHEDULED_PUBLISH = "scheduled_publish"
    PUBLISH_STATUS = "publish_status"
    API_ANALYTICS = "api_analytics"
    DATA_IMPORT = "data_import"
    # 广告能力（待 tencent_ads 连接器实现）
    ADS_OAUTH = "ads_oauth"
    ADS_ACCOUNT_TREE = "ads_account_tree"
    ADS_REPORT = "ads_report"
    ADS_UPDATE_BID = "ads_update_bid"
    ADS_UPDATE_BUDGET = "ads_update_budget"
    ADS_UPDATE_TARGETING = "ads_update_targeting"
    ADS_PAUSE = "ads_pause"
    ADS_LEADS = "ads_leads"
    ADS_CONVERSION_CALLBACK = "ads_conversion_callback"
    # web 操作能力（待 zhihu_web/xiaohongshu_web 连接器实现）
    WEB_LOGIN_SESSION = "web_login_session"
    WEB_SEARCH = "web_search"
    WEB_READ_PAGE = "web_read_page"
    WEB_POST_CONTENT = "web_post_content"
    WEB_COMMENT = "web_comment"
    WEB_DM = "web_dm"
    WEB_INTERACTION_TRACKING = "web_interaction_tracking"


class ReviewDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


class VariantStatus(str, Enum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class PublishStatus(str, Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    QUEUED = "queued"
    PUBLISHING = "publishing"
    SUBMITTED = "submitted"
    POLLING = "polling"
    PUBLISHED = "published"
    READY_FOR_MANUAL_PUBLISH = "ready_for_manual_publish"
    MANUALLY_CONFIRMED = "manually_confirmed"
    RETRY_WAIT = "retry_wait"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STATUS_UNKNOWN = "status_unknown"

