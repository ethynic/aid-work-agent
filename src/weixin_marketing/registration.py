"""weixin 适配器受信注册点（R42 门控：enabled=false 时零注册零执行）

由受信初始化点调用（src/main.py 启动序列 / src/background_runner.py 资源初始化）：
- enabled=false（默认）→ 不注册任何适配器、不建任何调度，线上默认零行为变化；
- enabled=true 且租户白名单语义由 config 承载（tick/清扫接线在后续工作包）。
仅进程内显式注册（TrustedAdapterRegistry 无动态加载能力）。
"""

import threading

from loguru import logger

from src.desktop_automation.adapters import TrustedAdapterRegistry
from src.weixin_marketing.config import get_weixin_marketing_config
from src.weixin_marketing.constants import SCENARIO_KEY

_LOCK = threading.Lock()
_REGISTERED = False


def ensure_registered() -> bool:
    """幂等注册 weixin.fixed_content.v1 适配器（enabled 门控内）。

    返回是否处于已启用状态；重复调用安全（进程内单次注册）。
    幂等标记同时复核 registry 存活性：标记为真但适配器已被注销（测试清理/其他
    受信代码反注册）时重新注册，保证 dispatch tick 每 tick 调用的自愈性。
    """
    global _REGISTERED
    config = get_weixin_marketing_config()
    if not config.enabled:
        return False
    with _LOCK:
        if _REGISTERED and TrustedAdapterRegistry.get(SCENARIO_KEY) is not None:
            return True
        from src.weixin_marketing.adapters import WeixinFixedContentAdapter

        # P2-3：不注入冻结 config——适配器每次调用活读 get_weixin_marketing_config()，
        # 与 config.py 语义一致（改 yaml 热生效，无需重启重新注册）
        TrustedAdapterRegistry.register(WeixinFixedContentAdapter())
        _REGISTERED = True
    logger.info("weixin_marketing 适配器已注册（weixin.fixed_content.v1，enabled=true）")
    return True


def reset_registration() -> None:
    """测试清理用：注销适配器并复位幂等标记（生产路径不调用）"""
    global _REGISTERED
    with _LOCK:
        TrustedAdapterRegistry.unregister("weixin.fixed_content.v1")
        _REGISTERED = False
