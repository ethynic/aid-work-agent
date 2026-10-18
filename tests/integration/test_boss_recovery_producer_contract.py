"""Real CLI operation producers -> real Runner admission, with browser primitives mocked.

Prerequisite: build clients/boss-resume-assistant. This never connects to Chrome or a model.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from src.local_tools import proxy_tool
from src.local_tools.boss_visual_recovery import cli_owns_recovery, is_visual_failure, recovery_context

pytestmark = pytest.mark.integration
CLI = Path(__file__).resolve().parents[2] / 'clients' / 'boss-resume-assistant'
PRODUCERS = [
    ('boss_filter', 'BossFilterTool'), ('boss_clear_filter', 'BossClearFilterTool'),
    ('boss_filter_options', 'BossFilterOptionsTool'), ('boss_goto', 'BossGotoTool'),
    ('boss_lookup_candidates', 'BossLookupCandidatesTool'), ('boss_greet_selected', 'BossGreetSelectedTool'),
    ('boss_greet', 'BossGreetTool'), ('boss_accept_resume', 'BossAcceptResumeTool'),
    ('boss_reject_current', 'BossRejectCurrentTool'), ('boss_interview_demo', 'BossInterviewDemoTool'),
    ('boss_send_to', 'BossSendToTool'), ('boss_send_current', 'BossSendCurrentTool'),
    ('boss_read_chat', 'BossReadChatTool'), ('boss_open_chat', 'BossOpenChatTool'),
    ('boss_list_jobs', 'BossListJobsTool'), ('boss_select_job', 'BossSelectJobTool'),
    ('boss_resume_detail', 'BossResumeDetailTool'), ('boss_resume_batch', 'BossResumeBatchTool'),
    ('boss_resume_next', 'BossResumeNextTool'), ('boss_open_detail', 'BossOpenDetailTool'),
    ('boss_greet_detail', 'BossGreetDetailTool'), ('boss_close_detail', 'BossCloseDetailTool'),
]
NODE_PRODUCERS = r"""
import { CodedOperationError } from './dist/src/main/operations/types.js';
import { TOOL_DEFS } from './dist/src/mcp/toolDefs.js';
import { withLocalRecovery } from './dist/src/main/boss/localOperationRecovery.js';
const cases = [
 ['bossFilter','createBossFilterOperation',{experience:'5-10年'}],
 ['bossFilter','createBossClearFilterOperation',{}],
 ['bossFilterOptions','createBossFilterOptionsOperation',{}],
 ['bossGoto','createBossGotoOperation',{target:'chat'}],
 ['bossCandidateSelection','createBossLookupCandidatesOperation',{names:['合成甲']}],
 ['bossCandidateSelection','createBossGreetSelectedOperation',{name:'合成甲',candidate_ref:`boss-card-v1:${Date.now()}:${'a'.repeat(64)}`}],
 ['bossGreet','createBossGreetOperation',{names:['合成甲'],limit:1}],
 ['bossAcceptResume','createBossAcceptResumeOperation',{limit:1,preview:false},'index'],
 ['bossRejectCurrent','createBossRejectCurrentOperation',{},'index'],
 ['bossInterviewDemo','createBossInterviewDemoOperation',{remark:'合成演示'},'index'],
 ['bossSendTo','createBossSendToOperation',{to:'合成甲',message:'合成消息'},'index'],
 ['bossSendCurrent','createBossSendCurrentOperation',{message:'合成消息'},'index'],
 ['bossReadChat','createBossReadChatOperation',{contact:'合成甲'},'index'],
 ['bossOpenChat','createBossOpenChatOperation',{contact:'合成甲'},'index'],
 ['bossListJobs','createBossListJobsOperation',{}],
 ['bossSelectJob','createBossSelectJobOperation',{job_name:'合成职位'}],
 ['bossResumeDetail','createBossResumeDetailOperation',{candidate_name:'合成甲'}],
 ['bossResumeBatch','createBossResumeBatchOperation',{limit:1}],
 ['bossResumeBatch','createBossResumeBatchOperation',{limit:1,exclude_names:['已读取乙'],expected_job_name:'合成职位'},'recommend',true],
 ['bossOpenDetail','createBossOpenDetailOperation',{name:'合成甲'}],
 ['bossGreetDetail','createBossGreetDetailOperation',{name:'合成甲',greet_in_detail:true}],
 ['bossCloseDetail','createBossCloseDetailOperation',{}],
];
const output = {}, local = {};
for (const [module,exported,args,page='recommend',continuation=false] of cases) {
 let inputs=0,captures=0,closes=0;
 const input=async()=>{ inputs++; throw Error('No input is expected in this test'); };
 const factory=async()=>({snapshot:async()=>{captures++;throw new CodedOperationError('UI_CHANGED','合成页面读异常');},
   getUrl:async()=>`https://www.zhipin.com/web/chat/${page}`, click:input,clickBrowse:input,clickAndType:input,
   mouseWheel:input,pressEscape:input,captureFullpage:async()=>Buffer.alloc(0),close:async()=>{closes++;}});
 const imported=await import(`./dist/src/main/operations/${module}.js`);
 const operation=imported[exported](factory,continuation);
 const result=await operation.execute(args,{signal:new AbortController().signal,progress(){}});
 output[operation.name]={result,inputs,captures,closes};
 local[operation.name]=await withLocalRecovery(operation).execute(args,{signal:new AbortController().signal,progress(){}});
}
console.log(JSON.stringify({declared:TOOL_DEFS.map(t=>t.name).filter(n=>!n.startsWith('boss_overlay_')),output,local}));
"""


@pytest.fixture(scope='module')
def actual_cli_receipts():
    node = shutil.which('node')
    if not node or not (CLI / 'dist' / 'src' / 'main' / 'operations' / 'index.js').exists():
        pytest.skip('Requires Node and npm run build:main in clients/boss-resume-assistant')
    # Explicitly prevent automatic browser startup in this child, including future fixture changes.
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {'PATH', 'SYSTEMROOT', 'TEMP', 'TMP', 'WINDIR'}}
    environment['AID_BOSS_AUTO_CHROME'] = '0'
    result = subprocess.run([node, '--input-type=module'], input=NODE_PRODUCERS,
                            cwd=CLI, env=environment, text=True, encoding='utf-8',
                            capture_output=True, timeout=30, check=True)
    return json.loads(result.stdout)


def test_every_advertised_domain_command_has_a_tested_recovery_producer(actual_cli_receipts):
    expected = {name for name, _ in PRODUCERS}
    assert set(actual_cli_receipts['declared']) == expected
    assert set(actual_cli_receipts['output']) == expected


@pytest.mark.parametrize('name,adapter', PRODUCERS)
def test_production_cli_fact_is_admitted_by_its_real_server_policy(actual_cli_receipts, name, adapter):
    produced = actual_cli_receipts['output'][name]
    result = produced['result']
    proxy = getattr(proxy_tool, adapter)()
    assert produced['inputs'] == 0 and produced['closes'] == 1
    # The established open-detail path also makes a read-only late-state check.
    assert 1 <= produced['captures'] <= 2
    assert result['code'] == 'UI_CHANGED' and result['effect'] == 'none'
    assert result['data']['operation_recovery']['operation']['name'] == name
    assert recovery_context(proxy, result) is not None, f'{name} producer/consumer contract mismatch'
    assert is_visual_failure(proxy, result), f'{name} must enter the shared diagnosis on a direct page error'
    assert result['data']['operation_recovery']['execution']['ordinary_retries_used'] == 0


@pytest.mark.parametrize('name,adapter', PRODUCERS)
def test_current_cli_wrapped_producer_prevents_a_second_server_recovery(actual_cli_receipts, name, adapter):
    result = actual_cli_receipts['local'][name]
    proxy = getattr(proxy_tool, adapter)()
    assert result['code'] == 'UI_CHANGED' and result['effect'] == 'none'
    assert cli_owns_recovery(result)
    assert result['data']['operation_recovery']['schema_version'] == 2
    assert result['data']['operation_recovery']['operation']['name'] == name
    assert recovery_context(proxy, result) is None
    assert not is_visual_failure(proxy, result)
