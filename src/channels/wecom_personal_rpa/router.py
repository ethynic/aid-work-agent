"""企业微信个人账号 RPA 会话路由与会话绑定授权

职责：
1. ``WeComPersonalRpaRouter.route``：``account_id + conversation_id`` → 稳定
   ``session_id``，格式 ``wecom_personal_rpa:{account_id}:{stable_id or conversation_id}``。
   ``stable_id`` 优先于客户端本地 ``conversation_id``，避免客户端重启后本地会话 ID
   变化导致路由漂移。
2. ``check_conversation_authorization``：基于 ``db.get_or_create_binding`` /
   ``find_binding_by_search_key`` 判定当前会话是否允许自动回复，返回
   ``AuthorizationResult(needs_review / paused / reason)``。

授权判定语义（对齐 docs/system/wecom-personal-rpa-protocol.md §A.8）：
- binding 不存在或 ``status=pending`` → ``needs_review=True``
  （错误码 ``conversation_needs_review``，客户端暂停该会话自动发送）
- ``status=active`` → 放行（``needs_review=False, paused=False``）
- ``status=paused`` / ``status=invalid`` → ``paused=True``
  （错误码 ``account_paused`` 语义，等待人工恢复）
- 同 ``search_key`` 在租户内存在多条绑定（重名歧义）→ ``needs_review=True``，
  ``reason="ambiguous"``，等待人工区分

所有判定结果都携带 ``session_id``，供下游 adapter / action_client 直接复用。
"""

from dataclasses import dataclass
from typing import List, Optional

from loguru import logger

from src.channels.wecom_personal_rpa import db

# session_id 前缀，与 docs/system/wecom-personal-rpa-protocol.md §A.6 对齐
_SESSION_ID_PREFIX = "wecom_personal_rpa"


class WeComPersonalRpaRouter:
    """account_id + conversation_id → session_id 路由器。

    route_key 选择策略：``stable_id`` 优先，回退到客户端本地 ``conversation_id``。
    ``stable_id`` 来自企微稳定 ID（external_userid / userid / room_id），跨客户端
    重启保持不变；``conversation_id`` 是客户端本地窗口标识，可能因重启而变化。
    优先使用 ``stable_id`` 保证同一对方始终路由到同一 session。
    """

    def route(
        self,
        account_id: str,
        conversation_id: str,
        stable_id: Optional[str] = None,
    ) -> str:
        """返回 ``wecom_personal_rpa:{account_id}:{stable_id or conversation_id}``。

        - ``stable_id`` 非空时优先使用，保证跨重启路由稳定。
        - ``stable_id`` 为空时回退到 ``conversation_id``。
        - ``account_id`` 与 ``conversation_id`` 均视为必需的非空字符串（由调用方保证）。
        """
        route_key = stable_id or conversation_id
        return f"{_SESSION_ID_PREFIX}:{account_id}:{route_key}"


# 模块级单例，供下游直接 import 使用
router = WeComPersonalRpaRouter()


def is_allowed_by_monitor_whitelist(
    binding: Optional[dict],
    sender_display_name: Optional[str],
    sender_stable_id: Optional[str],
) -> bool:
    """检查消息是否被绑定级监控白名单允许。

    客户端缓存白名单只是优化（减少 callback），真正的过滤必须服务端做。
    本函数在 _process_inbound_message 解析消息后调用，不通过则该消息不投递 agent。

    判定规则：
    - binding 为 None：放行（授权层会按 needs_review 拦截）
    - monitor_user_names / monitor_user_ids 都为空：放行（未配置白名单）
    - 任一字段非空：按"任一匹配即允许"过滤
      * names 命中 sender_display_name → 允许
      * ids 命中 sender_stable_id → 允许
      * 否则拒绝
    """
    if not binding:
        return True
    names: List[str] = binding.get("monitor_user_names") or []
    ids: List[str] = binding.get("monitor_user_ids") or []
    if not names and not ids:
        return True
    if names and sender_display_name and sender_display_name in names:
        return True
    if ids and sender_stable_id and sender_stable_id in ids:
        return True
    return False


@dataclass
class AuthorizationResult:
    """会话授权判定结果。

    Attributes:
        session_id: 路由生成的会话 ID（``wecom_personal_rpa:{account_id}:{route_key}``），
            下游 adapter / action_client 复用此值作为 ActionEnvelope.session_id。
        needs_review: True 表示该会话尚未人工确认（首次见到 / pending / 重名歧义），
            客户端应暂停自动发送，等待人工绑定后转 active。
        paused: True 表示该会话已被人工或系统暂停（paused / invalid），
            等待人工恢复，不允许自动发送。
        reason: 触发 ``needs_review`` / ``paused`` 的可读原因（脱敏，不含密钥/路径），
            放行时为 None。
    """

    session_id: str
    needs_review: bool
    paused: bool
    reason: Optional[str]


async def check_conversation_authorization(
    tenant_id: str,
    account_id: str,
    conversation_id: str,
    conversation_type: str,
    search_key: str,
    display_name: str,
    stable_id: Optional[str] = None,
) -> AuthorizationResult:
    """判定当前会话是否允许自动回复。

    流程：
    1. 调用 ``db.get_or_create_binding`` 获取（或首次插入 pending）绑定记录。
    2. 调用 ``db.find_binding_by_search_key`` 拿到权威绑定状态。
    3. 按 status 分支判定（见模块 docstring）。
    4. 额外检查同 search_key 在租户内的重名歧义。

    Args:
        tenant_id: 租户 ID（租户隔离）。
        account_id: 个人企微账号 ID。
        conversation_id: 客户端本地会话标识（用于路由回退）。
        conversation_type: 会话类型（internal_user / internal_group / external_user /
            external_group），写入 binding 元数据。
        search_key: 会话稳定搜索键（由调用方按规则归一化，如 external_userid 或
            account_id + display_name 组合），作为 binding 唯一性约束键。
        display_name: 对方显示名（仅用于展示与审计，可能重名）。
        stable_id: 对方稳定 ID（优先作为 route_key）。

    Returns:
        AuthorizationResult，session_id 始终非空；其余字段按授权语义填充。
    """
    session_id = router.route(account_id, conversation_id, stable_id)

    # 1. 获取或首次创建绑定（首次见到 → 插入 pending，等待人工确认）
    binding = db.get_or_create_binding(
        tenant_id=tenant_id,
        account_id=account_id,
        conversation_type=conversation_type,
        display_name=display_name,
        search_key=search_key,
        stable_id=stable_id,
    )

    if binding is None:
        # 绑定写入失败（数据库异常）—— 保守地触发 needs_review，
        # 不自动发送，等待人工介入或重试。
        logger.warning(
            f"RPA authorization: get_or_create_binding returned None "
            f"(tenant={tenant_id}, account={account_id}, key={search_key})"
        )
        return AuthorizationResult(
            session_id=session_id,
            needs_review=True,
            paused=False,
            reason="binding_unavailable",
        )

    # 2. 以 find_binding_by_search_key 为权威状态来源（避免 upsert 返回值漂移）
    authoritative = db.find_binding_by_search_key(
        tenant_id=tenant_id,
        account_id=account_id,
        search_key=search_key,
    )
    current = authoritative if authoritative is not None else binding
    status = current.get("status")

    # 3. 重名歧义检测：同 search_key 在租户内存在多条绑定 → 需人工区分。
    #    account 维度有 UNIQUE(account_id, search_key) 约束，故按租户维度统计。
    try:
        siblings = db.list_bindings(tenant_id=tenant_id)
        same_key_count = sum(1 for b in siblings if b.get("search_key") == search_key)
    except Exception as e:  # 防御性：统计失败不应阻断主流程
        logger.error(f"RPA authorization: list_bindings failed: {e}", exc_info=True)
        same_key_count = 1

    if same_key_count > 1:
        return AuthorizationResult(
            session_id=session_id,
            needs_review=True,
            paused=False,
            reason="ambiguous",
        )

    # 4. 按 binding.status 分支
    if status == "active":
        return AuthorizationResult(
            session_id=session_id,
            needs_review=False,
            paused=False,
            reason=None,
        )

    if status in ("paused", "invalid"):
        return AuthorizationResult(
            session_id=session_id,
            needs_review=False,
            paused=True,
            reason=status,
        )

    # status == "pending" 或其它未知值 → 默认触发 needs_review
    return AuthorizationResult(
        session_id=session_id,
        needs_review=True,
        paused=False,
        reason=status or "unknown",
    )
