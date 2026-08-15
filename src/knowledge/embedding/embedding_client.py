"""
通义 text-embedding-v3 Embedding 客户端
"""

import asyncio
import re
import time
from typing import List

import requests.exceptions
from dashscope import TextEmbedding
from loguru import logger

# 可重试的瞬时网络异常：DashScope SDK 用模块级 requests.Session 单例复用 keep-alive
# 连接，长期空闲后被服务端关闭，下次复用即抛 ConnectionError(RemoteDisconnected)。
# 这些异常重试时 SDK 会丢弃死连接、建新连接，第二次基本必中。
_RETRYABLE_EXC = (
    requests.exceptions.ConnectionError,    # 含 RemoteDisconnected / ConnectionReset
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)

# 瞬时错误关键字（兜底匹配未被 isinstance 覆盖的子类或被包装异常）
_TRANSIENT_KEYWORDS = (
    "connection aborted",
    "remote disconnected",
    "connection reset",
    "connection refused",
    "timed out",
    "connection closed",
)


def _is_transient_error(error: Exception) -> bool:
    """判断是否为可重试的瞬时网络错误"""
    if isinstance(error, _RETRYABLE_EXC):
        return True
    msg = str(error).lower()
    return any(kw in msg for kw in _TRANSIENT_KEYWORDS)


def _extract_usage_tokens(resp) -> int:
    """从 DashScope TextEmbedding 响应中提取 usage token 数

    DashScope 返回的 usage 是 dict 形式（如 {"total_tokens": 9}），而非带
    .tokens 属性的对象。兼容两种形态：
    - dict：读 total_tokens（优先）或 tokens
    - 对象：读 .total_tokens（优先）或 .tokens

    提取失败返回 0（调用方按 0 处理，即不计费）。
    """
    usage = getattr(resp, "usage", None)
    if usage is None:
        return 0
    if isinstance(usage, dict):
        tokens = usage.get("total_tokens") or usage.get("tokens")
    else:
        tokens = getattr(usage, "total_tokens", None) or getattr(usage, "tokens", None)
    if isinstance(tokens, (int, float)) and tokens > 0:
        return int(tokens)
    return 0


def sanitize_error_info(error_msg: str) -> str:
    """过滤错误信息中的敏感信息"""
    if not error_msg:
        return error_msg
    sensitive_patterns = [
        r'password["\s:=]+\S+',
        r'passwd["\s:=]+\S+',
        r'secret["\s:=]+\S+',
        r'token["\s:=]+\S+',
        r'api[_-]?key["\s:=]+\S+',
        r'access[_-]?key["\s:=]+\S+',
        r'private[_-]?key["\s:=]+\S+',
        r'auth[_-]?token["\s:=]+\S+',
    ]
    sanitized = error_msg
    for pattern in sensitive_patterns:
        sanitized = re.sub(
            pattern,
            lambda m: m.group(0).split('=')[0] + '=***',
            sanitized,
            flags=re.IGNORECASE
        )
    return sanitized


class TextEmbeddingV3Client:
    """通义 text-embedding-v3 客户端"""

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("Embedding API key 未配置，请检查 EMBEDDING_API_KEY 环境变量")
        import dashscope
        dashscope.api_key = api_key
        self._api_key = api_key  # 保存 key 用于日志
        self.model = "text-embedding-v3"
        self.dimension = 1024
        # 最近一次调用累加的 usage tokens（供调用方读取以接入计费）
        self.last_usage_tokens: int = 0

    async def embed_batch(self, texts: List[str], batch_size: int = 10) -> List[List[float]]:
        """
        批量向量化（自动分批，避免超限）

        Args:
            texts: 文本列表
            batch_size: 每批大小，默认 10（API 限制）

        Returns:
            向量列表，每个向量维度为 1024
        """
        if not texts:
            return []

        all_embeddings = []
        # 分批处理，每批最多 batch_size 个
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_embeddings = await self._embed_single_batch(batch)
            all_embeddings.extend(batch_embeddings)

        return all_embeddings

    def _call_dashscope_with_retry(self, texts):
        """调用 DashScope TextEmbedding.call，含 2 次重试覆盖 keep-alive 死连接

        仅 wrap HTTP 传输层调用；resp.status_code 业务错误由外层处理（业务错误不可重试）。
        """
        last_exc = None
        for attempt in range(1, 4):  # 1 + 2 = 3 次尝试
            try:
                return TextEmbedding.call(
                    model=self.model,
                    input=texts,
                    parameters={
                        "text_type": "document",
                        "dimension": self.dimension,
                    },
                )
            except Exception as e:
                last_exc = e
                if not _is_transient_error(e):
                    raise
                if attempt < 3:
                    logger.warning(
                        f"后端日志：Embedding 调用瞬时网络错误 (attempt={attempt}/3): "
                        f"{type(e).__name__}: {sanitize_error_info(str(e))}，1s 后重试"
                    )
                    time.sleep(1)
                else:
                    logger.error(
                        f"后端日志：Embedding 调用 3 次均失败: "
                        f"{type(e).__name__}: {sanitize_error_info(str(e))}"
                    )
        raise last_exc  # type: ignore[misc]

    async def _embed_single_batch(self, texts: List[str]) -> List[List[float]]:
        """
        单批次向量化（不拆分）

        Args:
            texts: 文本列表（最多 10 个）

        Returns:
            向量列表
        """
        try:
            # TextEmbedding.call 是同步 SDK，使用 to_thread 包装；
            # _call_dashscope_with_retry 内部对 keep-alive 死连接等瞬时网络错误重试 2 次
            resp = await asyncio.to_thread(self._call_dashscope_with_retry, texts)

            if resp.status_code != 200:
                sanitized_msg = sanitize_error_info(resp.message)
                raise Exception(f"Embedding API 失败: {sanitized_msg}")

            # 累加 usage tokens，供调用方接入计费
            self.last_usage_tokens += _extract_usage_tokens(resp)

            embeddings = [item["embedding"] for item in resp.output["embeddings"]]
            logger.info(f"后端日志：Embedding 批量调用成功，数量={len(texts)}")
            return embeddings

        except Exception as e:
            # 打印 API key 的前几位和后几位，便于排查问题
            key = getattr(self, '_api_key', None)
            if key:
                key_preview = f"{key[:6]}...{key[-4:]}" if len(key) > 10 else "***"
                logger.error(f"后端日志：Embedding 批量调用失败，使用的 key: {key_preview}", exc_info=True)
            error_str = str(e)
            # 避免重复过滤
            if "=***" not in error_str:
                error_str = sanitize_error_info(error_str)
            logger.error(f"后端日志：Embedding 批量调用失败: {error_str}", exc_info=True)
            raise

    def reset_usage(self) -> None:
        """重置累加的 usage tokens 计数器，方便调用方按段计费"""
        self.last_usage_tokens = 0

    def embed_sync(self, text: str) -> List[float]:
        """单文本向量化（同步版本）

        供同步代码路径（如 retriever、search tool 的 _embed 方法）使用，
        避免在 event loop 中嵌套 asyncio.run。同时累加 usage_tokens
        供调用方接入计费。
        """
        if not text:
            return []
        resp = self._call_dashscope_with_retry(text)
        if resp.status_code != 200:
            sanitized_msg = sanitize_error_info(resp.message)
            raise Exception(f"Embedding API 失败: {sanitized_msg}")
        # 累加 usage tokens
        self.last_usage_tokens += _extract_usage_tokens(resp)
        return resp.output["embeddings"][0]["embedding"]

    async def embed(self, text: str) -> List[float]:
        """单文本向量化"""
        embeddings = await self.embed_batch([text])
        return embeddings[0]
