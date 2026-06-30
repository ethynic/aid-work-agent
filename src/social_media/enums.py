from enum import Enum


class PlatformCapability(str, Enum):
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

