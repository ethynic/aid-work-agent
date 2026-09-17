"""本地工具协议模型（Pydantic）"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PairRequest(BaseModel):
    """Runtime 配对请求：用一次性配对码换设备 token"""

    code: str = Field(..., description="8 位配对码（明文）")
    name: Optional[str] = Field(None, description="设备名称")
    platform: Optional[str] = Field(None, description="平台，如 windows")
    runtime_version: Optional[str] = Field(None, description="Runtime 版本")
    capabilities: Optional[Dict[str, Any]] = Field(None, description="设备能力（含 provider_id）")
    machine_fingerprint: Optional[str] = Field(None, description="机器指纹（服务端只存哈希）")


class HeartbeatRequest(BaseModel):
    """Runtime 心跳：上报版本/能力/manifest 摘要"""

    runtime_version: Optional[str] = None
    capabilities: Optional[Dict[str, Any]] = None
    manifest_digest: Optional[str] = None


class ClaimResponse(BaseModel):
    """claim 成功返回（claim_token 明文仅此一次）"""

    invocation_id: str
    tool_name: str
    arguments: Dict[str, Any]
    claim_token: str
    lease_expires_at: str
    provider: Optional[str] = None


class StartedRequest(BaseModel):
    claim_token: str


class ProgressRequest(BaseModel):
    claim_token: str
    stage: Optional[str] = None
    current: Optional[int] = None
    total: Optional[int] = None
    message: Optional[str] = Field(None, description="进度文案（服务端截断 500 字符）")


class ResultRequest(BaseModel):
    """写入终态。success=False 且 code='EXECUTION_UNKNOWN' → state=unknown"""

    claim_token: str
    success: bool
    code: Optional[str] = None
    message: Optional[str] = None
    effect: Optional[str] = Field(None, description="none/applied/partial/unknown")
    data: Optional[Dict[str, Any]] = None
    retryable: Optional[bool] = None


class WriteAuthorizeRequest(BaseModel):
    """v2 写动作许可申请（Runtime 内部 API，不暴露给 LLM）。

    claim 身份、request_id、target_version、payload_hash——许可绑定
    invocation/device/claim/request_id/target_version/payload_hash/epoch/resource，任一变化拒绝。
    """

    claim_token: str
    request_id: str
    target_version: Optional[str] = Field(None, description="本次解析的目标版本（须与 invocation 一致）")
    payload_hash: Optional[str] = Field(None, description="本次载荷哈希（须与 invocation 一致）")


class OperationResultRequest(BaseModel):
    """v2 操作结果回传（持久 ACK；迟到只对账）。effect ∈ none/applied/unknown，
    phase ∈ prepared/may_have_started/verified/unknown（R10）。"""

    claim_token: str
    request_id: str
    effect: str
    phase: Optional[str] = None
    evidence_ref: Optional[str] = Field(None, description="验证证据引用（截图/消息 id 等受控引用）")
    safe_to_retry: Optional[bool] = None
    permit_id: Optional[str] = None
    permit_token: Optional[str] = None
    code: Optional[str] = None
    message: Optional[str] = None


class InvocationView(BaseModel):
    invocation_id: str
    tool_name: str
    state: str
    effect: Optional[str] = None
    created_at: Optional[str] = None


class DeviceView(BaseModel):
    device_id: str
    name: Optional[str] = None
    platform: Optional[str] = None
    runtime_version: Optional[str] = None
    capabilities: Optional[Dict[str, Any]] = None
    selected: bool = False
    status: str = "active"
    online: bool = False
    last_seen_at: Optional[str] = None
    created_at: Optional[str] = None


class ResumeEvaluateRequest(BaseModel):
    """简历评估请求（POST /runtime/resume/evaluate，2026-09-17 去 OCR 化 v2）。

    - image：拼接长图 PNG base64（服务端 Pillow 切带重叠横带）；
    - images：或已分段的图片 base64 列表（按阅读顺序直接作横带，不重复切片）；
      两字段二选一，都给时以 images 为准
    - candidate_name：必填（业务必填，模型层 Optional——422 统一走端点的 _http_error 错误形状）。
      传入 VL 提示词并与图中姓名（name_seen）比对（决策⑨姓名门，≤1 字容差），不符 422 不扣费
    - job_id / job_name：可选职位上下文，给了才评分；无 → score/match_summary=null 只出总结
    - model：可选，provider/model 语法（如 zhipu/GLM-5.3-Flash）；须在白名单内
    """

    image: Optional[str] = Field(None, description="拼接长图 PNG base64（服务端切片）")
    images: Optional[List[str]] = Field(None, description="分段图片 base64 列表（按阅读顺序）")
    candidate_name: Optional[str] = Field(None, max_length=30, description="页面候选人姓名（必填，姓名核对基准）")
    job_id: Optional[str] = Field(None, max_length=64, description="职位 id（可选，给了才评分）")
    job_name: Optional[str] = Field(None, max_length=100, description="职位名（可选，给了才评分）")
    model: Optional[str] = Field(None, max_length=100, description="识别模型 provider/model（可选，白名单校验）")
