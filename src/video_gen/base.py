"""视频生成 Provider 抽象层。

定义统一的请求/响应 dataclass 和 Provider ABC，屏蔽各 provider（万相 / MiniMax-H3 等）
在 API 端点、请求体、状态码、有效期上的差异。Service 层只感知本模块的统一类型，
不直接依赖具体 provider 实现。

设计依据：plan wobbly-riding-stroustrup.md §Provider 抽象层。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class VideoGenRequest:
    """统一的视频生成请求（provider 无关）。

    - prompt: 提示词主体
    - reference_image_data_url: 产品图 base64 data URL（万相作 reference_image 锁外观；
      MiniMax 在无 first_frame 时也可作首帧）
    - first_frame_data_url: 起始帧 base64 data URL（模特图优先，无则用产品图）
    - seed: 随机种子（差异化抽卡用）
    - negative_prompt: 反向提示词（万相支持；MiniMax 不支持，provider 自行忽略）
    - duration: 时长秒（万相 5/10/15；MiniMax 4-15 任意整数，前端只暴露 5/10/15）
    - resolution: 分辨率（万相 720P/1080P；MiniMax 768P/2K）
    - ratio: 视频比例（万相显式；MiniMax 文生视频显式，图生视频强制 adaptive）
    """
    prompt: str
    reference_image_data_url: Optional[str]
    first_frame_data_url: Optional[str]
    seed: int
    negative_prompt: str = ""
    duration: int = 5
    resolution: str = "720P"
    ratio: str = "9:16"


@dataclass
class SubmitResult:
    """提交任务结果。"""
    task_id: str
    task_status: str          # 统一大写：PENDING（MiniMax 内部把 queued 映射为 PENDING）
    raw: dict = field(default_factory=dict)


@dataclass
class PollResult:
    """查询任务结果。"""
    task_status: str          # 统一大写：PENDING/RUNNING/SUCCEEDED/FAILED/CANCELED/UNKNOWN
    video_url: Optional[str] = None
    duration: Optional[int] = None
    error: Optional[str] = None
    raw: Optional[dict] = None


@dataclass
class OptionItem:
    """前端选项项。"""
    value: str
    label: str
    price_per_sec: Optional[float] = None   # 用于前端展示性价比（元/秒）


@dataclass
class ProviderOptions:
    """Provider 暴露给前端 / service 的能力声明。"""
    provider: str
    resolutions: list[OptionItem]
    ratios: list[OptionItem]
    durations: list[OptionItem]
    default_resolution: str
    default_ratio: str
    default_duration: int
    supports_reference_image: bool          # 两 provider 都 True（都支持 i2v 首帧）
    supports_negative_prompt: bool          # 万相 True，MiniMax False
    task_max_age_hours: int                  # 万相 24，MiniMax 168（7 天）


class BaseVideoProviderError(Exception):
    """Provider 调用异常基类（万相 / MiniMax 等具体 error 的父类）。

    service 层 except 本类即可统一捕获所有 provider 异常，无需感知具体 provider 类型。
    """


class BaseVideoProvider(ABC):
    """视频生成 Provider 抽象基类。"""
    name: str

    @abstractmethod
    async def submit(self, req: VideoGenRequest) -> SubmitResult:
        """提交视频生成任务，返回 task_id 与初始状态。"""

    @abstractmethod
    async def poll(self, task_id: str) -> PollResult:
        """查询任务状态，统一返回大写状态码。"""

    @abstractmethod
    def get_options(self) -> ProviderOptions:
        """返回该 provider 支持的选项与能力声明。"""
