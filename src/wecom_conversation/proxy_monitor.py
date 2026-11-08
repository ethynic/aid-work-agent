"""企微代理任务后台监控循环与持久队列（代理任务契约 §3/§4，设计 §9.3，P5）。

宿主（契约 Q1 裁决）：background_runner 进程内 APScheduler 轮询（注册见
src/scheduler/manager.py，间隔 10s，max_instances=1 整轮未完成不重叠）；
调度器有 manager 级 Redis 锁，同一设备/账号全集群只有一个循环持有者。
每个 tick 汇总 running 任务＋active 目标，按（租户/用户/设备/企微账号）分组
串行处理，同组同会话只读一次；触发为固定 10s 栅格，整轮未完成不重叠
（max_instances=1），错过的触发合并为至多一次补跑（coalesce，宽限 300s），
不承诺每会话严格 10s（设计 §9.3）。

位置三事实（契约 §4，不可混用）：wb1_cursor＝下次监控读起点，每次成功排空后
随建批同一事务推进；processed_position＝已确认处理位置，只有 P6 核定推进，
监控只读不写；latest_seen_msg_id＝最近一次实际读取观测的库内最新位置。
批次表即持久队列（Q4），同会话仅一个未决批次由部分唯一索引强制（见 tables.py）。
消息级去重依赖"建批＋游标推进同事务"：崩溃后重读从原游标开始，候选按
msg_id > processed_position 重算，不会双建批。
"""
from __future__ import annotations

import asyncio
import json
import threading
from collections import OrderedDict

from loguru import logger

from . import proxy_tasks
from .proxy_api import READ_MAX_PAGES

# 监控读只核对读取能力（发送核对属 P6 派发路径，契约 §3 启动条件已含三者）
_MONITOR_REQUIRED_TOOLS = ('wecom_read_sessions',)

# 瞬态失败：本轮跳过等待重试，不判会话失败不 halt（外部 BUSY 等待重试，
# Runtime 离线恢复后自动从原位置继续；设备生命周期问题不转嫁为会话 halted）。
# IDENTITY_UNCONFIRMED＝企微切号（§9.3：中断且不改绑定，原账号重新登录后自动
# 继续）；PROXY_CAPABILITY_MISSING 为 invocation 级重映射码，与 failed 路径的
# LOCAL_DEVICE_UNSUPPORTED 对齐——缺读能力属环境条件，升级后恢复
_TRANSIENT_READ_CODES = frozenset({
    'BUSY', 'LOCAL_RECOVERY_BUSY',
    'DEVICE_OFFLINE', 'DEVICE_UNAVAILABLE', 'PROXY_CAPABILITY_MISSING',
    'LOCAL_DEVICE_OFFLINE', 'LOCAL_DEVICE_UNSUPPORTED', 'LOCAL_DEVICE_NOT_READY',
    'IDENTITY_UNCONFIRMED',
})

# 会话占用状态：未决批次（pending/running/waiting/reconcile_hold，契约 §4 队列
# 占用覆盖监控读与回答两阶段）＋终态 failed。failed 亦占用：终态失败释放队列
# 位置（其他会话先执行），但重试只允许 §5 规则 3 的一次新执行（P6 核定路径），
# 监控不得为其消息范围另建新批，否则绕过"重试一次"上限
_OCCUPYING_BATCH_STATUSES = (
    proxy_tasks.BATCH_PENDING, proxy_tasks.BATCH_RUNNING, proxy_tasks.BATCH_WAITING,
    proxy_tasks.BATCH_RECONCILE_HOLD, proxy_tasks.BATCH_FAILED,
)


def _proxy_api():
    """受信读取与设备闸门沿 P4 API 层（延迟导入，保持本模块轻量可测）。"""
    from . import proxy_api
    return proxy_api


# 组级互斥锁（P6 派发协调）：同进程内监控组读与派发器的"占用＋提交"临界区
# 互斥（try-acquire，抢不到本轮跳过），双向不与对方抢占同一设备 CLI；跨进程
# 单持有者由调度器 manager 级 Redis 锁保证（Q1）。
_group_locks_guard = threading.Lock()
_group_locks: dict = {}


def group_lock(key):
    """同设备/账号组的互斥锁（监控读与 P6 派发共用，惰性创建）。"""
    with _group_locks_guard:
        lock = _group_locks.get(key)
        if lock is None:
            lock = _group_locks[key] = threading.Lock()
        return lock


def _error_parts(error):
    """_err 返回（JSONResponse 或测试桩 dict）→ (code, message)。"""
    if isinstance(error, dict):
        return error.get('code') or 'PROXY_READ_FAILED', error.get('message') or ''
    body = getattr(error, 'body', None)
    if isinstance(body, (bytes, bytearray)):
        try:
            payload = json.loads(bytes(body).decode('utf-8'))
            return payload.get('code') or 'PROXY_READ_FAILED', payload.get('error') or ''
        except ValueError:
            pass
    return 'PROXY_READ_FAILED', ''


# ==================== tick：加载 → 停止收敛 → 等待批重排 → 分组读 ====================

async def run_tick():
    """监控单轮：停止收敛 → waiting 重排 → 按设备/账号分组串行监控读。

    单组/单步异常只记日志不中断整轮（下 tick 从持久事实继续）；无任务时空转。
    """
    tasks = await asyncio.to_thread(_load_running_tasks)
    items = await asyncio.to_thread(_load_work_items)
    if not tasks and not items:
        return
    logger.debug(f'后端日志：企微代理监控 tick tasks={len(tasks)} targets={len(items)}')
    try:
        await asyncio.to_thread(_settle_stop_requested, tasks)
    except Exception as e:  # noqa: BLE001 单步失败不中断整轮
        logger.opt(exception=True).error(f'后端日志：企微代理停止收敛异常: {e}')
    try:
        await asyncio.to_thread(_requeue_waiting, tasks)
    except Exception as e:  # noqa: BLE001
        logger.opt(exception=True).error(f'后端日志：企微代理等待批重排异常: {e}')
    for group in _group_items(items).values():
        try:
            await _run_group(group)
        except Exception as e:  # noqa: BLE001 单组失败不中断其他组
            logger.opt(exception=True).error(f'后端日志：企微代理监控组处理异常: {e}')


def _load_running_tasks():
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT * FROM bs_wecom_proxy_tasks WHERE status=%s ORDER BY id''',
            (proxy_tasks.TASK_RUNNING,))
        return [dict(row) for row in cursor.fetchall()]


def _load_work_items():
    """running 任务＋active 目标（含任务停止标志与位置三事实快照）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT t.id AS task_id, t.tenant_id, t.user_id, t.device_id,
                      t.wecom_account_id, t.agent_id, t.instruction, t.stop_requested,
                      s.id AS target_id, s.session, s.web_session_id, s.source_generation,
                      s.wb1_cursor, s.processed_position
               FROM bs_wecom_proxy_tasks t
               JOIN bs_wecom_proxy_task_sessions s
                 ON s.tenant_id=t.tenant_id AND s.task_id=t.id
               WHERE t.status=%s AND s.status=%s
               ORDER BY t.id, s.id''',
            (proxy_tasks.TASK_RUNNING, proxy_tasks.TARGET_ACTIVE))
        return [dict(row) for row in cursor.fetchall()]


def _group_items(items):
    """按（租户/用户/设备/企微账号）分组；设备绑定租户/用户，同组即同设备/账号。"""
    groups = OrderedDict()
    for item in items:
        key = (item['tenant_id'], item['user_id'], str(item['device_id']),
               item['wecom_account_id'])
        groups.setdefault(key, []).append(item)
    return groups


def _settle_stop_requested(tasks):
    """stop_requested 收敛落 stopped（契约 §4：当前批结束或进入等待后）。

    settle_stop_in_tx 内部核对无 running 批：停止请求不阻断已运行批次；组内
    已无未决批时本 tick 直接落 stopped，有等待批也收敛（等待批保留、停止期间
    不恢复，留待下次"启动"后排队——P4 test_settle_stop_waits_for_running_batch 语义）。
    """
    pending = [task for task in tasks if task.get('stop_requested')]
    if not pending:
        return
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        for task in pending:
            cursor.execute(
                '''SELECT * FROM bs_wecom_proxy_tasks WHERE id=%s AND tenant_id=%s FOR UPDATE''',
                (task['id'], task['tenant_id']))
            row = cursor.fetchone()
            if row is not None and proxy_tasks.settle_stop_in_tx(cursor, dict(row)):
                logger.info(f'后端日志：企微代理任务停止收敛 task={task["id"]}')
        conn.commit()


def _requeue_waiting(tasks):
    """组内任务的 waiting 批次恢复重排（stop_requested 任务不恢复）。"""
    task_ids_by_tenant = {}
    for task in tasks:
        if not task.get('stop_requested'):
            task_ids_by_tenant.setdefault(task['tenant_id'], []).append(task['id'])
    for tenant_id, task_ids in task_ids_by_tenant.items():
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            requeued = proxy_tasks.requeue_waiting_batches_in_tx(cursor, tenant_id,
                                                                 task_ids=task_ids)
            conn.commit()
        if requeued:
            logger.info(f'后端日志：企微代理等待批重排 tenant={tenant_id} count={requeued}')


# ==================== 组处理：设备闸门 → 占用核对 → 逐目标受信读 ====================

def _group_runner_busy(tenant_id, device_id, account):
    """组级 Runner 占用：同（租户/设备/企微账号）存在 running 批次时整组本轮不读。

    设计 §9.3：队列占用覆盖监控读与回答两阶段，Runner 运行时不发起下一轮
    监控读（避免监控读与自己的 Runner 抢 CLI），结束或进入等待后再补读。
    waiting/reconcile_hold 已释放/结束执行，不阻塞组级读取，仅按会话级占用跳过。
    """
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT 1 FROM bs_wecom_proxy_batches b
               JOIN bs_wecom_proxy_tasks t ON t.id=b.task_id AND t.tenant_id=b.tenant_id
               WHERE b.tenant_id=%s AND t.device_id=%s AND t.wecom_account_id=%s
                 AND b.status=%s LIMIT 1''',
            (tenant_id, device_id, account, proxy_tasks.BATCH_RUNNING))
        return cursor.fetchone() is not None


async def _run_group(items):
    """单设备/账号组一轮：设备闸门 → 组级 Runner 占用 → 会话占用 → 逐目标读。

    全程持有组级锁（P6 派发协调）：派发器正占用/提交该组批次时本轮跳过，
    不与 Runner 启动抢占同一 CLI（设计 §9.3 串行占用）。
    """
    first = items[0]
    key = (first['tenant_id'], str(first['device_id']), first['wecom_account_id'])
    lock = group_lock(key)
    if not lock.acquire(blocking=False):
        # P6 派发器正占用/提交该组批次：本轮跳过，下轮再读
        logger.info(f'后端日志：企微代理派发器占用组级锁，本轮跳过读取 '
                    f'device={key[1]} account={key[2]}')
        return
    try:
        await _run_group_locked(items)
    finally:
        lock.release()


async def _run_group_locked(items):
    """组级锁内的单组处理体。"""
    first = items[0]
    tenant_id, user_id = first['tenant_id'], first['user_id']
    device_id = str(first['device_id'])
    proxy_api = _proxy_api()
    device, error = await proxy_api._task_device(tenant_id, user_id, device_id,
                                                 _MONITOR_REQUIRED_TOOLS)
    if error:
        # Runtime 离线/设备不可用：本轮跳过并记日志，不失败任务（设计 §9.3）
        code, message = _error_parts(error)
        logger.warning(f'后端日志：企微代理监控设备不可用本轮跳过 device={device_id} '
                       f'code={code} message={message}')
        return
    if await asyncio.to_thread(_group_runner_busy, tenant_id, device_id,
                               first['wecom_account_id']):
        logger.info(f'后端日志：企微代理组内回答 Runner 执行中，本轮整组不读 '
                    f'device={device_id} account={first["wecom_account_id"]}')
        return
    occupied = await asyncio.to_thread(_occupied_sessions, tenant_id,
                                       [item['session'] for item in items])
    handled = set()
    for item in items:
        locator = item['session']
        if locator in handled:
            continue  # 同组同会话只读一次（设计 §9.3；启动归属唯一性下的防御）
        handled.add(locator)
        if item['stop_requested']:
            continue  # stop_requested 任务不读不建新批（契约 §4 停止检查点）
        if locator in occupied:
            continue  # 会话已有未决/失败批：不重读不重建（推进属 P6/人工）
        if not item['wb1_cursor']:
            # 无基线不读（启动流程保证有基线；防御中途数据异常避免盲读）
            logger.warning(f'后端日志：企微代理目标缺少基线游标本轮跳过 '
                           f'task={item["task_id"]} session={locator[:60]}')
            continue
        await _process_target(item, device)


def _occupied_sessions(tenant_id, sessions):
    """组内已被批次占用（未决＋终态失败）的会话集合。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT DISTINCT session FROM bs_wecom_proxy_batches
               WHERE tenant_id=%s AND session=ANY(%s) AND status=ANY(%s)''',
            (tenant_id, list(sessions), list(_OCCUPYING_BATCH_STATUSES)))
        return {row['session'] for row in cursor.fetchall()}


async def _process_target(item, device):
    """单目标：受信读排空 → 候选消费 → 建批/推进游标（失败按语义分流）。"""
    tenant_id, locator = item['tenant_id'], item['session']
    pages, error = await _drain_read(tenant_id, item['user_id'], device, locator,
                                     item['wb1_cursor'])
    if error is not None:
        code, message = error
        if code in _TRANSIENT_READ_CODES:
            logger.warning(f'后端日志：企微代理监控读瞬态失败本轮跳过 '
                           f'task={item["task_id"]} session={locator[:60]} code={code}')
            return
        # 源库代际/游标失效、目标读取失败等：halted 该会话并显示原因，其他会话继续
        # （监控读不重试到成功；halted_reason 记 CLI 失败码）
        logger.error(f'后端日志：企微代理目标停住待人工 task={item["task_id"]} '
                     f'session={locator[:60]} code={code} message={message}')
        await asyncio.to_thread(_halt_target, tenant_id, item['task_id'], locator, code)
        return
    candidates, max_seen = _collect_candidates(pages, locator, item['processed_position'])
    cursor, generation = _final_read_position(pages)
    try:
        outcome = await asyncio.to_thread(
            _persist_read_result, tenant_id, item['user_id'], item,
            cursor, generation, max_seen, candidates)
    except proxy_tasks.ConversationServiceError as exc:
        if exc.code == 'PROXY_BATCH_OCCUPIED':
            # 并发下同会话未决批已由他方创建（部分唯一索引判出）：回滚已由
            # 连接上下文完成，下轮按占用跳过
            logger.info(f'后端日志：企微代理建批遇会话占用，下轮跳过 '
                        f'task={item["task_id"]} session={locator[:60]}')
            return
        raise
    if outcome == 'batch':
        logger.info(f'后端日志：企微代理监控建批 task={item["task_id"]} '
                    f'session={locator[:60]} batch_end={candidates[-1]["msg_id"]} '
                    f'candidates={len(candidates)}')


async def _drain_read(tenant_id, user_id, device, locator, cursor):
    """单目标受信读按 has_more 有界排空（沿 _baseline_read 的页数上限）。

    返回 (页列表, None)；失败返回 (None, (code, message))——单目标 failed 数组
    透传 CLI 失败码，invocation 级错误透传其失败码。
    """
    proxy_api = _proxy_api()
    pages = []
    for _ in range(READ_MAX_PAGES):
        arguments = {'sessions': [locator], 'limit': 50}
        if cursor:
            arguments['cursor'] = cursor
        data, call_error = await proxy_api._read_sessions_call(tenant_id, user_id,
                                                               device, arguments)
        if call_error is not None:
            return None, _error_parts(call_error)
        # CLI 批量读对单目标失败仍返回总体成功＋failed 数组＋游标：与 P4 评审修复
        # 同语义，本目标按失败处理（halted），不把部分结果当成功消费
        failed = data.get('failed')
        if isinstance(failed, list) and failed:
            first = failed[0] if isinstance(failed[0], dict) else {}
            return None, (first.get('code') or 'PROXY_READ_FAILED', '目标会话本地读取失败')
        page_cursor = data.get('cursor')
        if not isinstance(page_cursor, str) or not page_cursor:
            return None, ('PROXY_CURSOR_MISSING', '本地读取结果缺少游标')
        pages.append(data)
        cursor = page_cursor
        if data.get('has_more') is not True:
            return pages, None
    return None, ('PROXY_BACKLOG_TOO_LARGE',
                  '会话积压超过单次读取上限（20 页×50 条），请先消化积压后重试')


def _page_messages(data, locator):
    """单页读取结果中该会话的消息列表（批量形状为主，单会话形状兜底）。"""
    if not isinstance(data, dict):
        return []
    for entry in data.get('sessions') or []:
        if isinstance(entry, dict) and entry.get('session') == locator:
            return [item for item in entry.get('messages') or [] if isinstance(item, dict)]
    if data.get('session') == locator and isinstance(data.get('messages'), list):
        return [item for item in data['messages'] if isinstance(item, dict)]
    return []


def _collect_candidates(pages, locator, processed_position):
    """读取页 → (候选消息列表, 本轮最大 msg_id)。

    候选＝side=peer、非系统、msg_id > processed_position（设计 §9.3：本机自己
    发送与系统消息不激活；群聊不按关键词预判，多个成员一并入批）。同页内按
    msg_id 去重保持原顺序，输入快照取原消息 dict（原文＋身份字段原样）。
    """
    candidates_by_id = {}
    max_seen = None
    for data in pages:
        for message in _page_messages(data, locator):
            msg_id = message.get('msg_id')
            if not isinstance(msg_id, int) or isinstance(msg_id, bool):
                continue
            if max_seen is None or msg_id > max_seen:
                max_seen = msg_id
            if message.get('side') != 'peer' or message.get('system') is True:
                continue
            if processed_position is not None and msg_id <= processed_position:
                continue
            candidates_by_id.setdefault(msg_id, message)
    candidates = [candidates_by_id[key] for key in sorted(candidates_by_id)]
    return candidates, max_seen


def _final_read_position(pages):
    """排空后的读取位置＝末页游标与源库代次（下次监控读起点，契约 §4 三事实①）。"""
    last = pages[-1] if pages else {}
    return last.get('cursor'), last.get('source_generation')


def _persist_read_result(tenant_id, user_id, item, cursor, generation, max_seen, candidates):
    """读取结果落库（单事务）：候选先建批（冻结上界＋输入快照），再推进游标。

    - 任务行 FOR UPDATE 复核：与停止/软删除 API 串行，停止/删除后不建批、
      不推进（下次启动从保存的游标补读停顿期间的新消息）；
    - wb1_cursor 推进到读取位置；processed_position 保持（推进语义属 P6 核定）；
      latest_seen_msg_id 更新为本轮最大 msg_id；
    - 无候选零批次；同会话并发建批由部分唯一索引判出（PROXY_BATCH_OCCUPIED）。
    返回 'batch'（已建批）| 'cursor'（仅推进游标）| 'skipped'（任务已停止等）。
    """
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        db = conn.cursor()
        db.execute(
            '''SELECT status, stop_requested FROM bs_wecom_proxy_tasks
               WHERE id=%s AND tenant_id=%s FOR UPDATE''',
            (item['task_id'], tenant_id))
        task = db.fetchone()
        if task is None or task['status'] != proxy_tasks.TASK_RUNNING \
                or task['stop_requested']:
            return 'skipped'
        if candidates:
            proxy_tasks.create_batch_in_tx(
                db, tenant_id, user_id, task_id=item['task_id'], session=item['session'],
                batch_end_msg_id=max(message['msg_id'] for message in candidates),
                input_snapshot={
                    'schema_version': proxy_tasks.PROXY_SCHEMA_VERSION,
                    'session': item['session'],
                    'source_generation': generation,
                    'messages': candidates,
                },
                agent_id=item['agent_id'],
                instruction_snapshot=item['instruction'],
                web_session_id=item['web_session_id'])
        assignments = ['wb1_cursor=%s', 'updated_at=CURRENT_TIMESTAMP']
        params = [cursor]
        if generation:
            assignments.append('source_generation=%s')
            params.append(generation)
        if max_seen is not None:
            assignments.append('latest_seen_msg_id=%s')
            params.append(max_seen)
        params.append(item['target_id'])
        db.execute(
            f'''UPDATE bs_wecom_proxy_task_sessions SET {', '.join(assignments)}
                WHERE id=%s AND status='active' ''', tuple(params))
        conn.commit()
    return 'batch' if candidates else 'cursor'


def _halt_target(tenant_id, task_id, session, reason):
    """目标停住待人工（CLI 失败码入 halted_reason）；其他会话不受影响。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        if proxy_tasks.mark_target_halted_in_tx(cursor, tenant_id, task_id, session, reason):
            conn.commit()
            return True
        return False
