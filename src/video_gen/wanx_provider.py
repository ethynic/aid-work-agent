"""通义万相 2.7 图生视频 Provider（r2v）。

spike 验证结论（2026-07-30，已实测通过，设计文档 §5）：
- 模型 wan2.7-r2v-2026-06-12（r2v = reference-to-video，非 i2v）
- 防变形：reference_image（锁定产品外观）+ first_frame（控制起始画面）组合，产品不变形
- 图片传输：base64 直传（data:image/jpeg;base64,...），无需公网URL/OSS
- 必须用 Python httpx（curl 的 JSON 编码会触发 Required body invalid）
- 复用 QWEN_API_KEYS（同百炼账号通用）
- 旧域名 dashscope.aliyuncs.com，无需 workspace_id
- 生成耗时约 3 分钟，输出 5 秒 720P mp4；video_url 24h 有效
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx
from loguru import logger

_SUBMIT_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
_POLL_BASE = "https://dashscope.aliyuncs.com/api/v1/tasks/"


@dataclass
class WanxSubmitResult:
    """提交任务结果。"""
    task_id: str
    task_status: str          # 通常 "PENDING"


@dataclass
class WanxPollResult:
    """查询任务结果。"""
    task_status: str          # SUCCEEDED/RUNNING/PENDING/FAILED/CANCELED/UNKNOWN
    video_url: str | None     # 仅 SUCCEEDED 有值
    duration: int | None      # 来自 usage.output_video_duration
    error: str | None


class WanxProviderError(Exception):
    """万相 API 调用异常（非 2xx / 解析失败）。"""


class WanxProvider:
    """通义万相 r2v 调用（提交 + 查询）。"""

    def __init__(self, api_key: str, model: str = "wan2.7-r2v-2026-06-12"):
        if not api_key:
            raise WanxProviderError("万相 api_key 未配置（需 WANX_API_KEY 或 QWEN_API_KEYS）")
        self._api_key = api_key
        self._model = model

    async def submit(
        self,
        prompt: str,
        reference_image_data_url: str,  # 产品图 base64 data URL（reference_image，锁定产品外观防变形）
        first_frame_data_url: str,      # 起始帧 base64 data URL（模特图优先，无则用产品图）
        seed: int,
        negative_prompt: str = "",
        duration: int = 5,
        resolution: str = "720P",
    ) -> WanxSubmitResult:
        """提交参考图生视频任务（r2v）。

        media 固定格式：reference_image（产品图，防变形锁定）+ first_frame（起始帧，
        有模特图传模特图，否则传产品图）。negative_prompt 放 parameters 下（spike 实测确认）。
        """
        # 防御性校验：万相 2.7 r2v 单次调用 duration 上限 15s，防止前端脏数据直传阿里云
        if duration not in (5, 10, 15):
            raise WanxProviderError(f"duration 仅支持 5/10/15 秒，收到: {duration}")
        body = {
            "model": self._model,
            "input": {
                "prompt": prompt,
                "media": [
                    {"type": "reference_image", "url": reference_image_data_url},
                    {"type": "first_frame", "url": first_frame_data_url},
                ],
            },
            "parameters": {
                "resolution": resolution,
                "duration": duration,
                "negative_prompt": negative_prompt,
                "prompt_extend": False,     # 自己用提示词引擎扩展，不让万相再改写
                "watermark": False,          # 自己烧录合规 AI 标识，不用万相水印
                "seed": seed,
            },
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            "X-DashScope-Async": "enable",   # 缺少必报错
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(_SUBMIT_URL, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise WanxProviderError(f"万相提交网络异常: {exc}") from exc

        if resp.status_code != 200:
            raise WanxProviderError(f"万相提交失败 (HTTP {resp.status_code}): {resp.text}")

        data = resp.json()
        output = data.get("output") or {}
        task_id = output.get("task_id")
        if not task_id:
            raise WanxProviderError(f"万相提交未返回 task_id: {data}")
        task_status = output.get("task_status", "PENDING")
        logger.info(f"万相提交成功 task_id={task_id} status={task_status}")
        return WanxSubmitResult(task_id=task_id, task_status=task_status)

    async def poll(self, task_id: str) -> WanxPollResult:
        """查询任务状态。"""
        headers = {"Authorization": f"Bearer {self._api_key}"}
        url = f"{_POLL_BASE}{task_id}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise WanxProviderError(f"万相轮询网络异常 (task={task_id}): {exc}") from exc

        if resp.status_code != 200:
            raise WanxProviderError(f"万相轮询失败 (HTTP {resp.status_code}): {resp.text}")

        data = resp.json()
        output = data.get("output") or {}
        task_status = output.get("task_status", "UNKNOWN")
        usage = data.get("usage") or {}

        video_url = output.get("video_url") if task_status == "SUCCEEDED" else None
        duration = usage.get("output_video_duration")
        error = None
        if task_status == "FAILED":
            error = output.get("message") or output.get("errors") or "万相生成失败"
        return WanxPollResult(
            task_status=task_status,
            video_url=video_url,
            duration=int(duration) if duration is not None else None,
            error=error,
        )
