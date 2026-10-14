"""供应商无关的搜索请求与异步适配器接口。"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol


@dataclass(frozen=True)
class SearchRequest:
    keyword: str
    max_results: int
    include_answer: bool
    search_depth: str
    topic: str
    time_range: Optional[str]
    include_domains: Optional[List[str]]
    exclude_domains: Optional[List[str]]


class SearchProvider(Protocol):
    async def search(self, request: SearchRequest) -> Dict[str, Any]: ...
