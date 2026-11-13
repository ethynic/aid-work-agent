"""Exercise the historical quotation interruption with real control, worker and tools."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
from socketserver import StreamRequestHandler, TCPServer
from threading import Thread
import uuid

import pytest
from openpyxl import Workbook

from .provider import Reply, tool_reply
from .test_api import start_api_pair
from .test_worker import accept, api_pair, decoded, prices, runner, terminal, workers
from .test_worker_recovery import control


pytestmark = pytest.mark.integration


@contextmanager
def interrupt_tool_checkpoint(database, runner_id, tool_name):
    from psycopg2 import sql
    with database.connect() as connection, connection.cursor() as cursor:
        cursor.execute("""CREATE FUNCTION aid_test_interrupt_tool() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.runner_id=TG_ARGV[0] AND EXISTS (
                    SELECT 1 FROM jsonb_each(NEW.checkpoint#>'{execution,tools}') AS item
                    WHERE item.value#>>'{call,name}'=TG_ARGV[1] AND item.value->>'phase'='completed'
                ) THEN
                    RAISE EXCEPTION 'fictional tool checkpoint storage interruption';
                END IF;
                RETURN NEW;
            END $$""")
        cursor.execute(sql.SQL('CREATE TRIGGER aid_test_interrupt_tool BEFORE UPDATE ON agent_runners '
            'FOR EACH ROW EXECUTE FUNCTION aid_test_interrupt_tool({},{})').format(
                sql.Literal(runner_id),sql.Literal(tool_name)))
    try:
        yield
    finally:
        database.rows('DROP TRIGGER aid_test_interrupt_tool ON agent_runners')
        database.rows('DROP FUNCTION aid_test_interrupt_tool()')


def expired_worker_attempt(database, runner_id):
    row = runner(database,runner_id)
    if row['status'] == 'running':
        # A storage failure may leave the old lease until the ordinary reaper.
        from src.services.agent_runner.execution_repository import ExecutionRepository
        database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s",
                      (runner_id,))
        ExecutionRepository(database.connect).reap_expired()
        row = runner(database,runner_id)
    assert row['status'] == 'interrupted', row['status']
    return row


@pytest.mark.parametrize('name',['read','grep'])
def test_read_only_text_call_resumes_in_original_runner(workers,actors,service_database,name):
    from shutil import which
    if name == 'grep' and which('rg') is None:
        pytest.skip('requires the worker ripgrep binary')
    actor = actors['a']
    source = workers.root / 'recovery-input.txt'
    source.write_text('fictional-recovered-needle\n',encoding='utf-8')
    args = {'file_path':str(source)} if name == 'read' else {'path':str(source),'pattern':'fictional-recovered'}

    def after_tool(request):
        tool_results = [item['content'] for item in request['messages'] if item['role']=='tool']
        assert len(tool_results) == 1 and 'fictional-recovered-needle' in tool_results[0]
        return Reply(content='continued from original text call')

    marker = workers.provider.register(tool_reply(name,args,call_id='original-text-call'),
                                       Reply(produce=after_tool))
    runner_id = accept(workers.api,actor,marker)['runner_id']
    with interrupt_tool_checkpoint(service_database,runner_id,name):
        worker,_ = workers.start()
        workers.assert_clean_exit(worker)
    interrupted = expired_worker_attempt(service_database,runner_id)
    original = interrupted['checkpoint']['execution']['pending'][0]
    control(workers.api,actor,runner_id,'resume')
    worker,_ = workers.start()
    workers.assert_clean_exit(worker)
    completed = terminal(service_database,runner_id)
    assert completed['status'] == 'completed' and completed['attempt'] == interrupted['attempt'] + 1
    saved = completed['checkpoint']['execution']['tools'][original['id']]
    assert saved['call']['arguments'] == args and saved['result_recorded']
    assert len(workers.provider.requests(marker)) == 2 and not workers.provider.errors


@pytest.mark.parametrize('shape',['query-params','multi-headers','prepared-multi-headers'])
def test_skipped_http_post_recognizes_the_same_request(workers,actors,service_database,shape):
    delivered = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            delivered.append((self.path,self.headers.get_all('X-Targets',[])))
            self.send_response(204)
            self.end_headers()

        def log_message(self,*_):
            pass

    actor = actors['a']
    with ThreadingHTTPServer(('127.0.0.1',0),Receiver) as server:
        peer = Thread(target=server.serve_forever,daemon=True)
        peer.start()
        url = f'http://127.0.0.1:{server.server_address[1]}/submit'
        if shape != 'query-params':
            original_args = {'method':'POST','url':url,'headers':{'x-targets':'a','X-Targets':'b'}}
            duplicate_args = {**original_args,'headers':{'X-TARGETS':'a','X-Targets':'b'}}
            independent_args = {**original_args,'headers':{'x-targets':'b'}}
            first, second = ('/submit',['a','b']), ('/submit',['b'])
        else:
            original_args = {'method':'POST','url':url+'?mode=test'}
            duplicate_args = {'method':'POST','url':url,'query_params':{'mode':'test'}}
            independent_args = {**duplicate_args,'query_params':{'mode':'live'}}
            first, second = ('/submit?mode=test',[]), ('/submit?mode=live',[])
        original = tool_reply('http_api',original_args,call_id='original-post')
        duplicate = tool_reply('http_api',duplicate_args,call_id='same-post')
        independent = tool_reply('http_api',independent_args,call_id='independent-post')
        marker = workers.provider.register(original,duplicate,independent,Reply(content='remaining steps done'))
        runner_id = accept(workers.api,actor,marker)['runner_id']
        try:
            if shape == 'prepared-multi-headers':
                from threading import Event
                original.release = Event()
                worker,_ = workers.start()
                try:
                    assert original.arrived.wait(15)
                    control(workers.api,actor,runner_id,'pause')
                finally:
                    original.release.set()
                workers.assert_clean_exit(worker)
                parked = runner(service_database,runner_id)
                assert parked['status'] == 'paused' and not delivered
                execution = parked['checkpoint']['execution']
                pending = execution['pending'][0]
                assert execution['tools'][pending['id']]['phase'] == 'prepared'
                assert list(pending['arguments']['headers']) == ['X-Targets','x-targets']
                control(workers.api,actor,runner_id,'resume')
            with interrupt_tool_checkpoint(service_database,runner_id,'http_api'):
                worker,_ = workers.start()
                workers.assert_clean_exit(worker)
            expired_worker_attempt(service_database,runner_id)
            assert delivered == [first]
            control(workers.api,actor,runner_id,'resume')
            worker,_ = workers.start()
            workers.assert_clean_exit(worker)
            wait = runner(service_database,runner_id)['public_snapshot']['waiting']
            assert '原请求编号' in wait['question']
            control(workers.api,actor,runner_id,'reply',answer='无法确认，跳过此步骤',
                    wait_id=wait['wait_id'],target_execution_id=wait['target_execution_id'])
            worker,_ = workers.start()
            workers.assert_clean_exit(worker)
            assert terminal(service_database,runner_id)['status'] == 'completed'
            assert delivered == [first,second]
            assert not workers.provider.errors
        finally:
            server.shutdown()
            peer.join(timeout=5)


def test_skipped_email_equivalent_recipient_list_cannot_resend_through_real_worker(
        workers,actors,service_database):
    from src.db.email_credential import EmailCredentialDB

    delivered = []

    class Mailbox(StreamRequestHandler):
        def handle(self):
            self.wfile.write(b'220 local SMTP fixture\r\n')
            while line := self.rfile.readline():
                command = line.split(b' ',1)[0].strip().upper()
                if command == b'EHLO':
                    self.wfile.write(b'250-local SMTP fixture\r\n250 AUTH PLAIN\r\n')
                elif command == b'AUTH':
                    self.wfile.write(b'235 accepted\r\n')
                elif command == b'DATA':
                    self.wfile.write(b'354 start mail input\r\n')
                    message = []
                    while (part := self.rfile.readline()) and part != b'.\r\n':
                        message.append(part)
                    delivered.append(b''.join(message))
                    self.wfile.write(b'250 accepted\r\n')
                elif command == b'QUIT':
                    self.wfile.write(b'221 bye\r\n')
                    return
                else:
                    self.wfile.write(b'250 ok\r\n')

    actor = actors['a']
    with TCPServer(('127.0.0.1',0),Mailbox) as mailbox:
        peer = Thread(target=mailbox.serve_forever,daemon=True)
        peer.start()
        workers.environment['SIMULATION_MODE'] = 'false'
        port = mailbox.server_address[1]
        assert EmailCredentialDB.upsert(actor.user_id,{'email_address':'sender@example.invalid',
            'smtp_server':'127.0.0.1','smtp_port':port,'smtp_user':'sender@example.invalid',
            'smtp_password':secrets.token_urlsafe(16),'smtp_encryption':'none',
            'imap_server':'127.0.0.1','imap_port':port})
        args = {'action':'send','to':'recipient@example.invalid','subject':'Original delivery','body':'Original body'}
        repeated = tool_reply('email_process',{**args,'to':['recipient@example.invalid']},
                              call_id='same-smtp-delivery')
        independent = tool_reply('email_process',{**args,'subject':'Separate delivery'},call_id='independent-delivery')
        marker = workers.provider.register(tool_reply('email_process',args,call_id='original-delivery'),
            repeated,independent,Reply(content='remaining steps done'))
        runner_id = accept(workers.api,actor,marker)['runner_id']
        try:
            with interrupt_tool_checkpoint(service_database,runner_id,'email_process'):
                first,_ = workers.start()
                workers.assert_clean_exit(first)
            expired_worker_attempt(service_database,runner_id)
            assert len(delivered) == 1 and b'Subject: Original delivery' in delivered[0]
            control(workers.api,actor,runner_id,'resume')
            worker,_ = workers.start()
            workers.assert_clean_exit(worker)
            wait = runner(service_database,runner_id)['public_snapshot']['waiting']
            assert 'recipient@example.invalid' in wait['question'] and 'Original delivery' in wait['question']
            control(workers.api,actor,runner_id,'reply',answer='无法确认，跳过此步骤',
                    wait_id=wait['wait_id'],target_execution_id=wait['target_execution_id'])
            worker,_ = workers.start()
            workers.assert_clean_exit(worker)
            assert terminal(service_database,runner_id)['status'] == 'completed'
            assert len(delivered) == 2 and b'Subject: Separate delivery' in delivered[-1]
            assert not workers.provider.errors
        finally:
            mailbox.shutdown()
            peer.join(timeout=5)
            service_database.rows('DELETE FROM user_email_settings WHERE user_id=%s',(actor.user_id,))


@pytest.fixture
def authorized_publication(workers, actors, service_database, monkeypatch, tmp_path):
    from src.core import secret_crypto
    from src.local_tools.repository import create_device
    from src.session_tasks import config, service
    from src.session_tasks.models import TaskDraftCreatePayload
    from src.weixin_conversation import bindings, config as wx_config, registration
    from tests.unit.session_tasks.conftest import build_draft_payload

    actor = actors['a']
    # Only redirect the real hot-read gate to an isolated tenant allowlist.
    # No business logic, authority check, tool, worker or DB is substituted.
    gate = tmp_path / 'publication.yaml'
    gate.write_text(json.dumps({name: {'enabled':True,'tenant_allowlist':[actor.tenant_id]}
        for name in ('session_tasks','weixin_conversation')}), encoding='utf-8')
    monkeypatch.setattr(config,'_gate_yaml_path',lambda: gate)
    monkeypatch.setattr(wx_config,'_gate_yaml_path',lambda: gate)
    key = secrets.token_urlsafe(32)
    monkeypatch.setenv('APP_SECRET_KEY',key)
    monkeypatch.setattr(secret_crypto,'_fernet',None)
    assert registration.ensure_registered()
    worker_entry = """from pathlib import Path
import sys
from src.session_tasks import config
from src.weixin_conversation import config as wx_config
gate = Path(sys.argv.pop(1))
config._gate_yaml_path = lambda: gate
wx_config._gate_yaml_path = lambda: gate
from src.services.agent_runner.worker import main
main()
"""

    def run():
        child = workers.processes.start(['-c',worker_entry,str(gate),'--once',
            '--worker-id','publication-' + uuid.uuid4().hex],
            environment={**workers.environment,'APP_SECRET_KEY':key},private_working_directory=True)
        workers.assert_clean_exit(child)

    device = create_device(actor.tenant_id,actor.user_id,token_hash=uuid.uuid4().hex,
        name='publication fixture',capabilities={'providers':['weixin'],
            'capabilities':['session_task_v1','session_observer_v1','weixin_message_send_v2']})
    account = str(uuid.uuid4())
    binding = bindings.create_binding(actor.tenant_id,actor.user_id,str(device['id']),account,'direct')
    binding_id = binding['conversation_binding_id']
    # This disposable test DB substitutes for the verified device binding. The
    # publication and once-only confirmation still use production services.
    service_database.rows("""UPDATE bs_weixin_conversation_bindings SET verification_status='verified',
        verifier_version='test-fixture',identity_version=1,verified_at=CURRENT_TIMESTAMP,expires_at=%s
        WHERE tenant_id=%s AND id=%s""",
        (datetime.now(timezone.utc)+timedelta(days=1),actor.tenant_id,binding_id))
    draft = service.create_draft(actor.tenant_id,actor.user_id,TaskDraftCreatePayload.model_validate(
        build_draft_payload({'device_id':str(device['id']),'account_binding_id':account,
                             'conversation_binding_id':binding_id})))
    task_id = uuid.UUID(draft['task_id'])
    confirmation = service.issue_publish_confirmation(actor.tenant_id,actor.user_id,task_id,draft['version'])
    args = {'task_id':str(task_id),'expected_version':draft['version'],
            'confirmation_id':confirmation['confirmation_id']}
    try:
        yield args,run
    finally:
        registration.reset_registration()


@contextmanager
def interrupt_publication_before_commit(database, actor):
    from psycopg2 import sql
    with database.connect() as connection, connection.cursor() as cursor:
        cursor.execute("""CREATE FUNCTION aid_test_interrupt_publication() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.tenant_id=TG_ARGV[0] THEN
                    RAISE EXCEPTION 'fictional publication storage interruption';
                END IF;
                RETURN NEW;
            END $$""")
        cursor.execute(sql.SQL('CREATE TRIGGER aid_test_interrupt_publication BEFORE INSERT ON session_tasks_idempotency_keys '
            'FOR EACH ROW EXECUTE FUNCTION aid_test_interrupt_publication({})').format(sql.Literal(actor.tenant_id)))
    try:
        yield
    finally:
        database.rows('DROP TRIGGER aid_test_interrupt_publication ON session_tasks_idempotency_keys')
        database.rows('DROP FUNCTION aid_test_interrupt_publication()')


@pytest.mark.parametrize('resolution', [
    'confirmed_done','skipped','retry','late-receipt','original-receipt','retry-interrupted'])
def test_publication_recovery_reuses_call_and_never_repeats_effect(
        workers,actors,service_database,authorized_publication,resolution):
    from src.session_tasks import service

    actor = actors['a']
    args,run = authorized_publication
    operation = 'session_task_publish'
    original = tool_reply(operation,args,call_id='original-publication')
    repeated = tool_reply(operation,{**args,'expected_version':str(args['expected_version'])},
                          call_id='model-must-not-repeat')
    user_result = 'skipped' if resolution == 'retry-interrupted' else resolution

    def after_tool(request):
        content = [json.loads(item['content']) for item in request['messages'] if item['role']=='tool']
        last = content[-1]
        if user_result in {'confirmed_done','skipped'}:
            if last['recovery']['decision'] != user_result:
                return Reply(content='incorrect-user-confirmation')
            if len(content) == 1:
                return repeated
        elif not (last.get('success') and last.get('task_id') == args['task_id']):
            return Reply(content='missing-authoritative-receipt')
        return Reply(content='original-publication-settled')

    marker = workers.provider.register(original,Reply(produce=after_tool),Reply(produce=after_tool))
    accepted = accept(workers.api,actor,marker)
    runner_id = accepted['runner_id']
    with interrupt_tool_checkpoint(service_database,runner_id,operation):
        if resolution == 'original-receipt':
            run()
        else:
            with interrupt_publication_before_commit(service_database,actor):
                run()
    first = expired_worker_attempt(service_database,runner_id)
    call_id = decoded(first['checkpoint'])['execution']['pending'][0]['id']
    assert len(workers.provider.requests(marker)) == 1
    control(workers.api,actor,runner_id,'resume')
    run()
    if resolution != 'original-receipt':
        # Read from the other API process after the owner exited.
        url = f'/v1/runners/{runner_id}'
        parked = workers.api.call('GET',url,actor=actor,api=1).json()['runner']
        wait = parked['snapshot']['waiting']
        assert parked['status'] == 'waiting' and wait['tool_call_id'] == call_id
        assert args['task_id'] in wait['question']
        assert args['confirmation_id'] not in json.dumps(parked)
        assert 'recovery_reply' not in wait
        assert len(workers.provider.requests(marker)) == 1
        target = {'wait_id':wait['wait_id'],'target_execution_id':wait['target_execution_id']}
        choices = wait['form']['questions'][0]['options']
        assert len(choices) == 3
        invalid = workers.api.call('POST',url+'/controls',actor=actor,json={
            'action':'reply','client_request_id':uuid.uuid4().hex,'answer':'没有完成',**target})
        assert invalid.status_code == 422
        number = {'confirmed_done':0,'retry':1,'late-receipt':1,'skipped':2,'retry-interrupted':1}[resolution]
        answers = [{'question_id':'q1','selected_keys':[choices[number]['key']],'text':None}]
        receipt,request = control(workers.api,actor,runner_id,'reply',answers=answers,**target)
        duplicate,_ = control(workers.api,actor,runner_id,'reply',key=request['client_request_id'],
                              answers=answers,**target)
        assert duplicate['control']['control_id'] == receipt['control']['control_id']
        if resolution == 'late-receipt':
            service.publish_task_once(actor.tenant_id,actor.user_id,uuid.UUID(args['task_id']),
                                      args['expected_version'],uuid.UUID(args['confirmation_id']))
        if resolution == 'retry-interrupted':
            with interrupt_tool_checkpoint(service_database,runner_id,operation):
                with interrupt_publication_before_commit(service_database,actor):
                    run()
            expired_worker_attempt(service_database,runner_id)
            control(workers.api,actor,runner_id,'resume')
            run()
            renewed = workers.api.call('GET',url,actor=actor,api=1).json()['runner']['snapshot']['waiting']
            assert renewed['wait_id'] != wait['wait_id'] and renewed['tool_call_id'] == call_id
            stale = workers.api.call('POST',url+'/controls',actor=actor,json={
                **request,'client_request_id':uuid.uuid4().hex})
            assert stale.status_code == 409 and 'CONTROL_WAIT_STALE' in stale.text
            control(workers.api,actor,runner_id,'reply',answer='无法确认，跳过此步骤',
                    wait_id=renewed['wait_id'],target_execution_id=renewed['target_execution_id'])
        run()
    last = terminal(service_database,runner_id)
    assert last['status'] == 'completed' and decoded(last['result'])['output'] == 'original-publication-settled'
    settled = decoded(last['checkpoint'])['execution']['tools'][call_id]
    assert settled['call']['arguments'] == args and settled['result_recorded']
    expected_writes = 0 if user_result in {'confirmed_done','skipped'} else 1
    assert len(service_database.rows('SELECT revision FROM session_task_specs WHERE task_id=%s',
                                     (args['task_id'],))) == expected_writes
    assert len(workers.provider.requests(marker)) == (3 if expected_writes == 0 else 2)
    assert not workers.provider.errors


@pytest.mark.parametrize('replace_file', [False, True], ids=['prior-delivery','explicit-reupload'])
def test_quotation_resume_preserves_original_step(workers, actors, service_database, replace_file):
    actor = actors['a']
    profile = 'ai-quotation'
    service_database.rows(
        """INSERT INTO subscriptions(subscription_id,tenant_id,subagent_type,status,payment_status)
           VALUES (%s,%s,%s,'active','paid')""",
        (uuid.uuid4().hex,actor.tenant_id,profile))
    service_database.rows(
        'INSERT INTO user_agent_permissions(user_id,tenant_id,agent_id) VALUES (%s,%s,%s)',
        (actor.user_id,actor.tenant_id,profile))
    source = '/app/storage/tmp/spec2quote_1794548459/excel/原始报价清单.xlsx'
    filename = '原始报价清单.xlsx'
    resumed_name = '重新上传的清单.xlsx' if replace_file else filename
    directory = workers.storage / 'tenants' / actor.tenant_id.removeprefix('tenant_') / 'conversation'
    directory.mkdir(parents=True)
    file_id = 'file_' + uuid.uuid4().hex[:12]
    delivered = directory / f'{file_id}.xlsx'
    workbook = Workbook()
    workbook.active['A6'] = '编号'
    workbook.active['L6'] = '依据'
    workbook.active['A7'] = 'QUOTE-RESUME-SKU'
    workbook.active['B7'] = 'Fixture recovered original workbook'
    workbook.save(delivered)
    workbook.close()
    service_database.rows(
        '''INSERT INTO file_assets (file_id, tenant_id, user_id, name, path, source)
           VALUES (%s,%s,%s,%s,%s,'delivery')''',
        (file_id,actor.tenant_id,actor.user_id,filename,str(delivered)))

    # The incident already has a completed cp and file asset. Seed that
    # historical provenance, then produce the interrupted call via real tools.
    prior_marker = workers.provider.register(Reply(content='prior-delivered-quotation'))
    prior = accept(workers.api,actor,prior_marker,profile_id=profile)
    prior_worker,_ = workers.start()
    finished = terminal(service_database,prior['runner_id'])
    workers.assert_clean_exit(prior_worker)
    assert finished['status'] == 'completed'
    checkpoint = decoded(finished['checkpoint'])
    checkpoint['execution']['tools']['prior-cp'] = {
        'call': {'id':'prior-cp','name':'cp','arguments':{'source_file_path':source,'display_name':filename}},
        'phase':'completed','success':True,'result_recorded':True,
        'result':{'file_id':file_id,'file_path':str(delivered),'file_name':filename},
    }
    service_database.rows('UPDATE agent_runners SET checkpoint=%s::jsonb WHERE runner_id=%s',
                          (json.dumps(checkpoint),prior['runner_id']))

    command = (
        'OUT=/app/storage/tmp/quoteprice_$(date +%s) && mkdir -p $OUT && echo "OUT=$OUT"'
        f' && cd /app/src/skills/quotation-pricing-1.0.0 && python scripts/parse_quotation.py "{source}"'
        ' --json $OUT/items_extracted.json --project "测试项目"'
        ' && echo "=====JSON HEAD=====" && head -c 3000 $OUT/items_extracted.json'
    )
    args = {'skill':'quotation-pricing','command':command}

    def after_tool(request):
        tool_messages = [item['content'] for item in request['messages'] if item['role'] == 'tool']
        if any('QUOTE-RESUME-SKU' in item and resumed_name in item for item in tool_messages):
            return Reply(content='continued-from-original-quotation')
        return Reply(content='original-quotation-result-missing')

    marker = workers.provider.register(
        tool_reply('use_skill',{'skill':'quotation-pricing'},call_id='load-quotation'),
        tool_reply('skill_execute',args,call_id='interrupted-quotation'),
        Reply(produce=after_tool),
    )
    accepted = accept(workers.api,actor,marker,profile_id=profile)
    runner_id = accepted['runner_id']
    # A failed checkpoint write immediately before the dispatch outcome leaves
    # the same durable shape as the observed incident. Keep preceding IO real.
    # The original full command points at a fictional /app scratch file.
    # Stop its attempt via the actual checkpoint failure, not a fabricated state.
    with interrupt_tool_checkpoint(service_database,runner_id,'skill_execute'):
        first,_ = workers.start()
        workers.assert_clean_exit(first)
    stopped = expired_worker_attempt(service_database,runner_id)

    pending = decoded(stopped['checkpoint'])['execution']
    call_id = pending['pending'][0]['id']
    assert pending['tools'][call_id]['phase'] == 'dispatching'
    assert pending['tools'][call_id]['result'] is None
    assert pending['pending'][0]['arguments'] == args
    assert len(workers.provider.requests(marker)) == 2

    if replace_file:
        # An earlier cp is insufficient if its delivery belongs to another actor.
        service_database.rows('UPDATE file_assets SET user_id=%s WHERE file_id=%s',
                              (actors['a_other'].user_id,file_id))
    receipt,_ = control(workers.api,actor,runner_id,'resume')
    resumed,_ = workers.start()
    workers.assert_clean_exit(resumed)
    if replace_file:
        parked = runner(service_database,runner_id)
        wait = parked['public_snapshot']['waiting']
        assert parked['status'] == 'waiting' and wait['kind'] == 'clarification'
        assert wait['tool_call_id'] == call_id
        assert len(workers.provider.requests(marker)) == 2
        target = {'wait_id':wait['wait_id'],'target_execution_id':wait['target_execution_id']}
        reply = {'client_request_id':uuid.uuid4().hex,'action':'reply',**target}
        url = f'/v1/runners/{runner_id}/controls'
        # Mirror the deployed API and Worker shared upload mount for all checks.
        reply_api = start_api_pair(workers.processes,extra_environment={
            'AGENT_RUNNER_STORAGE_ROOT':str(workers.storage)})
        repeated_resume = reply_api.call('POST',url,actor=actor,json={
            'action':'resume','client_request_id':uuid.uuid4().hex})
        assert repeated_resume.status_code == 409 and 'CONTROL_WAIT_KIND_MISMATCH' in repeated_resume.text
        service_database.rows("UPDATE file_assets SET source='upload',type='file' WHERE file_id=%s",(file_id,))
        for bad, error in [
            ({'answer':'继续'}, 'RECOVERY_EXCEL_REQUIRED'),
            ({'attachments':[{'file_id':file_id}]}, 'RECOVERY_EXCEL_UNAVAILABLE'),
            ({'attachments':[{'file_id':file_id},{'file_id':file_id}]}, 'RECOVERY_EXCEL_REQUIRED'),
        ]:
            response = reply_api.call('POST',url,actor=actor,json={**reply,**bad})
            assert response.status_code == 422 and error in response.text
        assert runner(service_database,runner_id)['resume_control_id'] is None
        uploaded_id = 'file_' + uuid.uuid4().hex[:12]
        uploaded_path = directory / f'{uploaded_id}.xlsx'
        uploaded_path.write_bytes(b'not an Excel workbook')
        service_database.rows(
            """INSERT INTO file_assets (file_id,tenant_id,user_id,name,path,source,type)
               VALUES (%s,%s,%s,%s,%s,'upload','file')""",
            (uploaded_id,actor.tenant_id,actor.user_id,resumed_name,str(uploaded_path)))
        malformed = reply_api.call('POST',url,actor=actor,json={
            **reply,'attachments':[{'file_id':uploaded_id}]})
        assert malformed.status_code == 422 and 'RECOVERY_EXCEL_UNAVAILABLE' in malformed.text
        uploaded_path.write_bytes(delivered.read_bytes())
        resumed_receipt, request = control(reply_api,actor,runner_id,'reply',key=reply['client_request_id'],
            attachments=[{'file_id':uploaded_id,'name':'supplied-name-is-not-trusted.xlsx'}],**target)
        repeated,_ = control(reply_api,actor,runner_id,'reply',key=request['client_request_id'],
            attachments=request['attachments'],**target)
        assert repeated['control']['control_id'] == resumed_receipt['control']['control_id']
        worker,_ = workers.start()
        workers.assert_clean_exit(worker)
        receipt = resumed_receipt
    result = runner(service_database,runner_id)
    stored = service_database.rows('SELECT status, consumed_attempt, error_code FROM agent_runner_controls WHERE control_id=%s',
                                   (receipt['control']['control_id'],))[0]
    assert stored == {'status':'consumed','consumed_attempt':result['attempt'],'error_code':None}
    assert result['status'] == 'completed' and result['attempt'] == stopped['attempt'] + 1 + replace_file, result['public_snapshot'].get('waiting')
    assert decoded(result['result'])['output'] == 'continued-from-original-quotation'
    settled = decoded(result['checkpoint'])['execution']
    assert settled['tools'][call_id]['call']['arguments'] == args
    assert settled['tools'][call_id]['phase'] == 'completed'
    assert len(workers.provider.requests(marker)) == 3
    assert not workers.provider.errors
    usage = service_database.rows('SELECT phase, applied FROM agent_runner_usage_receipts WHERE runner_id=%s',
                                 (runner_id,))
    assert len(usage) == 3 and all(row == {'phase':'observed','applied':True} for row in usage)
    records = service_database.rows(
        'SELECT prompt_tokens, completion_tokens, credit_cost FROM chat_records WHERE record_id=%s',
        (result['record_id'],))
    assert records == [{'prompt_tokens':33,'completion_tokens':21,'credit_cost':Decimal('0.01')}]
