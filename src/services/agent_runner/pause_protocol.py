"""Safe pause acknowledgement requires a stopped, checkpointed owned tree."""


def safe_paused_tree(state):
    def safe(data):
        outcome = data.get('outcome')
        if outcome not in {'paused','waiting','completed','iteration_limit','failed','cancelled'}:
            return False
        if outcome in {'failed','cancelled'} and any(
                fact.get('phase') in {'dispatching','waiting'} for fact in data.get('tools',{}).values()):
            # A stopped asyncio task does not prove an already dispatched write
            # finished. Preserve verification rather than acknowledging safety.
            return False
        for child in data.get('children',{}).values():
            if child.get('unstarted') is True:
                if child.get('checkpoint') or child.get('status')!='unstarted' or not child.get('task_record'):
                    return False
            elif not isinstance(child.get('checkpoint'),dict) or not safe(child['checkpoint']):
                return False
        return True
    return safe(state.checkpoint())
