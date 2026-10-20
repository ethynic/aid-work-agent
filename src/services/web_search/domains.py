"""搜索适配器共用的域名规范化与完整域名边界匹配。"""

import re
from typing import List, Optional


def normalize_domains(domains: Optional[List[str]]) -> List[str]:
    normalized = []
    for domain in domains or []:
        value = domain.strip().rstrip(".").encode("idna").decode("ascii").lower()
        if not value or len(value) > 253 or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in value.split(".")
        ):
            raise ValueError("invalid domain")
        if value not in normalized:
            normalized.append(value)
    return normalized


def matches_domain(host: str, domains: List[str]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)
