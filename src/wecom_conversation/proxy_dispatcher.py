"""企微代理批次派发器与 Runner 编排（代理任务契约 §5/§6，设计 §9.3，P6）。

宿主：background_runner 进程内 APScheduler 轮询（注册见 src/scheduler/manager.py，
与监控循环同进程——Q1 裁决全集群单持有者）；单轮四步：
① 核定：running 批次 Runner 已终态 → proxy_settlement.adjudicate_batch；
② 释放：Runner 进入 waiting/paused → 批次 running→waiting（释放队列位置，
   监控恢复重排后由本模块恢复原 Runner，非新批）；
③ 恢复驱动：Runner interrupted（发送 unknown 等待核验）→ 幂等 resume 控制
   （同 attempt 同键幂等）；attempt 达上限仍中断 → 按事实落 reconcile_hold；
④ 派发：pending 批次按 updated_at＋id 取队首（P5 遗留① tiebreaker）→
   派发前核对 → 批次置 running（占用即监控组级阻塞信号）→ 提交 Runner。

与监控循环的协调（同进程，线程锁精确串行）：批次置 running 与监控组级读取
共用 proxy_monitor.group_lock（同设备/账号组互斥）——监控读期间不派发，
Runner 运行期间监控经 _group_runner_busy（查 running 批）整组不读；双向不抢
同一 CLI。本模块不改监控逻辑。

提交身份：wecom_proxy 服务身份（契约 §6）；幂等键 '{batch_id}:{retry_count}'
——提交层瞬时失败保留 running 批次同键重试（幂等命中原 Runner，不重复建
执行），确定性拒绝按语义停住/回退，不让队列卡死。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from loguru import logger

from . import proxy_settlement, proxy_tasks
from .proxy_monitor import group_lock

# 服务身份（configs/config.yaml agent_runner.peers.wecom_proxy）；
# token 复用同一 .env 的 AGENT_RUNNER_WEB_SERVICE_TOKEN，无新增 env
PROXY_SERVICE_ID = 'wecom_proxy'

# 派发前核对的运行条件（契约 §3：监控读取/历史读取/发送能力）
DISPATCH_REQUIRED_TOOLS = proxy_tasks.RUNTIME_TOOL_NAMES

# 确定性提交拒绝中的权限/额度类：失权即停任务（契约 §6"恢复后由用户手动启动"）
_PERMISSION_REJECT_CODES = frozenset({
    'CREDIT_BLOCKED', 'PROFILE_FORBIDDEN', 'TENANT_FORBIDDEN', 'TENANT_UNAVAILABLE',
    'TENANT_EXPIRED', 'USER_UNAUTHORIZED',
})

# 任务已不在可派发状态（派发核对竞态）：批次回 pending 留待下次启动排队
_REQUEUE_REJECT_CODES = frozenset({'WECOM_PROXY_TASK_NOT_RUNNING'})


def _error_code(error):
    return getattr(error, 'code', '') or ''


def _is_transient(error):
    """网络未知/服务不可用类：不当作确定性结果（沿调度侧语义重试）。"""
    from src.services.agent_runner.contracts import RunnerError

    if not isinstance(error, RunnerError):
        return True
    return error.code in ('RUNNER_SERVICE_UNAVAILABLE', 'RUNNER_GATEWAY_NOT_CONFIGURED',
                          'RUNNER_SERVICE_INVALID_RESPONSE', 'RUNNER_SERVICE_ERROR',
                          'RUNNER_SERVICE_REDIRECT_FORBIDDEN') or (
        error.status is not None and error.status >= 500)


class _ProxyServiceView:
    """仅暴露 RunnerServiceClient.request 用到的两个字段（沿调度侧薄封装）。"""

    def __init__(self, source):
        self.api_url = getattr(source, 'api_url', None) or 'http://127.0.0.1:8091'
        self.web_service_id = PROXY_SERVICE_ID


def _proxy_client():
    """wecom_proxy 服务身份的 Runner 网关客户端。"""
    from src.services.agent_runner.web_client import RunnerServiceClient

    config = None
    try:
        from src.config.settings import settings

        config = settings.agent_runner
    except Exception:  # noqa: BLE001 配置缺失时回退默认 api_url，token 仍取 env
        pass
    return RunnerServiceClient(_ProxyServiceView(config))


def _proxy_api():
    from . import proxy_api
    return proxy_api


# ==================== 单轮编排 ====================

async def run_tick():
    """派发单轮：核定 → 释放 → 恢复驱动 → 派发（单步异常不中断整轮）。"""
    batches = await asyncio.to_thread(_load_active_batches)
    if not batches:
        return
    runner_states = await asyncio.to_thread(
        _load_runner_states, [item['runner_id'] for item in batches if item['runner_id']])
    running, pending, orphans = [], [], []
    for batch in batches:
        if batch['status'] == proxy_tasks.BATCH_RUNNING:
            # Runner 行缺失（提交已置占用但回写失败/行丢失）：同键重试提交
            if not batch['runner_id'] or batch['runner_id'] not in runner_states:
                orphans.append(batch)
            else:
                running.append(batch)
        else:
            pending.append(batch)
    for batch in running:
        try:
            await _settle_running_batch(batch, runner_states)
        except Exception as e:  # noqa: BLE001 单批失败不中断整轮
            logger.opt(exception=True).error(
                f'后端日志：企微代理运行批处理异常 batch={batch["id"]} {e}')
    for batch in orphans:
        try:
            await _submit_running_batch(batch)
        except Exception as e:  # noqa: BLE001
            logger.opt(exception=True).error(
                f'后端日志：企微代理孤儿批重提异常 batch={batch["id"]} {e}')
    # 同组派发前按当前占用重查：running/孤儿批（含本轮新占用）阻塞同组其余批次
    busy_groups = {(item['tenant_id'], str(item['device_id']), item['wecom_account_id'])
                   for item in running + orphans}
    dispatched = set()
    for batch in pending:
        key = (batch['tenant_id'], str(batch['device_id']), batch['wecom_account_id'])
        if key in busy_groups or key in dispatched:
            continue  # 同设备/账号组同时只允许一个 Runner（组级占用语义）
        try:
            if await _dispatch_batch(batch, runner_states):
                dispatched.add(key)
        except Exception as e:  # noqa: BLE001 单批失败不中断整轮
            logger.opt(exception=True).error(
                f'后端日志：企微代理派发异常 batch={batch["id"]} {e}')


def _load_active_batches():
    """running＋可派发 pending 批次（含任务/目标派发核对字段快照）。

    pending 仅取任务 running、未 stop_requested、目标 active 的批次（派发前
    核对下沉到装载，契约 §6 新批派发检查点）；running 批次不受这些条件过滤
    ——停止请求与目标移除不阻断已运行批次（契约 §6）。
    """
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT b.*, t.device_id, t.wecom_account_id, t.status AS task_status,
                      t.stop_requested, t.self_user_id, s.status AS target_status
               FROM bs_wecom_proxy_batches b
               JOIN bs_wecom_proxy_tasks t ON t.id=b.task_id AND t.tenant_id=b.tenant_id
               LEFT JOIN bs_wecom_proxy_task_sessions s ON s.tenant_id=b.tenant_id
                      AND s.task_id=b.task_id AND s.session=b.session
               WHERE b.status=%s OR (b.status=%s AND t.status=%s AND t.stop_requested=FALSE
                     AND s.status=%s)
               ORDER BY b.updated_at, b.id''',
            (proxy_tasks.BATCH_RUNNING, proxy_tasks.BATCH_PENDING, proxy_tasks.TASK_RUNNING,
             proxy_tasks.TARGET_ACTIVE))
        return [dict(row) for row in cursor.fetchall()]


def _load_runner_states(runner_ids):
    """runner_id → {status, attempt, resume_control_id}。"""
    if not runner_ids:
        return {}
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT runner_id, status, attempt, resume_control_id FROM agent_runners '
                       'WHERE runner_id=ANY(%s)', (list(runner_ids),))
        return {row['runner_id']: dict(row) for row in cursor.fetchall()}


# ==================== 运行批：核定 / 释放 / 恢复驱动 ====================

async def _settle_running_batch(batch, runner_states):
    """running 批次按 Runner 状态分流：终态核定 / 等待释放 / 中断恢复驱动。"""
    state = runner_states[batch['runner_id']]
    status = state['status']
    if status in ('completed', 'failed', 'cancelled'):
        await asyncio.to_thread(proxy_settlement.adjudicate_batch, batch)
        return
    if status in ('waiting', 'paused'):
        await asyncio.to_thread(_release_to_waiting, batch, state)
        return
    if status == 'interrupted':
        await _drive_interrupted_resume(batch, state)
        return
    # queued/running/finalizing：执行中，占用保持


def _release_to_waiting(batch, state=None):
    """Runner 持久等待/暂停：批次 running→waiting 释放队列位置（契约 §4）。

    Runner 已有受理未消费的 resume 控制（resume_control_id 非空）时不释放：
    等待消费（attempt 变化或离开 waiting）后再评估，避免"释放→重排→同键
    幂等命中已消费旧控制"的死循环。
    """
    if state is not None and state.get('resume_control_id'):
        return  # 恢复控制待消费：占用保持
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, batch['tenant_id'], batch['id'],
                                           proxy_tasks.BATCH_WAITING)
        proxy_settlement._settle_stop_if_requested(cursor, batch['tenant_id'], batch['task_id'])
        conn.commit()
    logger.info(f'后端日志：企微代理 Runner 持久等待释放队列 batch={batch["id"]} '
                f'runner={batch.get("runner_id")}')


async def _drive_interrupted_resume(batch, state):
    """中断 Runner 幂等 resume（核验收敛）；attempt 耗尽按事实落 reconcile_hold。"""
    runner_id = batch['runner_id']
    if state.get('resume_control_id'):
        return  # 已有受理控制待消费
    attempt = int(state.get('attempt') or 0)
    if attempt >= proxy_settlement.MAX_RESUME_ATTEMPT:
        await asyncio.to_thread(proxy_settlement.resume_exhausted, batch)
        return
    await _post_resume(batch, runner_id, attempt, key_prefix='wecom-proxy-resume')


async def _post_resume(batch, runner_id, attempt, *, key_prefix):
    """幂等恢复控制提交；RUNNER_RESUME_PENDING 表示已有控制待消费（非失败）。"""
    body = {'action': 'resume',
            'client_request_id': f'{key_prefix}:{runner_id}:{attempt}',
            'answer': '', 'attachments': []}
    try:
        await _proxy_client().request('POST', f'/v1/runners/{runner_id}/controls',
                                      authorization=None, body=body)
    except Exception as error:  # noqa: BLE001
        code = _error_code(error)
        if code in ('RUNNER_NOT_PARKED', 'CONTROL_EXECUTION_CLOSED', 'RUNNER_NOT_FOUND',
                    'RUNNER_RESUME_PENDING'):
            return  # 状态自解或已有控制待消费：下轮按新状态处理
        if not _is_transient(error):
            # 确定性拒绝（额度/权限类）：不再恢复，按事实停住
            await asyncio.to_thread(proxy_settlement.resume_exhausted, batch)
            return
        logger.warning(f'后端日志：企微代理恢复控制暂时失败 batch={batch["id"]} '
                       f'runner={runner_id} code={code or type(error).__name__}')
        return
    logger.info(f'后端日志：企微代理恢复控制已受理 batch={batch["id"]} '
                f'runner={runner_id} attempt={attempt}')


# ==================== 派发：核对 → 占用 → 提交 ====================

async def _dispatch_batch(batch, runner_states):
    """单批派发；返回是否已提交（同组后续批次让位下一轮）。

    pending 批带 runner_id＝等待恢复来源（P5 重排）：占用取得后恢复原 Runner
    （恢复控制，非新批，契约 §6）；无 runner_id＝新批：置 running＋提交新执行。
    """
    tenant_id = batch['tenant_id']
    device_id = str(batch['device_id'])
    proxy_api = _proxy_api()
    device, error = await proxy_api._task_device(tenant_id, batch['user_id'], device_id,
                                                 DISPATCH_REQUIRED_TOOLS)
    if error:
        # 设备/账号运行条件不满足：批次留在 pending，下轮重试（沿 P5 瞬态语义，
        # 设备生命周期问题不转嫁为会话 halted）
        code, message = proxy_monitor_error_parts(error)
        logger.warning(f'后端日志：企微代理派发设备不可用本轮跳过 batch={batch["id"]} '
                       f'device={device_id} code={code} message={message}')
        return False
    if not await _agent_allowed(batch):
        # 失权走占用→终态路径（状态白名单 running→failed；同时停任务）
        await asyncio.to_thread(_occupy_batch, batch)
        await _fail_deterministic(batch, 'AGENT_NOT_ALLOWED', stop_task=True)
        return False
    key = (tenant_id, device_id, batch['wecom_account_id'])
    lock = group_lock(key)
    if not lock.acquire(blocking=False):
        # 监控组读进行中：本轮不派发，下轮再取（占用取得在锁内，双向互斥）
        logger.info(f'后端日志：企微代理监控占用组级锁，派发本轮跳过 batch={batch["id"]}')
        return False
    try:
        # 占用＋提交在同一临界区：监控组读（同锁）不会与 Runner 启动交错
        if await asyncio.to_thread(_group_has_running, tenant_id, device_id,
                                   batch['wecom_account_id']):
            return False
        if not await asyncio.to_thread(_occupy_batch, batch):
            return False  # 并发下批次状态已变（停止/删除/他方派发），跳过
        if batch['runner_id']:
            # 等待恢复来源：恢复原 Runner（占用已取得，工具门随之放行）
            state = runner_states.get(batch['runner_id']) or {}
            await _post_resume(batch, batch['runner_id'],
                               int(state.get('attempt') or 0), key_prefix='wecom-proxy-wake')
            return True
        try:
            runner = await _submit_runner(batch)
        except Exception as error:  # noqa: BLE001
            await _handle_submit_error(batch, error)
            return False
        runner_id = (runner.get('runner') or {}).get('runner_id')
        await asyncio.to_thread(_record_runner_id, tenant_id, batch['id'], runner_id)
    finally:
        lock.release()
    logger.info(f'后端日志：企微代理批次已派发 batch={batch["id"]} '
                f'session={batch["session"][:60]} runner={runner_id} '
                f'end={batch["batch_end_msg_id"]}')
    return True


async def _submit_running_batch(batch):
    """孤儿批（占用已置、Runner 行缺失/提交结果未知）同键重试提交。"""
    try:
        runner = await _submit_runner(batch)
    except Exception as error:  # noqa: BLE001
        await _handle_submit_error(batch, error)
        return
    runner_id = (runner.get('runner') or {}).get('runner_id')
    await asyncio.to_thread(_record_runner_id, batch['tenant_id'], batch['id'], runner_id)
    logger.info(f'后端日志：企微代理孤儿批重提成功 batch={batch["id"]} runner={runner_id}')


def proxy_monitor_error_parts(error):
    """_err 返回（JSONResponse 或测试桩 dict）→ (code, message)（沿监控实现）。"""
    if isinstance(error, dict):
        return error.get('code') or 'PROXY_READ_FAILED', error.get('message') or ''
    body = getattr(error, 'body', None)
    if isinstance(body, (bytes, bytearray)):
        import json as _json
        try:
            payload = _json.loads(bytes(body).decode('utf-8'))
            return payload.get('code') or 'PROXY_READ_FAILED', payload.get('error') or ''
        except ValueError:
            pass
    return 'PROXY_READ_FAILED', ''


def _group_has_running(tenant_id, device_id, account):
    """同组已占用核对（与 proxy_monitor._group_runner_busy 同口径）。"""
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


def _occupy_batch(batch):
    """pending→running（组内占用原子取得；状态已变返回 False）。"""
    from src.db.database import get_db_connection

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            proxy_tasks.start_batch_in_tx(cursor, batch['tenant_id'], batch['id'])
            conn.commit()
        return True
    except proxy_tasks.ConversationServiceError as exc:
        if exc.code == 'PROXY_BATCH_STATE_INVALID':
            return False
        raise


async def _agent_allowed(batch):
    """智能体仍有权（派发前核对；用户级授权按任务属主，设计 §9.5）。"""
    try:
        from src.saas.permissions.checker import check_agent_access
        from src.db.models import UserDB

        user = await asyncio.to_thread(UserDB.get_by_id, batch['user_id'])
        if not user:
            return False
        return bool(await asyncio.to_thread(check_agent_access, batch['agent_id'], dict(user)))
    except Exception as e:  # noqa: BLE001 权限检查异常按无权处理（fail-closed）
        logger.opt(exception=True).warning(
            f'后端日志：企微代理智能体权限核对异常 batch={batch["id"]} {e}')
        return False


def _record_runner_id(tenant_id, batch_id, runner_id):
    """提交受理后回写 runner_id（占用事务已提交，独立短事务即可）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''UPDATE bs_wecom_proxy_batches SET runner_id=%s, updated_at=CURRENT_TIMESTAMP
               WHERE id=%s AND tenant_id=%s AND status=%s''',
            (runner_id, batch_id, tenant_id, proxy_tasks.BATCH_RUNNING))
        conn.commit()


async def _handle_submit_error(batch, error):
    """提交层错误分流：瞬时保留占用同键重试；确定性拒绝按语义停住/回退。"""
    if _is_transient(error):
        # 网络未知/5xx：批次保持 running（占用保持），下轮同幂等键重试——
        # 若服务端已受理则幂等命中原 Runner，不重复建执行
        logger.warning(f'后端日志：企微代理提交暂时失败保留占用 batch={batch["id"]} '
                       f'code={_error_code(error) or type(error).__name__}')
        return
    code = _error_code(error) or 'SUBMIT_REJECTED'
    if code in _REQUEUE_REJECT_CODES:
        # 任务已停止/删除竞态：批次回 pending 留待下次启动排队（不循环重试）
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                proxy_tasks.revert_batch_in_tx(cursor, batch['tenant_id'], batch['id'], code)
            except proxy_tasks.ConversationServiceError as exc:
                if exc.code != 'PROXY_BATCH_STATE_INVALID':
                    raise
                conn.rollback()
                return
            conn.commit()
        logger.info(f'后端日志：企微代理提交遇任务不可派发，批次回队列 batch={batch["id"]} '
                    f'code={code}')
        return
    await _fail_deterministic(batch, code,
                              stop_task=code in _PERMISSION_REJECT_CODES)


async def _fail_deterministic(batch, code, *, stop_task):
    """确定性提交失败：批次转 failed＋记 cli_error；权限类同时停任务。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            proxy_tasks.transition_batch_in_tx(
                cursor, batch['tenant_id'], batch['id'], proxy_tasks.BATCH_FAILED,
                cli_error=f'submit_rejected:{code}')
        except proxy_tasks.ConversationServiceError as exc:
            if exc.code != 'PROXY_BATCH_STATE_INVALID':
                raise
            conn.rollback()
            return  # 并发下状态已变，不覆盖
        # 会话停住待人工（提交确定性失败），其他会话继续；页面展示 cli_error
        proxy_tasks.mark_target_halted_in_tx(
            cursor, batch['tenant_id'], batch['task_id'], batch['session'],
            f'submit_rejected:{code}')
        if stop_task:
            # 失权即停任务（契约 §6）：置 stop_requested，无 running 批时立即
            # 收敛 stopped；位置与日志保留，恢复后由用户手动启动
            cursor.execute(
                '''UPDATE bs_wecom_proxy_tasks SET stop_requested=TRUE,
                       updated_at=CURRENT_TIMESTAMP WHERE id=%s AND tenant_id=%s''',
                (batch['task_id'], batch['tenant_id']))
            proxy_settlement._settle_stop_if_requested(cursor, batch['tenant_id'],
                                                       batch['task_id'])
        conn.commit()
    logger.error(f'后端日志：企微代理提交被确定性拒绝 batch={batch["id"]} code={code} '
                 f'task_stopped={stop_task}')


# ==================== 提交体（契约 §6 受信提交） ====================

def render_batch_text(batch):
    """本批原文与身份 → Runner 输入文本（不能只传消息 ID 让 Runner 猜内容）。"""
    snapshot = batch.get('input_snapshot') or {}
    messages = [item for item in snapshot.get('messages') or [] if isinstance(item, dict)]
    lines = [f'目标会话：{batch["session"]}',
             f'本批新消息 {len(messages)} 条，批次截止 msg_id={batch["batch_end_msg_id"]}：']
    for message in messages:
        send_time = message.get('send_time')
        time_text = datetime.fromtimestamp(send_time, tz=timezone.utc).strftime('%m-%d %H:%M') \
            if isinstance(send_time, int) else '--'
        mentions = [item for item in message.get('mentions') or [] if isinstance(item, dict)]
        mention_text = ''
        if mentions:
            parts = [('@所有人' if item.get('kind') == 'all'
                      else f"@{item.get('name') or item.get('user_id')}") for item in mentions]
            mention_text = f' mentions={",".join(parts)}'
            if message.get('mentions_complete') is False:
                mention_text += '（提及解析不完整，需结合原文判断是否 @ 本人）'
        lines.append(f'[msg_id={message.get("msg_id")} {time_text}] '
                     f'{message.get("sender_name") or "未知"}'
                     f'({message.get("sender_platform") or "?"},sender_id='
                     f'{message.get("sender_id")}){mention_text}: {message.get("text") or ""}')
    return '\n'.join(lines)


def reply_discipline_augmentation(batch):
    """回复纪律（内部受信追加，代理任务契约 §6 prompt_augmentations）。

    固定模板：目标会话限定、@我必答（服务端仍按 mentions 核定）、结束 JSON
    输出格式（契约 §5 字段，引用实际发送回执字段）。
    """
    return (
        '本次为企业微信代理任务批次执行，无法向网页用户提问；缺少信息时按任务指令'
        '自主判断，不得请求用户补充。\n'
        '执行纪律：\n'
        '1. 只允许操作目标会话（用输入给出的稳定会话标识原样调用读取与发送工具），'
        '不读取、不回复任何其他会话；\n'
        '2. 结合会话近期历史与本批消息判断是否需要回复：明确 @ 当前账号的消息必须回复，'
        '不能被"闲聊不回复"规则吞掉；消息的 mentions 标记可能不完整'
        '（mentions_complete=false），需结合原文判断是否 @ 本人；\n'
        '3. 群内多个成员的问题可分别回复或合并回复，无实质内容则不发送；'
        '发送用 wecom_message_send 并原样传入目标会话标识；\n'
        '4. 最终输出必须是且仅是一个合法 JSON（UTF-8，不带其它文字或代码围栏），'
        '结构：{"schema_version":1,"batch_id":"' + str(batch['id']) + '","task_id":"'
        + str(batch['task_id']) + '","session":"' + batch['session'] + '","batch_end_msg_id":'
        + str(batch['batch_end_msg_id']) + ',"needs_reply":true/false,"replies":'
        '[{"inbound_msg_ids":[消息ID],"text":"回复文本","receipt_id":"发送回执ID",'
        '"verification":"local-record","verification_detail":{"method":"local-record",'
        '"message_id":本机库消息ID,"source_generation":"读取回执中的代际"}}],'
        '"unanswered":[{"msg_id":消息ID,"reason":"原因","cli_error":{"code":"错误码",'
        '"invocation_id":"调用ID"}}]}；\n'
        '5. replies 只列真实发送成功的项，receipt_id/verification/verification_detail '
        '必须引用发送工具回执中的实际字段，不得编造；未成功或未回复的消息列入 '
        'unanswered 并写真实错误码；全批无需回复时 replies 与 unanswered 为空数组、'
        'needs_reply=false。'
    )


def build_submit_body(batch):
    """RunnerSubmit 字典（契约 §6）：固定 web_session_id＋冻结指令快照＋本批原文。"""
    return {
        'source': PROXY_SERVICE_ID,
        'session': {'kind': 'web', 'session_id': batch['web_session_id']},
        # 幂等键：同批同 retry_count 重试返回原 Runner；零发送重试 retry_count+1
        # 换新键 → 新 Runner（契约 §5 规则 3）
        'client_request_id': f'{batch["id"]}:{int(batch.get("retry_count") or 0)}',
        'text': render_batch_text(batch),
        'attachments': [],
        'profile_id': batch['agent_id'],
        'instance_id': None,
        'prompt_augmentations': [
            batch.get('instruction_snapshot') or '',
            reply_discipline_augmentation(batch),
        ],
        'request_data': {
            # 受信业务描述（仅服务端构造，契约 §6；工具派发门按批次核定）
            'wecom_proxy_batch': {
                'schema_version': proxy_tasks.PROXY_SCHEMA_VERSION,
                'batch_id': batch['id'],
                'task_id': batch['task_id'],
                'session': batch['session'],
                'batch_end_msg_id': batch['batch_end_msg_id'],
                'self_user_id': batch.get('self_user_id'),
            },
            # 任务固定设备（R05 受信提示，企微工具按任务设备派发）
            'wecom_task_device': str(batch['device_id']),
        },
    }


async def _submit_runner(batch):
    return await _proxy_client().request('POST', '/v1/runners', authorization=None,
                                         body=build_submit_body(batch))
