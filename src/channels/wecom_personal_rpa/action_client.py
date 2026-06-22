"""企业微信个人账号 RPA 出站动作投递

在线客户端：通过 ``connection.ClientConnectionRegistry`` 直接推送 ``ActionEnvelope``。
离线客户端：序列化 actions 后调用 ``db.enqueue_action`` 写入 ``wecom_rpa_action_outbox``，
            由客户端后续拉取执行。

任何一步失败只记录 ``logger.error`` 与审计，不向上抛出（投递是尽力而为，
调用方在 send_message 已成功返回 True 后不再处理投递异常）。

签名契约：docs/system/wecom-personal-rpa-protocol.md §B.4。
"""
import json
from typing import Any, Dict, List

from loguru import logger

from src.channels.wecom_personal_rpa import db
from src.channels.wecom_personal_rpa.connection import client_connection_registry
from src.channels.wecom_personal_rpa.schemas import ActionEnvelope

# 文本消息单段最大字符数（与 adapter 拆分阈值保持一致）
_TEXT_SEGMENT_MAX = 2000


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
) -> None:
    """投递出站动作信封。

    Args:
        tenant_id: 租户 ID。
        account_id: 目标个人企微账号 ID。
        conversation_id: 客户端侧会话标识，用于定位企微会话窗口。
        request_id: 本次下发的请求 ID（与 RpaActionResultPayload.request_id 对应）。
        session_id: 服务端会话 ID（``wecom_personal_rpa:{account_id}:{route_key}``）。
        actions: ``RpaAction`` 列表（dict 或 pydantic 实例）。

    在线：通过 ``client_connection_registry.send`` 推送 ``ActionEnvelope``。
    离线：``db.enqueue_action`` 写 outbox，``dedup_key=wecom_personal_rpa:{tenant_id}:{request_id}``。
    账号不存在：记 ``logger.error`` 并写审计后返回（不抛）。
    """
    # 1. 解析 account → client_id（决定在线/离线路径）
    account = db.get_account(account_id)
    if not account:
        logger.error(
            f"RPA deliver 失败：账号不存在 account_id={account_id} "
            f"request_id={request_id}"
        )
        db.write_audit(
            tenant_id=tenant_id,
            client_id=None,
            account_id=account_id,
            category="action_deliver",
            payload_json=json.dumps(
                {
                    "request_id": request_id,
                    "conversation_id": conversation_id,
                    "session_id": session_id,
                    "error": "account_not_found",
                },
                ensure_ascii=False,
            ),
        )
        return

    client_id = account.get("client_id")

    # 2. 序列化 actions 与构造信封
    actions_serialized = [_serialize_action(a) for a in (actions or [])]
    envelope = ActionEnvelope(
        request_id=request_id,
        session_id=session_id,
        account_id=account_id,
        conversation_id=conversation_id,
        actions=actions_serialized,
    )
    envelope_dict = envelope.model_dump()

    # 3. 在线 → 直接推送
    if client_id and client_connection_registry.is_online(client_id):
        try:
            await client_connection_registry.send(client_id, envelope_dict)
            return
        except Exception as e:
            # 推送失败：降级走离线入队，保证不丢
            logger.error(
                f"RPA 在线推送失败，降级入队 client_id={client_id} "
                f"request_id={request_id}: {e}"
            )
            # 继续走离线分支

    # 4. 离线 → 写 outbox
    dedup_key = f"wecom_personal_rpa:{tenant_id}:{request_id}"
    try:
        db.enqueue_action(
            tenant_id=tenant_id,
            account_id=account_id,
            conversation_id=conversation_id,
            request_id=request_id,
            session_id=session_id,
            actions_json=json.dumps(actions_serialized, ensure_ascii=False),
            dedup_key=dedup_key,
        )
    except Exception as e:
        # 入队失败不抛：send_message 已判定逻辑成功，投递失败仅记录
        logger.error(
            f"RPA outbox 入队失败 account_id={account_id} "
            f"request_id={request_id}: {e}"
        )
