"""Exercise the historical quotation interruption with real control, worker and tools."""

from decimal import Decimal
import json
import uuid

import pytest
from openpyxl import Workbook

from .provider import Reply, tool_reply
from .test_worker import accept, api_pair, decoded, prices, runner, terminal, workers
from .test_worker_recovery import control


pytestmark = pytest.mark.integration


def test_quotation_resume_consumes_delivered_input_and_preserves_original_step(
        workers, actors, service_database):
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
        if any('QUOTE-RESUME-SKU' in item and filename in item for item in tool_messages):
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
    service_database.rows("""
        CREATE FUNCTION aid_test_interrupt_quote() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.runner_id=TG_ARGV[0] AND EXISTS (
                SELECT 1 FROM jsonb_each(NEW.checkpoint#>'{execution,tools}') AS item
                WHERE item.value#>>'{call,name}'='skill_execute' AND item.value->>'phase'='completed'
            ) THEN
                RAISE EXCEPTION 'fictional quotation checkpoint storage interruption';
            END IF;
            RETURN NEW;
        END $$;
    """)
    from psycopg2 import sql
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute(sql.SQL('CREATE TRIGGER aid_test_interrupt_quote BEFORE UPDATE ON agent_runners '
            'FOR EACH ROW EXECUTE FUNCTION aid_test_interrupt_quote({})').format(sql.Literal(runner_id)))
    # The original full command points at a fictional /app scratch file.
    # Stop its attempt via the actual checkpoint failure, not a fabricated state.
    try:
        first,_ = workers.start()
        workers.assert_clean_exit(first)
        stopped = runner(service_database,runner_id)
        if stopped['status'] == 'running':
            # The injected write error can leave a worker's last persisted lease
            # valid. An expired lease follows the normal interrupted recovery.
            service_database.rows(
                "UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s",
                (runner_id,))
    finally:
        service_database.rows('DROP TRIGGER aid_test_interrupt_quote ON agent_runners')
        service_database.rows('DROP FUNCTION aid_test_interrupt_quote()')
    if stopped['status'] == 'running':
        from src.services.agent_runner.execution_repository import ExecutionRepository
        ExecutionRepository(service_database.connect).reap_expired()
        stopped = runner(service_database,runner_id)
    assert stopped['status'] == 'interrupted', (stopped['status'],len(workers.provider.requests(marker)))

    pending = decoded(stopped['checkpoint'])['execution']
    call_id = pending['pending'][0]['id']
    assert pending['tools'][call_id]['phase'] == 'dispatching'
    assert pending['tools'][call_id]['result'] is None
    assert pending['pending'][0]['arguments'] == args
    assert len(workers.provider.requests(marker)) == 2

    # The matching earlier cp is insufficient when its registered copy belongs
    # to someone else in the same tenant. Verify the public control refuses it.
    service_database.rows('UPDATE file_assets SET user_id=%s WHERE file_id=%s',
                          (actors['a_other'].user_id,file_id))
    rejected,_ = control(workers.api,actor,runner_id,'resume')
    probe,_ = workers.start()
    workers.assert_clean_exit(probe)
    assert service_database.rows(
        'SELECT status,error_code FROM agent_runner_controls WHERE control_id=%s',
        (rejected['control']['control_id'],)) == [{'status':'rejected','error_code':'RECOVERY_INPUT_UNAVAILABLE'}]
    assert runner(service_database,runner_id)['attempt'] == stopped['attempt']
    assert len(workers.provider.requests(marker)) == 2
    service_database.rows('UPDATE file_assets SET user_id=%s WHERE file_id=%s',(actor.user_id,file_id))

    receipt,_ = control(workers.api,actor,runner_id,'resume')
    resumed,_ = workers.start()
    workers.assert_clean_exit(resumed)
    result = runner(service_database,runner_id)
    stored = service_database.rows('SELECT status, consumed_attempt, error_code FROM agent_runner_controls WHERE control_id=%s',
                                   (receipt['control']['control_id'],))[0]
    assert stored == {'status':'consumed','consumed_attempt':result['attempt'],'error_code':None}
    assert result['status'] == 'completed' and result['attempt'] == stopped['attempt'] + 1, result['public_snapshot'].get('waiting')
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
