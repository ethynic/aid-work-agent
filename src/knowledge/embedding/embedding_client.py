"""
通义 text-embedding-v3 Embedding 客户端
"""

import asyncio
import re
from typing import List
import logging

from dashscope import TextEmbedding

logger = logging.getLogger(__name__)


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

    async def _embed_single_batch(self, texts: List[str]) -> List[List[float]]:
        """
        单批次向量化（不拆分）

        Args:
            texts: 文本列表（最多 10 个）

        Returns:
            向量列表
        """
        try:
            # TextEmbedding.call 是同步 SDK，使用 to_thread 包装
            resp = await asyncio.to_thread(
                TextEmbedding.call,
                model=self.model,
                input=texts,
                parameters={
                    "text_type": "document",
                    "dimension": self.dimension
                }
            )

            if resp.status_code != 200:
                sanitized_msg = sanitize_error_info(resp.message)
                raise Exception(f"Embedding API 失败: {sanitized_msg}")

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

    async def embed(self, text: str) -> List[float]:
        """单文本向量化"""
        embeddings = await self.embed_batch([text])
        return embeddings[0]
