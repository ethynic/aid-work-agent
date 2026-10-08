"""Application composition for Local and the original live Browser runtime."""

import asyncio
import copy
from .browser_completion import BrowserCompletionRepository, BrowserRecoveryUnavailable
from .browser_binding import owned_browser_execution, _same_binding
from .local_recovery import LocalRecoveryProof
from .ownership import LeaseLost
from .contracts import RunnerError


def browser_references(checkpoint):
    if checkpoint is None:
        return
    if not isinstance(checkpoint,dict):
        raise RunnerError('CHECKPOINT_TREE_INVALID',409)
    if checkpoint.get('execution') is None:
        return
    pending = [checkpoint['execution']]
    seen = set()
    while pending:
        node = pending.pop()
        if not isinstance(node,dict):
            raise RunnerError('CHECKPOINT_TREE_INVALID',409)
        execution_id=node.get('execution_id')
        resources,tools,children=(node.get(key,{}) for key in ('resources','tools','children'))
        if (not isinstance(execution_id,str) or not execution_id or execution_id in seen or len(seen)>=1000
                or not all(isinstance(value,dict) for value in (resources,tools,children))
                or not isinstance(node.get('waiting') or {},dict)):
            raise RunnerError('CHECKPOINT_TREE_INVALID',409)
        seen.add(execution_id)
        for key in ('browser_runs','browser_completions','browser_waits'):
            if not isinstance(resources.get(key,{}),dict):
                raise RunnerError('CHECKPOINT_TREE_INVALID',409)
        for tool in tools.values():
            if (not isinstance(tool,dict) or not isinstance(tool.get('call'),dict)
                    or not isinstance(tool['call'].get('arguments',{}),dict)):
                raise RunnerError('CHECKPOINT_TREE_INVALID',409)
        for call_id,ref in resources.get('browser_runs',{}).items():
            tool = tools.get(call_id)
            if (not isinstance(call_id,str) or not isinstance(ref,dict) or not isinstance(tool,dict)
                    or not isinstance(ref.get('waits',{}),dict)
                    or any(not isinstance(ref.get(key),str) or not ref[key] for key in
                        ('runner_id','run_id','runner_execution_id','runner_tool_call_id',
                         'owner_worker_id','owner_boot_id','browser_epoch','owner_endpoint'))):
                raise RunnerError('CHECKPOINT_TREE_INVALID',409)
            if tool.get('phase') in {'dispatching','waiting'}:
                yield node,call_id,ref
        for child in children.values():
            if not isinstance(child,dict) or not isinstance(child.get('task_record') or {},dict):
                raise RunnerError('CHECKPOINT_CHILD_INCOMPLETE',409)
            saved = child.get('checkpoint')
            if isinstance(saved,dict):
                pending.append(saved)
            elif saved is not None or child.get('unstarted') is not True:
                raise RunnerError('CHECKPOINT_CHILD_INCOMPLETE',409)


class BrowserRecoveryProof:
    def __init__(self,process,connection_factory):
        self.process = process
        self.repository = BrowserCompletionRepository(connection_factory)

    def supports(self,fact):
        from src.tools.browser.automation_tool import BrowserAutomationTool
        return fact.call.name == BrowserAutomationTool.name

    @staticmethod
    def _matches(row,node,call_id,ref,fact):
        binding=fact.get('binding') or {}
        if (any(ref.get(key)!=binding.get(key) for key in
                ('runner_id','run_id','runner_execution_id','runner_tool_call_id','owner_worker_id',
                 'owner_boot_id','browser_epoch','owner_endpoint'))
                or binding.get('runner_execution_id')!=node['execution_id']
                or binding.get('runner_tool_call_id')!=call_id
                or any(binding.get(key)!=row[key] for key in ('tenant_id','user_id','session_id'))
                or ref.get('waits',{}).get(fact['wait_id'])!=fact['assistance_id']):
            raise BrowserRecoveryUnavailable('BROWSER_COMPLETION_OWNER_MISMATCH')

    def _affinity_rows(self,row):
        refs = list(browser_references(row.get('checkpoint')))
        live_refs=[]
        with self.repository.connection_factory() as connection:
            cursor = connection.cursor()
            for node,call_id,ref in refs:
                owned_browser_execution(row,row['checkpoint'],node['execution_id'],call_id)
                cursor.execute('SELECT *,clock_timestamp() AS database_now FROM bs_browser_runs WHERE run_id=%s',
                    (ref.get('run_id'),))
                run = cursor.fetchone()
                keys=('runner_id','run_id','runner_execution_id','runner_tool_call_id',
                      'owner_worker_id','owner_boot_id','browser_epoch','owner_endpoint')
                _same_binding(run,{**{key:ref[key] for key in keys},
                    **{key:row[key] for key in ('tenant_id','user_id','session_id')}})
                completion = node.get('resources',{}).get('browser_completions',{}).get(call_id)
                if completion:
                    cursor.execute('''SELECT * FROM bs_browser_assistance_requests
                        WHERE runner_id=%s AND completion_ref=%s''',(row['runner_id'],completion))
                    saved = cursor.fetchone()
                    fact = self.repository.fact(saved) if saved else None
                    if fact:
                        self._matches(row,node,call_id,ref,fact)
                    if fact and fact['continuation']['phase']=='completed' and not fact['continuation']['result'].get('next_assistance_id'):
                        continue
                if (not run or run['runtime_state']!='live' or not run['owner_lease_until']
                        or run['owner_lease_until']<=run['database_now']):
                    raise BrowserRecoveryUnavailable('BROWSER_RUNTIME_OWNER_LOST')
                if not self.process or run['owner_boot_id']!=self.process['boot']:
                    return False,[]
                live_refs.append((node,call_id,ref))
        return True,live_refs

    async def affinity(self,row):
        try:
            eligible,refs=await asyncio.to_thread(self._affinity_rows,row)
        except LeaseLost as error:
            raise BrowserRecoveryUnavailable('BROWSER_RUNTIME_OWNER_LOST') from error
        if not eligible:
            return False
        from src.tools.browser.human_control import get_owned_runtime
        for node,call_id,ref in refs:
            runtime=await get_owned_runtime(row['tenant_id'],ref['run_id'])
            owner=getattr(getattr(runtime,'manager',None),'execution_owner',None)
            if (not owner or owner.process_resources is not self.process
                    or owner.manager not in self.process.get('retained_managers',set())
                    or owner.worker_boot!=ref['owner_boot_id'] or owner.browser_epoch!=ref['browser_epoch']
                    or owner.state.execution_id!=node['execution_id'] or owner.call_id!=call_id):
                raise BrowserRecoveryUnavailable('BROWSER_RUNTIME_NOT_AVAILABLE')
        return True

    def assert_claim_in_tx(self,cursor,row,control,*,worker_id):
        refs=list(browser_references(row.get('checkpoint')))
        for node,call_id,ref in refs:
            fact=None
            completion=node.get('resources',{}).get('browser_completions',{}).get(call_id)
            if completion:
                cursor.execute('''SELECT * FROM bs_browser_assistance_requests
                    WHERE runner_id=%s AND completion_ref=%s''',(row['runner_id'],completion))
                saved=cursor.fetchone()
                fact=self.repository.fact(saved) if saved else None
                if fact is None:
                    raise BrowserRecoveryUnavailable('BROWSER_COMPLETION_FACT_MISSING')
                self._matches(row,node,call_id,ref,fact)
                if fact and fact['continuation']['phase']=='completed' and not fact['continuation']['result'].get('next_assistance_id'):
                    self.repository.scope(cursor,row,fact['binding'],fact['assistance_id'],live=False)
                    continue
                if fact['continuation']['phase']=='started':
                    raise BrowserRecoveryUnavailable()
            if (not self.process or ref.get('owner_boot_id')!=self.process['boot']
                    or ref.get('owner_worker_id')!=worker_id):
                raise BrowserRecoveryUnavailable('BROWSER_RECOVERY_NOT_OWNER')
            binding={**ref,**{key:row[key] for key in ('tenant_id','user_id','session_id')}}
            waiting=node.get('waiting') or {}
            assistance_id=waiting.get('assistance_id')
            if fact and fact['continuation']['phase']=='observed':
                expected=dict(kind='human_assistance',wait_id=fact['wait_id'],assistance_id=fact['assistance_id'],
                    tool_call_id=call_id,target_execution_id=node['execution_id'])
                original=node['resources'].get('browser_waits',{}).get(call_id)
                if (not isinstance(original,dict)
                        or any(original.get(key)!=value for key,value in expected.items())
                        or (waiting and any(waiting.get(key)!=value for key,value in expected.items()))):
                    raise BrowserRecoveryUnavailable('BROWSER_COMPLETION_WAIT_MISMATCH')
                # Engine clears waiting before recover; a pre-start pause can
                # retain only the exact prepared original wait and observation.
                assistance_id=fact['assistance_id']
            elif fact and fact['continuation']['phase']=='completed':
                result=fact['continuation']['result']
                next_wait=result.get('waiting')
                if (not isinstance(next_wait,dict) or not waiting
                        or result.get('next_assistance_id')!=waiting.get('assistance_id')
                        or any(waiting.get(key)!=next_wait.get(key) for key in
                            ('kind','wait_id','assistance_id','tool_call_id','target_execution_id'))):
                    raise BrowserRecoveryUnavailable('BROWSER_COMPLETION_WAIT_MISMATCH')
            _,_,_,wait=self.repository.scope(cursor,row,binding,assistance_id)
            if control['action']=='browser_complete' and control['payload'].get('target_execution_id')==node['execution_id']:
                actual=self.repository.fact(wait)
                if (actual['completion_ref']!=control['payload']['completion_ref']
                        or actual.get('control_id')!=control['control_id']
                        or actual['continuation']['phase']!='observed'):
                    raise BrowserRecoveryUnavailable()

    def apply_completion(self,row,control,states):
        payload=control['payload']
        fact=self.repository.read_fact(row['runner_id'],payload['completion_ref'])
        state=states.get(payload['target_execution_id'])
        waiting=state.waiting if state else None
        if (not waiting or waiting.get('wait_id')!=payload['wait_id']
                or waiting.get('assistance_id')!=fact['assistance_id']
                or fact.get('control_id')!=control['control_id'] or fact['wait_id']!=payload['wait_id']
                or fact['continuation']['phase']!='observed'):
            raise BrowserRecoveryUnavailable()
        call_id=waiting['tool_call_id']
        tool=state.tools.get(call_id)
        if tool is None or not self.supports(tool) or tool.phase!='waiting':
            raise BrowserRecoveryUnavailable('BROWSER_COMPLETION_CALL_MISMATCH')
        state.resources.setdefault('browser_completions',{})[call_id]=fact['completion_ref']
        state.resources.setdefault('browser_waits',{})[call_id]=copy.deepcopy(waiting)

    def can_restore(self,row,state,fact):
        reference=state.resources.get('browser_completions',{}).get(fact.call.id)
        if not reference:
            ref=state.resources.get('browser_runs',{}).get(fact.call.id)
            waiting=state.waiting or {}
            if (ref and fact.phase=='waiting' and waiting.get('kind')=='human_assistance'
                    and ref.get('waits',{}).get(waiting.get('wait_id'))==waiting.get('assistance_id')):
                state.resources.setdefault('browser_waits',{})[fact.call.id]=copy.deepcopy(waiting)
                return True
            raise BrowserRecoveryUnavailable('BROWSER_COMPLETION_FACT_MISSING')
        completion=self.repository.read_fact(row['runner_id'],reference)
        ref=state.resources.get('browser_runs',{}).get(fact.call.id)
        binding=completion.get('binding') or {}
        if (not ref or any(ref.get(key)!=binding.get(key) for key in
                ('runner_id','run_id','runner_execution_id','runner_tool_call_id','owner_worker_id',
                 'owner_boot_id','browser_epoch','owner_endpoint'))):
            raise BrowserRecoveryUnavailable('BROWSER_COMPLETION_OWNER_MISMATCH')
        if completion['continuation']['phase'] not in {'observed','completed'}:
            raise BrowserRecoveryUnavailable()
        return True

    def hold(self,row,error):
        return self.repository.hold(row['runner_id'],row['revision'],row['resume_control_id'],error.code)


class ApplicationToolRecovery:
    """Finite installed capabilities; the scheduling/kernel ports stay neutral."""
    def __init__(self,process,connection_factory):
        self.local=LocalRecoveryProof()
        self.browser=BrowserRecoveryProof(process,connection_factory)

    async def affinity(self,row):
        return await self.browser.affinity(row)

    def assert_claim_in_tx(self,cursor,row,control,*,worker_id):
        return self.browser.assert_claim_in_tx(cursor,row,control,worker_id=worker_id)

    def apply_completion(self,row,control,states):
        return self.browser.apply_completion(row,control,states)

    def can_restore(self,row,state,fact):
        return (self.browser.can_restore(row,state,fact) if self.browser.supports(fact)
                else self.local.can_restore(row,state,fact))

    def hold(self,row,error):
        return self.browser.hold(row,error)
