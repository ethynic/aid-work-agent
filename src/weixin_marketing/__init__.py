"""微信营销自动化场景模块（P2-A，宪章 R39-R48）

仅承载微信任务/版本、内容包、账号/群绑定、触发配置与场景授权；
执行链（subject/schedule/occurrence/run/delivery/attempt/permit/quota/outbox）
复用 src/desktop_automation 与 src/local_tools 底座，经 TrustedAdapterRegistry
注册 weixin.fixed_content.v1 适配器（registration.ensure_registered，enabled 门控）。

惰性 __init__：任何子模块 import 不触发副作用（不建表、不注册适配器、不拉起调度）。
"""

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "SCENARIO_KEY":
        from src.weixin_marketing.constants import SCENARIO_KEY

        return SCENARIO_KEY
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["SCENARIO_KEY"]
