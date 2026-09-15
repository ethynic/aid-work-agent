"""UUID driver values must survive the event snapshot JSON boundary."""
import json
from unittest.mock import MagicMock
import uuid

from psycopg2.extras import Json
from src.desktop_automation.events import find_eligible_subscriptions


def test_uuid_schedule_id_is_json_serializable_and_preserves_snapshot():
    identifier = uuid.uuid4()
    row = {"id": identifier, "task_ref": "task", "revision_ref": "revision",
           "scenario_key": "weixin.fixed_content.v1", "condition_ref": None, "delay_seconds": 7}
    cursor = MagicMock()
    cursor.fetchall.return_value = [row]
    snapshot = find_eligible_subscriptions(cursor, "tenant", {"source_ref": "source"}, "event")
    decoded = json.loads(Json(snapshot).dumps(snapshot))
    assert decoded == [{**row, "id": str(identifier)}]
    assert row["id"] == identifier
