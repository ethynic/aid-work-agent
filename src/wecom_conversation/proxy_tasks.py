"""企微代理任务配置、状态机与批次事实（代理任务契约 §4，P4）。

职责边界：本模块只做本地 SQL 与状态转换；创建检查/基线读取等受信
invocation 由 API 层沿目录先例发起（结果作为参数传入，失败不写脏数据）。
监控循环与批次派发属 P5、回答 Runner 属 P6，本模块仅提供可复用的
批次建行与状态迁移函数。

状态机（契约 §4 冻结）：
- 任务 stopped|running|deleted：创建后 stopped；软删除仅可从已停止到达；
  stop_requested=true 表达"已请求停止、当前批仍在执行"，无 running 批
  （当前批结束或进入等待）后落 stopped。
- 目标 active|halted|removed：halted=零发送重试再失败等停住待人工；
  移除目标使未决批次转 ended_by_removal。
- 批次 pending|running|waiting|reconcile_hold|succeeded|failed|
  ended_by_removal|ended_by_delete：批次表即持久队列；waiting 可自动恢复
  重排，reconcile_hold 不可自动恢复。
"""
from __future__ import annotations

from psycopg2 import errors as pg_errors
from psycopg2.extras import Json

from src.db.database import get_db_connection

from .adapters import decode_locator
from .service import ConversationServiceError

PROXY_SCHEMA_VERSION = 1

# 任务状态（契约 §4）
TASK_STOPPED = 'stopped'
TASK_RUNNING = 'running'
TASK_DELETED = 'deleted'

# 目标状态
TARGET_ACTIVE = 'active'
TARGET_HALTED = 'halted'
TARGET_REMOVED = 'removed'

# 批次状态（契约 §4 冻结清单）
BATCH_PENDING = 'pending'
BATCH_RUNNING = 'running'
BATCH_WAITING = 'waiting'
BATCH_RECONCILE_HOLD = 'reconcile_hold'
BATCH_SUCCEEDED = 'succeeded'
BATCH_FAILED = 'failed'
BATCH_ENDED_BY_REMOVAL = 'ended_by_removal'
BATCH_ENDED_BY_DELETE = 'ended_by_delete'

BATCH_UNDECIDED = (BATCH_PENDING, BATCH_WAITING, BATCH_RECONCILE_HOLD)
BATCH_TERMINAL = (BATCH_SUCCEEDED, BATCH_FAILED, BATCH_ENDED_BY_REMOVAL, BATCH_ENDED_BY_DELETE)

# 未决批次结束映射（移除目标/软删除；含 waiting、reconcile_hold 中的未发送批次，
# 不得再恢复发送）
BATCH_END_STATUSES = (BATCH_ENDED_BY_REMOVAL, BATCH_ENDED_BY_DELETE)

# 批次状态迁移白名单（契约 §4/§5）：reconcile_hold 不可自动恢复（仅人工处置或
# 任务级结束）；failed→pending 仅承载"零发送重试一次"（retry_count 门槛由调用方核对）
BATCH_TRANSITIONS = {
    BATCH_PENDING: {BATCH_RUNNING, BATCH_ENDED_BY_REMOVAL, BATCH_ENDED_BY_DELETE},
    BATCH_RUNNING: {BATCH_WAITING, BATCH_RECONCILE_HOLD, BATCH_SUCCEEDED, BATCH_FAILED},
    BATCH_WAITING: {BATCH_PENDING, BATCH_RUNNING, BATCH_ENDED_BY_REMOVAL, BATCH_ENDED_BY_DELETE},
    BATCH_RECONCILE_HOLD: {BATCH_SUCCEEDED, BATCH_FAILED,
                           BATCH_ENDED_BY_REMOVAL, BATCH_ENDED_BY_DELETE},
    BATCH_FAILED: {BATCH_PENDING, BATCH_ENDED_BY_REMOVAL, BATCH_ENDED_BY_DELETE},
    BATCH_SUCCEEDED: set(),
    BATCH_ENDED_BY_REMOVAL: set(),
    BATCH_ENDED_BY_DELETE: set(),
}

# 创建检查要求配置工具能力；启动只核对运行条件（既有工具能力，不要求配置工具，
# 契约 §3：已存在任务不因缺少创建专用配置能力失去运行路径）
CONFIG_TOOL_NAMES = ('wecom_config_doctor', 'wecom_config_calibrate')
RUNTIME_TOOL_NAMES = ('wecom_read_sessions', 'wecom_read_session', 'wecom_message_send')

TITLE_MAX = 100
INSTRUCTION_MAX = 5000
SESSIONS_MAX = 50


def _err(code, message, status=409):
    return ConversationServiceError(code, message, status)


def _row(row):
    return dict(row) if row is not None else None


# ==================== 创建检查就绪判定（契约 §2.1/§2.2 服务端消费规则） ====================

def _int_point(point):
    """坐标须为整数像素（契约 §2.2：坐标为整数像素）。"""
    if not isinstance(point, dict):
        return None
    x, y = point.get('x'), point.get('y')
    if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, int) or not isinstance(y, int):
        return None
    return (x, y)


def evaluate_creation_readiness(doctor_data, calibrate_data):
    """契约就绪判定：全部满足才允许保存；返回具体不满足项列表（空=通过）。

    success/localdb_ready/mode=db/runtime_readiness/账号非空＋标定双点坐标；
    doctor 的 self_user_id 是创建页账号核定值（Q2），须与标定账号一致。
    """
    problems = []
    if not isinstance(doctor_data, dict) or doctor_data.get('schema_version') != 1:
        return ['配置检查结果结构无效，请升级客户端后再试']
    if doctor_data.get('success') is not True:
        problems.append('配置检查未通过（success=false），请按诊断项处理本机环境')
    localdb = doctor_data.get('localdb')
    if not isinstance(localdb, dict) or localdb.get('localdb_ready') is not True:
        problems.append('本地库通道未就绪（localdb_ready≠true）')
    mode_decision = localdb.get('mode_decision') if isinstance(localdb, dict) else None
    if not isinstance(mode_decision, dict) or mode_decision.get('mode') != 'db':
        problems.append('本地库读取模式不是 db（mode_decision.mode≠db），代理任务不可用')
    readiness = doctor_data.get('runtime_readiness')
    if not isinstance(readiness, dict) or readiness.get('ready') is not True:
        problems.append('桌面运行条件未就绪（runtime_readiness.ready≠true）')
    account = doctor_data.get('self_user_id')
    if not isinstance(account, str) or not account:
        problems.append('当前登录账号未确认（self_user_id 缺省），无法核定企微账号')
    if not isinstance(calibrate_data, dict) or calibrate_data.get('schema_version') != 1:
        problems.append('双点标定结果结构无效，请升级客户端后再试')
        return problems
    if _int_point(calibrate_data.get('title_point')) is None:
        problems.append('标题点坐标缺失或非整数像素，标定未完成')
    if _int_point(calibrate_data.get('input_point')) is None:
        problems.append('输入点坐标缺失或非整数像素，标定未完成')
    calibrate_account = calibrate_data.get('self_user_id')
    if not isinstance(calibrate_account, str) or not calibrate_account:
        problems.append('标定未确认当前登录账号（self_user_id 缺省）')
    elif account and calibrate_account != account:
        problems.append('标定账号与配置检查账号不一致，请核对企微登录状态')
    return problems


# ==================== 入参校验 ====================

def _validate_title(value):
    if not isinstance(value, str) or not value.strip():
        raise _err('VALIDATION_FAILED', '请填写任务标题', 400)
    value = value.strip()
    if len(value) > TITLE_MAX:
        raise _err('VALIDATION_FAILED', f'任务标题不超过 {TITLE_MAX} 字', 400)
    return value


def _validate_instruction(value):
    if not isinstance(value, str) or not value.strip():
        raise _err('VALIDATION_FAILED', '请填写代理指令', 400)
    value = value.strip()
    if len(value) > INSTRUCTION_MAX:
        raise _err('VALIDATION_FAILED', f'代理指令不超过 {INSTRUCTION_MAX} 字', 400)
    return value


def _validate_sessions(value):
    """会话目标多选：ws1. 稳定 locator 清单，去重保序。"""
    if not isinstance(value, list) or not value or len(value) > SESSIONS_MAX or any(
            not isinstance(item, str) or not item.startswith('ws1.') or len(item) > 2048
            for item in value):
        raise _err('VALIDATION_FAILED', f'请选择 1–{SESSIONS_MAX} 个本地目录返回的稳定会话标识', 400)
    return list(dict.fromkeys(value))


def _validate_agent_id(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise _err('VALIDATION_FAILED', '请选择租户内有权使用的智能体', 400)
    return value.strip()


def _validated_targets(cursor, tenant_id, user_id, locators, *, account, device_id):
    """校验会话目标归属同租户/用户/账号/设备（复用 bs_wecom_conversations 登记）。"""
    decoded = [decode_locator(locator) for locator in locators]
    if any(item is None for item in decoded):
        raise _err('VALIDATION_FAILED', '会话标识无效，请重新从本地目录选择', 400)
    cursor.execute(
        '''SELECT id, session_locator, source_account_id, device_id FROM bs_wecom_conversations
           WHERE tenant_id=%s AND user_id=%s
             AND (source_account_id, source_conversation_id) IN (
                 SELECT * FROM unnest(%s::text[], %s::text[]))''',
        (tenant_id, user_id, [item[0] for item in decoded], [item[1] for item in decoded]))
    by_locator = {row['session_locator']: row for row in cursor.fetchall()}
    for locator in locators:
        row = by_locator.get(locator)
        if row is None:
            raise _err('PROXY_SESSION_INVALID', '会话尚未登记，请先在会话目录中添加', 409)
        if row['source_account_id'] != account:
            raise _err('PROXY_SESSION_INVALID', '所选会话不属于当前企微账号', 409)
        if row['device_id'] is None or str(row['device_id']) != str(device_id):
            raise _err('PROXY_SESSION_INVALID', '所选会话不属于当前设备', 409)
    return by_locator


def _task_for_update(cursor, tenant_id, user_id, task_id):
    cursor.execute(
        '''SELECT * FROM bs_wecom_proxy_tasks WHERE id=%s AND tenant_id=%s AND user_id=%s FOR UPDATE''',
        (task_id, tenant_id, user_id))
    task = _row(cursor.fetchone())
    if task is None:
        raise _err('PROXY_NOT_FOUND', '代理任务不存在或无权访问', 404)
    return task


def _task_targets(cursor, tenant_id, task_id, *, statuses=None, for_update=False):
    sql = '''SELECT * FROM bs_wecom_proxy_task_sessions WHERE tenant_id=%s AND task_id=%s'''
    params = [tenant_id, task_id]
    if statuses:
        sql += ' AND status=ANY(%s)'
        params.append(list(statuses))
    sql += ' ORDER BY id'
    if for_update:
        sql += ' FOR UPDATE'
    cursor.execute(sql, tuple(params))
    return [_row(item) for item in cursor.fetchall()]


def _running_session_owners(cursor, tenant_id, account, task_id, sessions):
    """同租户同账号下，与给定稳定会话重叠的其他 running 任务归属。"""
    if not sessions:
        return []
    cursor.execute(
        '''SELECT DISTINCT t.id AS task_id, t.title, s.session
           FROM bs_wecom_proxy_task_sessions s
           JOIN bs_wecom_proxy_tasks t ON t.id=s.task_id AND t.tenant_id=s.tenant_id
           WHERE s.tenant_id=%s AND t.status='running' AND t.wecom_account_id=%s
             AND t.id != %s AND s.status='active' AND s.session=ANY(%s)
           ORDER BY t.id, s.session''',
        (tenant_id, account, task_id, list(sessions)))
    return [_row(item) for item in cursor.fetchall()]


def _lock_start(cursor, tenant_id, account):
    """同账号启动串行化（并发启动唯一归属检查与翻转同事务）。"""
    cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                   (f'bs_wecom_proxy:start:{tenant_id}:{account}',))


def proxy_web_session_id(tenant_id, user_id, task_id, locator):
    """任务＋目标固定 web_session_id（新任务不复用旧智能体会话历史，契约 §6）。"""
    from .plans import business_session_id
    return business_session_id(tenant_id, user_id, 'proxy', f'{task_id}|{locator}')


def _register_web_session_in_tx(cursor, tenant_id, user_id, task_id, title, locator):
    """登记 web_session_id（复用现有 Web 会话存储与 ops 元数据语义，不另造存储）。"""
    from .plans import _ensure_session
    session_id = proxy_web_session_id(tenant_id, user_id, task_id, locator)
    _ensure_session(cursor, tenant_id=tenant_id, user_id=user_id, session_id=session_id,
                    title=f'企微代理：{title}'[:100])
    return session_id


# ==================== 创建 / 查询 ====================

def create_task(tenant_id, user_id, *, device_id, account, self_user_id, title, agent_id,
                instruction, sessions, profile_id='main', instance_id=None):
    """创建代理任务（固定 Runtime/账号/智能体；初始目标待首次启动建基线）。"""
    title = _validate_title(title)
    instruction = _validate_instruction(instruction)
    agent_id = _validate_agent_id(agent_id)
    locators = _validate_sessions(sessions)
    profile_id = profile_id if isinstance(profile_id, str) and profile_id.strip() else 'main'
    if len(profile_id) > 64 or (instance_id is not None and (
            not isinstance(instance_id, str) or not instance_id.strip() or len(instance_id) > 64)):
        raise _err('VALIDATION_FAILED', 'profile 或实例标识无效', 400)
    if not account or self_user_id != account:
        raise _err('PROXY_ACCOUNT_MISMATCH', '配置检查账号与标定账号不一致，未创建任务')
    with get_db_connection() as conn:
        cursor = conn.cursor()
        _validated_targets(cursor, tenant_id, user_id, locators,
                           account=account, device_id=device_id)
        cursor.execute(
            '''INSERT INTO bs_wecom_proxy_tasks
                   (tenant_id, user_id, profile_id, instance_id, device_id, wecom_account_id,
                    self_user_id, title, agent_id, instruction, status)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'stopped') RETURNING *''',
            (tenant_id, user_id, profile_id, instance_id, str(device_id), account,
             self_user_id, title, agent_id, instruction))
        task = _row(cursor.fetchone())
        for locator in locators:
            cursor.execute(
                '''INSERT INTO bs_wecom_proxy_task_sessions
                       (tenant_id, user_id, task_id, session, status)
                   VALUES (%s,%s,%s,%s,'active')''',
                (tenant_id, user_id, task['id'], locator))
        conn.commit()
    return task


def get_task(tenant_id, user_id, task_id, *, include_targets=True):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT * FROM bs_wecom_proxy_tasks WHERE id=%s AND tenant_id=%s AND user_id=%s''',
            (task_id, tenant_id, user_id))
        task = _row(cursor.fetchone())
        if task is None:
            return None
        targets = _task_targets(cursor, tenant_id, task_id) if include_targets else []
    if include_targets:
        task['targets'] = targets
    return task


def list_tasks(tenant_id, user_id, *, include_deleted=False, limit=50, offset=0):
    """任务列表（历史含软删除只读可查；最新在前）。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        clause = '' if include_deleted else " AND status != 'deleted'"
        cursor.execute(
            f'''SELECT t.*, (
                    SELECT count(*) FROM bs_wecom_proxy_task_sessions s
                    WHERE s.tenant_id=t.tenant_id AND s.task_id=t.id AND s.status='active'
                ) AS active_target_count
                FROM bs_wecom_proxy_tasks t
                WHERE t.tenant_id=%s AND t.user_id=%s{clause}
                ORDER BY t.updated_at DESC LIMIT %s OFFSET %s''',
            (tenant_id, user_id, min(int(limit), 200), int(offset)))
        return [_row(item) for item in cursor.fetchall()]


def get_task_detail(tenant_id, user_id, task_id, *, batch_limit=50):
    """任务详情：配置＋每目标位置只读投影＋最近批次（历史只读可查）。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT * FROM bs_wecom_proxy_tasks WHERE id=%s AND tenant_id=%s AND user_id=%s''',
            (task_id, tenant_id, user_id))
        task = _row(cursor.fetchone())
        if task is None:
            return None
        targets = _task_targets(cursor, tenant_id, task_id)
        cursor.execute(
            '''SELECT id, task_id, session, batch_end_msg_id, agent_id, web_session_id,
                      runner_id, status, cli_error, retry_count, created_at, updated_at,
                      started_at, finished_at
               FROM bs_wecom_proxy_batches WHERE tenant_id=%s AND task_id=%s
               ORDER BY id DESC LIMIT %s''',
            (tenant_id, task_id, min(int(batch_limit), 200)))
        batches = [_row(item) for item in cursor.fetchall()]
    task['targets'] = targets
    task['batches'] = batches
    return task


# ==================== 启动（基线与 web_session 登记） ====================

def start_prep(tenant_id, user_id, task_id):
    """启动前置核对（DB 侧）：状态、目标与同账号同会话归属冲突预检。

    设备在线/能力核对与受信基线读取由 API 层执行；归属唯一性在
    complete_start 事务内权威复核（预检仅为提前给出明确错误）。
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        task = _task_for_update(cursor, tenant_id, user_id, task_id)
        if task['status'] == TASK_DELETED:
            raise _err('PROXY_TASK_DELETED', '任务已删除，不可启动')
        if task['status'] == TASK_RUNNING:
            raise _err('PROXY_STATE_CONFLICT', '任务已在运行中')
        targets = _task_targets(cursor, tenant_id, task_id, statuses=(TARGET_ACTIVE,))
        if not targets:
            raise _err('PROXY_NO_TARGETS', '任务没有启用的会话目标，请先编辑添加', 409)
        conflicts = _running_session_owners(cursor, tenant_id, task['wecom_account_id'],
                                            task_id, [item['session'] for item in targets])
    return task, targets, conflicts


def complete_start(tenant_id, user_id, task_id, *, device_id, self_user_id, baselines):
    """启动落位：核对账号一致与归属唯一，建缺失基线并登记 web_session_id。

    - baselines：{locator: {'cursor','generation','watermark'}}，仅覆盖
      wb1_cursor 为空（尚未建基线）的目标；编辑加入时已记水位的基线不覆盖；
    - 账号一致性以受信读取返回的 self_user_id 核对（运行条件，不要求配置工具）；
    - 归属唯一性在同账号 advisory lock 事务内权威复核，冲突明确报错。
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        task = _task_for_update(cursor, tenant_id, user_id, task_id)
        if task['status'] == TASK_DELETED:
            raise _err('PROXY_TASK_DELETED', '任务已删除，不可启动')
        if task['status'] == TASK_RUNNING:
            raise _err('PROXY_STATE_CONFLICT', '任务已在运行中')
        if str(device_id) != str(task['device_id']):
            raise _err('PROXY_DEVICE_MISMATCH', '读取来源设备与任务固定设备不一致，未启动')
        if self_user_id != task['wecom_account_id']:
            raise _err('PROXY_ACCOUNT_MISMATCH', '设备当前企微账号与任务固定账号不一致，未启动')
        _lock_start(cursor, tenant_id, task['wecom_account_id'])
        targets = _task_targets(cursor, tenant_id, task_id, statuses=(TARGET_ACTIVE,),
                                for_update=True)
        if not targets:
            raise _err('PROXY_NO_TARGETS', '任务没有启用的会话目标，请先编辑添加')
        conflicts = _running_session_owners(cursor, tenant_id, task['wecom_account_id'],
                                            task_id, [item['session'] for item in targets])
        if conflicts:
            owner = conflicts[0]['task_id']
            raise _err('PROXY_SESSION_OWNED',
                       f'同一企微账号的会话已由运行中的任务 #{owner} 监控，请先停止该任务或调整会话')
        baselines = baselines or {}
        for target in targets:
            locator = target['session']
            if target['wb1_cursor'] is None:
                baseline = baselines.get(locator)
                if baseline is None:
                    raise _err('PROXY_BASELINE_MISSING',
                               f'会话基线读取结果缺失，未启动：{locator[:60]}')
                cursor.execute(
                    '''UPDATE bs_wecom_proxy_task_sessions SET
                           wb1_cursor=%s, source_generation=%s, latest_seen_msg_id=%s,
                           processed_position=%s, baseline_origin='initial', updated_at=CURRENT_TIMESTAMP
                       WHERE id=%s''',
                    (baseline.get('cursor'), baseline.get('generation'),
                     baseline.get('watermark'), baseline.get('watermark'), target['id']))
            if not target['web_session_id']:
                session_id = _register_web_session_in_tx(
                    cursor, tenant_id, user_id, task_id, task['title'], locator)
                cursor.execute(
                    '''UPDATE bs_wecom_proxy_task_sessions SET web_session_id=%s,
                           updated_at=CURRENT_TIMESTAMP WHERE id=%s''',
                    (session_id, target['id']))
        cursor.execute(
            '''UPDATE bs_wecom_proxy_tasks SET status='running', stop_requested=FALSE,
                   revision=revision+1, updated_at=CURRENT_TIMESTAMP WHERE id=%s RETURNING *''',
            (task_id,))
        task = _row(cursor.fetchone())
        conn.commit()
    return task


# ==================== 停止 / 编辑 / 软删除 ====================

def settle_stop_in_tx(cursor, task):
    """停止收敛：stop_requested 置位且无 running 批（当前批结束或进入等待）时落 stopped。

    stop_requested 保留为请求留痕，启动时清零；P4 无真实批次时立即收敛，
    P5 批次结束后复用。返回是否发生收敛。
    """
    if not task.get('stop_requested') or task.get('status') != TASK_RUNNING:
        return False
    cursor.execute(
        '''SELECT 1 FROM bs_wecom_proxy_batches
           WHERE tenant_id=%s AND task_id=%s AND status='running' LIMIT 1''',
        (task['tenant_id'], task['id']))
    if cursor.fetchone():
        return False
    cursor.execute(
        '''UPDATE bs_wecom_proxy_tasks SET status='stopped', updated_at=CURRENT_TIMESTAMP
           WHERE id=%s''', (task['id'],))
    return True


def request_stop(tenant_id, user_id, task_id):
    """停止：置 stop_requested（running 中允许）；当前无执行时立即落 stopped。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        task = _task_for_update(cursor, tenant_id, user_id, task_id)
        if task['status'] == TASK_DELETED:
            raise _err('PROXY_TASK_DELETED', '任务已删除')
        if task['status'] != TASK_RUNNING:
            raise _err('PROXY_STATE_CONFLICT', '任务不在运行中')
        cursor.execute(
            '''UPDATE bs_wecom_proxy_tasks SET stop_requested=TRUE, updated_at=CURRENT_TIMESTAMP
               WHERE id=%s RETURNING *''', (task_id,))
        task = _row(cursor.fetchone())
        settled = settle_stop_in_tx(cursor, task)
        if settled:
            cursor.execute('SELECT * FROM bs_wecom_proxy_tasks WHERE id=%s', (task_id,))
            task = _row(cursor.fetchone())
        conn.commit()
    task['stop_settled'] = settled
    return task


def end_undecided_batches_in_tx(cursor, tenant_id, task_id, sessions, end_status):
    """未决批次（pending/waiting/reconcile_hold）转 ended_by_*，不得再恢复发送。"""
    if end_status not in BATCH_END_STATUSES:
        raise ValueError(f'非法批次结束状态：{end_status}')
    conditions = ['tenant_id=%s', 'task_id=%s', 'status=ANY(%s)']
    params = [tenant_id, task_id, list(BATCH_UNDECIDED)]
    if sessions:
        conditions.append('session=ANY(%s)')
        params.append(list(sessions))
    cursor.execute(
        f'''UPDATE bs_wecom_proxy_batches
            SET status=%s, finished_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
            WHERE {' AND '.join(conditions)}''',
        (end_status, *params))
    return cursor.rowcount


def mark_target_halted_in_tx(cursor, tenant_id, task_id, session, reason):
    """目标停住待人工（零发送重试再失败、代际/游标失效等；其他会话继续）。P5 复用。"""
    cursor.execute(
        '''UPDATE bs_wecom_proxy_task_sessions SET status='halted', halted_reason=%s,
               halted_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
           WHERE tenant_id=%s AND task_id=%s AND session=%s AND status='active' ''',
        (reason[:500], tenant_id, task_id, session))
    return cursor.rowcount > 0


def edit_stopped_task(tenant_id, user_id, task_id, *, title=None, instruction=None,
                      add_sessions=None, add_baselines=None, remove_sessions=None):
    """已停止编辑：标题/指令直接改；新增目标沿编辑加入水位落行；移除目标结批。

    - add_baselines：{locator: {'cursor','generation','watermark'}}，由编辑 API
      经受信一次性读取取得（Q3：baseline_origin='edit_join_read'，保存游标＋
      代际＋当时水位，后续启动不得覆盖）；
    - running 期间拒绝一切编辑（含 stop_requested 等待收敛期间）。
    """
    new_title = _validate_title(title) if title is not None else None
    new_instruction = _validate_instruction(instruction) if instruction is not None else None
    add_locators = _validate_sessions(add_sessions) if add_sessions else []
    remove_locators = _validate_sessions(remove_sessions) if remove_sessions else []
    overlap = set(add_locators) & set(remove_locators)
    if overlap:
        raise _err('VALIDATION_FAILED', '同一会话不能同时新增和移除', 400)
    baselines = add_baselines or {}
    with get_db_connection() as conn:
        cursor = conn.cursor()
        task = _task_for_update(cursor, tenant_id, user_id, task_id)
        if task['status'] == TASK_DELETED:
            raise _err('PROXY_TASK_DELETED', '任务已删除，不可编辑')
        if task['status'] != TASK_STOPPED:
            raise _err('PROXY_STATE_CONFLICT', '任务运行中不可编辑，请先停止')
        existing = _task_targets(cursor, tenant_id, task_id, for_update=True)
        existing_by_session = {item['session']: item for item in existing}
        for locator in add_locators:
            current = existing_by_session.get(locator)
            if current is not None and current['status'] in (TARGET_ACTIVE, TARGET_HALTED):
                raise _err('PROXY_SESSION_INVALID', '会话已在本任务中，无需重复添加', 409)
            baseline = baselines.get(locator)
            if baseline is None:
                raise _err('PROXY_BASELINE_MISSING', f'编辑加入水位读取结果缺失：{locator[:60]}')
        if add_locators:
            _validated_targets(cursor, tenant_id, user_id, add_locators,
                               account=task['wecom_account_id'], device_id=task['device_id'])
        for locator in add_locators:
            baseline = baselines[locator]
            cursor.execute(
                '''INSERT INTO bs_wecom_proxy_task_sessions
                       (tenant_id, user_id, task_id, session, source_generation, wb1_cursor,
                        baseline_origin, processed_position, latest_seen_msg_id, status)
                   VALUES (%s,%s,%s,%s,%s,%s,'edit_join_read',%s,%s,'active')
                   ON CONFLICT (tenant_id, task_id, session) DO UPDATE SET
                       status='active', source_generation=EXCLUDED.source_generation,
                       wb1_cursor=EXCLUDED.wb1_cursor,
                       baseline_origin='edit_join_read',
                       processed_position=EXCLUDED.processed_position,
                       latest_seen_msg_id=EXCLUDED.latest_seen_msg_id,
                       halted_reason=NULL, halted_at=NULL, updated_at=CURRENT_TIMESTAMP''',
                (tenant_id, user_id, task_id, locator, baseline.get('generation'),
                 baseline.get('cursor'), baseline.get('watermark'), baseline.get('watermark')))
        if remove_locators:
            cursor.execute(
                '''UPDATE bs_wecom_proxy_task_sessions SET status='removed',
                       updated_at=CURRENT_TIMESTAMP
                   WHERE tenant_id=%s AND task_id=%s AND session=ANY(%s)
                     AND status IN ('active','halted')''',
                (tenant_id, task_id, remove_locators))
            end_undecided_batches_in_tx(cursor, tenant_id, task_id, remove_locators,
                                        BATCH_ENDED_BY_REMOVAL)
        fields, params = [], []
        if new_title is not None:
            fields.append('title=%s')
            params.append(new_title)
        if new_instruction is not None:
            fields.append('instruction=%s')
            params.append(new_instruction)
        fields.append('revision=revision+1')
        fields.append('updated_at=CURRENT_TIMESTAMP')
        cursor.execute(
            f'''UPDATE bs_wecom_proxy_tasks SET {', '.join(fields)} WHERE id=%s RETURNING *''',
            (*params, task_id))
        task = _row(cursor.fetchone())
        targets = _task_targets(cursor, tenant_id, task_id)
        conn.commit()
    task['targets'] = targets
    return task


def soft_delete_task(tenant_id, user_id, task_id):
    """软删除：仅已停止可删；置 deleted、退出归属，未决批次转 ended_by_delete。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        task = _task_for_update(cursor, tenant_id, user_id, task_id)
        if task['status'] == TASK_DELETED:
            raise _err('PROXY_ALREADY_DELETED', '任务已删除')
        if task['status'] != TASK_STOPPED:
            raise _err('PROXY_STATE_CONFLICT', '仅已停止任务可删除，请先停止')
        cursor.execute(
            '''UPDATE bs_wecom_proxy_tasks SET status='deleted', deleted_at=CURRENT_TIMESTAMP,
                   revision=revision+1, updated_at=CURRENT_TIMESTAMP WHERE id=%s RETURNING *''',
            (task_id,))
        task = _row(cursor.fetchone())
        end_undecided_batches_in_tx(cursor, tenant_id, task_id, None, BATCH_ENDED_BY_DELETE)
        conn.commit()
    return task


# ==================== 批次建行与状态迁移（P5/P6 复用） ====================

def create_batch_in_tx(cursor, tenant_id, user_id, *, task_id, session, batch_end_msg_id,
                       input_snapshot, agent_id, instruction_snapshot, web_session_id):
    """建 pending 批次（冻结上界＋输入快照＋当时指令；P5 监控循环接入）。

    同会话仅一个未决批次由部分唯一索引 uq_bs_wecom_proxy_batches_session_undecided
    在事务内核对（P4 CR 备注③）：SELECT FOR UPDATE 锁不住不存在的行（幻影读），
    唯一索引在 READ COMMITTED 下原子判重且覆盖 P6 全部写入路径。并发重复建批时
    后写方收到 PROXY_BATCH_OCCUPIED（唯一冲突已中止当前事务，由调用方回滚）。
    """
    try:
        cursor.execute(
            '''INSERT INTO bs_wecom_proxy_batches
                   (tenant_id, user_id, task_id, session, batch_end_msg_id, input_snapshot,
                    agent_id, instruction_snapshot, web_session_id, status)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending') RETURNING *''',
            (tenant_id, user_id, task_id, session, batch_end_msg_id,
             Json(input_snapshot) if input_snapshot is not None else None,
             agent_id, instruction_snapshot, web_session_id))
    except pg_errors.UniqueViolation:
        raise _err('PROXY_BATCH_OCCUPIED',
                   f'该会话已有未决批次，不重复建批：{(session or "")[:60]}') from None
    return _row(cursor.fetchone())


def requeue_waiting_batches_in_tx(cursor, tenant_id, *, task_ids=None):
    """waiting 批次恢复重排（P5 机制，恢复 Runner 提交属 P6 挂钩）。

    任务 running、目标 active 且任务未 stop_requested 的等待批 waiting→pending
    回队列；updated_at 刷新为重排时间，派发侧（P6）按 updated_at 取队首即得
    "重排到队尾、其他会话先执行"语义。stop_requested、目标 removed 或任务
    deleted 的等待批不恢复（批次保留，留待下次启动后排队）。reconcile_hold
    不可自动恢复（状态白名单拒绝）；终态 failed 不在此恢复——failed→pending
    仅承载零发送重试一次，由 P6 核定路径执行，监控/恢复路径不对终态失败批
    再建同 Runner。返回重排批次数。
    """
    sql = '''SELECT b.id FROM bs_wecom_proxy_batches b
             JOIN bs_wecom_proxy_tasks t ON t.id=b.task_id AND t.tenant_id=b.tenant_id
             JOIN bs_wecom_proxy_task_sessions s ON s.tenant_id=b.tenant_id
                    AND s.task_id=b.task_id AND s.session=b.session
             WHERE b.tenant_id=%s AND b.status=%s AND t.status=%s
               AND t.stop_requested=FALSE AND s.status=%s'''
    params = [tenant_id, BATCH_WAITING, TASK_RUNNING, TARGET_ACTIVE]
    if task_ids:
        sql += ' AND b.task_id=ANY(%s)'
        params.append(list(task_ids))
    sql += ' ORDER BY b.id'
    cursor.execute(sql, tuple(params))
    batch_ids = [row['id'] for row in cursor.fetchall()]
    requeued = 0
    for batch_id in batch_ids:
        try:
            transition_batch_in_tx(cursor, tenant_id, batch_id, BATCH_PENDING)
            requeued += 1
        except ConversationServiceError as exc:
            if exc.code != 'PROXY_BATCH_STATE_INVALID':
                raise
            # 并发下状态已变（如 P6 已恢复派发）：跳过该批，不中断重排
    return requeued


def transition_batch_in_tx(cursor, tenant_id, batch_id, to_status, *, runner_id=None,
                           cli_error=None, end_json=None, retry=False):
    """按契约状态机推进批次；非法迁移拒绝（reconcile_hold 不可自动恢复）。

    - →running 记 started_at（首派 COALESCE 保留首次）；终态记 finished_at；
    - retry=True 仅承载 failed→pending 的零发送重试一次（retry_count+1）；
    - runner_id/cli_error/end_json 提供时随迁移写入。
    """
    cursor.execute(
        '''SELECT * FROM bs_wecom_proxy_batches WHERE id=%s AND tenant_id=%s FOR UPDATE''',
        (batch_id, tenant_id))
    batch = _row(cursor.fetchone())
    if batch is None:
        raise _err('PROXY_NOT_FOUND', '批次不存在或无权访问', 404)
    if batch['status'] == to_status:
        return batch
    allowed = BATCH_TRANSITIONS.get(batch['status'], set())
    if to_status not in allowed:
        raise _err('PROXY_BATCH_STATE_INVALID',
                   f'批次状态不允许从 {batch["status"]} 迁移到 {to_status}')
    fields = ['status=%s', 'updated_at=CURRENT_TIMESTAMP']
    params = [to_status]
    if runner_id is not None:
        fields.append('runner_id=%s')
        params.append(runner_id)
    if cli_error is not None:
        fields.append('cli_error=%s')
        params.append(cli_error)
    if end_json is not None:
        fields.append('end_json=%s')
        params.append(Json(end_json))
    if to_status == BATCH_RUNNING:
        fields.append('started_at=COALESCE(started_at, CURRENT_TIMESTAMP)')
    if to_status in BATCH_TERMINAL:
        fields.append('finished_at=CURRENT_TIMESTAMP')
    if retry:
        fields.append('retry_count=retry_count+1')
    cursor.execute(
        f'''UPDATE bs_wecom_proxy_batches SET {', '.join(fields)} WHERE id=%s RETURNING *''',
        (*params, batch_id))
    return _row(cursor.fetchone())


def session_watermark(data, locator):
    """从 wecom_read_sessions 结果提取该会话已见最高 msg_id（当前水位）。"""
    if not isinstance(data, dict):
        return None
    entries = []
    if isinstance(data.get('sessions'), list):
        entries = [item for item in data['sessions'] if isinstance(item, dict)]
    elif data.get('session') == locator and isinstance(data.get('messages'), list):
        entries = [{'session': locator, 'messages': data['messages']}]
    for entry in entries:
        if entry.get('session') != locator:
            continue
        ids = [item.get('msg_id') for item in entry.get('messages') or []
               if isinstance(item, dict) and isinstance(item.get('msg_id'), int)
               and not isinstance(item.get('msg_id'), bool)]
        return max(ids) if ids else None
    return None
