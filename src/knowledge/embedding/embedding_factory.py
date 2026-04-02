"""
Embedding 客户端工厂
"""

from .embedding_client import TextEmbeddingV3Client


def create_embedding_client(provider: str = "qwen", **kwargs):
    """
    Embedding 客户端工厂

    Args:
        provider: 提供商（qwen/zhipu/bge3）
        **kwargs: 配置参数

    Returns:
        Embedding 客户端实例
    """
    # TODO: 后期根据 provider 切换不同实现
    # if provider == "zhipu":
    #     from .zhipu_embedding_client import ZhipuEmbedding3Client
    #     return ZhipuEmbedding3Client(api_key=kwargs["api_key"])
    # elif provider == "bge3":
    #     from .bge3_client import BGE3Client
    #     return BGE3Client(model_path=kwargs.get("model_path", "BAAI/bge-m3"))

    if provider == "qwen":
        return TextEmbeddingV3Client(api_key=kwargs["api_key"])

    raise ValueError(f"不支持的 Embedding 提供商: {provider}")
