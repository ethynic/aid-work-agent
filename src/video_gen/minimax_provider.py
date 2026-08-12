"""MiniMax-H3 视频生成 Provider。

API 文档：ext/Minimax-H3.md
- 端点：POST https://api.minimaxi.com/v2/video_generation
- 鉴权：Bearer + Content-Type: application/json
- 请求体 content 数组：text 项必填 + 可选 image_url 项作 first_frame
- 状态码小写（queued/running/succeeded/failed/cancelled），映射到统一大写
- 任务有效期 7 天（168h）
- 不支持 negative_prompt（provider 内部忽略，不报错）
- 图生视频时 ratio 强制 adaptive（由图片决定，文档明确要求）
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

_SUBMIT_URL = "https://api.minimaxi.com/v2/video_generation"
_POLL_BASE = "https://api.minimaxi.com/v2/query/video_generation/"

# MiniMax 小写状态码 -> 统一大写状态码（与万相对齐）
_STATUS_MAP = {
    "queued": "PENDING",
    "running": "RUNNING",
    "succeeded": "SUCCEEDED",
    "failed": "FAILED",
    "cancelled": "CANCELED",
}


class MiniMaxProviderError(BaseVideoProviderError):
    """MiniMax API 调用异常（非 2xx / 解析失败）。"""


class MiniMaxProvider(BaseVideoProvider):
    """MiniMax-H3 视频生成调用（提交 + 查询）。

    TODO: 当前只接入主生成模式（/v2/video_generation），未实现 H3-Regeneration（超分再生成）
    和参考图片计费（前 5 张免费，超出 0.20 元/张）。H3-Regeneration 接入时需扩 token_cost_prices
    的 price_per_second_by_resolution schema 区分生成模式，参考图计费需在请求层加张数统计。
    """

    name = "minimax"

    def __init__(self, api_key: str, model: str = "MiniMax-H3"):
        if not api_key:
            raise MiniMaxProviderError("MiniMax api_key 未配置（需 MINIMAX_API_KEY）")
        self._api_key = api_key
        self._model = model

    async def submit(self, req: VideoGenRequest) -> SubmitResult:
        """提交视频生成任务。

        - content 数组：text 项必填 + 可选 image_url 项作 first_frame
        - first_frame_url 优先用 first_frame_data_url，回退到 reference_image_data_url
        - 图生视频（有 first_frame_url）时 ratio 强制 adaptive（MiniMax 文档要求）
        - aigc_watermark=False（项目自己烧录 AI 标识）
        - negative_prompt 在 MiniMax 不支持，provider 内部忽略
        """
        # 临时 tlog：标记 minimax submit 入口
        try:
            from src.core.temp_logger import tlog
            tlog(
                "video-agent-阶段三",
                "minimax.submit 入口 model={model} duration={dur} resolution={res} ratio={ratio} "
                "seed={seed} has_first_frame={has_ff}",
                model=self._model,
                dur=req.duration,
                res=req.resolution,
                ratio=req.ratio,
                seed=req.seed,
                has_ff=bool(req.first_frame_data_url or req.reference_image_data_url),
            )
        except Exception:
            pass

        # 防御性校验：MiniMax 支持 768P/2K，duration 4-15 任意整数
        if req.resolution not in ("768P", "2K"):
            raise MiniMaxProviderError(f"resolution 仅支持 768P/2K，收到: {req.resolution}")
        if not 4 <= req.duration <= 15:
            raise MiniMaxProviderError(f"duration 仅支持 4-15 秒，收到: {req.duration}")

        # 构造 content 数组：text 必填 + 可选 first_frame image
        content: list[dict] = [{"type": "text", "text": req.prompt}]
        # i2v：用 first_frame_data_url（若有）作首帧；否则用 reference_image_data_url
        first_frame_url = req.first_frame_data_url or req.reference_image_data_url
        if first_frame_url:
            content.append({
                "type": "image_url",
                "image_url": {"url": first_frame_url},
                "role": "first_frame",
            })
        # 图生视频时 ratio 恒为 adaptive（由图片决定），文生视频时用显式 ratio
        ratio = "adaptive" if first_frame_url else req.ratio
        body = {
            "model": self._model,
            "content": content,
            "resolution": req.resolution,
            "duration": req.duration,
            "ratio": ratio,
            "aigc_watermark": False,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(_SUBMIT_URL, json=body, headers=headers)
        except httpx.HTTPError as exc:
            try:
                from src.core.temp_logger import tlog
                tlog("video-agent-阶段三", "minimax.submit 网络异常 err={err}", err=str(exc), level="ERROR")
            except Exception:
                pass
            raise MiniMaxProviderError(f"MiniMax 提交网络异常: {exc}") from exc

        if resp.status_code != 200:
            try:
                from src.core.temp_logger import tlog
                tlog(
                    "video-agent-阶段三",
                    "minimax.submit HTTP 失败 status={st} body={body}",
                    st=resp.status_code,
                    body=resp.text[:300],
                    level="ERROR",
                )
            except Exception:
                pass
            raise MiniMaxProviderError(f"MiniMax 提交失败 (HTTP {resp.status_code}): {resp.text}")

        data = resp.json()
        task_id = data.get("task_id")
        if not task_id:
            try:
                from src.core.temp_logger import tlog
                tlog("video-agent-阶段三", "minimax.submit 未返回 task_id data={data}", data=str(data)[:300], level="ERROR")
            except Exception:
                pass
            raise MiniMaxProviderError(f"MiniMax 提交未返回 task_id: {data}")
        try:
            from src.core.temp_logger import tlog
            tlog("video-agent-阶段三", "minimax.submit 成功 task_id={tid}", tid=task_id)
        except Exception:
            pass
        logger.info(f"MiniMax 提交成功 task_id={task_id}")
        return SubmitResult(task_id=task_id, task_status="PENDING", raw=data)

    async def poll(self, task_id: str) -> PollResult:
        """查询任务状态，把 MiniMax 小写状态码映射到统一大写。"""
        # 临时 tlog：标记 minimax poll 入口
        try:
            from src.core.temp_logger import tlog
            tlog("video-agent-阶段三", "minimax.poll 入口 task_id={tid}", tid=task_id)
        except Exception:
            pass

        url = f"{_POLL_BASE}{task_id}"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            try:
                from src.core.temp_logger import tlog
                tlog("video-agent-阶段三", "minimax.poll 网络异常 task={tid} err={err}", tid=task_id, err=str(exc), level="ERROR")
            except Exception:
                pass
            raise MiniMaxProviderError(f"MiniMax 轮询网络异常 (task={task_id}): {exc}") from exc

        if resp.status_code != 200:
            try:
                from src.core.temp_logger import tlog
                tlog("video-agent-阶段三", "minimax.poll HTTP 失败 task={tid} status={st} body={body}", tid=task_id, st=resp.status_code, body=resp.text[:300], level="ERROR")
            except Exception:
                pass
            raise MiniMaxProviderError(f"MiniMax 轮询失败 (HTTP {resp.status_code}): {resp.text}")

        data = resp.json()
        task = data.get("task") or {}
        raw_status = task.get("status", "queued")
        mapped = _STATUS_MAP.get(raw_status, "UNKNOWN")

        video_url = None
        if mapped == "SUCCEEDED":
            content_obj = task.get("content") or {}
            video_url = content_obj.get("url")
        duration = task.get("duration")
        error = None
        if mapped == "FAILED":
            err_obj = task.get("error") or {}
            error = err_obj.get("message") or "MiniMax 生成失败"
        try:
            from src.core.temp_logger import tlog
            tlog(
                "video-agent-阶段三",
                "minimax.poll 返回 task_id={tid} raw_status={raw} mapped={st} has_video={hv} err={err}",
                tid=task_id,
                raw=raw_status,
                st=mapped,
                hv=bool(video_url),
                err=error,
            )
        except Exception:
            pass
        return PollResult(
            task_status=mapped,
            video_url=video_url,
            duration=int(duration) if duration is not None else None,
            error=error,
            raw=task,
        )

    def get_options(self) -> ProviderOptions:
        """MiniMax 能力声明：768P/2K、7 个 ratio、5/10/15s、168h（7 天）有效期。"""
        return ProviderOptions(
            provider="minimax",
            resolutions=[
                OptionItem("768P", "768P", 0.50),
                OptionItem("2K", "2K", 0.80),
            ],
            ratios=[OptionItem(r, r) for r in
                    ("9:16", "16:9", "1:1", "4:3", "3:4", "21:9", "adaptive")],
            durations=[OptionItem(str(d), f"{d}s") for d in (5, 10, 15)],
            default_resolution="768P",
            default_ratio="9:16",
            default_duration=5,
            supports_reference_image=True,    # MiniMax 也支持 first_frame
            supports_negative_prompt=False,  # MiniMax 无此字段
            task_max_age_hours=168,           # 7 天
        )
