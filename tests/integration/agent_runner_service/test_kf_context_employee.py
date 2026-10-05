"""Trusted received employee text is history even when SDK state is unavailable."""
import asyncio

import pytest

from .kf_context_fixtures import context_receipts, kf_scope, human_text
from .kf_admission_peer import StateReply

pytestmark=pytest.mark.integration


def test_actual_employee_sdk_invalid_state_keeps_real_assistant_history_without_false_human_task_authority(
        context_receipts,monkeypatch):
    from src.channels.wecom_kf.context_repository import ContextRepository
    from src.channels.wecom_kf.context_worker import ContextWorker
    from src.services.recap import runner as old_recap
    c,s=context_receipts,context_receipts.scope
    employee=c.receive(human_text(s,'employee_unavailable_state_'+s.marker,
        'Fictional employee message despite unavailable SDK state',employee=True))[0]
    before=c.receipt(employee)
    # A real failing HTTP status is translated by the original ingress SDK
    # into its safe errcode=-1 response. No SDK or observation method is mocked.
    c.platform.states[:]=[StateReply({'errcode':0,'service_state':3},status=503)]
    forbidden=[]

    def no_legacy_void_task(*args,**kwargs):
        forbidden.append('OLD_VOID_RECAP')
        raise AssertionError('OLD_VOID_RECAP_MUST_NOT_EXECUTE')
    monkeypatch.setattr(old_recap,'enqueue_human_period_tasks',no_legacy_void_task)
    repository=ContextRepository(s.database.connect)

    async def receive_history():
        worker=ContextWorker(c.config.wecom_kf,repository=repository,
            client_factory=c.original_client)
        try:
            return await worker.run_once()
        finally:
            await worker.close()
            assert not worker.tasks

    projected=asyncio.run(receive_history())
    assert projected['disposition']=='persisted'
    content='[人工客服] Fictional employee message despite unavailable SDK state'
    stored=[row for row in c.stored_history() if row['message_id']==projected['history_id']]
    assert len(stored)==1 and stored[0]['role']=='user' and stored[0]['content']==content
    assert stored[0]['metadata']['source']=='servicer'
    for reader in (c.original_history,c.runtime_history):
        roles=[(row['role'],row['content']) for row in reader()]
        assert ('assistant',content) in roles and ('user',content) not in roles
    fixed=s.rows('SELECT classification,observed_state,observed_at FROM wecom_kf_receipt_classifications '
        'WHERE account_id=%s AND namespace=%s AND message_id=%s',
        (employee.account_id,employee.namespace,employee.message_id))
    assert fixed==[{'classification':'employee','observed_state':None,'observed_at':None}]
    assert s.rows('SELECT 1 FROM wecom_kf_context_task_intents WHERE account_id=%s',
        (employee.account_id,))==[]
    assert c.receipt(employee)==before and len(c.platform.calls)==1 and not c.platform.errors
    assert repository.commit(employee)['history_id']==projected['history_id']
    assert len(c.platform.calls)==1 and len(c.stored_history())==2 and forbidden==[]
    assert s.rows('SELECT 1 FROM agent_runners WHERE session_id=%s',(s.legacy_sid,))==[]
    assert s.rows('SELECT 1 FROM chat_records WHERE session_id=%s',(s.legacy_sid,))==[]


def test_two_original_employee_consumers_reconcile_one_history_and_late_real_state_three_intents_once(
        context_receipts,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from src.channels.wecom_kf.context_repository import ContextRepository
    from src.channels.wecom_kf.context_worker import ContextWorker
    c,s=context_receipts,context_receipts.scope
    locator=c.receive(human_text(s,'employee_observation_race_'+s.marker,
        'Fictional concurrent employee observation',employee=True))[0]
    release=threading.Event()
    held=StateReply({'errcode':0,'service_state':3},release=release)
    c.platform.states[:]=[held]
    repository=ContextRepository(s.database.connect)

    async def one_original_consumer():
        worker=ContextWorker(c.config.wecom_kf,repository=repository,
            client_factory=c.original_client)
        try:
            return await worker.run_once()
        finally:
            await worker.close()
            assert not worker.tasks

    with ThreadPoolExecutor(max_workers=1) as pool:
        first=pool.submit(lambda:asyncio.run(one_original_consumer()))
        try:
            # Genuine SDK response timing DI only. First classification is
            # already durably employee, while observations remain NULL.
            assert held.arrived.wait(5)
            assert s.rows('SELECT classification,observed_state,observed_at,io_config_version '
                'FROM wecom_kf_receipt_classifications WHERE account_id=%s AND message_id=%s',
                (locator.account_id,locator.message_id))==[{'classification':'employee',
                    'observed_state':None,'observed_at':None,'io_config_version':None}]
            second=asyncio.run(one_original_consumer())
            assert second['disposition']=='persisted'
            assert len(c.platform.calls)==1  # fixed-path second worker cannot mint another SDK read
            history_before=c.stored_history()
            assert len(history_before)==2
            assert history_before[1]['content']=='[人工客服] Fictional concurrent employee observation'
            assert s.rows('SELECT 1 FROM wecom_kf_context_task_intents WHERE account_id=%s',
                (locator.account_id,))==[]
            release.set()
            returned=first.result(timeout=12)
            assert returned['disposition']=='persisted' and returned['history_id']==second['history_id']
        finally:
            release.set()
    assert c.stored_history()==history_before
    fixed=s.rows('SELECT classification,observed_state,observed_at,io_config_version '
        'FROM wecom_kf_receipt_classifications WHERE account_id=%s AND message_id=%s',
        (locator.account_id,locator.message_id))
    assert len(fixed)==1 and fixed[0]['classification']=='employee' and fixed[0]['observed_state']==3
    assert fixed[0]['observed_at'] is not None and fixed[0]['io_config_version']==c.stored_config()['updated_at']
    tasks=s.rows('SELECT task_id,task_name,history_id,state FROM wecom_kf_context_task_intents '
        'WHERE account_id=%s ORDER BY task_name',(locator.account_id,))
    assert len(tasks)==2 and {row['task_name'] for row in tasks}=={'lead_refresh','external_push_human'}
    assert all(row['history_id']==second['history_id'] and row['state']=='pending_adapter' for row in tasks)
    assert repository.commit(locator)['history_id']==second['history_id']
    assert s.rows('SELECT task_id,task_name,history_id,state FROM wecom_kf_context_task_intents '
        'WHERE account_id=%s ORDER BY task_name',(locator.account_id,))==tasks
    assert len(c.platform.calls)==1 and not c.platform.errors
    content='[人工客服] Fictional concurrent employee observation'
    for reader in (c.original_history,c.runtime_history):
        roles=[(row['role'],row['content']) for row in reader()]
        assert ('assistant',content) in roles and ('user',content) not in roles
    # Own PG projection corruption and explicit history-clear DI. A real
    # persisted fact must not authorize intents through a mismatched row or
    # recreate an intentionally cleared history. No SDK fact is manufactured.
    from src.channels.wecom_kf.ingress_auth import KfIngressError
    try:
        s.rows('UPDATE channel_messages SET content=%s WHERE message_id=%s AND tenant_id=%s',
            ('Fictional wrong projection',second['history_id'],s.tenant_id))
        with pytest.raises(KfIngressError) as mismatch:
            repository.commit(locator)
        assert mismatch.value.code=='KF_CONTEXT_HISTORY_CONFLICT'
        assert s.rows('SELECT task_id,task_name,history_id,state FROM wecom_kf_context_task_intents '
            'WHERE account_id=%s ORDER BY task_name',(locator.account_id,))==tasks
    finally:
        s.rows('UPDATE channel_messages SET content=%s WHERE message_id=%s AND tenant_id=%s',
            (content,second['history_id'],s.tenant_id))
    assert c.stored_history()==history_before
    s.rows('DELETE FROM channel_messages WHERE message_id=%s AND tenant_id=%s',
        (second['history_id'],s.tenant_id))
    assert repository.commit(locator)['history_id']==second['history_id']
    assert c.stored_history()==history_before[:1]
    assert s.rows('SELECT task_id,task_name,history_id,state FROM wecom_kf_context_task_intents '
        'WHERE account_id=%s ORDER BY task_name',(locator.account_id,))==tasks
    assert len(c.platform.calls)==1 and not c.platform.errors
    assert s.rows('SELECT 1 FROM agent_runners WHERE session_id=%s',(s.legacy_sid,))==[]
    assert s.rows('SELECT 1 FROM chat_records WHERE session_id=%s',(s.legacy_sid,))==[]

    # Separate same-domain durable-recovery window: first worker exits after
    # original classify has COMMITTED a genuine SDK3, before commit_text.
    # This is transparent scheduling/exit DI, not a synthesized class or SQL
    # result. The second worker has already persisted one history with no task.
    recovery_locator=c.receive(human_text(s,'employee_committed_exit_'+s.marker,
        'Fictional employee durable observation recovery',employee=True))[0]
    recovery_release=threading.Event()
    recovery_reply=StateReply({'errcode':0,'service_state':3},release=recovery_release)
    c.platform.states.append(recovery_reply)
    original_classify=repository.classify
    committed=[]

    class CommittedObservationExit(RuntimeError):
        pass

    def original_classify_then_exit(actual_locator,actual_observation):
        result=original_classify(actual_locator,actual_observation)
        if actual_locator==recovery_locator:
            assert result['classification']=='employee' and result['observed_state']==3
            committed.append(True)
            raise CommittedObservationExit('FICTIONAL_EXIT_AFTER_CLASSIFY_COMMIT')
        return result

    monkeypatch.setattr(repository,'classify',original_classify_then_exit)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first=pool.submit(lambda:asyncio.run(one_original_consumer()))
        try:
            assert recovery_reply.arrived.wait(5)
            persisted=asyncio.run(one_original_consumer())
            assert persisted['disposition']=='persisted'
            stable_history=c.stored_history()
            assert len(stable_history)==2
            assert stable_history[-1]['message_id']==persisted['history_id']
            assert stable_history[-1]['content']=='[人工客服] Fictional employee durable observation recovery'
            assert s.rows('SELECT 1 FROM wecom_kf_context_task_intents WHERE account_id=%s '
                'AND namespace=%s AND message_id=%s',
                (recovery_locator.account_id,recovery_locator.namespace,recovery_locator.message_id))==[]
            recovery_release.set()
            with pytest.raises(CommittedObservationExit):
                first.result(timeout=12)
        finally:
            recovery_release.set()
    assert committed==[True] and c.stored_history()==stable_history
    durable=s.rows('SELECT classification,observed_state FROM wecom_kf_receipt_classifications '
        'WHERE account_id=%s AND namespace=%s AND message_id=%s',
        (recovery_locator.account_id,recovery_locator.namespace,recovery_locator.message_id))
    assert durable==[{'classification':'employee','observed_state':3}]
    assert len(c.platform.calls)==2 and not c.platform.errors
    disabled=c.config.wecom_kf.model_copy(deep=True)
    disabled.enabled=False

    async def original_persistent_retry():
        worker=ContextWorker(disabled,repository=repository,client_factory=c.original_client)
        try:
            restored=await worker.run_once()
            assert restored['disposition']=='persisted' and restored['history_id']==persisted['history_id']
            assert await worker.run_once() is None
            return restored
        finally:
            await worker.close()
            assert not worker.tasks

    asyncio.run(original_persistent_retry())
    recovered_tasks=s.rows('SELECT task_id,task_name,history_id,state FROM wecom_kf_context_task_intents '
        'WHERE account_id=%s AND namespace=%s AND message_id=%s ORDER BY task_name',
        (recovery_locator.account_id,recovery_locator.namespace,recovery_locator.message_id))
    assert len(recovered_tasks)==2
    assert {row['task_name'] for row in recovered_tasks}=={'lead_refresh','external_push_human'}
    assert all(row['history_id']==persisted['history_id'] and row['state']=='pending_adapter'
        for row in recovered_tasks)
    assert c.stored_history()==stable_history and committed==[True]
    assert len(c.platform.calls)==2 and not c.platform.errors
    assert s.rows('SELECT 1 FROM agent_runners WHERE session_id=%s',(s.legacy_sid,))==[]
    assert s.rows('SELECT 1 FROM chat_records WHERE session_id=%s',(s.legacy_sid,))==[]
