"""
Moonshot（Kimi）LLM提供者

通过 Moonshot 官方 OpenAI 兼容接口（https://api.moonshot.cn/v1）调用 kimi 系列模型。
首期用途：weixin-cli 视觉定位（kimi-k3）走服务端代理集中计费，
见 docs/design/weixin/weixin-cli-billing.md §4.1。

实现复用 QwenProvider 的 OpenAI 兼容链路，仅覆盖差异点：
- base_url 默认 api.moonshot.cn
- kimi 推理模型（kimi-k3 等）temperature 必须为 1 或省略 → 统一省略，
  避免调用方传入 0 触发 400
- 不写 enable_thinking / cache_control：基类 _is_qwen_model() 对 kimi 前缀
  返回 False，天然生效
"""

from typing import Any, Dict

from .qwen import QwenProvider


class MoonshotProvider(QwenProvider):
    """Moonshot（Kimi）提供者（OpenAI 兼容模式）。"""

    PROVIDER_NAME = "moonshot"
    DISPLAY_NAME = "Moonshot（Kimi）"
    # 默认 OpenAI 兼容端点，可通过 base_url 覆盖
    DEFAULT_BASE_URL = "https://api.moonshot.cn/v1"
    # kimi 推理模型：content 为空时答案在 reasoning_content，开启兜底
    # （与 weixin-cli 驱动直调时的兜底行为一致）
    REASONING_CONTENT_FALLBACK = True

    def _adjust_request_body(self, request_body: Dict[str, Any]) -> None:
        """kimi 推理模型 temperature 仅允许 1 或省略，统一省略调用方传入值。"""
        if str(self.model or "").lower().startswith("kimi"):
            request_body.pop("temperature", None)
