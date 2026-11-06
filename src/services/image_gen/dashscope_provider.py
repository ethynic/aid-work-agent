"""DashScope 百炼图像生成 provider（提交 + 轮询）。

wire 形态说明（mode 可配置，真机冒烟定版，见计划 docs）：
- async_task（默认）：POST {base_url}/services/aigc/{endpoint}，header X-DashScope-Async: enable；
  有参考图走 image2image/image-synthesis（input.images 携带 data URL），纯文生图走
  text2image/image-synthesis；轮询 GET /api/v1/tasks/{task_id}，结果在 output.results[].url。
- sync：POST {base_url}/services/aigc/multimodal-generation/generation 同步直返
  （messages 多模态形态），结果直接在 output 中，不走任务轮询。

图片传输：base64 data URL 直传（与 wanx 视频 provider 相同约定），无需公网 URL/OSS。
"""
from __future__ import annotations

import httpx
from loguru import logger

from .base import (
    ImageGenProviderError,
    ImageGenRequest,
    PollResult,
    SubmitResult,
)

_MAX_REFERENCE_IMAGES = 3


class DashScopeImageProvider:
    """DashScope 图像生成调用（qwen-image-edit 系列）。"""

    name = "dashscope"

    def __init__(self, api_key: str, model: str = "qwen-image-edit",
                 mode: str = "async_task", base_url: str = "https://dashscope.aliyuncs.com/api/v1"):
        if not api_key:
            raise ImageGenProviderError("图像生成 api_key 未配置（需 WANX_API_KEY 或 QWEN_API_KEYS）")
        if mode not in ("async_task", "sync"):
            raise ImageGenProviderError(f"mode 仅支持 async_task/sync，收到: {mode}")
        self._api_key = api_key
        self._model = model
        self._mode = mode
        self._base_url = base_url.rstrip("/")

    async def submit(self, req: ImageGenRequest, default_size: str = "1280*960") -> SubmitResult:
        """提交出图请求。

        参考图上限 3 张（qwen-image-edit 约束，参考图顺序即语义顺序）。
        """
        refs = req.reference_images_data_urls or []
        if len(refs) > _MAX_REFERENCE_IMAGES:
            raise ImageGenProviderError(f"参考图最多 {_MAX_REFERENCE_IMAGES} 张，收到 {len(refs)} 张")
        size = req.size or default_size

        if self._mode == "sync":
            return await self._submit_sync(req, refs, size)
        return await self._submit_async(req, refs, size)

    # ---------------- async_task 模式 ----------------

    async def _submit_async(self, req: ImageGenRequest, refs: list, size: str) -> SubmitResult:
        endpoint = (
            "image2image/image-synthesis" if refs else "text2image/image-synthesis"
        )
        url = f"{self._base_url}/services/aigc/{endpoint}"
        input_payload: Dict[str, Any] = {
            "prompt": req.prompt,
        }
        if req.negative_prompt:
            input_payload["negative_prompt"] = req.negative_prompt
        if refs:
            input_payload["images"] = refs
        body = {
            "model": self._model,
            "input": input_payload,
            "parameters": {
                "size": size,
                "n": req.n,
                "watermark": False,   # 平台合规水印由业务层决定，不在 provider 硬加
            },
        }
        if req.seed is not None:
            body["parameters"]["seed"] = req.seed

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            "X-DashScope-Async": "enable",   # 缺少必报错
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise ImageGenProviderError(f"图像生成提交网络异常: {exc}") from exc

        if resp.status_code != 200:
            raise ImageGenProviderError(
                f"图像生成提交失败 (HTTP {resp.status_code}): {resp.text[:300]}"
            )

        data = resp.json()
        output = data.get("output") or {}
        task_id = output.get("task_id")
        if not task_id:
            raise ImageGenProviderError(f"图像生成提交未返回 task_id: {data}")
        task_status = output.get("task_status", "PENDING")
        logger.info(f"[image_gen] 提交成功 model={self._model} task_id={task_id} status={task_status} refs={len(refs)}")
        return SubmitResult(task_id=task_id, task_status=task_status, raw=data)

    async def poll(self, task_id: str) -> PollResult:
        """查询异步任务状态（SUCCEEDED 时提取 output.results[].url）。"""
        headers = {"Authorization": f"Bearer {self._api_key}"}
        url = f"{self._base_url}/tasks/{task_id}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise ImageGenProviderError(f"图像生成轮询网络异常 (task={task_id}): {exc}") from exc

        if resp.status_code != 200:
            raise ImageGenProviderError(
                f"图像生成轮询失败 (HTTP {resp.status_code}): {resp.text[:300]}"
            )

        data = resp.json()
        output = data.get("output") or {}
        task_status = output.get("task_status", "UNKNOWN")

        image_urls: list = []
        error = None
        if task_status == "SUCCEEDED":
            results = output.get("results") or []
            for item in results:
                url_ = (item or {}).get("url") if isinstance(item, dict) else None
                if url_:
                    image_urls.append(url_)
            if not image_urls:
                task_status = "FAILED"
                error = "任务成功但未返回图片 url"
        elif task_status == "FAILED":
            error = output.get("message") or output.get("code") or "图像生成失败"

        return PollResult(task_status=task_status, image_urls=image_urls, error=error, raw=data)

    # ---------------- sync 模式（multimodal-generation 形态） ----------------

    async def _submit_sync(self, req: ImageGenRequest, refs: list, size: str) -> SubmitResult:
        url = f"{self._base_url}/services/aigc/multimodal-generation/generation"
        content: list = [{"image": ref} for ref in refs]
        content.append({"text": req.prompt})
        body = {
            "model": self._model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": {
                "size": size,
                "n": req.n,
                "watermark": False,
            },
        }
        if req.negative_prompt:
            body["parameters"]["negative_prompt"] = req.negative_prompt
        if req.seed is not None:
            body["parameters"]["seed"] = req.seed

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise ImageGenProviderError(f"图像生成(sync)网络异常: {exc}") from exc

        if resp.status_code != 200:
            raise ImageGenProviderError(
                f"图像生成(sync)失败 (HTTP {resp.status_code}): {resp.text[:300]}"
            )

        data = resp.json()
        output = data.get("output") or {}
        image_urls = self._extract_sync_urls(output)
        if not image_urls:
            raise ImageGenProviderError(f"图像生成(sync)未返回图片: {str(data)[:300]}")
        logger.info(f"[image_gen] sync 出图成功 model={self._model} count={len(image_urls)}")
        return SubmitResult(task_id=None, task_status="SUCCEEDED", image_urls=image_urls, raw=data)

    @staticmethod
    def _extract_sync_urls(output: Dict[str, Any]) -> list:
        """兼容 sync 响应的两种常见结构：output.results[].url 与 choices[].message.content[].image。"""
        urls: list = []
        for item in output.get("results") or []:
            if isinstance(item, dict) and item.get("url"):
                urls.append(item["url"])
        if urls:
            return urls
        for choice in output.get("choices") or []:
            for part in ((choice or {}).get("message") or {}).get("content") or []:
                if isinstance(part, dict) and part.get("image"):
                    urls.append(part["image"])
        return urls
