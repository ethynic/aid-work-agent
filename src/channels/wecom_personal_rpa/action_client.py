"""企业微信个人账号 RPA 出站动作投递

所有动作先幂等写入数据库 outbox；在线连接可在入库后收到完整信封兼容直推，
可靠性仍由客户端轮询权威 outbox 保证。

任何一步失败只记录 ``logger.error`` 与审计，不向上抛出（投递是尽力而为，
调用方在 send_message 已成功返回 True 后不再处理投递异常）。

签名契约：docs/system/wecom-personal-rpa-protocol.md §B.4。
"""
import json
from typing import Any, Dict, List, Optional

from loguru import logger

from src.channels.wecom_personal_rpa import db
from src.channels.wecom_personal_rpa.connection import client_connection_registry
from src.channels.wecom_personal_rpa.schemas import ActionEnvelope
from src.channels.wecom_personal_rpa.archive import audit as archive_audit

# 文本消息单段最大字符数（与 adapter 拆分阈值保持一致）
_TEXT_SEGMENT_MAX = 2000


def _build_reply_digests(actions: List[Dict[str, Any]]) -> List[str]:
    """按实际发送 action 生成可与单条存档消息关联的 HMAC 摘要。"""
    digests: List[str] = []
    for action in actions:
        action_type = str(action.get("type") or "")
        value: Optional[str] = None
        if action_type == "send_text":
            value = str(action.get("text") or "")
        elif action_type in ("send_image", "send_file"):
            value = str(action.get("filename") or "")
        if value:
            digest = archive_audit.digest_reply_component(action_type, value)
            if digest not in digests:
                digests.append(digest)
    return digests


def _serialize_action(action: Any) -> Dict[str, Any]:
    """把单个 RpaAction（pydantic 实例或 dict）序列化为可 JSON 化的 dict。"""
    if isinstance(action, dict):
        return action
    if hasattr(action, "model_dump"):
        return action.model_dump()
    if hasattr(action, "dict"):
        return action.dict()
    # 兜底：尝试 vars()，失败则原样返回让下游报错
    return dict(vars(action)) if hasattr(action, "__dict__") else {"value": str(action)}


async def deliver_actions(
    tenant_id: str,
    account_id: str,
    conversation_id: str,
    request_id: str,
    session_id: str,
    actions: List[Any],
    reply_context: Optional[Dict[str, Any]] = None,
) -> bool:
    """投递出站动作信封。

    Args:
        tenant_id: 租户 ID。
        account_id: 目标个人企微账号 ID。
        conversation_id: 客户端侧会话标识，用于定位企微会话窗口。
        request_id: 本次下发的请求 ID（与 RpaActionResultPayload.request_id 对应）。
        session_id: 服务端会话 ID（``wecom_personal_rpa:{account_id}:{route_key}``）。
        actions: ``RpaAction`` 列表（dict 或 pydantic 实例）。

    始终先写 outbox；在线时再尽力兼容直推完整 ``ActionEnvelope``。
    账号不存在：记 ``logger.error`` 并写审计后返回（不抛）。
    """
    # 1. 解析 account → client_id（决定在线/离线路径）
    account = db.get_account_for_tenant(tenant_id, account_id)
    if not account:
        logger.error(
            f"RPA deliver 失败：账号不存在 account_id={account_id} "
            f"request_id={request_id}"
        )
        try:
            db.write_audit(
                tenant_id=tenant_id,
                client_id=None,
                account_id=account_id,
                category="action_deliver",
                # conversation/session 可能包含企微 userid，账号缺失时不写明文审计。
                payload_json=json.dumps(
                    {"request_id": request_id, "error": "account_not_found"},
                    ensure_ascii=False,
                ),
            )
        except Exception as exc:
            logger.warning(
                f"RPA deliver 账号缺失审计降级 account_id={account_id}: {type(exc).__name__}"
            )
        return False

    client_id = account.get("client_id")

    # 2. 序列化 actions 与构造信封
    actions_serialized = [_serialize_action(a) for a in (actions or [])]
    self_ids = {
        str(value).strip()
        for value in [account.get("wecom_user_id"), *(account.get("wecom_user_aliases") or [])]
        if value and str(value).strip()
    }
    if str(conversation_id).startswith("dm:"):
        target_value = (reply_context or {}).get("sender_stable_id") or str(conversation_id)[3:]
    else:
        target_value = conversation_id
    target_peer_id = str(target_value or "").strip()
    sender_target = str((reply_context or {}).get("sender_stable_id") or "").strip()
    if not self_ids or not target_peer_id or target_peer_id in self_ids or sender_target in self_ids:
        reason = (
            "self_identity_missing" if not self_ids
            else "target_missing" if not target_peer_id
            else "target_is_self"
        )
        if reason == "target_is_self":
            try:
                from src.saas.api.wecom_personal_rpa_routes import _record_self_echo_escape

                _record_self_echo_escape(tenant_id, account_id, request_id)
            except Exception as exc:
                logger.warning(
                    f"RPA dangerous target 熔断计数降级 account_id={account_id}: {type(exc).__name__}"
                )
        try:
            db.write_audit(
                tenant_id=tenant_id,
                client_id=client_id,
                account_id=account_id,
                category="self_echo_escaped" if reason == "target_is_self" else "action_target_rejected",
                payload_json=json.dumps(
                    {"request_id": request_id, "reason": reason}, ensure_ascii=False
                ),
            )
        except Exception as exc:
            logger.warning(
                f"RPA deliver 安全拒绝审计降级 account_id={account_id}: {type(exc).__name__}"
            )
        logger.error(
            f"RPA deliver 安全拒绝 account_id={account_id} request_id={request_id} reason={reason}"
        )
        return False
    # actions 是客户端真实执行权威源。逐 action 摘要可匹配企微存档拆分后的单条
    # 文本/附件；不依赖可缺失的 agent_reply_text，也不额外保存正文。
    reply_digests = _build_reply_digests(actions_serialized)
    reply_digest = archive_audit.digest_reply_text(
        json.dumps(actions_serialized, ensure_ascii=False, sort_keys=True)
    )
    # 3. outbox 是唯一权威源：无论本 worker 是否持有连接都先入队
    dedup_key = f"wecom_personal_rpa:{tenant_id}:{request_id}"
    try:
        queued = db.enqueue_action(
            tenant_id=tenant_id,
            account_id=account_id,
            conversation_id=conversation_id,
            request_id=request_id,
            session_id=session_id,
            actions_json=json.dumps(actions_serialized, ensure_ascii=False),
            reply_context_json=json.dumps(reply_context, ensure_ascii=False) if reply_context else None,
            dedup_key=dedup_key,
            target_peer_id=target_peer_id,
            reply_digest=reply_digest,
            reply_digests=reply_digests,
        )
        if queued is None:
            return False
    except Exception as e:
        # 入队失败不抛：send_message 已判定逻辑成功，投递失败仅记录
        logger.error(
            f"RPA outbox 入队失败 account_id={account_id} "
            f"request_id={request_id}: {e}"
        )
        return False

    # 4. 滚动升级兼容：当前 worker 持有连接时直推完整信封，让尚未实现 GET
    # /outbox 的旧客户端继续工作。新客户端按 request_id + action_index 本地幂等；
    # 跨 worker 看不到连接或发送失败时，由权威 outbox 的 5 秒轮询兜底。
    if client_id and client_connection_registry.is_online(client_id):
        envelope = ActionEnvelope(
            request_id=request_id,
            session_id=session_id,
            account_id=account_id,
            conversation_id=conversation_id,
            reply_context=reply_context,
            actions=actions_serialized,
        ).model_dump()
        envelope["type"] = "actions"
        try:
            await client_connection_registry.send(client_id, envelope)
        except Exception as e:
            logger.info(
                f"RPA outbox 兼容直推失败 client_id={client_id} "
                f"request_id={request_id}: {type(e).__name__}"
            )
    return True
