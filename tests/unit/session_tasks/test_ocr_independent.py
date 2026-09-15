import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4
import pytest
from src.session_tasks import service, decisions, texts
from src.session_tasks.constants import SessionTaskError
from src.session_tasks.ocr_matching import ocr_text_matches

@pytest.mark.parametrize('current,conflict', [('请确认hello world谢谢',False),('请确认其他完全不同的内容',True)])
def test_materialize_same_id_keeps_initial_payload(monkeypatch,current,conflict):
    original='请确认“HELLO WORLD”，谢谢。'
    conn=MagicMock();cursor=conn.cursor.return_value
    cursor.fetchone.side_effect=[None,{'sender':'peer','encrypted_payload':'synthetic'}]
    monkeypatch.setattr(texts,'_crypto',lambda:(None,lambda _:json.dumps({'text':original}).encode()))
    store=MagicMock();monkeypatch.setattr(service,'store_text',store)
    binding=uuid4();payload={'batch_id':str(uuid4()),'conversation_binding_id':str(binding),'messages':[{'local_message_id':'m-same','sender':'peer','text':current}],'input_version':2}
    if conflict:
        with pytest.raises(SessionTaskError) as error:service._materialize_batch(conn,'tenant',uuid4(),binding,payload,batch_status='historical')
        assert error.value.code=='CONFLICT'
    else:
        service._materialize_batch(conn,'tenant',uuid4(),binding,payload,batch_status='historical')
        inserts=[c.args for c in cursor.execute.call_args_list if 'INSERT INTO session_task_batches' in c.args[0]]
        assert len(inserts)==1 and json.loads(inserts[0][1][4])==['m-same']
    store.assert_not_called()
    assert not any('UPDATE session_task_texts' in c.args[0] or 'INSERT INTO session_task_messages' in c.args[0] for c in cursor.execute.call_args_list)

@pytest.mark.parametrize('sent,observed,manual',[
 (['abcdefghijklmnopqrst','Xbcdefghijklmnopqrst'],['Ybcdefghijklmnopqrst'],True),
 (['abcdefghijklmnopqrst'],['Xbcdefghijklmnopqrst'],False),
 (['abcdefghijklmnopqrst'],['Xbcdefghijklmnopqrst','Ybcdefghijklmnopqrst'],True),
])
def test_verified_fuzzy_echo_unique_capacity(monkeypatch,sent,observed,manual):
    conn=MagicMock();cursor=conn.cursor.return_value
    cursor.fetchall.side_effect=[[],[{'reply_text_id':f'd{i}','sent_count':1} for i in range(len(sent))],[{'text_id':f'm{i}'} for i in range(len(observed))]]
    payloads={**{f'd{i}':{'text':x} for i,x in enumerate(sent)},**{f'm{i}':{'text':x} for i,x in enumerate(observed)}}
    monkeypatch.setattr(decisions,'load_text',lambda conn,tenant,task,key,**kw:payloads[key])
    assert decisions.check_manual_intervention(conn,'tenant','task',[{'text':observed[-1]}]) is manual

def test_python_ts_shared_boundaries():
    pairs=[['这是用于测试数字不能错误合并的长消息𐄇','这是用于测试数字不能错误合并的长消息𐄈'],['这是一条用于测试英文否定词边界识别方式的很长消息请not发送','这是一条用于测试英文否定词边界识别方式的很长消息请发送'],['你好\ufeff世界','你好世界'],['“你好，世界！”','你好 世界'],['ＡＢＣ','abc'],['abcdefghij','abcdefghiX'],['好','不好'],['金额−100元','金额-100元'],['金额-100元','金额100元'],['今天会议时间为12:30请确认','今天会议时间为13:30请确认'],['今天会议已经确认请等待😀','今天会议已经确认请等待😢'],['a'*19999+'b','a'*19999+'c']]
    root=Path(__file__).resolve().parents[3]
    script="import {ocrTextMatches} from './clients/weixin-cli/dist/src/platform/ocrTextMatch.js';let s='';for await(const c of process.stdin)s+=c;console.log(JSON.stringify(JSON.parse(s).map(([a,b])=>ocrTextMatches(a,b))));"
    result=subprocess.run(['node','--input-type=module','-e',script],input=json.dumps(pairs),text=True,capture_output=True,cwd=root,check=True)
    assert json.loads(result.stdout)==[ocr_text_matches(a,b) for a,b in pairs]


def test_myers_matches_reference_edit_distance():
    import random
    rng=random.Random(194)
    for _ in range(400):
        a=''.join(rng.choice('abcd') for _ in range(rng.randrange(10,45)))
        b=list(a)
        for __ in range(rng.randrange(0,7)):
            if rng.randrange(2) and b:b.pop(rng.randrange(len(b)))
            else:b.insert(rng.randrange(len(b)+1),rng.choice('abcd'))
        b=''.join(b)
        previous=list(range(len(b)+1))
        for i,x in enumerate(a,1):
            row=[i]
            for j,y in enumerate(b,1):row.append(min(row[-1]+1,previous[j]+1,previous[j-1]+(x!=y)))
            previous=row
        expected=(a==b) or (min(len(a),len(b))>=10 and previous[-1]<=max(len(a),len(b))//10)
        assert ocr_text_matches(a,b) is expected,(a,b,previous[-1])

def test_twenty_thousand_periodic_changes_are_bounded_and_match_ts():
    import time
    a='甲'*20000;b=('甲'*9+'乙')*2000
    started=time.perf_counter()
    assert ocr_text_matches(a,b)
    assert time.perf_counter()-started<3
    root=Path(__file__).resolve().parents[3]
    script="import {ocrTextMatches} from './clients/weixin-cli/dist/src/platform/ocrTextMatch.js';console.log(ocrTextMatches('甲'.repeat(20000),'甲'.repeat(9).concat('乙').repeat(2000)));"
    result=subprocess.run(['node','--input-type=module','-e',script],cwd=root,capture_output=True,text=True,check=True,timeout=10)
    assert result.stdout.strip()=='true'
