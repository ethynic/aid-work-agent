"""图像生成服务（提交 → 轮询 → 下载 → 注册为租户图片资产）。"""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import httpx
from loguru import logger

from .base import ImageGenProviderError, ImageGenRequest, PollResult, SubmitResult


def _build_provider():
    """按配置构建 provider；api_key 空回退 qwen.api_keys[0]（同百炼账号通用）。"""
    from src.config.settings import create_settings

    settings = create_settings()
    cfg = settings.image_gen
    dash = cfg.dashscope
    api_key = dash.api_key
    if not api_key:
        qwen_keys = settings.llm.qwen.api_keys or []
        api_key = qwen_keys[0] if qwen_keys else ""
    from .dashscope_provider import DashScopeImageProvider
    return DashScopeImageProvider(
        api_key=api_key,
        model=dash.model,
        mode=dash.mode,
        base_url=dash.base_url,
        enable_thinking=dash.enable_thinking,
    ), cfg


class ImageGenService:
    """图像生成统一入口：校验 → 提交 → 轮询 → 下载注册。"""

    def __init__(self):
        self._provider = None
        self._cfg = None

    def _ensure_provider(self):
        if self._provider is None:
            self._provider, self._cfg = _build_provider()
        return self._provider, self._cfg

    async def render(
        self,
        req: ImageGenRequest,
        tenant_id: str,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Tuple[List[Any], Dict[str, Any]]:
        """出图并注册为租户图片资产。

        Returns:
            (image_refs, meta)；meta 含 task_id/model/requested_n/succeeded_n
        Raises:
            ImageGenProviderError: 提交失败 / 超时 / 全部失败
        """
        provider, cfg = self._ensure_provider()

        refs_count = len(req.reference_images_data_urls or [])
        if refs_count > 3:
            raise ImageGenProviderError(f"参考图最多 3 张，收到 {refs_count} 张")

        submit: SubmitResult = await provider.submit(req, default_size=cfg.dashscope.default_size)
        task_id = submit.task_id

        image_urls: List[str] = list(submit.image_urls)
        if task_id:
            image_urls = await self._poll_until_done(provider, task_id, cfg)

        if not image_urls:
            raise ImageGenProviderError("图像生成未返回任何图片")

        # 下载 + 注册（部分失败按成功张数交付并计费）
        from src.core.image_asset import get_image_registry
        registry = get_image_registry()
        image_refs = []
        for url in image_urls:
            try:
                local_path = await self._download_to_temp(url, tenant_id)
                ref = await registry.register(
                    source_path=local_path,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    display_name=f"效果图_{uuid.uuid4().hex[:6]}.png",
                    source="tool_generated",
                    usage="inline",
                    source_ref=session_id,
                    move=True,
                )
                image_refs.append(ref)
            except Exception as e:
                logger.opt(exception=True).error(f"[image_gen] 结果图下载/注册失败: url={url[:80]}...: {e}")

        if not image_refs:
            raise ImageGenProviderError("所有生成图片下载/注册失败")

        meta = {
            "task_id": task_id,
            "model": cfg.dashscope.model,
            "requested_n": req.n,
            "succeeded_n": len(image_refs),
        }
        return image_refs, meta

    async def _poll_until_done(self, provider, task_id: str, cfg) -> List[str]:
        """轮询任务直到 SUCCEEDED/FAILED 或超时。"""
        interval = max(1, cfg.poll_interval_seconds)
        timeout = max(30, cfg.poll_timeout_seconds)
        # monotonic 计时含 poll 请求耗时本身，避免慢请求把实际等待放大到超时之外
        deadline = time.monotonic() + timeout
        while True:
            poll: PollResult = await provider.poll(task_id)
            if poll.task_status == "SUCCEEDED":
                return poll.image_urls
            if poll.task_status == "FAILED":
                raise ImageGenProviderError(f"图像生成任务失败: {poll.error or '未知原因'}")
            if poll.task_status in ("CANCELED", "CANCELLED"):
                raise ImageGenProviderError("图像生成任务已取消")
            if time.monotonic() >= deadline:
                raise ImageGenProviderError(
                    f"图像生成等待超时（{timeout}s，task={task_id}，最后状态 {poll.task_status}）"
                )
            await asyncio.sleep(interval)

    async def _download_to_temp(self, url: str, tenant_id: str) -> str:
        """下载生成图到租户 temp 目录（注册时 move 进 images/ 月度目录，不留外链）。"""
        from src.core.storage import ensure_tenant_storage_dir

        temp_root = os.path.abspath(ensure_tenant_storage_dir(tenant_id, "temp"))
        os.makedirs(temp_root, exist_ok=True)
        target = os.path.join(temp_root, f"image_gen_{uuid.uuid4().hex[:12]}.png")
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.content
        if not data:
            raise ImageGenProviderError("生成图下载内容为空")
        await asyncio.to_thread(self._write_bytes, target, data)
        return target

    @staticmethod
    def _write_bytes(path: str, data: bytes):
        with open(path, "wb") as f:
            f.write(data)


# 惰性单例（模块 import 无副作用）
_image_gen_service: Optional[ImageGenService] = None


def get_image_gen_service() -> ImageGenService:
    global _image_gen_service
    if _image_gen_service is None:
        _image_gen_service = ImageGenService()
    return _image_gen_service
