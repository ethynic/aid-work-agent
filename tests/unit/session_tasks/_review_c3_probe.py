"""C3 review reproduction cases; run explicitly after supplying an authorized test DB.

These assert intended behavior and currently fail. The leading underscore keeps
them out of default discovery until the developer integrates the regressions.
"""
import json
import threading
import uuid

from tests.unit.session_tasks.test_c3_gate import gate_env
from tests.unit.session_tasks.test_c3_decisions import (
    _publish_and_claim, _submit_reply, _feed_batch, _task_row, _decision_row,
)
from src.session_tasks import decisions as d


def test_review_unknown_attempt_not_released(tenant_id, device_row, verified_binding):
    from src.db.database import get_db_connection
    _, task = _publish_and_claim(tenant_id, device_row, verified_binding)
    ref = str(uuid.uuid4())
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO session_task_cost_reservations (tenant_id,task_id,purpose,ref_key,amount) VALUES (%s,%s,'decision',%s,1)", (tenant_id,task['task_id'],ref))
        # An earlier model attempt has an unknown billing outcome; another decision finalizes.
        d._release_unstarted_reservations_keep_last(conn,tenant_id,task['task_id'],'another-decision',False)
        c.execute("SELECT state FROM session_task_cost_reservations WHERE tenant_id=%s AND task_id=%s AND ref_key=%s",(tenant_id,task['task_id'],ref))
        state = c.fetchone()['state']
        conn.rollback()
    assert state == 'reserved', state


def test_review_stale_handoff_does_not_change_task(tenant_id, device_row, verified_binding):
    _, task = _publish_and_claim(tenant_id, device_row, verified_binding)
    _, dec = _submit_reply(tenant_id,device_row,task,verified_binding,1,[{'local_message_id':'m1','sender':'peer','text':'first','source_evidence_ref':'e'}])
    entered, release = threading.Event(), threading.Event()
    def model(*args):
        entered.set()
        assert release.wait(15)
        return {'content':json.dumps({'action':'handoff','reason_code':'old-input'}),'usage':{'prompt_tokens':1,'completion_tokens':1},'model':'test'}
    worker=threading.Thread(target=lambda:d.run_decision_tick(model_call=model))
    worker.start()
    try:
        assert entered.wait(15)
        _feed_batch(tenant_id,device_row,task,verified_binding,2,[{'local_message_id':'m2','sender':'peer','text':'new input','source_evidence_ref':'e'}])
        assert _decision_row(tenant_id,dec['decision_id'])['status']=='superseded'
    finally:
        release.set()
        worker.join(20)
    assert _task_row(tenant_id,task['task_id'])['status']=='active'


def test_review_judged_failed_criterion_cannot_complete(tenant_id, device_row, verified_binding):
    from tests.unit.session_tasks.conftest import build_spec
    from tests.unit.session_tasks.test_c3_decisions import _fake_model
    _,task=_publish_and_claim(tenant_id,device_row,verified_binding,build_spec('judged'))
    _submit_reply(tenant_id,device_row,task,verified_binding,1,[{'local_message_id':'m1','sender':'peer','text':'no agreement','source_evidence_ref':'e'}])
    item={'criterion':'对方明确同意沟通','message_ids':['m1'],'quotes':['invented quote'],'reason':'x'}
    model,_=_fake_model([json.dumps({'action':'done','criterion_results':[item,item]})])
    d.run_decision_tick(model_call=model)
    review,_=_fake_model([json.dumps({'agree':True,'criterion_checks':[{'criterion':'unrelated','satisfied':False}]})])
    d.run_decision_tick(model_call=review)
    assert _task_row(tenant_id,task['task_id'])['status']!='completed'


def test_review_corrupt_transcript_blocks_model(tenant_id, device_row, verified_binding,monkeypatch):
    _,task=_publish_and_claim(tenant_id,device_row,verified_binding)
    _submit_reply(tenant_id,device_row,task,verified_binding,1,[{'local_message_id':'m1','sender':'peer','text':'important constraint','source_evidence_ref':'e'}])
    original=d.load_text
    def broken(*args,**kw):
        if kw.get('expected_purpose')=='message':
            raise d.SessionTaskError('corrupt message','CRYPTO_UNAVAILABLE',503)
        return original(*args,**kw)
    monkeypatch.setattr(d,'load_text',broken)
    calls=[]
    def model(*args):
        calls.append(1)
        return {'content':json.dumps({'action':'reply','reply_text':'reply without constraint'}),'usage':{'prompt_tokens':1,'completion_tokens':1},'model':'test'}
    d.run_decision_tick(model_call=model)
    assert calls==[]


def test_review_concurrent_prepare_creates_one_invocation(tenant_id,device_row,verified_binding,monkeypatch):
    from tests.unit.session_tasks.test_c3_gate import _ready_reply
    from src.desktop_automation import executor
    from src.db.database import get_db_connection
    task,decision_id=_ready_reply(tenant_id,device_row,verified_binding)
    first_at_precheck=threading.Event()
    second_at_precheck=threading.Event()
    first_finished=threading.Event()
    original=executor.precheck_quota
    mutex=threading.Lock()
    calls=[0]
    def precheck(*args,**kw):
        with mutex:
            calls[0]+=1
            index=calls[0]
        if index==1:
            first_at_precheck.set()
            assert second_at_precheck.wait(15)
        else:
            second_at_precheck.set()
            assert first_finished.wait(15)
        return original(*args,**kw)
    monkeypatch.setattr(executor,'precheck_quota',precheck)
    errors=[]
    def prepare(first=False):
        try:d.prepare_send(tenant_id,device_row['id'],uuid.UUID(task['assignment_id']),task['fence'],uuid.UUID(decision_id))
        except Exception as e:errors.append(type(e).__name__)
        finally:
            if first:first_finished.set()
    a=threading.Thread(target=lambda:prepare(True))
    b=threading.Thread(target=prepare)
    a.start()
    assert first_at_precheck.wait(15)
    b.start()
    a.join(25);b.join(25)
    assert not a.is_alive() and not b.is_alive()
    assert errors==[],errors
    with get_db_connection() as conn:
        c=conn.cursor()
        c.execute("SELECT COUNT(*) AS n FROM local_tool_invocations WHERE tenant_id=%s AND business_ref->>'decision_id'=%s",(tenant_id,decision_id))
        n=c.fetchone()['n']
    assert n==1,n


def test_review_failed_billing_must_not_settle(tenant_id,device_row,verified_binding,monkeypatch):
    """账务写入失败不得标记已结算（五轮注入缝更新：账务走 chat_records.billing_ref
    幂等锚链路，注入 d._bill_attempt_once 失败——语义与原 ChatRecordDB.create 相同）。"""
    from src.db.database import get_db_connection
    def fail_insert(*a,**kw): raise RuntimeError('review injected billing write failure')
    monkeypatch.setattr(d,'_bill_attempt_once',fail_insert)
    _,task=_publish_and_claim(tenant_id,device_row,verified_binding)
    _,dec=_submit_reply(tenant_id,device_row,task,verified_binding,1,[{'local_message_id':'m1','sender':'peer','text':'hello','source_evidence_ref':'e'}])
    def model(*args):return {'content':json.dumps({'action':'reply','reply_text':'hello'}),'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2},'model':'test'}
    d.run_decision_tick(model_call=model)
    with get_db_connection() as conn:
        c=conn.cursor()
        c.execute("SELECT state FROM session_task_cost_reservations WHERE tenant_id=%s AND task_id=%s AND ref_key=%s",(tenant_id,task['task_id'],dec['decision_id']))
        state=c.fetchone()['state']
    assert state!='settled',state
