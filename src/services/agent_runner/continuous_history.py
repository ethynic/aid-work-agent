"""Archive closed root history before dropping bounded recovery projections."""

import copy
from src.core.agent_engine.contracts import CheckpointFailure
from .ownership import lock_runner
from .transaction_repository import write_history

KEEP_MESSAGES = 48
KEEP_FACTS = 32


def compaction_cut(messages):
    return closed_prefix(messages,max(0,len(messages)-KEEP_MESSAGES)) if len(messages)>=KEEP_MESSAGES*2 else 0


def closed_prefix(messages, limit):
    """A cut never separates a model tool declaration from any of its results."""
    open_calls = set()
    cut = 0
    for index, message in enumerate(messages[:limit]):
        open_calls.update(call['id'] for call in message.get('tool_calls') or [])
        if message.get('role') == 'tool':
            open_calls.discard(message.get('tool_call_id'))
        if not open_calls:
            cut = index+1
    return cut


def archive_and_compact(control, state):
    if state.execution_id != control.attempt.runner_id:
        raise CheckpointFailure('HISTORY_ROOT_OWNER_MISMATCH')
    generated = state.messages[state.initial_len:]
    cut = compaction_cut(generated)
    offset = state.resources.get('history_offset',0)
    if cut and state.resources.get('continuous_summary_through') != offset+cut:
        raise CheckpointFailure('CONTINUOUS_CONTEXT_NOT_COMPACTED')
    archive = []
    for index,message in enumerate(generated[:cut]):
        value = copy.deepcopy(message)
        value['_history_id'] = state.execution_id+':history:'+str(offset+index)
        if value.get('role')=='user' and (value.get('metadata') or {}).get('control_id'):
            value['content'] = value['metadata'].get('submitted_text',value.get('content',''))
        archive.append(value)
    if archive:
        with control.repository.connection_factory() as connection:
            cursor = connection.cursor()
            runner = lock_runner(cursor,control.attempt.runner_id,control.attempt)
            write_history(cursor,runner,{'status':runner['status'],'messages':archive,'archival':True})
            lock_runner(cursor,control.attempt.runner_id,control.attempt)
            connection.commit()
        # A crash here repeats a same-ID insert, never loses already stored rows.
        del state.messages[state.initial_len:state.initial_len+cut]
        state.resources['history_offset'] = offset+cut
        archived_controls = {(message.get('metadata') or {}).get('control_id')
            for message in archive if message.get('role')=='user'}-{None}
        for key,source in (('supplemental_inputs',control.envelope),('supplementalInputs',control.snapshot)):
            if isinstance(source.get(key),list):
                source[key] = [item for item in source[key] if item.get('control_id') not in archived_controls]
        for control_id in archived_controls:
            state.resources.get('continuation_inputs',{}).pop(control_id,None)
    state.resources['history_version'] = 1
    retire_local_references(control,state)
    archive_artifacts(control,state)
    compact_facts(state,retire_resources=lambda data: retire_child_resources(control,state,data))
    retire_attachment_resources(control,state)
    # Public progress and display-only controls are projections, not authority.
    for source in (control.envelope.get('presentation',{}),control.snapshot):
        for key in ('progressMessages','verboseMessages','clarificationQuestions'):
            if isinstance(source.get(key),list):
                source[key] = source[key][-KEEP_MESSAGES:]
    retained = set(state.tools)
    durations = control.envelope.get('tool_durations')
    if isinstance(durations,dict):
        control.envelope['tool_durations'] = {key:value for key,value in durations.items() if key in retained}


def retire_attachment_resources(control,state):
    """Retire attachment owner copies after their model/recovery references leave the window."""
    import json
    from .resource_paths import cleanup_workspaces
    resource_directory = getattr(control,'resource_directory',None)
    if resource_directory is None:
        return
    live = set()
    nodes = []

    def references(value):
        if isinstance(value,str):
            try:
                value = json.loads(value)
            except (ValueError,TypeError):
                return
        if isinstance(value,dict):
            if isinstance(value.get('file_id'),str):
                live.add(value['file_id'])
            for item in value.values():
                if isinstance(item,(dict,list)):
                    references(item)
        elif isinstance(value,list):
            for item in value:
                references(item)

    def visit(node):
        nodes.append(node)
        for message in node.get('messages',[]) + node.get('followup_messages',[]):
            references(message.get('content'))
        for fact in node.get('tools',{}).values():
            references(fact.get('result'))
        for child in node.get('children',{}).values():
            if isinstance(child.get('checkpoint'),dict):
                visit(child['checkpoint'])

    checkpoint = {'execution_id':state.execution_id,'messages':state.messages,
                  'followup_messages':state.followup_messages,
                  'tools':{key:{'result':fact.result} for key,fact in state.tools.items()},
                  'resources':state.resources,'children':state.children}
    visit(checkpoint)
    for node in nodes:
        resources = node.get('resources') or {}
        artifacts = resources.get('image_artifacts') or []
        kept = [item for item in artifacts if item.get('source') != 'local_attachment'
                or (item.get('image_ref') or {}).get('file_id') in live]
        kept_paths = {item.get('path') for item in kept}
        for item in artifacts:
            if item in kept:
                continue
            if item.get('path') not in kept_paths and not cleanup_workspaces(resource_directory,state.identity,
                    {'execution_id':node['execution_id'],'resources':{'image_artifacts':[item]}}):
                kept.append(item)  # A temporary filesystem error must not lose cleanup ownership.
        if artifacts:
            resources['image_artifacts'] = kept


def compact_facts(state,retire_resources=None):
    """Only closed facts are discarded; pending effects retain full recovery proof."""
    from src.core.execution_usage import execution_token_usage
    before = execution_token_usage(state.checkpoint())
    from .continuous_context import retained_context
    instructions,cursors = retained_context(state,state.messages[state.initial_len:])
    if instructions or cursors:
        state.resources.update(continuous_instructions=instructions,continuous_cursors=cursors)
    removable = [fact for fact in state.model_calls
        if fact.get('phase')=='completed' and fact.get('applied') is True]
    prune = removable[:-KEEP_FACTS]
    if prune:
        identifiers = {id(fact) for fact in prune}
        state.model_calls[:] = [fact for fact in state.model_calls if id(fact) not in identifiers]
        state.usage_watermark = max(0,state.usage_watermark-len(prune))
    pending = {call.id for call in state.pending}
    recent = set(list(state.tools)[-KEEP_FACTS:])
    referenced = set(state.resources.get('round_tool_ids') or [])
    referenced.update(phase.get('tool_call_id') for phase in
        (state.resources.get('local_domain_phases') or {}).values() if phase.get('phase')!='completed')
    protected = pending|recent|referenced
    for child in state.children.values():
        data = child.get('checkpoint')
        if isinstance(data,dict):
            from src.core.agent_engine.contracts import ExecutionState
            restored = ExecutionState.restore(data)
            compact_facts(restored,retire_resources)
            generated = restored.messages[restored.initial_len:]
            cut = compaction_cut(generated)
            if (cut and restored.outcome.value not in {'completed','failed','cancelled','iteration_limit'}
                    and restored.resources.get('continuous_summary_through') != restored.resources.get('history_offset',0)+cut):
                raise CheckpointFailure('CONTINUOUS_CONTEXT_NOT_COMPACTED')
            del restored.messages[restored.initial_len:restored.initial_len+cut]
            if cut:
                restored.resources['history_offset'] = restored.resources.get('history_offset',0)+cut
            child['checkpoint'] = restored.checkpoint()
    from .aggregate_tree import child_tree_finished
    for key,child in list(state.children.items()):
        fact = state.tools.get(key)
        data = child.get('checkpoint') or {}
        if (key not in protected and fact and fact.phase=='completed' and fact.result_recorded
                and data.get('outcome') in {'completed','failed','cancelled','iteration_limit'}
                and child_tree_finished(data) and retired_subtree(data)
                and (retire_resources(data) if retire_resources else not subtree_resources(data))):
            state.children.pop(key)
            state.resources['archived_child_count'] = state.resources.get('archived_child_count',0)+1
    removable_tools = [key for key,fact in state.tools.items() if key not in pending|recent|referenced
        and fact.phase=='completed' and fact.result_recorded and key not in state.children]
    for key in removable_tools:
        # Local references are also persistent recovery evidence. Preserve those
        # facts until the owning adapter has explicitly retired its reference.
        if any(value.get('tool_call_id')==key for value in
                (state.resources.get('local_invocations') or {}).values()):
            continue
        state.tools.pop(key)
        state.resources.get('tool_cursor_orders',{}).pop(key,None)
        state.resources['archived_tool_count'] = state.resources.get('archived_tool_count',0)+1
        state.resources.get('plan_tasks',{}).pop(key,None)
        phases = state.resources.get('local_domain_phases') or {}
        for phase_key,phase in list(phases.items()):
            if phase.get('tool_call_id')==key and phase.get('phase')=='completed':
                phases.pop(phase_key)
    after = execution_token_usage(state.checkpoint())
    totals = state.resources.setdefault('archived_token_usage',{'input':0,'output':0,'cached':0})
    for key in totals:
        totals[key] += before[key]-after[key]



def archive_artifacts(control,state,root=None):
    """Persist display references before shrinking the current projection."""
    root = state if root is None else root
    presentation = (control.envelope.get('presentation') or {}) if state is root else {}
    files = presentation.get('downloadableFiles') or []
    images = state.images
    old_images,old_files = images[:-KEEP_MESSAGES],files[:-KEEP_MESSAGES]
    if old_images or old_files:
        archive_artifact_window(control,state,root,presentation,old_images,old_files)
    for child in state.children.values():
        if isinstance(child.get('checkpoint'),dict):
            from src.core.agent_engine.contracts import ExecutionState
            restored = ExecutionState.restore(child['checkpoint'])
            archive_artifacts(control,restored,root)
            child['checkpoint'] = restored.checkpoint()


def archive_artifact_window(control,state,root,presentation,old_images,old_files):
    offset = root.resources.get('artifact_history_offset',0)
    from .public_view import _public_images,_public_presentation
    metadata = {'images':_public_images(old_images),
        **_public_presentation({'downloadableFiles':old_files})}
    persist_artifacts(control,root.execution_id+':history:artifacts:'+str(offset),metadata)
    del state.images[:len(old_images)]
    if 'downloadableFiles' in presentation:
        del presentation['downloadableFiles'][:len(old_files)]
    if state is root:
        control.snapshot['images'] = copy.deepcopy(state.images)
        control.snapshot['downloadableFiles'] = copy.deepcopy(presentation.get('downloadableFiles') or [])
    root.resources['artifact_history_offset'] = offset+1


def persist_artifacts(control,message_id,metadata):
    with control.repository.connection_factory() as connection:
        cursor = connection.cursor()
        runner = lock_runner(cursor,control.attempt.runner_id,control.attempt)
        write_history(cursor,runner,{'status':runner['status'],'archival':True,'messages':[
            {'role':'assistant','content':'','_history_id':message_id,'metadata':metadata}]})
        lock_runner(cursor,control.attempt.runner_id,control.attempt)
        connection.commit()


def subtree_resources(data):
    resources = data.get('resources') or {}
    return (bool(resources.get('workspace') or resources.get('reply_workspaces') or resources.get('image_artifacts'))
        or any(subtree_resources(child.get('checkpoint') or {}) for child in data.get('children',{}).values()))


def retire_child_resources(control,root,data):
    """Closed child resources are idempotently cleaned under their original owner.

    A crash before the next checkpoint leaves a closed child with missing temp
    resources; recovery already ignores resources of closed children. Never
    delete a directory still named by the remaining live execution context.
    """
    from .contracts import canonical_json
    from .resource_paths import cleanup_workspaces,workspace_directory
    resource_directory = getattr(control,'resource_directory',None)
    if subtree_resources(data) and resource_directory is None:
        return False
    identity = root.identity
    def without_owner(node):
        if node.get('execution_id')==data.get('execution_id'):
            return None
        return {**node,'children':{key:{**child,'checkpoint':without_owner(child.get('checkpoint') or {})}
            for key,child in node.get('children',{}).items()}}
    live = canonical_json(without_owner(root.checkpoint())).replace('\\\\','/').replace('\\','/')
    def clean(node):
        if node.get('identity')!=identity.__dict__ or not node.get('execution_id'):
            return False
        if subtree_resources(node):
            expected = str(workspace_directory(resource_directory,identity,node['execution_id'])).replace('\\','/')
            if expected in live:
                return False
        for child in node.get('children',{}).values():
            if not clean(child.get('checkpoint') or {}):
                return False
        # Public image/download registries copy assets to tenant storage; only
        # private input artifacts/workspaces inside this namespace are retired.
        if node.get('images'):
            from .public_view import _public_images
            persist_artifacts(control,root.execution_id+':history:child-artifacts:'+node['execution_id'],{'images':_public_images(node['images'])})
        if subtree_resources(node):
            try:
                return cleanup_workspaces(resource_directory,identity,node,strict=True)
            except OSError:
                from loguru import logger
                logger.opt(exception=True).warning('AgentRunner child resource retirement unavailable')
                return False
        return True
    return clean(data)

def retired_subtree(data):
    resources = data.get('resources') or {}
    return (not resources.get('local_invocations')
        and all(phase.get('phase')=='completed' for phase in (resources.get('local_domain_phases') or {}).values())
        and not any(fact.get('phase') in {'dispatching','waiting'} for fact in data.get('tools',{}).values())
        and all(retired_subtree(child.get('checkpoint') or {}) for child in data.get('children',{}).values()))


def retire_local_references(control,state,retire_all=False):
    """Retire closed DB reads or positively completed original invocations."""
    pending = {call.id for call in state.pending}
    protected = pending if retire_all else pending|set(list(state.tools)[-KEEP_FACTS:])|set(state.resources.get('round_tool_ids') or [])
    refs = state.resources.get('local_invocations') or {}
    candidates = {key:ref for key,ref in refs.items()
        if ref.get('tool_call_id') not in protected
        and (fact:=state.tools.get(ref.get('tool_call_id'))) is not None
        and fact.phase=='completed' and fact.result_recorded}
    if candidates:
        with control.repository.connection_factory() as connection:
            cursor = connection.cursor()
            lock_runner(cursor,control.attempt.runner_id,control.attempt)
            cursor.execute('''SELECT * FROM local_tool_invocations WHERE id=ANY(%s::uuid[])
                AND tenant_id IS NOT DISTINCT FROM %s AND user_id IS NOT DISTINCT FROM %s
                AND session_id=%s AND business_kind='agent_runner' AND business_ref->>'runner_id'=%s''',
                ([ref['invocation_id'] for ref in candidates.values()],state.identity.tenant_id,
                 state.identity.user_id,state.identity.session_id,control.attempt.runner_id))
            from src.local_tools.repository import operation_completion_known
            from src.local_tools.wecom_flow import readonly_db_call
            completed = {str(row['id']) for row in cursor.fetchall()
                if operation_completion_known(dict(row)) or (row['state']=='unknown'
                    and readonly_db_call(row['tool_name'],row.get('arguments_json') or {}))}
        for key,ref in candidates.items():
            if ref['invocation_id'] in completed:
                refs.pop(key,None)
    from .aggregate_tree import child_tree_finished
    for call_id,child in state.children.items():
        if isinstance(child.get('checkpoint'),dict):
            from src.core.agent_engine.contracts import ExecutionState
            restored = ExecutionState.restore(child['checkpoint'])
            fact = state.tools.get(call_id)
            finished = (call_id not in protected and fact and fact.phase=='completed' and fact.result_recorded
                and child['checkpoint'].get('outcome') in {'completed','failed','cancelled','iteration_limit'}
                and child_tree_finished(child['checkpoint']))
            retire_local_references(control,restored,retire_all=bool(finished))
            child['checkpoint'] = restored.checkpoint()
