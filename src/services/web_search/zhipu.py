"""兼容旧的智谱搜索模块路径；新实现位于 providers/zhipu.py。"""

from .providers.zhipu import SEARCH_ENDPOINT, ENGINES, RECENCY_FILTERS, search_zhipu, httpx

__all__ = ["SEARCH_ENDPOINT", "ENGINES", "RECENCY_FILTERS", "search_zhipu"]
