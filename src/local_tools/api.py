"""本地工具 API：Web 用户 API + Runtime API

- Web 用户 API：get_current_user + request.state.tenant_id（TenantContextMiddleware 解析）
- Runtime API：设备 Bearer token 自认证（_require_device 依赖），所有隔离键取 device.tenant_id，
  不信任何请求体里的 tenant/user

所有同步 DB 调用一律 asyncio.to_thread 包裹，不阻塞事件循环。
"""

import asyncio
import json
import uuid as _uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from loguru import logger

from src.api.auth import get_current_user
from src.db.client_binding_db import ClientUsageLogDB
from src.desktop_automation import audit, payload_resolver
from src.desktop_automation.adapters import AdapterNotFoundError
from src.desktop_automation.constants import BUSINESS_KIND_DESKTOP_AUTOMATION, OPERATION_PROTOCOL_V2
from src.local_tools import catalog, operation_result, pairing, permits, repository
from src.local_tools.models import (
    HeartbeatRequest,
    OperationResultRequest,
    PairRequest,
    ProgressRequest,
    ResultRequest,
    ResumeEvaluateRequest,
    StartedRequest,
    WriteAuthorizeRequest,
)
from src.local_tools.pricing import RESUME_RECOGNITION_TOOL_NAME, resume_recognition_price
from src.services import recruiting_match_service, resume_vl_service
from src.local_tools.security import generate_claim_token, sha256_hex
from src.utils import sanitize_error_info

router = APIRouter(prefix="/api/local-tools", tags=["local-tools"])

LEASE_SECONDS = 60          # claim 租约时长
CLAIM_POLL_INTERVAL = 0.5   # 长轮询间隔（秒）
CLAIM_MAX_WAIT = 30         # 长轮询最长等待（秒）
ONLINE_THRESHOLD_SECONDS = 30  # last_seen_at 距今 ≤30s 视为在线
PROGRESS_MESSAGE_MAX_LEN = 500


def _http_error(status_code: int, error: str, exc: Optional[Exception] = None) -> HTTPException:
    """统一错误响应：{error, debug(sanitize 后)}"""
    detail: Dict[str, Any] = {"error": error}
    if exc is not None:
        detail["debug"] = sanitize_error_info(str(exc))
    return HTTPException(status_code=status_code, detail=detail)


async def _current_user_and_tenant(request: Request) -> tuple:
    """Web API 鉴权：用户 token + middleware 解析的租户上下文"""
    user = await asyncio.to_thread(get_current_user, request)
    if not user:
        raise _http_error(401, "未登录或登录已过期")
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise _http_error(400, "缺少租户上下文（tenant_id）")
    return user, tenant_id


def _require_device(request: Request) -> Dict[str, Any]:
    """Runtime API 鉴权（同步依赖，FastAPI 在线程池执行）：设备 token → active 设备"""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise _http_error(401, "缺少设备 token")
    device = repository.get_device_by_token_hash(sha256_hex(auth_header[7:]))
    if not device:
        raise _http_error(401, "设备 token 无效或设备已撤销")
    return device


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)


def _valid_uuid(value: str) -> bool:
    """路径参数 UUID 形态预检：非法形态直接按 404 处理（P2-6），避免 DB 端 DataError 500"""
    try:
        _uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


# ==================== Web 用户 API ====================


@router.post("/pairing-tickets")
async def create_pairing_ticket(request: Request):
    """创建一次性配对码（明文仅此一次返回，5 分钟有效）"""
    user, tenant_id = await _current_user_and_tenant(request)
    try:
        result = await asyncio.to_thread(
            pairing.create_pairing_ticket, tenant_id, user["user_id"]
        )
        return {"success": True, "code": result["code"], "expires_at": _iso(result["expires_at"])}
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：创建配对码失败: {e}")
        raise _http_error(500, "创建配对码失败，请稍后重试", e)


@router.get("/devices")
async def list_devices(request: Request):
    """当前 tenant+user 设备列表（含在线状态与选定标记）"""
    user, tenant_id = await _current_user_and_tenant(request)
    try:
        devices = await asyncio.to_thread(repository.list_devices, tenant_id, user["user_id"])
        now = datetime.now()
        items = []
        for d in devices:
            last_seen = d.get("last_seen_at")
            online = bool(last_seen and (now - last_seen).total_seconds() <= ONLINE_THRESHOLD_SECONDS)
            items.append(
                {
                    "device_id": str(d["id"]),
                    "name": d.get("name"),
                    "platform": d.get("platform"),
                    "runtime_version": d.get("runtime_version"),
                    # 设计 §0：不向 Web 暴露原始 capability payload（设备 token/CLI 路径同理不下发）
                    "selected": bool(d.get("selected")),
                    "status": d.get("status"),
                    "online": online,
                    "last_seen_at": _iso(last_seen),
                    "created_at": _iso(d.get("created_at")),
                }
            )
        return {"success": True, "devices": items}
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：查询设备列表失败: {e}")
        raise _http_error(500, "查询设备列表失败，请稍后重试", e)


@router.post("/devices/{device_id}/select")
async def select_device(device_id: str, request: Request):
    """选定当前设备（单选，校验归属与 active）"""
    user, tenant_id = await _current_user_and_tenant(request)
    try:
        ok = await asyncio.to_thread(
            repository.select_device, tenant_id, user["user_id"], device_id
        )
        if not ok:
            raise _http_error(404, "设备不存在、无权限或已撤销")
        return {"success": True, "device_id": device_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：选定设备失败 device={device_id}: {e}")
        raise _http_error(500, "选定设备失败，请稍后重试", e)


@router.delete("/devices/{device_id}")
async def revoke_device(device_id: str, request: Request):
    """撤销设备（token 立即失效，selected 清除）"""
    user, tenant_id = await _current_user_and_tenant(request)
    try:
        ok = await asyncio.to_thread(
            repository.revoke_device, tenant_id, user["user_id"], device_id
        )
        if not ok:
            raise _http_error(404, "设备不存在、无权限或已撤销")
        logger.info(f"后端日志：撤销本地工具设备 device={device_id} tenant={tenant_id}")
        return {"success": True, "device_id": device_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：撤销设备失败 device={device_id}: {e}")
        raise _http_error(500, "撤销设备失败，请稍后重试", e)


# ==================== Runtime API ====================


@router.post("/runtime/pair")
async def runtime_pair(body: PairRequest):
    """配对码换设备 + 设备 token（均无 header；token 明文仅此一次返回）"""
    try:
        result = await asyncio.to_thread(
            pairing.pair,
            body.code,
            body.name,
            body.platform,
            body.runtime_version,
            body.capabilities,
            body.machine_fingerprint,
        )
        if not result:
            raise _http_error(400, "配对码无效、已过期或已被使用")
        return {"success": True, **result}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：设备配对失败: {e}")
        raise _http_error(500, "设备配对失败，请稍后重试", e)


@router.post("/runtime/heartbeat")
async def runtime_heartbeat(body: HeartbeatRequest, device: Dict = Depends(_require_device)):
    """心跳：更新 last_seen / 版本 / 能力 / manifest 摘要"""
    try:
        row = await asyncio.to_thread(
            repository.touch_device_seen,
            str(device["id"]),
            body.runtime_version,
            body.capabilities,
            body.manifest_digest,
        )
        if not row:
            raise _http_error(404, "设备不存在")
        return {
            "success": True,
            "selected": bool(row.get("selected")),
            "server_time": datetime.now().isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：设备心跳失败 device={device['id']}: {e}")
        raise _http_error(500, "心跳失败，请稍后重试", e)


@router.post("/runtime/claim")
async def runtime_claim(wait: int = 20, device: Dict = Depends(_require_device)):
    """长轮询领取 invocation（claim_token 明文仅此一次返回）。
    超时返回 {"success": true, "invocation": null}（200，简化客户端）。

    provider 语义（契约补充）：响应 provider = invocation 行 provider_key；
    旧行 NULL 时回退设备 capabilities 解析的默认 provider。领取过滤：
    行 provider_key NULL 任意设备可领（旧行为），非 NULL 须设备能力包含该 provider。
    """
    device_id = str(device["id"])
    tenant_id = device["tenant_id"]
    default_provider_key = catalog.get_provider_key_for_device(device.get("capabilities_json"))
    provider_keys = catalog.get_provider_keys_for_device(device.get("capabilities_json"))
    wait = max(0, min(wait, CLAIM_MAX_WAIT))

    try:
        # 顺带清理租约过期的 claimed/running（MVP 无后台任务）
        await asyncio.to_thread(repository.expire_stale_claims)

        deadline = asyncio.get_event_loop().time() + wait
        while True:
            claim_token = generate_claim_token()
            invocation = await asyncio.to_thread(
                repository.claim_next,
                device_id,
                tenant_id,
                sha256_hex(claim_token),
                LEASE_SECONDS,
                provider_keys,
            )
            if invocation:
                # 校验 tool_name 在该 invocation Provider（行 provider_key 优先）批准的 tools 内
                invocation_provider = invocation.get("provider_key") or default_provider_key
                if not catalog.is_tool_allowed(invocation_provider, invocation["tool_name"]):
                    logger.warning(
                        f"后端日志：invocation {invocation['id']} 工具 "
                        f"{invocation['tool_name']} 不在设备 Provider 批准清单内，置为失败"
                    )
                    await asyncio.to_thread(
                        repository.write_result,
                        str(invocation["id"]),
                        tenant_id,
                        sha256_hex(claim_token),
                        False,
                        "TOOL_NOT_ALLOWED",
                        "工具不在设备 Provider 批准清单内",
                    )
                else:
                    logger.info(
                        f"后端日志：invocation {invocation['id']} 被设备 {device_id} 领取 "
                        f"tool={invocation['tool_name']} provider={invocation_provider}"
                    )
                    return {
                        "success": True,
                        "invocation_id": str(invocation["id"]),
                        "tool_name": invocation["tool_name"],
                        "arguments": invocation["arguments_json"],
                        "claim_token": claim_token,
                        "lease_expires_at": _iso(invocation["lease_expires_at"]),
                        "provider": invocation_provider,
                    }
            if asyncio.get_event_loop().time() >= deadline:
                return {"success": True, "invocation": None}
            await asyncio.sleep(CLAIM_POLL_INTERVAL)
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：设备领取 invocation 失败 device={device_id}: {e}")
        raise _http_error(500, "领取任务失败，请稍后重试", e)


@router.post("/runtime/invocations/{invocation_id}/started")
async def runtime_started(
    invocation_id: str, body: StartedRequest, device: Dict = Depends(_require_device)
):
    """claimed → running（幂等：重复调用返回当前状态）"""
    tenant_id = device["tenant_id"]
    try:
        row = await asyncio.to_thread(
            repository.mark_started, invocation_id, tenant_id, sha256_hex(body.claim_token)
        )
        if not row:
            raise _http_error(404, "invocation 不存在或 claim token 不匹配")
        if row["state"] != "running":
            raise _http_error(409, f"当前状态不允许标记开始: {row['state']}")
        return {"success": True, "state": row["state"]}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：标记 invocation 开始失败 id={invocation_id}: {e}")
        raise _http_error(500, "标记开始失败，请稍后重试", e)


@router.post("/runtime/invocations/{invocation_id}/progress")
async def runtime_progress(
    invocation_id: str, body: ProgressRequest, device: Dict = Depends(_require_device)
):
    """追加进度事件（seq 递增 + 续租），返回 cancel 标志"""
    tenant_id = device["tenant_id"]
    message = body.message[:PROGRESS_MESSAGE_MAX_LEN] if body.message else None
    try:
        result = await asyncio.to_thread(
            repository.append_event,
            invocation_id,
            tenant_id,
            sha256_hex(body.claim_token),
            body.stage,
            body.current,
            body.total,
            message,
            LEASE_SECONDS,
        )
        if not result:
            raise _http_error(404, "invocation 不存在、claim token 不匹配或状态不允许上报进度")
        seq, cancel = result
        return {"success": True, "seq": seq, "cancel": cancel}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：上报进度失败 id={invocation_id}: {e}")
        raise _http_error(500, "上报进度失败，请稍后重试", e)


@router.post("/runtime/invocations/{invocation_id}/result")
async def runtime_result(
    invocation_id: str, body: ResultRequest, device: Dict = Depends(_require_device)
):
    """写入终态（幂等：已终态重复写返回当前状态）"""
    tenant_id = device["tenant_id"]
    try:
        row = await asyncio.to_thread(
            repository.write_result,
            invocation_id,
            tenant_id,
            sha256_hex(body.claim_token),
            body.success,
            body.code,
            body.message,
            body.effect,
            body.data,
            body.retryable,
        )
        if not row:
            raise _http_error(404, "invocation 不存在或 claim token 不匹配")
        # data_bytes：与客户端「终态已回传 data_bytes=」行比对，定位回传链路丢/裁 data（2026-09-10 排障整改）
        data_bytes = len(json.dumps(body.data).encode("utf-8")) if body.data else 0
        logger.info(
            f"后端日志：invocation {invocation_id} 写入终态 state={row['state']} effect={row['effect']} "
            f"tool={row['tool_name']} data_bytes={data_bytes}"
        )
        return {"success": True, "state": row["state"], "effect": row["effect"]}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：写入 invocation 终态失败 id={invocation_id}: {e}")
        raise _http_error(500, "写入结果失败，请稍后重试", e)


@router.post("/runtime/resume/evaluate")
async def runtime_resume_evaluate(
    body: ResumeEvaluateRequest, device: Dict = Depends(_require_device)
):
    """简历评估接口（2026-09-17 去 OCR 化 v2，boss-resume-vl-recognition-design.md §4.4）：
    拼接长图/分段图 → 云端 GLM 多模态一次评估 → 返回 {name_seen, resume_summary, score,
    match_summary, key_info}，评估成功按份扣简历识别费。

    客户端（boss CLI 等）用设备 token 调用。姓名门（决策⑨）：candidate_name 必填并传入
    VL 提示词，服务端将其与图中姓名（name_seen）确定性比对，不符 422 RESUME_NAME_MISMATCH
    不扣费 + 全量日志——防 runtime 点击错位开错人。模型可显式指定（provider/model，白名单
    校验），缺省 zhipu/GLM-5.3-Flash——VL 绝不随主链路（可能纯文本）漂移。

    计费语义（租户决策 2026-09-17）：截图不扣费，VL 评估成功（姓名门通过）即扣一份
    resume_recognition_price（识别失败不扣）；台账 tool_name=boss_resume_recognition
    与工具层同科目，对账同源。
    """
    tenant_id = device["tenant_id"]
    candidate_name = (body.candidate_name or "").strip()
    if not candidate_name:
        # 姓名门的比较基准，必填（决策⑨）
        raise _http_error(422, "缺少 candidate_name：页面候选人姓名必传（姓名核对用）")
    # 模型白名单校验放切片之前：非法 model + 大图不必白耗一次 Pillow 解码切片
    try:
        provider_name, model = resume_vl_service.resolve_model_spec(body.model)
    except resume_vl_service.ResumeVLModelError as e:
        raise _http_error(422, f"{e}")
    bands = [b for b in (body.images or []) if (b or "").strip()]
    if not bands and (body.image or "").strip():
        # 切片是同步 CPU 活（Pillow 解码 + 裁剪 + PNG 重编码），放线程池不阻塞事件循环
        try:
            bands = await asyncio.to_thread(
                resume_vl_service.slice_stitched_image, body.image
            )
        except resume_vl_service.ResumeVLError as e:
            raise _http_error(422, f"拼接长图切片失败：{e}")
    if not bands:
        raise _http_error(422, "缺少识别入参：请传 image（拼接长图 base64）或 images（分段图列表）")

    # 职位上下文（给了 job_id/job_name 才评分；职位库未命中 → 最小上下文走隐含要求路径，
    # 沿袭「带职位名就评」语义；无 → score/match_summary=null 只出总结）
    job_ctx = None
    if (body.job_id or body.job_name or "").strip():
        job_ctx = await asyncio.to_thread(
            recruiting_match_service._load_job_context, tenant_id, body.job_id, body.job_name
        ) or {"job_name": body.job_name}

    try:
        evaluation = await resume_vl_service.evaluate_resume(
            bands, candidate_name, job_ctx, body.model
        )
    except resume_vl_service.ResumeVLModelError as e:
        raise _http_error(422, f"{e}")
    except resume_vl_service.ResumeVLError as e:
        # 评估失败（含重试后仍失败）：不扣费，fail-loud 让调用方重试
        logger.error(
            f"后端日志：简历评估接口失败 tenant={tenant_id} device={device.get('id')} "
            f"model={provider_name}/{model}: {e}"
        )
        raise _http_error(502, f"简历评估失败（重试后仍失败），本次不扣费：{e}")

    # 姓名门（决策⑨）：不符 422 + 全量日志（name_seen 区分识别误读 vs 真点错人），不扣费
    name_seen = evaluation.get("name_seen") or ""
    if not resume_vl_service.resume_name_matches(candidate_name, name_seen):
        logger.error(
            f"后端日志：简历评估接口姓名核对不匹配（不扣费） tenant={tenant_id} "
            f"device={device.get('id')} candidate_name={candidate_name} name_seen={name_seen} "
            f"bands={len(bands)} model={evaluation.get('model')}"
        )
        raise _http_error(
            422,
            f"姓名核对不匹配（页面候选人「{candidate_name}」与简历所示「{name_seen}」不一致）："
            "疑似图片简历与所传姓名不符，请人工核对",
        )

    # 评估成功（姓名门通过）→ 按份扣简历识别费（弹层自愈同款编排层直记；失败只告警不回滚）
    price = resume_recognition_price()
    billing: Dict[str, Any] = {"credit_cost": 0.0, "balance_after": None}
    if price > 0:
        try:
            usage_row = await asyncio.to_thread(
                ClientUsageLogDB.record_tool_usage,
                tenant_id=tenant_id,
                tool_name=RESUME_RECOGNITION_TOOL_NAME,
                credit_cost=price,
                session_id=None,
                user_id=device.get("user_id"),
                # 无 invocation_id 的直记场景，device_id 是对账唯一线索
                device_id=str(device["id"]) if device.get("id") is not None else None,
            )
            billing = {
                "credit_cost": price,
                "balance_after": (usage_row or {}).get("balance_after"),
            }
        except Exception as e:  # noqa: BLE001 计费失败不影响评估结果返回（台账可对账）
            logger.opt(exception=True).error(
                f"后端日志：简历评估接口计费落账失败（不影响返回）tenant={tenant_id}: {e}"
            )
    logger.info(
        f"后端日志：简历评估接口成功 tenant={tenant_id} device={device.get('id')} "
        f"candidate={candidate_name} name_seen={name_seen} score={evaluation.get('score')} "
        f"model={evaluation.get('model')} cost={billing['credit_cost']}"
    )
    return {
        "success": True,
        "name_seen": name_seen,
        "resume_summary": evaluation.get("resume_summary"),
        "score": evaluation.get("score"),
        "match_summary": evaluation.get("match_summary"),
        "key_info": evaluation.get("key_info"),
        "model": evaluation.get("model"),
        "bands": len(bands),
        "billing": billing,
    }


# ==================== Runtime API（v2 写动作许可 / 结果回传，不暴露给 LLM）====================


@router.post("/runtime/invocations/{invocation_id}/write-authorize")
async def runtime_write_authorize(
    invocation_id: str, body: WriteAuthorizeRequest, device: Dict = Depends(_require_device)
):
    """v2 写动作许可签发（§5.2）：单事务校验 claim/租约/取消/业务类型/截止/场景授权/额度，
    返回 permit_id/permit_token（明文仅此一次）/deadline_at。旧 invocation（business_kind
    非 desktop_automation）返回 409。"""
    tenant_id = device["tenant_id"]
    if not _valid_uuid(invocation_id):
        raise _http_error(404, "invocation 不存在")
    try:
        result = await asyncio.to_thread(
            permits.write_authorize,
            tenant_id=tenant_id,
            device_id=str(device["id"]),
            invocation_id=invocation_id,
            claim_token_hash=sha256_hex(body.claim_token),
            request_id=body.request_id,
            target_version=body.target_version,
            payload_hash=body.payload_hash,
        )
        return {
            "success": True,
            "permit_id": result["permit_id"],
            "permit_token": result["permit_token"],
            "deadline_at": _iso(result["deadline_at"]),
        }
    except permits.PermitError as e:
        logger.warning(
            f"后端日志：写动作许可拒绝 tenant={tenant_id} invocation={invocation_id} "
            f"code={e.code}: {e.message}"
        )
        raise HTTPException(status_code=e.http_status, detail={"error": e.code, "message": e.message})
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(
            f"后端日志：写动作许可签发失败 tenant={tenant_id} invocation={invocation_id}: {e}"
        )
        raise _http_error(500, "许可签发失败，请稍后重试", e)


@router.post("/runtime/invocations/{invocation_id}/operation-result")
async def runtime_operation_result(
    invocation_id: str, body: OperationResultRequest, device: Dict = Depends(_require_device)
):
    """v2 操作结果回传（持久 ACK 幂等；迟到只对账不改判）。effect/phase 优先于
    success/code 解释（R10）；v2 路径默认 0 价计费（R2）。"""
    tenant_id = device["tenant_id"]
    if not _valid_uuid(invocation_id):
        raise _http_error(404, "invocation 不存在")
    try:
        result = await asyncio.to_thread(
            operation_result.apply_operation_result,
            tenant_id=tenant_id,
            device_id=str(device["id"]),
            invocation_id=invocation_id,
            claim_token_hash=sha256_hex(body.claim_token),
            request_id=body.request_id,
            effect=body.effect,
            phase=body.phase,
            evidence_ref=body.evidence_ref,
            safe_to_retry=body.safe_to_retry,
            code=body.code,
            message=body.message,
            permit_id=body.permit_id,
            permit_token=body.permit_token,
        )
        return {
            "success": True,
            "state": result["state"],
            "effect": result["effect"],
            "run_state": result["run_state"],
            "late": result["late"],
        }
    except operation_result.OperationResultError as e:
        logger.warning(
            f"后端日志：v2 结果回传拒绝 tenant={tenant_id} invocation={invocation_id} "
            f"code={e.code}: {e.message}"
        )
        raise HTTPException(status_code=e.http_status, detail={"error": e.code, "message": e.message})
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(
            f"后端日志：v2 结果回传失败 tenant={tenant_id} invocation={invocation_id}: {e}"
        )
        raise _http_error(500, "结果回传失败，请稍后重试", e)


class PayloadEndpointError(Exception):
    """payload 端点拒绝（code 对应 HTTP status；context 供审计补充）"""

    def __init__(self, code: str, http_status: int, message: str, context: Optional[Dict] = None):
        self.code = code
        self.http_status = http_status
        self.message = message
        self.context = context or {}
        super().__init__(f"{code}: {message}")


def _serve_invocation_payload(tenant_id: str, device_id: str, invocation_id: str):
    """payload 端点同步服务（线程池执行）：校验链 + resolver 取受控字节。

    校验链（与 write-authorize permits 同口径排序）：invocation 归属租户/设备（跨租户/
    跨设备统一 404 不区分存在性）→ v2 业务类型 → protocol_version=2 → 已领取未终态
    （claimed/running/cancel_requested，queued 拒绝）→ payload_ref 存在 → 场景绑定
    fail-closed（business_ref 缺 scenario/task 一律 409）→ resolve_payload（hash
    核对，不符 500+审计）。claim token 校验未实现（GET 无 body；对 R17 的明示偏差）。
    """
    inv = repository.get_invocation(invocation_id, tenant_id)
    if inv is None or str(inv["device_id"]) != device_id:
        raise PayloadEndpointError("INVOCATION_NOT_FOUND", 404, "invocation 不存在或不属于当前设备")
    if inv["business_kind"] != BUSINESS_KIND_DESKTOP_AUTOMATION:
        # 与 write-authorize 同口径：仅 v2 桌面自动任务链路（fail-closed）
        raise PayloadEndpointError(
            "NOT_DESKTOP_AUTOMATION", 409, "仅桌面自动任务 v2 invocation 支持载荷下载"
        )
    args = inv["arguments_json"] or {}
    if args.get("protocol_version") != OPERATION_PROTOCOL_V2:
        # 与 write-authorize parity（permits NOT_V2_OPERATION）
        raise PayloadEndpointError("NOT_V2_OPERATION", 409, "非 v2 操作描述，不支持载荷下载")
    if inv["state"] not in payload_resolver.PAYLOAD_ALLOWED_INVOCATION_STATES:
        if inv["state"] == "queued":
            raise PayloadEndpointError(
                "INVOCATION_NOT_CLAIMED", 409, "invocation 尚未被设备领取，拒绝下载载荷"
            )
        raise PayloadEndpointError(
            "INVOCATION_TERMINATED", 409, f"invocation 已终态，拒绝下载载荷: {inv['state']}"
        )
    payload_ref = args.get("payload_ref")
    if not payload_ref or not isinstance(payload_ref, str):
        raise PayloadEndpointError("PAYLOAD_REF_MISSING", 422, "invocation 未携带 payload_ref")
    business_ref = inv["business_ref"] or {}
    try:
        parsed_scenario, _opaque = payload_resolver.parse_payload_ref(payload_ref)
    except payload_resolver.PayloadRefError as e:
        raise PayloadEndpointError("INVALID_PAYLOAD_REF", 422, str(e))
    bound_scenario = business_ref.get("scenario_key") or ""
    bound_task_ref = business_ref.get("task_ref") or ""
    if not bound_scenario or not bound_task_ref:
        # fail-closed（与 write-authorize permits SCENARIO_BINDING_MISSING 同口径）：
        # business_ref 缺 scenario/task 绑定一律 409，不允许跳过场景一致性校验
        raise PayloadEndpointError(
            "SCENARIO_BINDING_MISSING", 409, "invocation 未绑定 scenario/task，拒绝下载载荷"
        )
    if parsed_scenario != bound_scenario:
        raise PayloadEndpointError(
            "PAYLOAD_REF_SCENARIO_MISMATCH", 422, "payload_ref 场景与 invocation 绑定场景不一致"
        )
    try:
        return payload_resolver.resolve_payload(
            tenant_id,
            payload_ref,
            expectation_hash=args.get("payload_hash"),
            user_id=str(inv["user_id"]) if inv.get("user_id") else None,
            task_ref=bound_task_ref,
            revision_ref=str(args.get("authorization_revision") or ""),
        )
    except payload_resolver.PayloadHashMismatch as e:
        raise PayloadEndpointError(
            "PAYLOAD_HASH_MISMATCH", 500, "载荷哈希校验失败，拒绝下发",
            context={
                "payload_ref": payload_ref,
                "expected_hash": e.expected_hash,
                "actual_hash": e.actual_hash,
            },
        ) from e
    except AdapterNotFoundError:
        raise PayloadEndpointError(
            "SCENARIO_NOT_REGISTERED", 500, f"场景 {parsed_scenario} 未注册受信适配器"
        )


@router.get("/runtime/invocations/{invocation_id}/payload")
async def runtime_payload(invocation_id: str, device: Dict = Depends(_require_device)):
    """v2 invocation 载荷下载（R17）：设备 token + invocation 归属设备 + 已领取未终态校验
    （queued 拒绝；claim token 校验未实现——GET 无 body，为对 R17 的明示偏差，见
    payload_resolver.PAYLOAD_ALLOWED_INVOCATION_STATES 注释）；经 TrustedAdapterRegistry
    场景适配器 serve_payload 取受控字节（不接任意 URL/路径），响应带 X-Payload-Hash、
    Content-Type 与 X-Content-Type-Options: nosniff；hash 不符 500 + 审计。"""
    tenant_id = device["tenant_id"]
    if not _valid_uuid(invocation_id):
        raise _http_error(404, "invocation 不存在")
    try:
        resolution = await asyncio.to_thread(
            _serve_invocation_payload, tenant_id, str(device["id"]), invocation_id
        )
    except PayloadEndpointError as e:
        if e.code == "PAYLOAD_HASH_MISMATCH":
            # 审计：字节与授权凭据漂移必须留痕（只记哈希与引用，不写正文）；
            # 同步 DB 调用须 to_thread，不阻塞事件循环
            await asyncio.to_thread(
                audit.log_audit,
                tenant_id, "payload_hash_mismatch", "invocation", invocation_id,
                detail={
                    "payload_ref": e.context.get("payload_ref"),
                    "expected_hash": e.context.get("expected_hash"),
                    "actual_hash": e.context.get("actual_hash"),
                },
            )
        logger.warning(
            f"后端日志：payload 下载拒绝 tenant={tenant_id} invocation={invocation_id} "
            f"code={e.code}: {e.message}"
        )
        raise HTTPException(
            status_code=e.http_status, detail={"error": e.code, "message": e.message}
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(
            f"后端日志：payload 下载失败 tenant={tenant_id} invocation={invocation_id}: {e}"
        )
        raise _http_error(500, "载荷下载失败，请稍后重试", e)
    return Response(
        content=resolution.data,
        media_type=resolution.mime,
        headers={
            "X-Payload-Hash": resolution.payload_hash,
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/runtime/invocations/{invocation_id}/assets/{asset_id}")
async def runtime_invocation_asset(
    invocation_id: str, asset_id: str, device: Dict = Depends(_require_device)
):
    """微信场景 invocation 素材下载（R57 P4-A）：设备 token + invocation 归属设备 +
    已领取未终态 + 场景绑定 fail-closed + revision 绑定资产集校验（素材必须被本
    invocation 绑定 revision 的 image 块引用，不接受任意 asset_id）；受控字节流 +
    X-Asset-Hash，无重定向/无任意 URL；文件 hash 与登记不符 500 + 审计。
    校验链核心在 src/weixin_marketing/assets.serve_invocation_asset（场景域逻辑
    留场景模块，此处只做 transport；懒 import 避免 local_tools → weixin 顶层依赖）。"""
    tenant_id = device["tenant_id"]
    if not _valid_uuid(invocation_id) or not _valid_uuid(asset_id):
        raise _http_error(404, "invocation 或素材不存在")
    try:
        from src.weixin_marketing.assets import AssetEndpointError, serve_invocation_asset

        resolution = await asyncio.to_thread(
            serve_invocation_asset, tenant_id, str(device["id"]), invocation_id, asset_id
        )
    except AssetEndpointError as e:
        if e.code in ("ASSET_HASH_MISMATCH", "ASSET_FILE_MISSING"):
            # 审计：素材字节漂移/失联必须留痕（只记哈希与引用，不写图片内容）
            await asyncio.to_thread(
                audit.log_audit,
                tenant_id, e.code.lower(), "invocation", invocation_id,
                detail={
                    "asset_id": e.context.get("asset_id"),
                    "invocation_id": e.context.get("invocation_id"),
                },
            )
        logger.warning(
            f"后端日志：素材下载拒绝 tenant={tenant_id} invocation={invocation_id} "
            f"asset={asset_id} code={e.code}: {e.message}"
        )
        raise HTTPException(
            status_code=e.http_status, detail={"error": e.code, "message": e.message}
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(
            f"后端日志：素材下载失败 tenant={tenant_id} invocation={invocation_id} "
            f"asset={asset_id}: {e}"
        )
        raise _http_error(500, "素材下载失败，请稍后重试", e)
    return Response(
        content=resolution.data,
        media_type=resolution.mime,
        headers={
            "X-Asset-Hash": resolution.sha256,
            "X-Content-Type-Options": "nosniff",
        },
    )
