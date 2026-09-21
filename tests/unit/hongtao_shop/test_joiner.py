"""hongtao_shop joiner 单测：设计 §7 五组实测样例 + 边界。"""

from src.tenant_custom.hongtao_shop.joiner import (
    build_model_index,
    catalog_model,
    extract_full_tokens,
    extract_model_from_name,
    extract_truncated_tokens,
    match_post,
)


def _catalog():
    """目录样例：覆盖精确/后缀唯一/多命中三种形态（native_id 用设计样例）。"""
    return [
        {"native_id": "419", "name": "TPJ157042米克萨斯", "procode": ""},
        {"native_id": "422", "name": "TEZJ157004P金水流砂", "procode": ""},
        {"native_id": "430", "name": "TPG80357芭菲米白", "procode": ""},
        {"native_id": "431", "name": "TFZJ157004欧典米灰", "procode": ""},
        # 157004 系列共用者（设计：14 个系列，此处取 3 代表多命中）
        {"native_id": "432", "name": "TMB157004梦幻半山", "procode": ""},
        {"native_id": "433", "name": "TXL157004和沐莱姆石", "procode": ""},
        # procode 优先：name 无型号前缀但 procode 有值
        {"native_id": "434", "name": "编号:777 定制款", "procode": "TFZJ1890014"},
    ]


def test_extract_model_from_name():
    assert extract_model_from_name("TFZJ1890014欧典米灰") == "TFZJ1890014"
    assert extract_model_from_name("TPJ157042米克萨斯") == "TPJ157042"
    assert extract_model_from_name("TEZJ157004P金水流砂") == "TEZJ157004P"
    assert extract_model_from_name("编号:777 占位名") is None
    assert extract_model_from_name("") is None
    assert extract_model_from_name(None) is None


def test_catalog_model_procode_priority():
    assert catalog_model({"name": "编号:777 定制款", "procode": "TFZJ1890014"}) == "TFZJ1890014"
    assert catalog_model({"name": "TPJ157042米克萨斯", "procode": ""}) == "TPJ157042"
    assert catalog_model({"name": "无型号", "procode": ""}) is None


def test_token_extraction_patterns():
    assert extract_full_tokens("TPJ157042地面铺贴实景") == ["TPJ157042"]
    assert extract_full_tokens("tezj157004p实铺效果") == ["tezj157004p"]
    assert extract_full_tokens("厨房80357") == []
    assert extract_truncated_tokens("厨房80357") == ["80357"]
    assert extract_truncated_tokens("卫生间26048，26097") == ["26048", "26097"]
    # 完整 token 内的数字段被前视字母挡住，不重复提取
    assert extract_truncated_tokens("tezj157004p") == []
    # 裸数字带尾字母（157004p）按截短提取（走疑似路径）
    assert extract_truncated_tokens("编号157004p实铺") == ["157004"]
    # 7 位纯数字超出 {5,6} 不提取
    assert extract_truncated_tokens("1890014") == []


def test_sample_1_full_token_exact():
    """样例：帖 419 `TPJ157042地面铺贴实景` → 唯一挂 TPJ157042米克萨斯。"""
    result = match_post("TPJ157042地面铺贴实景", build_model_index(_catalog()))
    assert result.linked_native_ids == ["419"]
    assert result.evidence[0]["status"] == "exact"


def test_sample_2_full_token_case_insensitive():
    """样例：帖 422 `tezj157004p实铺效果` → 挂 TEZJ157004P金水流砂（大小写不敏感）。"""
    result = match_post("tezj157004p实铺效果", build_model_index(_catalog()))
    assert result.linked_native_ids == ["422"]


def test_sample_3_truncated_suffix_unique():
    """样例：`厨房80357` → 后缀唯一命中 TPG80357芭菲米白。"""
    result = match_post("厨房80357", build_model_index(_catalog()))
    assert result.linked_native_ids == ["430"]
    assert result.evidence[0]["status"] == "suffix_unique"


def test_sample_4_truncated_no_hit_ignored():
    """样例：`卫生间26048，26097` → 无命中忽略（宁缺勿误）。"""
    result = match_post("卫生间26048，26097", build_model_index(_catalog()))
    assert result.linked_native_ids == []
    assert result.evidence == []


def test_sample_5_truncated_ambiguous():
    """样例：`157004` 多命中记疑似不挂（match_evidence 存候选）。"""
    index = build_model_index(_catalog())
    result = match_post("157004", index)
    assert result.linked_native_ids == []
    assert len(result.evidence) == 1
    ev = result.evidence[0]
    assert ev["status"] == "ambiguous"
    assert sorted(ev["candidates"]) == ["422", "431", "432", "433"]


def test_exact_hit_suppresses_same_digits_ambiguity():
    """同内容既有完整命中又有裸数字段：数字段不再产生疑似噪声。"""
    content = "TEZJ157004P 金水流砂上墙，157004 同款可选"
    result = match_post(content, build_model_index(_catalog()))
    assert result.linked_native_ids == ["422"]
    assert all(ev["status"] == "exact" for ev in result.evidence)


def test_post_can_link_multiple_products():
    """一帖多 token 挂多产品。"""
    content = "客厅 TPJ157042，厨房80357"
    result = match_post(content, build_model_index(_catalog()))
    assert result.linked_native_ids == ["419", "430"]


def test_post_without_token_no_link():
    """无 token 的帖子不关联（v1）。"""
    result = match_post("分享一下铺贴心得，无型号", build_model_index(_catalog()))
    assert result.linked_native_ids == []
    assert match_post("", {}) .linked_native_ids == []
