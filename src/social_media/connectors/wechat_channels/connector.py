from datetime import datetime

from src.social_media.connectors.base import AccountCapabilities, ContentSpec, SocialPlatformConnector
from src.social_media.enums import PlatformCapability


SPEC = ContentSpec(
    platform="wechat_channels",
    version="2026-06-30",
    content_types=("video",),
    field_limits={"title": 30, "description": 1000},
    required_fields=("title", "description"),
    prompt_version="wechat_channels_video_v1",
)


class WeChatChannelsConnector(SocialPlatformConnector):
    platform = "wechat_channels"

    async def validate_account(self, account: dict) -> AccountCapabilities:
        return AccountCapabilities(
            supported=frozenset({PlatformCapability.ASSISTED_PUBLISH, PlatformCapability.DATA_IMPORT}),
            limits={"content_spec": SPEC.__dict__},
            detected_at=datetime.utcnow(),
        )

    async def validate_variant(self, variant: dict) -> dict:
        content = variant.get("content_json") or {}
        issues = []
        for field in SPEC.required_fields:
            if not content.get(field):
                issues.append({"field": field, "message": "必填字段缺失"})
        return {"valid": not issues, "issues": issues, "spec_version": SPEC.version}

    async def prepare_payload(self, variant: dict) -> dict:
        return {"platform": self.platform, "content": variant.get("content_json") or {}}

    async def build_assisted_package(self, variant: dict) -> dict:
        return {
            "platform": self.platform,
            "revision": variant.get("revision"),
            "content_hash": variant.get("content_hash"),
            "checklist": ["核对标题和描述", "上传最终视频和封面", "发布后回填链接和时间"],
            "content": variant.get("content_json") or {},
        }

    def map_error(self, error: Exception) -> dict:
        return {"code": "wechat_channels_error", "category": "permanent", "message": str(error)}

    def normalize_metrics(self, records: list[dict]) -> list[dict]:
        return records

