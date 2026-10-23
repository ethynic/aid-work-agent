"""Idempotent control acceptance under the original runner's ownership lock."""

import copy
import json
import uuid

from src.db.database import get_db_connection
from . import ask_form
from .contracts import RunnerError
from .ownership import lock_runner, LeaseLost
from .persistence_limits import capped_snapshot_dumps
from .repository import RunnerRepository, decoded
from .event_repository import EventRepository


def lock_owned_runner(cursor, principal, runner_id):
    try:
        row = lock_runner(cursor, runner_id)
    except LeaseLost as error:
        if str(error) == 'RUNNER_NOT_FOUND':
            raise RunnerError('RUNNER_NOT_FOUND', 404) from error
        raise
    RunnerRepository.assert_owner(principal, row)
    return row


def close_pending_controls(cursor, runner_id, reason):
    cursor.execute('''UPDATE agent_runner_controls SET status='rejected',error_code=%s,
        consumed_at=clock_timestamp() WHERE runner_id=%s AND status IN ('accepted','claimed')''',
        (reason,runner_id))


def execution_waits(checkpoint):
    """Walk only explicit execution/child facts, never arbitrary resource objects."""
    root = (checkpoint or {}).get('execution')
    pending = [root] if isinstance(root,dict) else []
    seen = set()
    while pending:
        state = pending.pop()
        execution_id = state.get('execution_id')
        if not isinstance(execution_id,str) or execution_id in seen:
            raise RunnerError('CHECKPOINT_TREE_INVALID',409)
        seen.add(execution_id)
        yield execution_id, state.get('waiting')
        children = state.get('children') or {}
        if not isinstance(children,dict) or len(seen) + len(pending) + len(children) > 1000:
            raise RunnerError('CHECKPOINT_TREE_INVALID',409)
        for child in children.values():
            if not isinstance(child,dict):
                raise RunnerError('CHECKPOINT_TREE_INVALID',409)
            value = child.get('checkpoint')
            if isinstance(value,dict):
                pending.append(value)


class ControlRepository:
    def __init__(self, connection_factory=get_db_connection, *, browser_completions=None):
        self.connection_factory = connection_factory
        self.browser_completions = browser_completions

    @staticmethod
    def waiting_for(row, request):
        """已通过 assert_wait 后定位目标等待本体（含 ask form），找不到返回 None。"""
        for execution_id, waiting in execution_waits(row.get('checkpoint')):
            if execution_id == request.target_execution_id and isinstance(waiting,dict) and waiting.get('wait_id') == request.wait_id:
                return waiting
        return None

    @staticmethod
    def assert_wait(row, request):
        waiting = ControlRepository.waiting_for(row, request)
        if waiting is None:
            raise RunnerError('CONTROL_WAIT_STALE',409)
        expected = ({'clarification'} if request.action == 'reply' else
                    {'timer','continuous_guard'} if request.action == 'resume' else {'human_assistance'})
        if waiting.get('kind') not in expected:
            raise RunnerError('CONTROL_WAIT_KIND_MISMATCH',409)

    def _accept_ask_form(self, row, request):
        """结构化提交的判定表校验与 canonical 生成（设计 §4.2/§5.4）。

        仅 wait 含 form 的结构化提交（answers 或 skip）进入本路径；校验失败同步
        422 + errors 逐题明细，不创建 control、不消费 wait、不写任何持久状态。
        wait 无 form（旧 clarify）上的结构化提交同样 422 拒绝：原路径只认
        非空 answer 文本，按空 reply 静默受理会丢弃结构化答案且消费 wait，
        客户端失去用原键重提的机会。
        """
        waiting = self.waiting_for(row, request) or {}
        form = waiting.get('form')
        if not isinstance(form, dict):
            if request.skip:
                raise RunnerError('FORM_SKIP_NOT_ALLOWED', 422, errors=[
                    {'question_id': ask_form.FORM_LEVEL_ID,
                     'code': 'FORM_SKIP_NOT_ALLOWED',
                     'message': '当前等待不包含提问表单，不能整体跳过'}])
            raise RunnerError('FORM_ANSWER_INVALID', 422, errors=[
                {'question_id': ask_form.FORM_LEVEL_ID,
                 'code': 'FORM_ANSWER_INVALID',
                 'message': '当前等待不包含提问表单，不能提交结构化答案'}])
        normalized = ask_form.normalize_answers(request.answers)
        errors = ask_form.validate_answers(form, normalized, skip=request.skip)
        if errors:
            raise RunnerError(errors[0]['code'], 422, errors=errors)
        return {'text': ask_form.canonical_text(form, normalized, skip=request.skip),
                'answers': normalized, 'form': copy.deepcopy(form)}

    def accept_completion_in_tx(self, cursor, row, request):
        """Internal Browser fact bridge under an already locked original owner.

        This accepts only browser_complete and never relaxes the parked guard.
        The caller commits its completion watermark in this same transaction.
        """
        if request.action != 'browser_complete' or self.browser_completions is None:
            raise RunnerError('CONTROL_COMPLETION_FORBIDDEN',403)
        if (row['cancel_requested'] or row['status'] not in {'paused','waiting','interrupted'}
                or row.get('lease_valid')):
            raise RunnerError('RUNNER_NOT_PARKED',409)
        cursor.execute('''SELECT * FROM agent_runner_controls WHERE runner_id=%s
            AND client_request_id=%s''',(row['runner_id'],request.client_request_id))
        previous = decoded(cursor.fetchone())
        if previous:
            if previous['intent_digest'] != request.digest():
                raise RunnerError('IDEMPOTENCY_INPUT_MISMATCH',409)
            return previous
        if row.get('resume_control_id'):
            raise RunnerError('RUNNER_RESUME_PENDING',409)
        self.assert_wait(row,request)
        cursor.execute('''SELECT 1 FROM agent_runner_controls WHERE runner_id=%s
            AND status='consumed' AND action='browser_complete'
            AND payload->>'wait_id'=%s AND payload->>'target_execution_id'=%s''',
            (row['runner_id'],request.wait_id,request.target_execution_id))
        if cursor.fetchone():
            raise RunnerError('CONTROL_WAIT_CONSUMED',409)
        self.browser_completions.assert_completion_in_tx(cursor,row,request)
        control_id = 'control_' + uuid.uuid4().hex
        cursor.execute('''INSERT INTO agent_runner_controls
            (control_id,runner_id,tenant_id,scope_key,action,client_request_id,intent_digest,payload,status)
            VALUES (%s,%s,%s,%s,'browser_complete',%s,%s,%s::jsonb,'accepted') RETURNING *''',
            (control_id,row['runner_id'],row['tenant_id'],row['scope_key'],request.client_request_id,
             request.digest(),json.dumps(request.intent(),ensure_ascii=False)))
        control = decoded(cursor.fetchone())
        cursor.execute('''UPDATE agent_runners SET resume_control_id=%s,
            control_revision=control_revision+1,view_revision=view_revision+1,
            updated_at=clock_timestamp() WHERE runner_id=%s''',(control_id,row['runner_id']))
        from .application_public_projection import sync_public_display
        cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s',(row['runner_id'],))
        sync_public_display(cursor,decoded(cursor.fetchone()),advance_view=False)
        return control

    def submit(self, principal, runner_id, request, *, expected_revision=None):
        with self.connection_factory() as connection:
            result=self.submit_in_tx(connection.cursor(),principal,runner_id,request,
                                     expected_revision=expected_revision)
            connection.commit()
            return result

    def submit_in_tx(self,cursor,principal,runner_id,request,*,expected_revision=None):
        row = lock_owned_runner(cursor, principal, runner_id)
        cursor.execute('SELECT * FROM agent_runner_controls WHERE runner_id=%s AND client_request_id=%s',
                       (runner_id,request.client_request_id))
        previous = decoded(cursor.fetchone())
        if previous:
            if previous['intent_digest'] != request.digest():
                raise RunnerError('IDEMPOTENCY_INPUT_MISMATCH',409)
            return previous,False
        # 控制输入字节闸：已受理控制的幂等重放不受限，仅新控制 413 拒绝。
        from .persistence_limits import assert_intent_within_request_limit
        assert_intent_within_request_limit(request.intent(), code='CONTROL_TOO_LARGE')
        if expected_revision is not None and expected_revision != row['revision']:
            raise RunnerError('CHECKPOINT_REVISION_CHANGED',409)
        if row['cancel_requested'] or row['status'] in ('finalizing','completed','cancelled','failed'):
            raise RunnerError('CONTROL_EXECUTION_CLOSED',409)
        structured = None
        if request.action != 'pause':
            if row['status'] not in ('paused','waiting','interrupted'):
                raise RunnerError('RUNNER_NOT_PARKED',409)
            if row.get('resume_control_id'):
                raise RunnerError('RUNNER_RESUME_PENDING',409)
            guard_wait = any((waiting or {}).get('kind')=='continuous_guard'
                for _,waiting in execution_waits(row.get('checkpoint')))
            if request.action=='resume' and ((row.get('checkpoint',{}).get('durable_sleep') or {}).get('phase')=='armed' or guard_wait):
                self.assert_wait(row,request)
            if request.action in ('reply','browser_complete'):
                self.assert_wait(row,request)
                cursor.execute('''SELECT 1 FROM agent_runner_controls WHERE runner_id=%s
                    AND status='consumed' AND action IN ('reply','browser_complete')
                    AND payload->>'wait_id'=%s AND payload->>'target_execution_id'=%s''',
                    (runner_id, request.wait_id, request.target_execution_id))
                if cursor.fetchone():
                    raise RunnerError('CONTROL_WAIT_CONSUMED',409)
                # 结构化提问受理（设计 §5.4）：失败同步 422 + errors，不落库；
                # 成功生成 canonical 与 form 快照。旧 clarify 原路径不受影响。
                structured = (self._accept_ask_form(row, request)
                              if request.action == 'reply'
                              and (request.answers is not None or request.skip) else None)
            if request.action == 'browser_complete':
                if self.browser_completions is None:
                    raise RunnerError('CONTROL_COMPLETION_FORBIDDEN',403)
                self.browser_completions.assert_completion_in_tx(cursor,row,request)
        control_id = 'control_' + uuid.uuid4().hex
        acknowledged_pause = request.action == 'pause' and row['status'] in ('paused','waiting','interrupted')
        status = 'consumed' if acknowledged_pause else 'accepted'
        payload = request.intent()
        if structured is not None:
            # canonical 写入既有 answer 键（recovery._apply_reply 读该键回注为
            # tool result）；form 快照与结构化答案仅供审计/历史，不参与 digest
            # （digest 基于客户端原始请求，已由 request.digest() 冻结）。
            payload['answer'] = structured['text']
            payload['form'] = structured['form']
        cursor.execute('''INSERT INTO agent_runner_controls
            (control_id,runner_id,tenant_id,scope_key,action,client_request_id,intent_digest,payload,
             status,consumed_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,
             CASE WHEN %s THEN clock_timestamp() ELSE NULL END) RETURNING *''',
            (control_id,runner_id,row['tenant_id'],row['scope_key'],request.action,request.client_request_id,
             request.digest(),json.dumps(payload,ensure_ascii=False),status,acknowledged_pause))
        control = dict(cursor.fetchone())
        if request.action == 'pause':
            # A later explicit pause supersedes a not-yet-consumed resume. It
            # cannot overwrite finalization, which was rejected above.
            if row.get('resume_control_id'):
                cursor.execute('''UPDATE agent_runner_controls SET status='rejected',
                    error_code='RESUME_SUPERSEDED_BY_PAUSE',consumed_at=clock_timestamp()
                    WHERE control_id=%s AND status='accepted' ''',(row['resume_control_id'],))
            cursor.execute('''UPDATE agent_runners SET pause_requested=TRUE,resume_control_id=NULL,
                control_revision=control_revision+1,view_revision=view_revision+1,
                updated_at=clock_timestamp() WHERE runner_id=%s''',(runner_id,))
        else:
            snapshot = dict(row.get('public_snapshot') or {})
            if request.action == 'reply' or (request.action=='resume' and (request.answer.strip() or request.attachments)):
                entry = {
                    'control_id':control_id,'message_id':control_id+':user','text':request.answer,
                    'client_request_id':request.client_request_id,
                    'wait_id':request.wait_id if request.action=='reply' else None,
                    'target_execution_id':request.target_execution_id if request.action=='reply' else runner_id,
                    'accepted_at':control['accepted_at'].isoformat(),
                    'attachments':[{key:item.model_dump(mode='json')[key]
                                    for key in ('file_id','name','type','mime_type','size')}
                                   for item in request.attachments]}
                if structured is not None:
                    # 结构化受理：text 保持 canonical（与 tool result 同源），另附
                    # 归一化答案与 form 快照供历史只读卡片渲染（设计 §7.1）。
                    entry['text'] = structured['text']
                    entry['answers'] = structured['answers']
                    entry['form'] = structured['form']
                snapshot.setdefault('supplementalInputs',[]).append(entry)
            cursor.execute('''UPDATE agent_runners SET resume_control_id=%s,
                public_snapshot=%s::jsonb,
                control_revision=control_revision+1,view_revision=view_revision+1,
                updated_at=clock_timestamp() WHERE runner_id=%s''',
                (control_id,capped_snapshot_dumps(snapshot),runner_id))
        from .application_public_projection import sync_public_display
        cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s',(runner_id,))
        sync_public_display(cursor,decoded(cursor.fetchone()),advance_view=False)
        EventRepository.notify_in_tx(cursor,row)
        return control,True


    def get(self, principal, runner_id, control_id):
        with self.connection_factory() as connection:
            cursor = connection.cursor()
            row = lock_owned_runner(cursor, principal, runner_id)
            cursor.execute('SELECT * FROM agent_runner_controls WHERE runner_id=%s AND control_id=%s', (runner_id,control_id))
            control = cursor.fetchone()
            if not control:
                raise RunnerError('CONTROL_NOT_FOUND',404)
            return dict(control)

    def find_request(self, principal, runner_id, request):
        with self.connection_factory() as connection:
            cursor = connection.cursor()
            lock_owned_runner(cursor,principal,runner_id)
            cursor.execute('SELECT * FROM agent_runner_controls WHERE runner_id=%s AND client_request_id=%s',
                           (runner_id,request.client_request_id))
            previous = cursor.fetchone()
            if previous and previous['intent_digest'] != request.digest():
                raise RunnerError('IDEMPOTENCY_INPUT_MISMATCH',409)
            return dict(previous) if previous else None
