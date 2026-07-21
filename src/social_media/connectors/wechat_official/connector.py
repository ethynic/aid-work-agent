from datetime import datetime

from src.social_media.connectors.base import AccountCapabilities, ContentSpec, SocialPlatformConnector
from src.social_media.enums import PlatformCapability


SPEC = ContentSpec(
    platform="wechat_official",
    version="2026-06-30",
    content_types=("article",),
    field_limits={"title": 64, "digest": 120},
    required_fields=("title", "body"),
    prompt_version="wechat_official_article_v1",
)


class WeChatOfficialConnector(SocialPlatformConnector):
    platform = "wechat_official"

    async def validate_account(self, account: dict) -> AccountCapabilities:
        # 当前为 stub：仅凭证绑定 + 本地规格校验真实可用。REMOTE_DRAFT / API_PUBLISH /
        # SCHEDULED_PUBLISH / PUBLISH_STATUS / API_ANALYTICS 对应方法均未实现（基类抛
        # CapabilityNotSupported），按 S0「未实现的能力不声明」原则不在此声明，待 C1
        # 接入真实 HTTP 后再补回，避免 CapabilityResolver 门禁形同虚设。
        return AccountCapabilities(
            supported=frozenset({PlatformCapability.ACCOUNT_CREDENTIALS}),
            limits={"content_spec": SPEC.__dict__},
            detected_at=datetime.utcnow(),
        )

    async def validate_variant(self, variant: dict) -> dict:
        content = variant.get("content_json") or {}
        issues = []
        for field in SPEC.required_fields:
            if not content.get(field):
                issues.append({"field": field, "message": "必填字段缺失"})
        if len(str(content.get("title", ""))) > SPEC.field_limits["title"]:
            issues.append({"field": "title", "message": "标题超出微信公众号限制"})
        return {"valid": not issues, "issues": issues, "spec_version": SPEC.version}

    async def prepare_payload(self, variant: dict) -> dict:
        return {"platform": self.platform, "content": variant.get("content_json") or {}}

    def map_error(self, error: Exception) -> dict:
        return {"code": "wechat_official_error", "category": "permanent", "message": str(error)}

    def normalize_metrics(self, records: list[dict]) -> list[dict]:
        return records

