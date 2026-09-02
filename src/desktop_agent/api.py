"""Additive Desktop D1 API. Existing Web/channel routers are intentionally untouched."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Annotated, Literal, Union

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth import get_current_user
from src.config.settings import settings
from src.core.agent import master_agent

from .gateway import GatewayError, PostgresGatewayStore, RemoteToolGateway
from .security import AuthorizationTicketError, AuthorizationTicketSigner
from .turn import AgentTurnService, ExistingAgentBackend, PostgresTurnStore

router = APIRouter(prefix="/api/desktop/v1", tags=["desktop-agent-d1"])
_gateway: RemoteToolGateway | None = None
_turn_service: AgentTurnService | None = None


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


NonEmptyString = Annotated[str, Field(min_length=1)]
ProtocolVersionString = Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+$")]


class CorrelationRequest(_StrictRequest):
    task_id: NonEmptyString; session_ref: NonEmptyString; execution_id: NonEmptyString; attempt_id: NonEmptyString
    action_id: NonEmptyString; invocation_id: NonEmptyString; artifact_id: NonEmptyString; evidence_stream_id: NonEmptyString
    release_id: NonEmptyString; policy_decision_id: NonEmptyString


class UserMessageInput(_StrictRequest):
    type: Literal["user_message"]
    content: str = Field(min_length=1)


class ToolResultInput(_StrictRequest):
    type: Literal["tool_result"]
    invocation_id: str = Field(min_length=1)
    result: dict[str, Any]


TurnInput = Annotated[Union[UserMessageInput, ToolResultInput], Field(discriminator="type")]


class AgentNextRequest(_StrictRequest):
    supported_protocol_versions: list[ProtocolVersionString] = Field(min_length=1)
    idempotency_key: str = Field(min_length=8)
    correlation: CorrelationRequest
    input: TurnInput


class RemoteToolInvokeRequest(_StrictRequest):
    supported_protocol_versions: list[ProtocolVersionString] = Field(min_length=1)
    idempotency_key: str = Field(min_length=8)
    correlation: CorrelationRequest
    tool_name: str = Field(min_length=1)
    target: Literal["server"]
    schema_version: str = Field(min_length=1)
    schema_digest: str = Field(min_length=1)
    arguments: dict[str, Any]
    policy_revision: str = Field(min_length=1)
    authorization_ticket: str = Field(min_length=1)


async def _identity(request: Request) -> tuple[str, str]:
    user = await asyncio.to_thread(get_current_user, request)
    if not user: raise HTTPException(status_code=401, detail="Authentication required")
    state_tenant_id = getattr(request.state, "tenant_id", None)
    user_tenant_id = user.get("tenant_id")
    role = str(user.get("role") or "")
    if state_tenant_id and user_tenant_id and str(state_tenant_id) != str(user_tenant_id) and role != "platform_admin":
        raise HTTPException(status_code=403, detail="Cross-tenant Desktop access is not permitted")
    tenant_id = state_tenant_id or user_tenant_id
    user_id = user.get("user_id") or user.get("id")
    if not tenant_id or not user_id: raise HTTPException(status_code=403, detail="Tenant identity required")
    if user.get("status") not in (None, "active"):
        raise HTTPException(status_code=403, detail="User account is not active")
    from src.saas.db.tenant_db import TenantDB
    tenant = await asyncio.to_thread(TenantDB.get_by_id, str(tenant_id))
    if not tenant or tenant.get("status") != "active":
        raise HTTPException(status_code=403, detail="Tenant is not active")
    return str(tenant_id), str(user_id)


def _services() -> tuple[RemoteToolGateway, AgentTurnService]:
    global _gateway, _turn_service
    cfg = settings.desktop_agent
    if not cfg.enabled: raise HTTPException(status_code=503, detail="Desktop Agent D1 is disabled")
    if _gateway is None:
        try:
            signer = AuthorizationTicketSigner(cfg.authorization_ticket_secret, cfg.authorization_ticket_ttl_seconds)
        except AuthorizationTicketError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        _gateway = RemoteToolGateway(master_agent.tool_registry, master_agent.tool_executor, signer, set(cfg.allowed_remote_tools), PostgresGatewayStore())
        _turn_service = AgentTurnService(ExistingAgentBackend(set(cfg.allowed_remote_tools)), _gateway, cfg.policy_revision, PostgresTurnStore())
    return _gateway, _turn_service


def _translate(exc: Exception) -> HTTPException:
    if "compatible" in str(exc).lower(): return HTTPException(status_code=426, detail=str(exc))
    if "Idempotency" in str(exc): return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, AuthorizationTicketError): return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.post("/agent/next")
async def agent_next(request: Request, body: AgentNextRequest):
    tenant_id, user_id = await _identity(request)
    _, service = _services()
    try: return await service.next(body.model_dump(), tenant_id, user_id)
    except (KeyError, TypeError, ValueError) as exc: raise _translate(exc) from exc


@router.get("/tools/catalog")
async def tool_catalog(request: Request):
    await _identity(request)
    gateway, _ = _services()
    return {"protocol_version": "1.0", "tools": gateway.catalog()}


@router.post("/tools/invoke")
async def tool_invoke(request: Request, body: RemoteToolInvokeRequest):
    tenant_id, user_id = await _identity(request)
    gateway, _ = _services()
    try: return await gateway.invoke(body.model_dump(), tenant_id, user_id)
    except (KeyError, TypeError, ValueError, GatewayError, AuthorizationTicketError) as exc: raise _translate(exc) from exc


@router.get("/tools/invocations/{idempotency_key}/events")
async def tool_events(request: Request, idempotency_key: str, after: int = 0):
    tenant_id, user_id = await _identity(request)
    gateway, _ = _services()
    try: return {"events": gateway.events(tenant_id, idempotency_key, user_id, max(0, after))}
    except GatewayError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/tools/invocations/{idempotency_key}/cancel")
async def tool_cancel(request: Request, idempotency_key: str):
    tenant_id, user_id = await _identity(request)
    gateway, _ = _services()
    try: return gateway.cancel(tenant_id, idempotency_key, user_id)
    except GatewayError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/tools/invocations/{idempotency_key}/events/stream")
async def tool_events_stream(request: Request, idempotency_key: str, after: int = 0):
    tenant_id, user_id = await _identity(request)
    gateway, _ = _services()
    try: events = gateway.events(tenant_id, idempotency_key, user_id, max(0, after))
    except GatewayError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def replay():
        for event in events:
            if await request.is_disconnected(): return
            yield f"id: {event['seq']}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(replay(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
