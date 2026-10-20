"""Unknown model commentary must not erase valid facts or loosen client/action contracts."""
from copy import deepcopy
import base64
import io
import json

from PIL import Image
import pytest

from src.services import boss_visual_service as visual
from tests.unit.api.test_boss_visual import api, payload, payload_v2, post, DIAGNOSIS

pytestmark = [pytest.mark.unit, pytest.mark.api]


def recorded_shape():
    # Recorded GLM extra-key failure: the six canonical fields were valid, including this exact point.
    return {"page_summary": "简历详情上叠加客服机器人弹窗，覆盖详情中部区域",
        "goal_state": "not_satisfied", "obstruction": "present",
        "reason": "详情仍存在，目标未达成。可见客服弹窗关闭×位于(1060,178)，先收起弹窗再继续原操作。",
        "missing_evidence": ["关闭详情时实际点击落点与命中归属"],
        "proposed_action": {"kind": "dismiss", "target_label": "机器人弹窗关闭×按钮",
            "space": "page_image_px", "point": {"x": 1060, "y": 178},
            "target_box": {"x": 1048, "y": 166, "width": 24, "height": 24}},
        "missing_evidence_note": ""}


def canonical():
    return {k: v for k, v in recorded_shape().items() if k != "missing_evidence_note"}


def parse(raw):
    return visual.parse_diagnosis_v2(json.dumps(raw, ensure_ascii=False), 1261, 944,
                                     "page_image_px", ["dismiss_click"])


def full_size_request():
    body = payload_v2()
    image = io.BytesIO()
    Image.new("RGB", (1261, 944), "white").save(image, format="PNG")
    body.update(purpose="diagnose", available_actions=["dismiss_click"])
    body["observation"].update(image_base64=base64.b64encode(image.getvalue()).decode(),
                               width=1261, height=944)
    return body


def test_recorded_extra_empty_note_keeps_complete_assessment_and_exact_valid_click():
    diagnosis, action_error, _ = parse(recorded_shape())
    assert diagnosis == canonical()
    assert diagnosis["proposed_action"]["point"] == {"x": 1060, "y": 178}
    assert action_error is None


def test_unknown_nested_payload_cannot_inject_actions_or_survive_the_public_projection():
    raw = recorded_shape()
    raw["extra_model_payload"] = {"instruction": "MUST_NOT_EXECUTE", "diagnosis": {
        "goal_state": "satisfied", "proposed_action": {"kind": "press_escape"}},
        "billing": {"factor": 0}, "nested": [{"download": "MUST_NOT_EXECUTE"}]}
    diagnosis, action_error, _ = parse(raw)
    assert diagnosis == canonical()
    assert action_error is None
    assert "MUST_NOT_EXECUTE" not in json.dumps(diagnosis)
    assert "billing" not in diagnosis and "extra_model_payload" not in diagnosis


@pytest.mark.parametrize("mutate", [
    lambda a: a.update(kind="press_escape"),
    lambda a: a.update(extra_action_payload={"instruction": "MUST_NOT_EXECUTE"}),
    lambda a: a["point"].update(x="1060"),
    lambda a: a["point"].update(x=1262),
    lambda a: a.update(space="window_image_px"),
])
def test_invalid_known_action_is_removed_while_all_valid_assessment_fields_survive(mutate):
    raw = recorded_shape()
    mutate(raw["proposed_action"])
    diagnosis, action_error, _ = parse(raw)
    assert diagnosis == {**canonical(), "proposed_action": None}
    assert action_error == "INVALID_MODEL_ACTION"


@pytest.mark.parametrize("field", ["page_summary", "goal_state", "obstruction", "reason", "missing_evidence"])
def test_missing_known_assessment_field_still_rejects_even_when_extra_payload_has_a_replacement(field):
    raw = recorded_shape()
    raw["extra_model_payload"] = {field: raw.pop(field)}
    with pytest.raises(ValueError):
        parse(raw)


@pytest.mark.parametrize("field,value", [
    ("page_summary", {"text": "看似正确"}), ("goal_state", "maybe"),
    ("obstruction", "none"), ("reason", None), ("missing_evidence", "无"),
])
def test_wrong_known_assessment_values_are_not_repaired_by_discarding_unknown_keys(field, value):
    raw = recorded_shape()
    raw[field] = value
    with pytest.raises(ValueError):
        parse(raw)


def test_unknown_nested_action_is_not_promoted_when_canonical_action_is_missing():
    raw = recorded_shape()
    raw["extra_model_payload"] = {"proposed_action": raw.pop("proposed_action")}
    diagnosis, action_error, _ = parse(raw)
    assert diagnosis == {**canonical(), "proposed_action": None}
    assert action_error == "INVALID_MODEL_ACTION"


def test_api_extra_model_note_is_billed_once_at_twenty_and_same_id_returns_identical_receipt(api):
    body = full_size_request()
    api[2].return_value["content"] = json.dumps(recorded_shape(), ensure_ascii=False)
    result = post(api, body)
    assert result.status_code == 200
    response = result.json()
    assert response["diagnosis"] == canonical()
    assert response["diagnosis_error"] is None and response["action_error"] is None
    assert response["receipt"]["factor"] == 20
    assert response["receipt"]["credit_cost"] == "0.20"
    pristine = deepcopy(response)
    assert post(api, body).json() == pristine
    assert post(api, {"api_version": 2, "request_id": body["request_id"], "retrieve_only": True}).json() == pristine
    assert len(api[1]) == 1
    api[2].assert_awaited_once()


@pytest.mark.parametrize("location", ["top", "operation", "execution", "observation"])
def test_client_api_request_extra_keys_stay_strict_and_never_reach_provider(api, location):
    body = full_size_request()
    target = body if location == "top" else body[location]
    target["missing_evidence_note"] = ""
    assert post(api, body).status_code == 422
    assert not api[1]
    api[2].assert_not_awaited()


def test_v1_model_and_client_request_contracts_remain_strict(api):
    raw = {**deepcopy(DIAGNOSIS), "missing_evidence_note": ""}
    with pytest.raises(ValueError):
        visual.parse_diagnosis(json.dumps(raw), 12, 8)
    body = payload()
    body["missing_evidence_note"] = ""
    assert post(api, body).status_code == 422
    assert not api[1]
    api[2].assert_not_awaited()
