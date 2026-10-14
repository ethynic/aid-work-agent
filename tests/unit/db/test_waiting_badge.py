"""会话列表「待回答」徽标命中判定（Phase 2，七轮评审修正回归）。

数据形状严格与 list_by_user 的 SQL 列名一致：
`SELECT session_id, public_snapshot->'waiting' AS waiting` —— 消费端读取
`row["waiting"]`（jsonb 自动解码为 dict；字符串形态兼容旧驱动行为）。
"""
from src.db.models import _waiting_ask_form_hit


def _waiting(**kw):
    node = {"kind": "clarification",
            "form": {"form_id": "frm_1", "wait_id": kw.pop("wait_id", "wait-1"),
                     "questions": [{"id": "q1", "type": "multi"}]}}
    node.update(kw)
    return node


def test_top_level_clarification_form_hits():
    assert _waiting_ask_form_hit(_waiting()) is True


def test_nested_child_wait_leaf_hits():
    # 子执行委派链：中间层无 form，叶节点 clarification/form（与通知生产者
    # _clarification_leaf 同结构、同下钻语义）。
    nested = {"kind": "child_wait",
              "child_wait": {"kind": "child_wait",
                             "child_wait": _waiting(wait_id="wait-leaf")}}
    assert _waiting_ask_form_hit(nested) is True


def test_non_ask_waiting_and_garbage_do_not_hit():
    # legacy clarify（无 form）不点亮徽标；空对象/None/坏 JSON 串不命中。
    assert _waiting_ask_form_hit({"kind": "clarification", "question": "哪种城市？"}) is False
    assert _waiting_ask_form_hit({}) is False
    assert _waiting_ask_form_hit(None) is False
    assert _waiting_ask_form_hit("not-json") is False


def test_string_json_form_hits_matches_raw_sql_driver_shape():
    # 个别驱动/配置下 jsonb 仍以字符串返回：字符串 JSON 同样命中。
    import json
    assert _waiting_ask_form_hit(json.dumps(_waiting())) is True
