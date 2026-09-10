"""webhook 测试支持：构造签名并直调 accept_webhook_event（单测层复用）"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.weixin_marketing import event_sources as wxm_sources


def post_webhook(
    tenant_id: str,
    source: Dict[str, Any],
    body: Dict[str, Any],
    *,
    nonce: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """默认合法签名投递（不指定 secret 时用创建响应的一次性明文）。"""
    now = now or datetime.now(timezone.utc)
    ts = str(int(now.timestamp()))
    nc = nonce if nonce is not None else f"n-{uuid.uuid4().hex[:12]}"
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    sig = wxm_sources.compute_webhook_signature(source["secret"], ts, nc, raw)
    result = wxm_sources.accept_webhook_event(
        tenant_id=tenant_id, source_id=source["source"]["id"],
        timestamp=ts, nonce=nc, signature=sig, key_id=source.get("key_id"),
        body=raw, now=now, max_body_bytes=1024 * 1024,
    )
    return result
