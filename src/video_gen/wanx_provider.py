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

import httpx
from loguru import logger

from src.video_gen.base import (
    BaseVideoProvider,
    BaseVideoProviderError,
    OptionItem,
    PollResult,
    ProviderOptions,
    SubmitResult,
    VideoGenRequest,
)

_SUBMIT_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
_POLL_BASE = "https://dashscope.aliyuncs.com/api/v1/tasks/"


class WanxProviderError(BaseVideoProviderError):
    """万相 API 调用异常（非 2xx / 解析失败）。"""


class WanxProvider(BaseVideoProvider):
    """通义万相 r2v 调用（提交 + 查询）。"""

    name = "wanx"

    def __init__(self, api_key: str, model: str = "wan2.7-r2v"):
        if not api_key:
            raise WanxProviderError("万相 api_key 未配置（需 WANX_API_KEY 或 QWEN_API_KEYS）")
        self._api_key = api_key
        self._model = model

    async def submit(self, req: VideoGenRequest) -> SubmitResult:
        """提交参考图生视频任务（r2v）。

        media 固定格式：reference_image（产品图，防变形锁定）+ first_frame（起始帧，
        有模特图传模特图，否则传产品图）。negative_prompt 放 parameters 下（spike 实测确认）。
        ratio 控制视频画面比例（9:16 竖版 / 16:9 横版 / 1:1 / 4:3 / 3:4）。
        """
        # 防御性校验：万相 2.7 r2v 单次调用 duration 上限 15s，防止前端脏数据直传阿里云
        if req.duration not in (5, 10, 15):
            raise WanxProviderError(f"duration 仅支持 5/10/15 秒，收到: {req.duration}")
        if req.resolution not in ("720P", "1080P"):
            raise WanxProviderError(f"resolution 仅支持 720P/1080P，收到: {req.resolution}")
        if req.ratio not in ("9:16", "16:9", "1:1", "4:3", "3:4"):
            raise WanxProviderError(f"ratio 仅支持 9:16/16:9/1:1/4:3/3:4，收到: {req.ratio}")
        if not req.reference_image_data_url:
            raise WanxProviderError("万相必填 reference_image_data_url 缺失")
        # 无模特图时 first_frame 回退到产品图（与 service 层一致）
        first_frame_url = req.first_frame_data_url or req.reference_image_data_url
        body = {
            "model": self._model,
            "input": {
                "prompt": req.prompt,
                "media": [
                    {"type": "reference_image", "url": req.reference_image_data_url},
                    {"type": "first_frame", "url": first_frame_url},
                ],
            },
            "parameters": {
                "resolution": req.resolution,
                "duration": req.duration,
                "ratio": req.ratio,
                "negative_prompt": req.negative_prompt,
                "prompt_extend": False,     # 自己用提示词引擎扩展，不让万相再改写
                "watermark": False,          # 自己烧录合规 AI 标识，不用万相水印
                "seed": req.seed,
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
        return SubmitResult(task_id=task_id, task_status=task_status, raw=data)

    async def poll(self, task_id: str) -> PollResult:
        """查询任务状态（万相状态码已是大写）。"""
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
        return PollResult(
            task_status=task_status,
            video_url=video_url,
            duration=int(duration) if duration is not None else None,
            error=error,
            raw=data,
        )

    def get_options(self) -> ProviderOptions:
        """万相能力声明：720P/1080P、5 个 ratio、5/10/15s、24h 有效期。"""
        return ProviderOptions(
            provider="wanx",
            resolutions=[
                OptionItem("720P", "720P", 0.60),
                OptionItem("1080P", "1080P", 1.00),
            ],
            ratios=[OptionItem(r, r) for r in ("9:16", "16:9", "1:1", "4:3", "3:4")],
            durations=[OptionItem(str(d), f"{d}s") for d in (5, 10, 15)],
            default_resolution="720P",
            default_ratio="9:16",
            default_duration=5,
            supports_reference_image=True,
            supports_negative_prompt=True,
            task_max_age_hours=24,
        )
