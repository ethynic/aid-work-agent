"""Thin allocation/close observation for two unchanged original associations.

Original constructors/start/close are called exactly once and their actual
returns/exception propagate. No env, DSN, arguments, bodies or logs are copied.
Only this already allocated process tree's audio filenames/hashes are observed.
"""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import secrets

import pytest

NODES={'test_actual_context_customer_and_servicer_original_history_are_once_and_recap_is_only_pending_adapter',
    'test_actual_voice_receipt_http_accept_then_original_worker_asr_model_record_history_once'}


@pytest.fixture(autouse=True)
def observe_original_association_resources(request,monkeypatch):
    if request.node.name not in NODES:
        yield
        return
    database=request.getfixturevalue('service_database')
    processes=request.getfixturevalue('service_processes')
    root=Path(__file__).resolve().parents[3]/'tmp/agent-runner-evidence/m6-kf-context-voice'
    name='owned-'+secrets.token_hex(16)+'.json';path=root/name
    record={'allocated_utc':datetime.now(timezone.utc).isoformat(),'node':request.node.nodeid,
        'database_name':database.name,'process_root':str(processes.root),
        'storage_roots':[],'conversation_roots':[],'owned_children':[],
        'ownership':'Actual original association process root, original Workers storage constructor, owned subprocess handles; observation only.'}
    def observe(stage):
        artifacts=[]
        for storage in record['storage_roots']:
            for file in sorted(Path(storage).rglob('*')):
                if file.is_file() and file.suffix.lower() in {'.wav','.mp3','.amr','.silk','.pcm','.opus','.aac'}:
                    artifacts.append({'path':str(file),'bytes':file.stat().st_size,
                        'sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
        record.update(stage=stage,observation_utc=datetime.now(timezone.utc).isoformat(),owned_artifacts=artifacts)
        path.write_text(json.dumps(record,indent=2)+'\n')
    def add_child(child):
        stat=Path('/proc')/str(child.pid)/'stat'
        record['owned_children'].append({'pid':child.pid,
            'start_ticks':stat.read_text().rsplit(')',1)[1].split()[19] if stat.exists() else None,
            'actual_exit':child.returncode})
    for child in processes.children: add_child(child)
    observe('allocated_before_original_test_body')
    original_start=type(processes).start
    def observe_start(self,*args,**kwargs):
        child=original_start(self,*args,**kwargs)
        if self is processes:
            add_child(child);observe('original_subprocess_allocated')
        return child
    monkeypatch.setattr(type(processes),'start',observe_start)
    from .test_worker import Workers
    original_init,original_close=Workers.__init__,Workers.close
    def observe_init(self,*args,**kwargs):
        original_init(self,*args,**kwargs)
        if self.processes is processes:
            record['storage_roots'].append(str(self.storage));observe('original_worker_storage_allocated')
    def observe_close(self,*args,**kwargs):
        if self.processes is processes: observe('before_original_worker_close')
        result=original_close(self,*args,**kwargs)
        if self.processes is processes:
            record['original_worker_close_returned']=True
            record['storage_exists_after_original_close']=[Path(p).exists() for p in record['storage_roots']]
            path.write_text(json.dumps(record,indent=2)+'\n')
        return result
    monkeypatch.setattr(Workers,'__init__',observe_init);monkeypatch.setattr(Workers,'close',observe_close)
    try:
        yield
    finally:
        # Actual fixture teardown ordering can follow this observer; only the
        # later original-container audit asserts DB/process-root removal.
        for owned in record['owned_children']:
            handle=next((child for child in processes.children if child.pid==owned['pid']),None)
            if handle is not None: owned['actual_exit']=handle.poll()
        record['observer_finally_utc']=datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(record,indent=2)+'\n')
