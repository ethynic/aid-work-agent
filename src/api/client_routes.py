"""
协会客户端对外 API（客户端 CLI 调用）。

设计文档：docs/tools/association-client-design.md §2.2
路由前缀：/api/client/v1
- POST /activate      激活码激活（无需鉴权）
- GET  /credits       积分余额查询
- POST /llm/chat      LLM 代理（计费 ×10；可选 model 白名单路由，如 kimi-k3 视觉模型）
- POST /ocr/parse     OCR 代理（不扣费，记录调用）
- POST /logs          日志上报
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel

from src.api.client_auth import ClientBinding, get_client_token_from_header, verify_client_token
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
        response = await gateway.chat(
            messages=req.messages,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            response_format=req.response_format,
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
