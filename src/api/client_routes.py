"""
协会客户端对外 API（客户端 CLI 调用）。

设计文档：docs/tools/association-client-design.md §2.2
路由前缀：/api/client/v1
- POST /activate      激活码激活（无需鉴权）
- GET  /credits       积分余额查询
- POST /llm/chat      LLM 代理（计费 ×10；可选 model 白名单路由，如 kimi-k3 视觉模型）
- POST /ocr/parse     OCR 代理（不扣费，记录调用）
- POST /logs          日志上报
- POST /usage/report  通用用量上报（C 模式，客户端计费统一接入 P4）
"""

from __future__ import annotations

import math
import os
from decimal import Decimal, ROUND_CEILING
import tempfile
from datetime import datetime
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel, Field, model_validator

from src.api.client_auth import ClientBinding, get_client_token_from_header, verify_client_token
from src.config.settings import settings
from src.db.client_binding_db import (
    ClientActivationCodeDB,
    ClientBindingDB,
    ClientUsageLogDB,
)
from src.llm.gateway import LLMGateway, llm_gateway

router = APIRouter(prefix="/api/client/v1", tags=["协会客户端"])


# 客户端可指定模型的白名单：model -> provider（首期仅 kimi-k3 视觉模型，走 moonshot 网关，
# 供 weixin-cli 视觉定位使用，见 docs/design/weixin/weixin-cli-billing.md §4.1）。
# 未传 model 的请求仍走全局默认 llm_gateway，行为与改动前完全一致。
CLIENT_MODEL_PROVIDER_MAP: Dict[str, str] = {
    "kimi-k3": "moonshot",
}

# 按 model 缓存专用网关实例（KeyPool 级并发控制需跨请求复用）
_model_gateways: Dict[str, LLMGateway] = {}


def _get_model_gateway(model: str) -> LLMGateway:
    """按白名单返回指定模型的专用网关实例（懒构建 + 缓存）。

    fail closed 双重校验：
    1. model 必须在 CLIENT_MODEL_PROVIDER_MAP 白名单内；
    2. model 必须在 token_cost_prices 有非零定价行——单价缺失时
       calculate_credit_cost 返回 0 等于免单，必须拒绝。
    """
    provider = CLIENT_MODEL_PROVIDER_MAP.get(model)
    if not provider:
        raise HTTPException(status_code=400, detail=f"MODEL_NOT_ALLOWED: 模型 {model} 不在客户端可用白名单")

    from src.db.models import TokenCostPriceDB

    price = TokenCostPriceDB.get_by_model_name(model)
    if not price or not (float(price.get("input_price_per_m") or 0) > 0 and float(price.get("output_price_per_m") or 0) > 0):
        raise HTTPException(status_code=400, detail=f"MODEL_NOT_PRICED: 模型 {model} 未配置计费单价，暂不可用")

    if model not in _model_gateways:
        # use_failover=False：prompt 与模型绑定（视觉定位），跨 provider 降级到文本模型
        # 既无法完成任务又会按错误模型计价
        _model_gateways[model] = LLMGateway(
            provider_name=provider, model_codes={provider: model}, use_failover=False,
        )
    return _model_gateways[model]


# ============== 请求/响应模型 ==============

class ActivateRequest(BaseModel):
    activation_code: str
    machine_id: str
    client_name: Optional[str] = None


class LlmChatRequest(BaseModel):
    messages: list[dict[str, Any]]
    temperature: float = 0
    max_tokens: int = 4000
    response_format: Optional[dict[str, Any]] = None
    purpose: str = "unknown"
    association: Optional[str] = None
    # 可选模型指定：仅放行 CLIENT_MODEL_PROVIDER_MAP 白名单（如 kimi-k3 视觉模型），
    # 未传时走全局默认 llm_gateway
    model: Optional[str] = None


class LogEntry(BaseModel):
    timestamp: Optional[str] = None
    level: str = "INFO"
    stage: Optional[str] = None
    association_name: Optional[str] = None
    message: str
    detail: Optional[dict[str, Any]] = None


class LogBatchRequest(BaseModel):
    session_id: Optional[str] = None
    logs: list[LogEntry]


# ============== 鉴权依赖 ==============

def _require_binding(authorization: Optional[str] = Header(None)) -> ClientBinding:
    """FastAPI 依赖：从 Authorization 头校验客户端令牌。"""
    token = get_client_token_from_header(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="UNAUTHORIZED")
    binding = verify_client_token(token)
    if not binding:
        raise HTTPException(status_code=401, detail="UNAUTHORIZED")
    return binding


def _check_credit(binding: ClientBinding) -> None:
    """余额阻断：≤0 时拒绝。"""
    balance = float(binding.tenant.get("credit_balance") or 0)
    if balance <= 0:
        raise HTTPException(status_code=402, detail="NO_CREDIT", headers={"X-Reason": "积分余额不足，请充值"})


# ============== 激活 ==============

@router.post("/activate")
async def activate(req: ActivateRequest):
    """激活码激活，生成客户端绑定凭证。"""
    code = req.activation_code.strip().upper()
    record = ClientActivationCodeDB.get_by_code(code)
    if not record:
        raise HTTPException(status_code=404, detail="ACTIVATION_CODE_NOT_FOUND")

    if record["status"] == "disabled":
        raise HTTPException(status_code=410, detail="ACTIVATION_CODE_DISABLED")

    # 校验 hash（防 DB 数据被篡改）
    if not ClientActivationCodeDB.verify_code(code, record["code_hash"]):
        logger.warning(f"激活码 hash 校验失败 code={code}")
        raise HTTPException(status_code=404, detail="ACTIVATION_CODE_NOT_FOUND")

    # 校验过期
    expires_at = record.get("expires_at")
    if expires_at and datetime.now() >= expires_at:
        raise HTTPException(status_code=410, detail="ACTIVATION_CODE_EXPIRED")

    # 校验用量
    if record["used_count"] >= record["max_uses"]:
        raise HTTPException(status_code=410, detail="ACTIVATION_CODE_USED")

    # 创建绑定
    binding = ClientBindingDB.create(
        tenant_id=record["tenant_id"],
        activation_code_id=record["id"],
        client_name=req.client_name or record.get("client_name"),
        machine_id=req.machine_id,
    )
    # 标记激活码已使用
    ClientActivationCodeDB.mark_used(record["id"], req.machine_id)

    # 查租户余额和名称
    from src.saas.db.tenant_db import TenantDB

    tenant = TenantDB.get_by_id(record["tenant_id"])
    credit_balance = float(tenant.get("credit_balance", 0)) if tenant else 0.0
    tenant_name = tenant.get("company_name") if tenant else None

    logger.info(
        f"客户端激活成功 code={code} binding={binding['binding_id']} tenant={record['tenant_id']}"
    )

    return {
        "binding_id": binding["binding_id"],
        "access_token": binding["access_token"],
        "tenant_id": record["tenant_id"],
        "tenant_name": tenant_name,
        "credit_balance": credit_balance,
        "expires_at": binding.get("expires_at"),
    }


# ============== 积分查询 ==============

@router.get("/credits")
async def get_credits(binding: ClientBinding = Depends(_require_binding)):
    """查询租户积分余额 + 客户端消耗汇总。"""
    balance = float(binding.tenant.get("credit_balance") or 0)
    summary = ClientUsageLogDB.get_tenant_summary(binding.tenant_id)
    return {
        "balance": balance,
        "today_consumed": summary["today"],
        "week_consumed": summary["week"],
        "total_consumed": summary["total"],
    }


@router.get("/credits/detail")
async def get_credits_detail(
    binding: ClientBinding = Depends(_require_binding),
    limit: int = 100,
):
    """查询消耗明细列表（时间 / 消耗积分 / 任务摘要）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT created_at, credit_cost, raw_credit_cost, association_name,
                      stage, status, model, total_tokens, session_id
               FROM client_usage_logs
               WHERE tenant_id = %s AND credit_cost > 0
               ORDER BY created_at DESC
               LIMIT %s""",
            (binding.tenant_id, min(limit, 500)),
        )
        rows = cursor.fetchall()

    return {
        "items": [
            {
                "time": r["created_at"].isoformat() if r.get("created_at") else "",
                "credit_cost": float(r.get("credit_cost") or 0),
                "raw_credit_cost": float(r.get("raw_credit_cost") or 0),
                "association": r.get("association_name") or "",
                "stage": r.get("stage") or "",
                "status": r.get("status") or "",
                "model": r.get("model") or "",
                "tokens": int(r.get("total_tokens") or 0),
            }
            for r in rows
        ],
    }


# ============== LLM 代理（计费 ×10） ==============

@router.post("/llm/chat")
async def llm_chat(
    req: LlmChatRequest,
    binding: ClientBinding = Depends(_require_binding),
):
    """LLM 代理：调用 llm_gateway 并计费（×10 系数扣减租户余额）。

    req.model 传入时走白名单专用网关（如 kimi-k3 → moonshot），未传走全局默认网关。
    """
    _check_credit(binding)

    gateway = _get_model_gateway(req.model) if req.model else llm_gateway

    try:
        # 协会客户端场景=信息收集小任务（JSON 抽取/解析），统一关思考：
        # CLI 侧 ProxyLLMGateway 不透传 thinking 参数，开关只能在服务端端点定；
        # 思考会显著拉长耗时并多扣积分 token，且烧穿小 max_tokens 致 content 为空。
        # 注意：req.model 命中白名单时 gateway 是模型专用网关（如 kimi-k3→moonshot），
        # 否则为全局默认网关。
        response = await gateway.chat(
            messages=req.messages,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            response_format=req.response_format,
            enable_thinking=False,
        )
    except Exception as e:
        logger.opt(exception=True).error(f"客户端LLM代理调用失败 binding={binding.binding_id}: {e}")
        raise HTTPException(status_code=502, detail=f"LLM_PROVIDER_ERROR: {type(e).__name__}")

    usage = response.get("usage") or {}
    # failover 切到备用 provider 时，计费必须按实际响应的模型计价：qwen 的
    # parse_response 带 "model" 字段，优先取；deepseek 不带则回退主 provider 名
    # （计费金额按 model 查价目表，记错模型 = 记错单价）
    model = response.get("model") or gateway.get_model_name()
    provider = response.get("provider") or gateway.get_provider_name()

    # 计费 ×10 同事务扣减
    billing = ClientUsageLogDB.record_llm_usage(
        tenant_id=binding.tenant_id,
        binding_id=binding.binding_id,
        model=model,
        provider=provider,
        usage=usage,
        association_name=req.association,
        stage=req.purpose or "llm",
    )

    return {
        "content": response.get("content", ""),
        "model": model,
        "provider": provider,
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "cached_tokens": usage.get("cached_tokens", usage.get("cached_input_tokens", 0)),
            "total_tokens": usage.get("total_tokens", 0),
        },
        "billing": {
            "raw_credit_cost": billing["raw_credit_cost"],
            "credit_cost": billing["credit_cost"],
            "balance_after": billing["balance_after"],
        },
    }


# ============== OCR 代理 ==============

@router.post("/ocr/parse")
async def ocr_parse(
    image: UploadFile = File(...),
    binding: ClientBinding = Depends(_require_binding),
):
    """OCR 代理：调用 PaddleOCR，不扣费但记录调用次数。"""
    content = await image.read()
    max_size = 10 * 1024 * 1024  # 10MB
    if len(content) > max_size:
        raise HTTPException(status_code=413, detail="IMAGE_TOO_LARGE")

    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(content)
            tmp_path = f.name

        from src.tools.ocr.ocr_tool import paddleocr_doc_parsing

        result = paddleocr_doc_parsing(file_path=tmp_path, file_type=1)
        text = result.get("text", "") if isinstance(result, dict) else str(result)

        ClientUsageLogDB.record_non_llm_usage(
            tenant_id=binding.tenant_id,
            binding_id=binding.binding_id,
            stage="ocr",
            status="success",
        )

        return {"text": text, "image_count": 1}
    except Exception as e:
        logger.opt(exception=True).error(f"客户端OCR代理失败 binding={binding.binding_id}: {e}")
        ClientUsageLogDB.record_non_llm_usage(
            tenant_id=binding.tenant_id,
            binding_id=binding.binding_id,
            stage="ocr",
            status="failed",
            detail={"error": type(e).__name__},
        )
        raise HTTPException(status_code=502, detail=f"OCR_FAILED: {type(e).__name__}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


# ============== 日志上报 ==============

@router.post("/logs")
async def upload_logs(
    req: LogBatchRequest,
    binding: ClientBinding = Depends(_require_binding),
):
    """批量上报客户端运行日志（记录到 client_usage_logs 的 detail 字段）。"""
    accepted = 0
    for entry in req.logs:
        # 遥测是批量 flush 的，created_at 用事件自带时间戳（真机对账教训：
        # 用 flush 时刻会让整批行同一秒，时间线失真）；解析失败回退 DB 当前时间
        event_time = None
        if entry.timestamp:
            try:
                event_time = datetime.fromisoformat(entry.timestamp)
            except (TypeError, ValueError):
                event_time = None
        ClientUsageLogDB.record_non_llm_usage(
            tenant_id=binding.tenant_id,
            binding_id=binding.binding_id,
            stage=entry.stage or "log",
            session_id=req.session_id,
            association_name=entry.association_name,
            status=entry.level.lower() if entry.level in ("ERROR", "WARNING") else "success",
            detail={"message": entry.message, "level": entry.level, **(entry.detail or {})},
            created_at=event_time,
        )
        accepted += 1
    return {"accepted": accepted}


# ============== 通用用量上报（C 模式，客户端计费统一接入 P4，设计 §4.4） ==============


class UsageReportItem(BaseModel):
    """单条用量上报：客户端只报事实（命令/参数摘要/次数），金额由服务端价目表计算。"""

    client_ref_id: str = Field(
        ..., min_length=8, max_length=100,
        description="客户端幂等键（UUID 等），租户内唯一；重复上报返回首次结果不重复扣费",
    )
    command: str = Field(..., min_length=1, max_length=100, description="命令名，如 weixin_add_friend")
    kind: Literal["action", "llm", "custom"] = Field("action", description="动作类别")
    quantity: int = Field(1, ge=1, le=1000, description="次数（单价 × quantity 计费）")
    arguments_summary: Optional[dict[str, Any]] = Field(
        None, description="参数摘要（事实存档，进台账 detail.arguments；≤1000 字符截断）",
    )
    session_id: Optional[str] = Field(None, max_length=100, description="客户端本地会话标识（可选）")
    occurred_at: Optional[str] = Field(
        None, max_length=40,
        description="事件真实发生时间 ISO 字符串（仅存档进 detail；计费时间轴以服务端接收时间为准）",
    )
    detail: Optional[dict[str, Any]] = Field(
        None,
        description="其它补充存档字段（进台账 detail，整体 >1500 字符丢弃；"
                    "command/quantity 等计费事实键以服务端为准，传了也不会生效）",
    )


class UsageReportRequest(BaseModel):
    reports: list[UsageReportItem] = Field(..., min_length=1, max_length=100)

    @model_validator(mode="before")
    @classmethod
    def _wrap_single(cls, data: Any) -> Any:
        """兼容单条上报：直接传单个报告对象等价于 reports:[对象]。"""
        if isinstance(data, dict) and "reports" not in data:
            return {"reports": [data]}
        return data


def _report_unit_price(command: str, client_name: Optional[str]) -> float:
    """价目匹配优先级：client_name:command（同命令按客户端差异化定价）> command > default。"""
    cfg = settings.client_usage_report
    prices = cfg.command_credit_prices
    unit = None
    if client_name:
        unit = prices.get(f"{client_name}:{command}")
    if unit is None:
        unit = prices.get(command, cfg.default_credit_price)
    try:
        return max(0.0, float(unit))
    except (TypeError, ValueError):
        return 0.0


@router.post("/usage/report")
async def report_usage(req: UsageReportRequest, binding: ClientBinding = Depends(_require_binding)):
    """C 模式标准用量上报：本地自主执行的客户端按事实上报，服务端按价目表计费。

    - payload 不含金额字段：金额 = 单价（服务端价目表）× quantity，客户端永远不上报金额
    - client_ref_id 租户内幂等（唯一索引兜底）：重复上报返回首次结果，不重复扣费
    - 批量 ≤100 条逐条处理，单条失败不影响其余（部分成功语义，逐条返回结果）
    - 不做余额阻断：动作已在客户端本地发生，拒绝上报无法撤回，照常落账（余额可为负，
      由充值/提醒机制兜底）
    """
    cfg = settings.client_usage_report
    if not cfg.enabled:
        raise HTTPException(status_code=404, detail="USAGE_REPORT_DISABLED")

    client_name = binding.client_name or "unknown-client"
    results: list[dict[str, Any]] = []
    accepted = 0
    for item in req.reports:
        unit = _report_unit_price(item.command, client_name)
        # Decimal 十进制计价防浮点错收：math.ceil(0.1*3*100)/100 会因二进制表示
        # 多收一分（0.31），价目表是首个管理员可配任意小数单价 × quantity 的矩阵
        cost = float(
            (Decimal(str(unit)) * item.quantity).quantize(
                Decimal("0.01"), rounding=ROUND_CEILING)
        )
        try:
            r = ClientUsageLogDB.record_client_report(
                tenant_id=binding.tenant_id,
                binding_id=binding.binding_id,
                client_name=client_name,
                command=item.command,
                kind=item.kind,
                quantity=item.quantity,
                credit_cost=cost,
                client_ref_id=item.client_ref_id,
                session_id=item.session_id,
                arguments=item.arguments_summary,
                occurred_at=item.occurred_at,
                extra_detail=item.detail,
            )
            results.append({
                "client_ref_id": item.client_ref_id,
                "success": True,
                "duplicate": r["duplicate"],
                "credit_cost": r["credit_cost"],
                "balance_after": r["balance_after"],
            })
            accepted += 1
        except Exception as e:  # noqa: BLE001 单条失败隔离，不拖垮批次其余条目
            logger.opt(exception=True).error(
                f"后端日志：客户端上报计费落账失败 client={client_name} "
                f"command={item.command} ref={item.client_ref_id}: {e}"
            )
            results.append({
                "client_ref_id": item.client_ref_id,
                "success": False,
                "error": "RECORD_FAILED",
            })
    return {
        "success": accepted == len(results),
        "accepted": accepted,
        "failed": len(results) - accepted,
        "results": results,
    }
