"""Finite original Browser resource proof for the cancellation composition.

This producer slice observes PG closure only; it never starts, resumes or
requests a different browser. Live/lost/unbound effects remain verification.
"""

from src.core.agent_engine.contracts import CheckpointFailure
from .browser_binding import owned_browser_execution, _same_binding


def cancellation_facts(cursor,row):
    root=(row.get('checkpoint') or {}).get('execution')
    pending=[root] if root else []
    facts=[]
    seen=set()
    while pending:
        node=pending.pop()
        execution_id=node.get('execution_id')
        if execution_id in seen or len(seen)>=1000:
            raise CheckpointFailure('BROWSER_TREE_OWNER_MISMATCH')
        seen.add(execution_id)
        for call_id,tool in node.get('tools',{}).items():
            if (tool.get('call') or {}).get('name')!='browser_automation':
                continue
            saved_ref=node.get('resources',{}).get('browser_runs',{}).get(call_id)
            if tool.get('phase')=='completed' and saved_ref is None:
                # A known zero-IO capability failure has no Browser resource.
                continue
            current,_,digest=owned_browser_execution(row,row['checkpoint'],execution_id,call_id)
            ref=current.get('resources',{}).get('browser_runs',{}).get(call_id)
            if ref is None:
                known=tool.get('phase')=='prepared'
            else:
                if ref.get('version')!=1 or ref.get('arguments_digest')!=digest:
                    raise CheckpointFailure('BROWSER_RUN_CHECKPOINT_MISMATCH')
                cursor.execute('SELECT * FROM bs_browser_runs WHERE run_id=%s',(ref['run_id'],))
                run=cursor.fetchone()
                expected=dict(runner_id=row['runner_id'],runner_execution_id=execution_id,
                    runner_tool_call_id=call_id,run_id=ref['run_id'],
                    **{key:row[key] for key in ('tenant_id','user_id','session_id')},
                    **{key:ref[key] for key in ('owner_worker_id','owner_boot_id','browser_epoch','owner_endpoint')})
                _same_binding(run,expected)
                known=run['runtime_state']=='closed' and run['owner_lease_until'] is None and run['closed_at'] is not None
            facts.append(dict(kind='domain',known=known,
                error_code=None if known else 'BROWSER_CLOSE_VERIFICATION_REQUIRED',
                execution_id=execution_id,tool_call_id=call_id))
        for child in node.get('children',{}).values():
            saved=child.get('checkpoint')
            if isinstance(saved,dict):
                pending.append(saved)
    return facts
