"""图像生成服务基础契约（request / submit / poll）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ImageGenRequest(BaseModel):
    """单次出图请求。

    - reference_images_data_urls: 参考图列表（data:image/xxx;base64,...），1-3 张，
      顺序即语义顺序（约定材质锚点在前、布局锚点在后）；空列表 = 纯文生图
    - size: 宽*高（如 1280*960）；None 用 provider 配置默认值
    - n: 生成张数（1-4），按张计费
    """
    prompt: str
    negative_prompt: Optional[str] = None
    reference_images_data_urls: List[str] = Field(default_factory=list)
    size: Optional[str] = None
    n: int = 1
    seed: Optional[int] = None


class SubmitResult(BaseModel):
    """提交结果。

    - task_id 非空：async_task 模式，后续 poll
    - task_id 为空：sync 模式，image_urls 直接携带结果
    """
    task_id: Optional[str] = None
    task_status: str = "PENDING"
    image_urls: List[str] = Field(default_factory=list)
    raw: Dict[str, Any] = Field(default_factory=dict)


class PollResult(BaseModel):
    """轮询结果。SUCCEEDED 时 image_urls 非空；FAILED 时 error 非空。"""
    task_status: str = "UNKNOWN"
    image_urls: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    raw: Dict[str, Any] = Field(default_factory=dict)


class ImageGenProviderError(Exception):
    """图像生成 API 调用异常（网络/非 2xx/解析失败）。"""
