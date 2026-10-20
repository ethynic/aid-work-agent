"""显式的搜索 provider 注册表；每次调用只创建被配置选中的适配器。"""

from typing import Any, Callable, Dict, Optional

from .models import SearchProvider
from .providers.baidu import BaiduProvider
from .providers.tavily import TavilyProvider
from .providers.zhipu import ZhipuProvider

# 新供应商增加适配器、注册项和配置，web_search 工具保持不变。
# 工厂不预执行，不共享请求凭据或实例，不按密钥是否存在自动切换。
PROVIDER_FACTORIES: Dict[str, Callable[[Any], SearchProvider]] = {
    "tavily": lambda settings: TavilyProvider(
        api_key=settings.tools.search.tavily_api_key,
        timeout_seconds=settings.tools.search.timeout_seconds,
    ),
    "baidu": lambda settings: BaiduProvider(
        api_key=settings.tools.search.baidu_api_key,
        timeout_seconds=settings.tools.search.timeout_seconds,
    ),
    "zhipu": lambda settings: ZhipuProvider(
        api_keys=settings.llm.zhipu.get_effective_keys(),
        engine=settings.tools.search.zhipu_search_engine,
        timeout_seconds=settings.tools.search.timeout_seconds,
    ),
}


def create_provider(name: str, settings: Any) -> Optional[SearchProvider]:
    factory = PROVIDER_FACTORIES.get(name)
    return factory(settings) if factory else None
