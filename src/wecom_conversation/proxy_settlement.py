"""企微代理批次结束核定（代理任务契约 §5 五条按序规则，P6）。

职责边界：Runner 终态后从持久事实取证并核定——结束 JSON 取自 Runner 最终
输出（agent_runners.result.output），发送事实取自本 runner 的
local_tool_invocations 原回执（不经 Agent 转述）；@我是服务端核定事实（按
input_snapshot 消息的 mentions 核定，不信任自报）。核定纯函数 evaluate_end
不触库；落库编排单事务完成批次终态、end_json、推进位置与目标 halt。

规则按序（契约 §5 冻结）：
① needs_reply=false＋零发送＋服务端核定无 @我 → 推进；
② ≥1 条 CLI 确认发送（回执 state=succeeded）→ 推进（其余失败/unknown 仅展示）；
③ 零确认发送且（无发送调用或全部证明未发送）→ 重试一次（failed→pending，
   retry_count+1，新 runner_id；再失败会话 halted，其他会话继续）；
④ 发生过发送调用且任一次无法证明未发送且无确认发送 → reconcile_hold
   （只读对账，不自动重发不推进）；
⑤ @我窄门槛：明确 @我＋全批无需回复＋零发送 不构成正常结束，落入规则 3。
"""

from __future__ import annotations

import json

from loguru import logger

from . import proxy_tasks

END_JSON_SCHEMA_VERSION = 1

# 结束 JSON 必需字段（契约 §5；缺失记批次失败，不猜成功）
END_JSON_REQUIRED_FIELDS = ('schema_version', 'batch_id', 'task_id', 'session',
                            'batch_end_msg_id', 'needs_reply', 'replies', 'unanswered')

# 核定取证的企微发送工具调用事实范围（模型可见发送面为 wecom_message_send；
# send_image/send_file 不在 agent_tools，列入仅为防绕过）
_SEND_TOOL_NAMES = ('wecom_message_send', 'wecom_send_image', 'wecom_send_file')

# 中断 Runner（发送 unknown 等待核验）的恢复驱动 attempt 上限：超过后不再自动
# resume，按事实落入规则 4 停住待人工，避免无限恢复循环反复消耗模型积分
MAX_RESUME_ATTEMPT = 5


def batch_has_explicit_mention(input_snapshot, self_user_id):
    """服务端核定本批是否存在明确 @当前账号（契约 §5，非 Runner 自报）。

    mentions 元素形状（CLI 冻结）：{kind:'user'|'all', user_id, name,
    name_source}；kind='all'（@所有人）视为明确 @我。mentions_complete 不参与
    核定——不凭缺 @ 判定无人提及，也不凭不完整提及判定有 @（交智能体结合
    原文判断，不因此卡批）。
    """
    if not isinstance(input_snapshot, dict) or not isinstance(self_user_id, str) or not self_user_id:
        return False
    for message in input_snapshot.get('messages') or []:
        if not isinstance(message, dict):
            continue
        for mention in message.get('mentions') or []:
            if not isinstance(mention, dict):
                continue
            if mention.get('kind') == 'all':
                return True
            if mention.get('kind') == 'user' and mention.get('user_id') == self_user_id:
                return True
    return False


def classify_send_facts(rows):
    """发送调用回执 → (confirmed, unprovable) 明细。

    - confirmed：回执 state=succeeded（write_result 仅 CLI 确认成功落该态，
      文本发送成功回执自带 verification='local-record' 本机库证据）；
    - unprovable：unknown，或 failed/cancelled 但 effect≠'none'（无法证明未
      执行发送动作），或仍非终态（防御：核定只在 Runner 终态/恢复耗尽后执行）；
    - 其余（failed/cancelled 且 effect='none'）：明确证明未发送，不计入两类。
    """
    confirmed, unprovable = [], []
    for row in rows:
        state = row.get('state')
        effect = row.get('effect')
        if state == 'succeeded':
            confirmed.append(row)
        elif state in ('failed', 'cancelled') and effect == 'none':
            continue
        else:
            unprovable.append(row)
    return confirmed, unprovable


def parse_end_json(end_raw, batch):
    """Runner 最终输出 → 结束 JSON（非 JSON/缺必需字段/标识不符返回 None）。

    严格解析：不接受围栏包裹或前后杂文（契约 §5"非 JSON、缺必需字段记批次
    失败，不猜成功"）；batch_id/task_id/session/batch_end_msg_id 须与批次
    冻结事实一致，防串批。
    """
    if not isinstance(end_raw, str) or not end_raw.strip():
        return None
    try:
        value = json.loads(end_raw.strip())
    except ValueError:
        return None
    if not isinstance(value, dict) or value.get('schema_version') != END_JSON_SCHEMA_VERSION:
        return None
    if any(field not in value for field in END_JSON_REQUIRED_FIELDS):
        return None
    if (str(value.get('batch_id')) != str(batch['id'])
            or str(value.get('task_id')) != str(batch['task_id'])
            or value.get('session') != batch['session']
            or value.get('batch_end_msg_id') != batch['batch_end_msg_id']):
        return None
    if not isinstance(value.get('replies'), list) or not isinstance(value.get('unanswered'), list):
        return None
    if not isinstance(value.get('needs_reply'), bool):
        return None
    return value


def evaluate_end(runner_status, end_raw, send_rows, *, explicit_mention, retry_count, batch):
    """五条按序核定的纯函数（契约 §5）；返回裁决 dict（不触库）。

    返回 {'action': 'advance'|'retry'|'halt'|'reconcile_hold', 'reason': str,
    'end_json': dict|None}。end_json 原样保存（解析失败为 None）。
    """
    end_json = parse_end_json(end_raw, batch) if runner_status == 'completed' else None
    confirmed, unprovable = classify_send_facts(send_rows)
    needs_reply = end_json.get('needs_reply') if end_json is not None else None

    # 规则 2：≥1 条 CLI 本地库确认发送即推进（规则 1 要求零发送，两条件互斥，
    # 判定顺序不影响结果；其余失败/unknown 仅在 unanswered 展示，不阻塞推进）
    if confirmed:
        return {'action': 'advance', 'reason': 'confirmed_send', 'end_json': end_json}
    # 规则 1：正常无需回复（needs_reply=false＋replies 空＋零发送＋核定无 @我；
    # 规则 5：明确 @我＋全批无需回复＋零发送不构成正常结束，不进本条）
    if (runner_status == 'completed' and end_json is not None
            and needs_reply is False and not end_json['replies']
            and not send_rows and not explicit_mention):
        return {'action': 'advance', 'reason': 'no_reply_needed', 'end_json': end_json}
    # 规则 4：发生过发送调用且任一次无法证明未发送（含 unknown/partial）且无
    # 已确认发送 → 只读对账，不自动重发、不推进
    if unprovable:
        detail = unprovable[0]
        return {'action': 'reconcile_hold',
                'reason': 'send_effect_unproven:' + str(detail.get('error_code')
                                                        or detail.get('state') or 'UNKNOWN'),
                'end_json': end_json}
    # 至此零确认发送、无不可证明调用：要么无发送调用，要么全部明确证明未发送
    if runner_status != 'completed':
        # Runner 终态失败/取消或中断恢复耗尽（§9.3：终态失败释放队列，不恢复
        # 同一终态 Runner；零发送符合重试规则时才创建一次新执行）
        if retry_count == 0:
            return {'action': 'retry', 'reason': f'runner_{runner_status}', 'end_json': end_json}
        return {'action': 'halt', 'reason': f'runner_{runner_status}_after_retry',
                'end_json': end_json}
    # 规则 3：无发送调用或全部调用证明未执行发送 → 自动重试一次；再失败停住
    if retry_count == 0:
        return {'action': 'retry', 'reason': 'zero_send_retry', 'end_json': end_json}
    return {'action': 'halt', 'reason': 'zero_send_retry_exhausted', 'end_json': end_json}


# ==================== 取证与落库编排 ====================

def _runner_row(runner_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT runner_id, status, attempt, result FROM agent_runners '
                       'WHERE runner_id=%s', (runner_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def _send_invocation_rows(tenant_id, runner_id):
    """本 runner 的企微发送调用事实（business_kind='agent_runner' 原回执）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT id, tool_name, state, effect, result_json, error_code
               FROM local_tool_invocations
               WHERE tenant_id=%s AND business_kind='agent_runner'
                 AND business_ref->>'runner_id'=%s
                 AND tool_name=ANY(%s) ORDER BY id''',
            (tenant_id, runner_id, list(_SEND_TOOL_NAMES)))
        return [dict(row) for row in cursor.fetchall()]


def _task_self_user_id(tenant_id, task_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT self_user_id FROM bs_wecom_proxy_tasks WHERE id=%s AND tenant_id=%s',
                       (task_id, tenant_id))
        row = cursor.fetchone()
        return row['self_user_id'] if row else None


def _settle_stop_if_requested(cursor, tenant_id, task_id):
    """批次离开 running 后收敛 stop_requested（复用 P4 语义，尽力而为）。"""
    cursor.execute('SELECT * FROM bs_wecom_proxy_tasks WHERE id=%s AND tenant_id=%s FOR UPDATE',
                   (task_id, tenant_id))
    task = cursor.fetchone()
    if task is not None:
        proxy_tasks.settle_stop_in_tx(cursor, dict(task))


def _apply_verdict(cursor, batch, verdict, *, cli_error_prefix=''):
    """裁决落库（调用方事务内）：批次终态＋end_json＋推进/halt/重试。"""
    tenant_id = batch['tenant_id']
    end_json = verdict['end_json']
    cli_error = (cli_error_prefix + verdict['reason']) if cli_error_prefix else verdict['reason']
    if verdict['action'] == 'advance':
        proxy_tasks.transition_batch_in_tx(
            cursor, tenant_id, batch['id'], proxy_tasks.BATCH_SUCCEEDED, end_json=end_json)
        proxy_tasks.update_processed_position_in_tx(
            cursor, tenant_id, batch['task_id'], batch['session'], batch['batch_end_msg_id'])
    elif verdict['action'] == 'retry':
        proxy_tasks.transition_batch_in_tx(
            cursor, tenant_id, batch['id'], proxy_tasks.BATCH_FAILED,
            end_json=end_json, cli_error=cli_error)
        # 重试一次：failed→pending（新 runner_id、同批冻结上界/快照），容忍与
        # 监控建批等并发（failed 行仍占用会话，无并发建批冲突面）
        proxy_tasks.retry_zero_send_batch_in_tx(cursor, tenant_id, batch['id'])
    elif verdict['action'] == 'reconcile_hold':
        proxy_tasks.transition_batch_in_tx(
            cursor, tenant_id, batch['id'], proxy_tasks.BATCH_RECONCILE_HOLD,
            end_json=end_json, cli_error=cli_error)
    else:  # halt
        proxy_tasks.transition_batch_in_tx(
            cursor, tenant_id, batch['id'], proxy_tasks.BATCH_FAILED,
            end_json=end_json, cli_error=cli_error)
        proxy_tasks.mark_target_halted_in_tx(
            cursor, tenant_id, batch['task_id'], batch['session'], cli_error)
    _settle_stop_if_requested(cursor, tenant_id, batch['task_id'])


def adjudicate_batch(batch, *, cli_error_prefix=''):
    """Runner 终态后的核定落库（单事务）。返回裁决 dict；缺 Runner 行返回 None。"""
    tenant_id = batch['tenant_id']
    runner_id = batch.get('runner_id')
    runner = _runner_row(runner_id) if runner_id else None
    if runner is None:
        logger.error(f'后端日志：企微代理核定缺 Runner 行 batch={batch["id"]} runner={runner_id}')
        return None
    runner_status = runner['status']
    result = runner.get('result') or {}
    end_raw = result.get('output') if isinstance(result, dict) else None
    send_rows = _send_invocation_rows(tenant_id, runner_id)
    self_user_id = _task_self_user_id(tenant_id, batch['task_id'])
    explicit_mention = batch_has_explicit_mention(batch.get('input_snapshot'), self_user_id)
    verdict = evaluate_end(
        runner_status, end_raw, send_rows,
        explicit_mention=explicit_mention, retry_count=int(batch.get('retry_count') or 0),
        batch=batch)
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        _apply_verdict(cursor, batch, verdict, cli_error_prefix=cli_error_prefix)
        conn.commit()
    logger.info(f'后端日志：企微代理批次核定 batch={batch["id"]} action={verdict["action"]} '
                f'reason={verdict["reason"]} runner={runner_id} mention={explicit_mention}')
    return verdict


def _abandon_runner(runner_id, tenant_id, session_id, owner_id):
    """放弃中断 Runner：请求取消以释放会话执行道（interrupted 行持有
    agent_runner_session_claims，不取消会让同会话后续 Runner 永远排队）。

    cancel 仅置 cancel_requested 并关闭待消费控制；unknown 在途发送由既有
    取消核验协议收敛（不伪造回执），收敛后 worker 落 cancelled 终态释放。
    """
    from src.services.agent_runner.contracts import Identity, Principal
    from src.services.agent_runner.repository import RunnerRepository, RunnerError

    principal = Principal(
        Identity(tenant_id, owner_id, session_id, 'wecom_proxy', 'web'),
        'system', 'wecom_proxy', 'wecom_proxy')
    try:
        RunnerRepository().cancel(principal, runner_id)
        logger.info(f'后端日志：企微代理放弃中断 Runner 已请求取消 runner={runner_id}')
    except RunnerError as error:
        logger.warning(f'后端日志：企微代理放弃中断 Runner 取消失败（不阻塞核定） '
                       f'runner={runner_id} code={getattr(error, "code", "")}')


def resume_exhausted(batch):
    """中断 Runner 恢复耗尽（attempt 达上限）后的核定出口。

    中断 Runner 不是终态：派发器先幂等 resume 驱动核验收敛；attempt 达上限仍
    中断说明核验无法收敛，按事实分流（有 unknown → reconcile_hold；零发送 →
    重试/停住），批次不再占用队列；被放弃的 Runner 请求取消以释放会话执行道。
    end_json 不可得（Runner 未正常结束）。
    """
    runner = _runner_row(batch.get('runner_id')) if batch.get('runner_id') else None
    if runner is None:
        return None
    send_rows = _send_invocation_rows(batch['tenant_id'], batch['runner_id'])
    self_user_id = _task_self_user_id(batch['tenant_id'], batch['task_id'])
    explicit_mention = batch_has_explicit_mention(batch.get('input_snapshot'), self_user_id)
    verdict = evaluate_end(
        'interrupted', None, send_rows,
        explicit_mention=explicit_mention, retry_count=int(batch.get('retry_count') or 0),
        batch=batch)
    if verdict['action'] == 'advance':
        # 仅来自确认发送事实（中断 Runner 不产出 end JSON，按空 end_json 推进）
        verdict = {**verdict, 'end_json': None}
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        _apply_verdict(cursor, batch, verdict,
                       cli_error_prefix='interrupted_resume_exhausted:')
        conn.commit()
    _abandon_runner(batch['runner_id'], batch['tenant_id'], batch['web_session_id'],
                    batch['user_id'])
    logger.warning(f'后端日志：企微代理中断 Runner 恢复耗尽 batch={batch["id"]} '
                   f'runner={batch.get("runner_id")} action={verdict["action"]} '
                   f'reason={verdict["reason"]}')
    return verdict
