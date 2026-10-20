"""hongtao_kb_remove_volatile_fields.py 清理脚本纯函数单测（不经 DB）。

按文件路径加载脚本模块（脚本自包含、src.db.database 延迟导入，
纯函数部分在无 DB 环境可测）。
"""

import importlib.util
import json
from pathlib import Path

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "hongtao_kb_remove_volatile_fields.py"
)
_spec = importlib.util.spec_from_file_location("hongtao_kb_remove_volatile_fields", str(_SCRIPT_PATH))
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


def _metadata_text(**raw_extra) -> str:
    """构造一条含 raw_payload 的 metadata JSON（键序无关，写回按 sort_keys）。"""
    raw_payload = {"name": "欧典米灰", "pics": [], **raw_extra}
    return json.dumps(
        {"raw_payload": raw_payload, "trace": {"native_id": "574"}},
        ensure_ascii=False,
        sort_keys=True,
    )


def test_clean_removes_all_volatile_keys():
    """正常删：四键全删，其余键保留，写回序列化与仓库惯例一致。"""
    status, new_text, removed = script.clean_metadata_text(
        _metadata_text(stock="1000", sales="9", comment_score="5.0", comment_num="2")
    )
    assert status == "changed"
    assert removed == list(script.VOLATILE_KEYS)
    data = json.loads(new_text)
    raw_payload = data["raw_payload"]
    for key in script.VOLATILE_KEYS:
        assert key not in raw_payload
    assert raw_payload["name"] == "欧典米灰"  # 非目标键原样保留
    assert data["trace"] == {"native_id": "574"}  # raw_payload 外结构不动
    # 序列化惯例：ensure_ascii=False + sort_keys=True（与 service._dump_metadata 一致）
    assert new_text == json.dumps(data, ensure_ascii=False, sort_keys=True)


def test_clean_partial_keys_keeps_rest():
    """部分命中：只删存在的键，removed 按常量顺序。"""
    status, new_text, removed = script.clean_metadata_text(_metadata_text(sales="9"))
    assert status == "changed"
    assert removed == ["sales"]
    assert "sales" not in json.loads(new_text)["raw_payload"]


def test_clean_no_raw_payload():
    """无 raw_payload / 空 metadata：跳过不写。"""
    for text in (
        None,
        "",
        json.dumps({"name": "旧平铺结构"}, ensure_ascii=False),
        json.dumps({"raw_payload": "不是字典"}),
    ):
        status, new_text, removed = script.clean_metadata_text(text)
        assert status == "no_raw_payload"
        assert new_text is None and removed == []


def test_clean_bad_json():
    """坏 JSON：计数跳过不写。"""
    status, new_text, removed = script.clean_metadata_text("不是JSON{")
    assert status == "bad_json"
    assert new_text is None and removed == []


def test_clean_idempotent():
    """幂等：无目标键时 clean 零写，已清理文本再跑结果不变。"""
    status, new_text, removed = script.clean_metadata_text(_metadata_text())
    assert status == "clean"
    assert new_text is None and removed == []

    _, cleaned_once, _ = script.clean_metadata_text(
        _metadata_text(stock="1", comment_num="2")
    )
    status2, new_text2, removed2 = script.clean_metadata_text(cleaned_once)
    assert status2 == "clean" and new_text2 is None and removed2 == []
    assert cleaned_once == json.dumps(
        json.loads(cleaned_once), ensure_ascii=False, sort_keys=True
    )
