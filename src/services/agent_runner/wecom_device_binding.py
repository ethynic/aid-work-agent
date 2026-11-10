"""Private root-owned device pin for group monitoring; never model authority."""

import copy
from collections.abc import Mapping

from .ownership import LeaseLost

RESOURCE = 'wecom_task_device'
IDENTITY_FIELDS = ('tenant_id', 'user_id', 'session_id', 'source', 'session_kind')

# wecom_proxy 行按 session 参数限定目标的工具（契约 §6"企微调用限定当前目标
# 会话"）；wecom_read_sessions 单独按 sessions 列表核对
_PROXY_SESSION_TOOLS = frozenset({'wecom_read_session', 'wecom_message_send'})


def _revoke_stop_task_in_tx(tenant_id, task_id):
    """失权即停任务（契约 §6、设计 §9.5）：置 stop_requested 并沿 P4 语义收敛。

    批次运行中保留位置与日志（settle_stop_in_tx 仅在无 running 批时落
    stopped，批次核定离开 running 后由既有收敛路径落 stopped）；独立短事务
    ——本门所在绑定事务将被派发拒绝回滚，不能承载业务状态写入。
    """
    from src.db.database import get_db_connection
    from src.wecom_conversation import proxy_tasks

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM bs_wecom_proxy_tasks WHERE id=%s AND tenant_id=%s FOR UPDATE',
            (task_id, tenant_id))
        task = cursor.fetchone()
        if task is None:
            return
        if not task['stop_requested']:
            cursor.execute(
                '''UPDATE bs_wecom_proxy_tasks SET stop_requested=TRUE,
                       updated_at=CURRENT_TIMESTAMP WHERE id=%s''', (task_id,))
            task['stop_requested'] = True
        proxy_tasks.settle_stop_in_tx(cursor, dict(task))
        conn.commit()


def _agent_has_access(cursor, agent_id, task_user_id):
    """智能体仍有权限（沿 _agent_allowed 的既有校验语义 check_agent_access，
    核对主体为任务的 user＋agent_id；用户行经本门绑定事务读取）。"""
    cursor.execute('SELECT user_id, tenant_id, role, status FROM users WHERE user_id=%s',
                   (task_user_id,))
    user = cursor.fetchone()
    if not user:
        return False
    from src.saas.permissions.checker import check_agent_access

    return bool(check_agent_access(agent_id, dict(user)))


def assert_proxy_dispatch_in_tx(cursor, row, tool_name, arguments):
    """wecom_proxy 行每次企微工具派发的门禁（代理任务契约 §6 核对检查点）。

    - 队列占用：批次必须处于 running——等待 Runner 唤醒/恢复后未重新取得
      队列占用期间禁止派发企微工具（设计 §9.3"恢复前必须重新排队取得该
      设备/账号的代理队列占用，才能再次派发企微工具"）；
    - 任务未软删＋目标仍 active＋智能体仍有权限（已运行批次检查点；不含
      stop_requested——契约 §6：停止请求不阻断已运行批次）；失权 fail-closed
      拒绝派发并停任务（恢复权限后由用户手动启动）；
    - 会话限定：企微读发仅限批次冻结目标会话（含按名发送/批量读取）。
    失败抛 DeviceOperationUnresolved：绑定事务回滚、无派发无副作用，调用方
    以业务失败结果呈现（不重试派发）。
    """
    if row.get('source') != 'wecom_proxy':
        return
    from src.local_tools.repository import DeviceOperationUnresolved

    cursor.execute(
        """SELECT b.status AS batch_status, b.session, b.task_id,
                  t.status AS task_status, t.user_id AS task_user_id,
                  t.agent_id AS task_agent_id, s.status AS target_status
           FROM bs_wecom_proxy_batches b
           JOIN bs_wecom_proxy_tasks t ON t.id=b.task_id AND t.tenant_id=b.tenant_id
           LEFT JOIN bs_wecom_proxy_task_sessions s ON s.tenant_id=b.tenant_id
                  AND s.task_id=b.task_id AND s.session=b.session
           WHERE b.tenant_id=%s AND b.runner_id=%s
           ORDER BY b.id DESC LIMIT 1""",
        (row['tenant_id'], row['runner_id']))
    batch = cursor.fetchone()
    if batch is None:
        # 批次已重试换新 runner 或数据异常：原 runner 不再允许派发
        raise DeviceOperationUnresolved('WECOM_PROXY_BATCH_MISSING')
    if batch['batch_status'] != 'running':
        raise DeviceOperationUnresolved('WECOM_PROXY_QUEUE_NOT_ACQUIRED')
    if batch['task_status'] == 'deleted':
        raise DeviceOperationUnresolved('WECOM_PROXY_TASK_DELETED')
    if batch['target_status'] != 'active':
        raise DeviceOperationUnresolved('WECOM_PROXY_TARGET_INVALID')
    if not _agent_has_access(cursor, batch['task_agent_id'], batch['task_user_id']):
        _revoke_stop_task_in_tx(row['tenant_id'], batch['task_id'])
        raise DeviceOperationUnresolved('WECOM_PROXY_AGENT_REVOKED')
    batch_session = batch['session']
    if tool_name == 'wecom_read_sessions':
        sessions = arguments.get('sessions')
        if (not isinstance(sessions, list) or not sessions
                or any(item != batch_session for item in sessions)):
            raise DeviceOperationUnresolved('WECOM_PROXY_SESSION_SCOPE')
        return
    if tool_name in _PROXY_SESSION_TOOLS or 'session' in arguments:
        if arguments.get('session') != batch_session:
            # 指令快照已固定目标会话稳定标识，不允许改发/改读其他会话
            raise DeviceOperationUnresolved('WECOM_PROXY_SESSION_SCOPE')


def accepted_daily_device(request_data):
    """Only a server-accepted v2 daily descriptor can fix first dispatch."""
    if not isinstance(request_data, Mapping):
        return None
    descriptor = request_data.get('wecom_conversation_business')
    if (not isinstance(descriptor, Mapping) or descriptor.get('schema_version') != 2
            or descriptor.get('kind') != 'daily'):
        return None
    device = descriptor.get('device')
    if (not isinstance(device, str) or not device
            or request_data.get(RESOURCE) != device):
        raise LeaseLost('WECOM_DAILY_DEVICE_INVALID')
    return device


def bound_device(root, runner_id, identity):
    if root.get('execution_id') != runner_id or root.get('identity') != identity:
        raise LeaseLost('WECOM_TASK_OWNER_MISMATCH')
    resources = root.get('resources', {})
    if RESOURCE not in resources:
        return None
    marker = resources[RESOURCE]
    if (not isinstance(marker, dict) or type(marker.get('version')) is not int
            or marker['version'] not in {1, 2} or marker.get('runner_id') != runner_id
            or marker.get('identity') != identity
            or not isinstance(marker.get('device_id'), str) or not marker['device_id']):
        raise LeaseLost('WECOM_TASK_DEVICE_INVALID')
    return marker['device_id']


def bind_or_validate(checkpoint, row, tool_name, provider_key, device_id, arguments):
    """Called under the root row lock, in the invocation insertion transaction."""
    if provider_key != 'wecom':
        return False
    root = checkpoint['execution']
    identity = {key: row[key] for key in IDENTITY_FIELDS}
    original = bound_device(root, row['runner_id'], identity)
    daily_device = accepted_daily_device((row.get('input') or {}).get('request_data'))
    modern = tool_name == 'wecom_read_sessions' or bool(arguments.get('session'))
    from src.local_tools.repository import DeviceOperationUnresolved

    if daily_device is not None and daily_device != str(device_id):
        raise DeviceOperationUnresolved('WECOM_TASK_DEVICE_CHANGED')
    if original is not None and original != str(device_id):
        raise DeviceOperationUnresolved('WECOM_TASK_DEVICE_CHANGED')
    marker = root.get('resources', {}).get(RESOURCE) or {}
    if original is not None and modern and marker.get('version') == 1:
        # A new three-tool read/send remains on the original device. Old
        # accepted group_cursor calls retain their original validation below.
        marker['version'] = 2
    if original is not None and not modern:
        if tool_name in {'wecom_send_image', 'wecom_send_file'}:
            raise DeviceOperationUnresolved('WECOM_TASK_SEND_SCOPE_REQUIRED')
        if tool_name == 'wecom_message_send' and (
                not isinstance(arguments.get('group_cursor'), str) or not arguments['group_cursor']
                or not arguments.get('target_name') or arguments.get('target_ref')):
            raise DeviceOperationUnresolved('WECOM_TASK_SEND_SCOPE_REQUIRED')
    if original is None and (tool_name == 'wecom_group_poll' or modern):
        root.setdefault('resources', {})[RESOURCE] = {
            'version': 2 if modern else 1, 'runner_id': row['runner_id'], 'identity': copy.deepcopy(identity),
            'device_id': str(device_id)}
    return original is not None or daily_device is not None
