"""Display/statistics aggregation owns no provider IO or billing writes."""
from copy import deepcopy

from src.core.execution_usage import execution_token_usage
from src.services.agent_runner.transaction_repository import execution_statistics


def test_parent_child_grandchild_display_usage_deduplicates_legacy_mixed_facts_without_mutating_checkpoints():
    def fact(owner, name, input_tokens, output_tokens, cached):
        return {"execution_id": owner, "call_id": name, "usage": {
            "prompt_tokens": input_tokens, "completion_tokens": output_tokens, "cached_tokens": cached}}
    # Call ids can coincide across independent owners. Legacy mixed parent
    # snapshots can repeat a descendant receipt fact; neither may distort stats.
    root_fact = fact("root", "same-call", 11, 7, 2)
    child_fact = fact("child", "same-call", 13, 5, 3)
    grand_fact = fact("grandchild", "grand-call", 17, 9, 4)
    grand = {"execution_id": "grandchild", "model_calls": [grand_fact], "children": {}}
    child = {"execution_id": "child", "profile_id": "fixture-child", "model_calls": [child_fact],
             "children": {"grand-delegate": {"execution_id": "grandchild", "checkpoint": grand}}}
    root = {"execution_id": "root", "model_calls": [root_fact, deepcopy(child_fact), deepcopy(grand_fact)],
            "children": {"child-delegate": {"execution_id": "child", "checkpoint": child}}, "tools": {}}
    original = deepcopy(root)
    assert execution_token_usage(root) == {"input": 41, "output": 21, "cached": 9}
    assert execution_token_usage(child) == {"input": 30, "output": 14, "cached": 7}
    stats = execution_statistics(root, "fixture-runner")
    assert len(stats["subagent_calls"]) == 1
    assert stats["subagent_calls"][0]["token_usage"] == {"input": 30, "output": 14, "cached": 7}
    assert root == original
