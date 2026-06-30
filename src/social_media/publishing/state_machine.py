from src.social_media.enums import PublishStatus


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    PublishStatus.DRAFT.value: {PublishStatus.SCHEDULED.value, PublishStatus.QUEUED.value, PublishStatus.READY_FOR_MANUAL_PUBLISH.value},
    PublishStatus.SCHEDULED.value: {PublishStatus.QUEUED.value, PublishStatus.CANCELLED.value},
    PublishStatus.QUEUED.value: {PublishStatus.PUBLISHING.value, PublishStatus.CANCELLED.value},
    PublishStatus.PUBLISHING.value: {PublishStatus.SUBMITTED.value, PublishStatus.READY_FOR_MANUAL_PUBLISH.value, PublishStatus.RETRY_WAIT.value, PublishStatus.FAILED.value, PublishStatus.STATUS_UNKNOWN.value},
    PublishStatus.SUBMITTED.value: {PublishStatus.POLLING.value, PublishStatus.PUBLISHED.value, PublishStatus.FAILED.value, PublishStatus.STATUS_UNKNOWN.value},
    PublishStatus.POLLING.value: {PublishStatus.PUBLISHED.value, PublishStatus.FAILED.value, PublishStatus.STATUS_UNKNOWN.value},
    PublishStatus.RETRY_WAIT.value: {PublishStatus.QUEUED.value, PublishStatus.CANCELLED.value},
    PublishStatus.READY_FOR_MANUAL_PUBLISH.value: {PublishStatus.MANUALLY_CONFIRMED.value, PublishStatus.CANCELLED.value},
}


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, set())

