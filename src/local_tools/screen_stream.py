"""Runtime 桌面实时画面：会话、信令转交与超时收敛（设计 runtime-screen-stream-design.md 第 3、5 节）

服务器只做三件事：鉴权、记录会话、在两端之间转交建连信令，并通过设备心跳给画面续期；
画面不经过服务器。本模块放常量、请求模型、纯规则函数和数据库访问，路由在 api.py 里做薄封装。

约定：
- 超时判定全部用数据库时钟：时间戳写 NOW()，比较用同一条 SELECT 带回的 NOW()::timestamp。
  不能直接取 NOW()：它在 Python 里带时区，而 TIMESTAMP 列不带，两者比较会抛 TypeError。
- 读取会话时顺带收敛超时（SELECT ... FOR UPDATE 后判定并写回），不设后台任务。
- 信令内容只转交和保存，不解析、不写日志；会话结束时清空。

调用方（FastAPI async endpoint）必须用 asyncio.to_thread 包裹，禁止在事件循环内直接调用。
"""

import json
import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator
from psycopg2.extras import Json

from src.db.database import get_db_connection

PROTOCOL_VERSION = 1
SCREEN_STREAM_CAPABILITY = "screen_stream_v1"

STATE_PENDING = "pending"
STATE_PREPARED = "prepared"
STATE_CONNECTING = "connecting"
STATE_VERIFYING = "verifying"
STATE_STREAMING = "streaming"
STATE_ENDED = "ended"

END_VIEWER_CLOSED = "viewer_closed"
END_DEVICE_OFFLINE = "device_offline"
END_RUNTIME_OFFLINE = "runtime_offline"
# Runtime 可以报告的结束原因；上面三个只由服务器判定
RUNTIME_END_REASONS = (
    "runtime_stopped", "runtime_rejected", "same_machine", "machine_check_unknown",
    "connect_failed", "renewal_timeout", "resource_busy",
)
END_CONNECT_FAILED = "connect_failed"
END_MACHINE_CHECK_UNKNOWN = "machine_check_unknown"

# 同机判定结果
VERDICT_OTHER = "other"
VERDICT_SAME = "same"
VERDICT_UNKNOWN = "unknown"

DIR_WEB = "web"
DIR_RT = "rt"
STATUS_STATES = ("connected", "streaming", "locked", "unlocked", "degraded", "restored")
PATHS = ("lan", "p2p")

# 各状态从「被等待的一方最后一次访问会话」起算的时限（秒）。设备离线阈值不在这里：
# 它就是 api.ONLINE_THRESHOLD_SECONDS，由路由层传入，免得模块之间循环导入
TIMEOUT_SECONDS = {"pending": 30, "prepared": 60, "connecting": 30, "verifying": 15, "streaming_viewer": 30}
# 开始传画面之后结束的会话，还在心跳里通知多久
ENDED_NOTICE_SECONDS = 30

MAX_SIGNALS = 128
MAX_PAYLOAD_BYTES = 16 * 1024

# 错误码 → (HTTP 状态, 用户可见文案)
ERRORS = {
    "SCREEN_STREAM_DISABLED": (503, "实时画面功能未启用"),
    "DEVICE_NOT_FOUND": (404, "设备不存在"),
    "DEVICE_OFFLINE": (409, "Runtime 电脑不在线"),
    "SCREEN_STREAM_UNSUPPORTED": (409, "当前 Runtime 版本不支持实时画面，请升级 Runtime"),
    "SCREEN_SESSION_ACTIVE": (409, "已有窗口正在查看这台电脑"),
    "SCREEN_SESSION_NOT_FOUND": (404, "会话不存在或无权访问"),
    "SCREEN_SESSION_ENDED": (409, "会话已结束"),
    "SCREEN_SIGNAL_LIMIT": (409, "信令条数超过上限"),
}


class ScreenStreamError(ValueError):
    """画面接口的类型化错误：路由层转成带 code 的 HTTP 错误"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
        self.status, self.message = ERRORS[code]


# ==================== 请求模型 ====================


class _Payload(BaseModel):
    """信令 payload 的公共约束：必须是 JSON 对象，序列化后不超过 16 KB"""

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def _check_size(self) -> "_Payload":
        # allow_nan=False：Python 的 JSON 解析接受 NaN / Infinity，PostgreSQL 的 json 却不接受；
        # 不在这一层拦下，就要到写库时才被数据库拒绝（500，错误信息里还会带出一段信令文本）
        serialized = json.dumps(self.model_dump(), ensure_ascii=False, allow_nan=False)
        if len(serialized.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError("信令内容超过大小上限")
        return self


class OpaquePayload(_Payload):
    """内容原样转交的 payload：不解析、不校验里面的字段"""


class StatusPayload(OpaquePayload):
    """status 信令的 payload：state 和 path 按字面量校验，其余字段原样转交"""

    # Literal[元组常量] 等价于逐个列出取值：请求模型与上面的常量只有一份
    state: Literal[STATUS_STATES]
    path: Optional[Literal[PATHS]] = None


class RuntimeEndPayload(OpaquePayload):
    """Runtime 的 end 信令：结束原因只能是 Runtime 可以报告的那七个"""

    reason: Literal[RUNTIME_END_REASONS]


class _ViewerSignal(BaseModel):
    """网页 → Runtime 的信令"""

    payload: OpaquePayload


class _RuntimeSignal(BaseModel):
    """Runtime → 网页的信令；会话编号取自心跳通知或信令轮询的响应"""

    session_id: uuid.UUID
    payload: OpaquePayload


class _ViewerStart(_ViewerSignal):
    type: Literal["start"]


class _ViewerAnswer(_ViewerSignal):
    type: Literal["answer"]


class _ViewerCandidate(_ViewerSignal):
    type: Literal["candidate"]


class _ViewerEnd(_ViewerSignal):
    type: Literal["end"]


class _RuntimePrepared(_RuntimeSignal):
    type: Literal["prepared"]


class _RuntimeOffer(_RuntimeSignal):
    type: Literal["offer"]


class _RuntimeCandidate(_RuntimeSignal):
    type: Literal["candidate"]


class _RuntimeStatus(_RuntimeSignal):
    type: Literal["status"]
    payload: StatusPayload


class _RuntimeEnd(_RuntimeSignal):
    type: Literal["end"]
    payload: RuntimeEndPayload


ViewerSignalRequest = Annotated[
    Union[_ViewerStart, _ViewerAnswer, _ViewerCandidate, _ViewerEnd],
    Field(discriminator="type"),
]
RuntimeSignalRequest = Annotated[
    Union[_RuntimePrepared, _RuntimeOffer, _RuntimeCandidate, _RuntimeStatus, _RuntimeEnd],
    Field(discriminator="type"),
]


class ViewerEndRequest(BaseModel):
    """网页结束会话的请求体：reason 原样忽略，结束原因固定记 viewer_closed"""

    reason: Optional[Any] = None


class RuntimeSessionRequest(BaseModel):
    """Runtime 取会话与网页信令"""

    session_id: uuid.UUID
    after_seq: int = Field(default=0, ge=0)


# ==================== 纯规则函数（不依赖数据库） ====================


def supports_screen_stream(capabilities_json: Any) -> bool:
    """设备能力体是否声明实时画面；旧 Runtime 不上报，功能对其不可用"""
    if not isinstance(capabilities_json, dict):
        return False
    capability_list = capabilities_json.get("capabilities")
    return isinstance(capability_list, list) and SCREEN_STREAM_CAPABILITY in capability_list


def _is_overdue(value: Optional[datetime], db_now: datetime, seconds: int) -> bool:
    """value 早于 db_now 减 seconds 即超时；从未有过时间（空值）按超时处理"""
    return value is None or value < db_now - timedelta(seconds=seconds)


def evaluate_timeout(
    session_row: Dict[str, Any],
    db_now: datetime,
    device_last_seen_at: Optional[datetime],
    device_offline_seconds: int,
) -> Optional[str]:
    """按会话状态给出超时的结束原因；未超时或已结束返回 None。

    pending 等 Runtime 应答，从 created_at 算；prepared 等网页发 start，从 viewer_seen_at 算；
    connecting 和 verifying 等 Runtime 的结果，从 runtime_seen_at 算；streaming 两边都看，
    先判设备离线再判网页。
    """
    state = session_row.get("state")
    if state == STATE_PENDING:
        if _is_overdue(session_row.get("created_at"), db_now, TIMEOUT_SECONDS["pending"]):
            return END_DEVICE_OFFLINE
    elif state == STATE_PREPARED:
        if _is_overdue(session_row.get("viewer_seen_at"), db_now, TIMEOUT_SECONDS["prepared"]):
            return END_VIEWER_CLOSED
    elif state == STATE_CONNECTING:
        if _is_overdue(session_row.get("runtime_seen_at"), db_now, TIMEOUT_SECONDS["connecting"]):
            return END_CONNECT_FAILED
    elif state == STATE_VERIFYING:
        if _is_overdue(session_row.get("runtime_seen_at"), db_now, TIMEOUT_SECONDS["verifying"]):
            return END_MACHINE_CHECK_UNKNOWN
    elif state == STATE_STREAMING:
        if _is_overdue(device_last_seen_at, db_now, device_offline_seconds):
            return END_RUNTIME_OFFLINE
        if _is_overdue(session_row.get("viewer_seen_at"), db_now, TIMEOUT_SECONDS["streaming_viewer"]):
            return END_VIEWER_CLOSED
    return None


def _end_changes(reason: str, db_now: datetime) -> Dict[str, Any]:
    """结束会话要写的字段：结束原因、结束时间，并清空信令"""
    changes: Dict[str, Any] = {"state": STATE_ENDED, "end_reason": reason, "ended_at": db_now, "signals": []}
    if reason == "same_machine":
        changes["verdict"] = VERDICT_SAME
    elif reason == END_MACHINE_CHECK_UNKNOWN:
        changes["verdict"] = VERDICT_UNKNOWN
    return changes


def apply_signal(session_row: Dict[str, Any], direction: str, signal_type: str, payload: Any) -> Dict[str, Any]:
    """按一条信令给出会话要更新的字段（含追加后的 signals）。

    只做两条校验：会话已结束后再发信令 → SCREEN_SESSION_ENDED（end 例外：幂等返回空字典，
    不改任何数据）；信令存满 → SCREEN_SIGNAL_LIMIT（end 不占存储，不受限）。
    信令类型属于该方向、payload 取值合法（state、path、结束原因）由请求模型保证，本函数不做这项校验。
    也不校验信令顺序，例如从 connecting 直接到 streaming 也接受并写相应字段。
    """
    db_now = session_row["db_now"]
    if signal_type == "end":
        if session_row["state"] == STATE_ENDED:
            return {}
        reason = payload["reason"] if direction == DIR_RT else END_VIEWER_CLOSED
        return _end_changes(reason, db_now)
    if session_row["state"] == STATE_ENDED:
        raise ScreenStreamError("SCREEN_SESSION_ENDED")
    signals = session_row.get("signals") or []
    if len(signals) >= MAX_SIGNALS:
        raise ScreenStreamError("SCREEN_SIGNAL_LIMIT")
    # 序号在会话内单调递增，两个方向共用一个计数
    changes: Dict[str, Any] = {
        "signals": [*signals, {"seq": len(signals) + 1, "dir": direction, "type": signal_type, "payload": payload}],
    }
    if signal_type == "prepared":
        changes["state"] = STATE_PREPARED
    elif signal_type == "start":
        changes["state"] = STATE_CONNECTING
    elif signal_type == "status":
        if payload["state"] == "connected":
            changes.update(state=STATE_VERIFYING, connected_at=db_now)
        elif payload["state"] == "streaming":
            changes.update(state=STATE_STREAMING, started_at=db_now, verdict=VERDICT_OTHER)
            if payload.get("path"):
                changes["path"] = payload["path"]
    return changes


# ==================== 数据库访问 ====================

# 会话归属条件：网页侧三元组，设备侧鉴权设备加租户。调用方不能自报，一律取自鉴权结果
Scope = Tuple[str, Tuple[Any, ...]]

_SESSION_COLUMNS = """id, state, end_reason, verdict, path, signals, created_at, connected_at, started_at,
       ended_at, viewer_seen_at, runtime_seen_at"""
_SEEN_COLUMN = {DIR_WEB: "viewer_seen_at", DIR_RT: "runtime_seen_at"}

# 锁住会话行，并带回数据库时钟和设备在线时间（子查询只读，不锁设备行）
_LOCK_SESSION_SQL = f"""
    SELECT {_SESSION_COLUMNS}, NOW()::timestamp AS db_now,
           (SELECT d.last_seen_at FROM local_tool_devices d WHERE d.id = s.device_id) AS device_last_seen_at
    FROM local_tool_screen_sessions s
    WHERE s.id = %s AND {{where}}
    FOR UPDATE OF s
"""

# 该设备未结束的会话。不带排序：规划器才能直接用部分索引 idx_lt_screen_sessions_active，
# 不必沿 (device_id, created_at) 扫过这台设备的全部历史会话
_ACTIVE_SESSIONS_SQL = f"""
    SELECT {_SESSION_COLUMNS} FROM local_tool_screen_sessions
    WHERE device_id = %s AND state <> 'ended'
    FOR UPDATE
"""

_SAVE_SQL = """
    UPDATE local_tool_screen_sessions
    SET state = %s, end_reason = %s, verdict = %s, path = %s, connected_at = %s, started_at = %s,
        ended_at = %s, signals = %s{touch}
    WHERE id = %s
"""
# 读取会话只刷新访问方的访问时间，不重写整行（信令最多 128 条，建连期间两端每 500 毫秒读一次）
_TOUCH_SQL = "UPDATE local_tool_screen_sessions SET {column} = NOW() WHERE id = %s"


def viewer_scope(tenant_id: str, user_id: str, device_id: str) -> Scope:
    """网页侧：会话必须同时属于当前租户、当前用户和路径里的设备"""
    return "tenant_id = %s AND user_id = %s AND device_id = %s", (tenant_id, user_id, device_id)


def runtime_scope(tenant_id: str, device_id: str) -> Scope:
    """设备侧：会话必须属于当前鉴权设备及其租户"""
    return "tenant_id = %s AND device_id = %s", (tenant_id, device_id)


def _save(cursor, row: Dict[str, Any], seen_column: Optional[str] = None) -> None:
    """把锁住会话行之后算出的字段写回；seen_column 是本次访问方的访问时间列"""
    cursor.execute(
        _SAVE_SQL.format(touch=f", {seen_column} = NOW()" if seen_column else ""),
        (row["state"], row["end_reason"], row["verdict"], row["path"], row["connected_at"], row["started_at"],
         row["ended_at"], Json(row["signals"] or []), row["id"]),
    )


def _lock_session(cursor, scope: Scope, session_id: str, device_offline_seconds: int) -> Optional[Dict[str, Any]]:
    """锁住会话行；已超时的会话在这里收敛成 ended 并写回，随调用方的事务一起提交。

    查不到（不存在或无权访问）返回 None。
    """
    where_sql, params = scope
    cursor.execute(_LOCK_SESSION_SQL.format(where=where_sql), (session_id, *params))
    row = cursor.fetchone()
    if row is None:
        return None
    row = dict(row)
    reason = evaluate_timeout(row, row["db_now"], row["device_last_seen_at"], device_offline_seconds)
    if reason is not None:
        row.update(_end_changes(reason, row["db_now"]))
        _save(cursor, row)
    return row


def _session_view(row: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": str(row["id"]), "state": row["state"], "end_reason": row["end_reason"]}


def _visible_signals(row: Dict[str, Any], direction: str, after_seq: int) -> List[Dict[str, Any]]:
    """读取方只拿对方发来的、序号大于游标的信令"""
    return [
        {"seq": signal["seq"], "type": signal["type"], "payload": signal["payload"]}
        for signal in row["signals"] or []
        if signal["dir"] == direction and signal["seq"] > after_seq
    ]


def create_session(
    tenant_id: str, user_id: str, device_id: str, *, device_offline_seconds: int,
) -> Dict[str, Any]:
    """网页创建会话：锁住设备行后依次校验归属、在线、能力、没有未结束会话，再插入 pending 会话"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT last_seen_at, capabilities_json, NOW()::timestamp AS db_now
               FROM local_tool_devices
               WHERE id = %s AND tenant_id = %s AND user_id = %s AND status = 'active'
               FOR UPDATE""",
            (device_id, tenant_id, user_id),
        )
        device = cursor.fetchone()
        if device is None:
            raise ScreenStreamError("DEVICE_NOT_FOUND")
        db_now = device["db_now"]
        if _is_overdue(device["last_seen_at"], db_now, device_offline_seconds):
            raise ScreenStreamError("DEVICE_OFFLINE")
        if not supports_screen_stream(device["capabilities_json"]):
            raise ScreenStreamError("SCREEN_STREAM_UNSUPPORTED")
        # 同一设备同一时间只允许一个未结束的会话；超时的旧会话先收敛，不挡新会话
        cursor.execute(_ACTIVE_SESSIONS_SQL, (device_id,))
        for stale in cursor.fetchall():
            stale = dict(stale)
            reason = evaluate_timeout(stale, db_now, device["last_seen_at"], device_offline_seconds)
            if reason is None:
                raise ScreenStreamError("SCREEN_SESSION_ACTIVE")
            _save(cursor, {**stale, **_end_changes(reason, db_now)})
        cursor.execute(
            """INSERT INTO local_tool_screen_sessions (tenant_id, user_id, device_id, state, viewer_seen_at)
               VALUES (%s, %s, %s, 'pending', NOW()) RETURNING id""",
            (tenant_id, user_id, device_id),
        )
        session_id = cursor.fetchone()["id"]
        conn.commit()
        return {"id": str(session_id), "state": STATE_PENDING}


def load_session(
    scope: Scope,
    session_id: str,
    *,
    viewer: bool,
    after_seq: int = 0,
    device_offline_seconds: int,
) -> Optional[Dict[str, Any]]:
    """读取会话（网页轮询或 Runtime 取信令）：收敛超时、刷新访问方的时间，返回会话和对方发来的新信令"""
    caller, peer = (DIR_WEB, DIR_RT) if viewer else (DIR_RT, DIR_WEB)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        row = _lock_session(cursor, scope, session_id, device_offline_seconds)
        if row is None:
            return None
        if row["state"] != STATE_ENDED:
            cursor.execute(_TOUCH_SQL.format(column=_SEEN_COLUMN[caller]), (row["id"],))
        conn.commit()
        return {"session": _session_view(row), "signals": _visible_signals(row, peer, after_seq)}


def append_signal(
    scope: Scope,
    session_id: str,
    direction: str,
    signal_type: str,
    payload: Dict[str, Any],
    *,
    device_offline_seconds: int,
) -> Optional[Dict[str, Any]]:
    """追加一条信令并应用状态流转。

    返回 {"seq": 新信令的序号, "session": 会话}。end 不占信令存储（会话结束时信令清空），
    seq 为 None；对已结束的会话再发 end 幂等成功，不改任何数据。
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        row = _lock_session(cursor, scope, session_id, device_offline_seconds)
        if row is None:
            return None
        try:
            changes = apply_signal(row, direction, signal_type, payload)
        except ScreenStreamError:
            # 刚做的超时收敛先落库，再把信令被拒的原因报给调用方
            conn.commit()
            raise
        if changes:
            _save(cursor, {**row, **changes}, _SEEN_COLUMN[direction])
        # 一律提交：_lock_session 可能已经把超时的会话收敛写进了本事务
        conn.commit()
        signals = changes.get("signals")
        return {"seq": signals[-1]["seq"] if signals else None, "session": _session_view({**row, **changes})}


# 心跳取最新一条有关会话：未结束的，或开始传画面之后结束、且结束时间还在通知期内的
_NOTICE_SQL = f"""
    SELECT {_SESSION_COLUMNS}, NOW()::timestamp AS db_now,
           (SELECT d.last_seen_at FROM local_tool_devices d WHERE d.id = %s) AS device_last_seen_at
    FROM local_tool_screen_sessions
    WHERE device_id = %s
      AND (state <> 'ended' OR (started_at IS NOT NULL AND ended_at > NOW() - %s * INTERVAL '1 second'))
    ORDER BY created_at DESC
    LIMIT 1
"""
# 条件里带上读到的状态：期间被别的事务改过就不覆盖
_NOTICE_CONVERGE_SQL = """
    UPDATE local_tool_screen_sessions
    SET state = 'ended', end_reason = %s, signals = '[]'::jsonb, ended_at = NOW()
    WHERE id = %s AND state = %s
"""
_NOTICE_RENEW_SQL = """
    UPDATE local_tool_screen_sessions SET runtime_seen_at = NOW()
    WHERE id = %s AND state = 'streaming'
"""


def heartbeat_notice(device_id: str, *, device_offline_seconds: int) -> Optional[Dict[str, Any]]:
    """心跳里的画面会话通知（设计 5.1）：一到两条按索引的语句，不显式加锁。

    返回 {"id", "state"}：pending 等 Runtime 应答；streaming 每次心跳都带，作为续期；
    ended 只在开始传画面之后结束的 30 秒内带。建连阶段（prepared/connecting/verifying）
    Runtime 自己在轮询信令，不带。没有有关会话返回 None。
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(_NOTICE_SQL, (device_id, device_id, ENDED_NOTICE_SECONDS))
        row = cursor.fetchone()
        if row is None:
            return None
        row = dict(row)
        session_id, state = str(row["id"]), row["state"]
        if state != STATE_ENDED:
            reason = evaluate_timeout(row, row["db_now"], row["device_last_seen_at"], device_offline_seconds)
            if reason is not None:
                # 超时未结束的会话先收敛，再按已结束处理
                cursor.execute(_NOTICE_CONVERGE_SQL, (reason, session_id, state))
                state = STATE_ENDED
            elif state == STATE_PENDING:
                return {"id": session_id, "state": state}
            elif state == STATE_STREAMING:
                cursor.execute(_NOTICE_RENEW_SQL, (session_id,))
            else:
                return None
            conn.commit()
        return {"id": session_id, "state": state}
