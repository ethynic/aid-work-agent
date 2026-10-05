"""Display usage aggregation over execution facts; never a billing owner."""


def execution_token_usage(checkpoint):
    totals = {'input': 0, 'output': 0, 'cached': 0}
    seen = set()
    receipts = set()

    def add(usage):
        for target, sources in (('input',('prompt_tokens','input_tokens')),
                ('output',('completion_tokens','output_tokens')),('cached',('cached_tokens','cached_input_tokens'))):
            value = next((usage[key] for key in sources if key in usage),0)
            totals[target] += int(value or 0)

    def visit(node):
        for fact in node.get('model_calls', []):
            owner = fact.get('execution_id') or fact.get('child_execution_id') or node.get('execution_id')
            key = (owner, fact.get('call_id'))
            if key in seen:
                continue
            seen.add(key)
            usage = fact.get('usage') or {}
            reference = usage.get('_runner_receipt_id') or fact.get('_runner_receipt_id')
            if reference:
                if reference in receipts:
                    continue
                receipts.add(reference)
            add(usage)
        # Domain provider responses have their own owner facts. They are
        # display statistics, never replayable Engine model steps or charges.
        for fact in (node.get('resources',{}).get('local_domain_phases') or {}).values():
            if (fact.get('phase')!='completed' or fact.get('execution_id')!=node.get('execution_id')
                    or (fact.get('request') or {}).get('model_purpose') not in {'llm','resume_recognition_covered'}):
                continue
            result = fact.get('result') or {}
            reference = result.get('_runner_receipt_id')
            if not reference or reference in receipts:
                continue
            receipts.add(reference)
            add(result.get('usage') or {})
        for child in node.get('children', {}).values():
            if isinstance(child.get('checkpoint'), dict):
                visit(child['checkpoint'])

    visit(checkpoint)
    return totals
