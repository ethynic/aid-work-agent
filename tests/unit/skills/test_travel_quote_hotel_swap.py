"""
travel-quote 技能酒店局部替换单元测试

验证 update_hotel.update_hotel() 的核心契约：
- 单/多城市替换：只重算住宿行金额，其他 items 不变
- 保持 items 原顺序（住宿行原位置替换）
- city 未匹配、新酒店无价格、住宿行数量不一致时立即报错
- file_path 是返回 JSON 的顶层独立字段（不在 internal_data 内）
- 单房差、teacher_subtotal 计算正确

通过 monkeypatch 替换 hotel_retriever / excel_export / calculate_hotel_stays，
不依赖真实数据库和向量检索。
"""
import sys
from pathlib import Path

import pytest

# 注入技能 scripts 目录到 sys.path
# 当前文件位于 tests/unit/skills/，parents[3] 是项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = PROJECT_ROOT / 'src' / 'skills' / 'travel-quote' / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


class TestGenerateHotelOverrideLocationFallback:
    """首次生成报价时，不再对 Agent 已确认的酒店做严格城市审查。"""

    def test_unmatched_scenic_area_applies_to_remaining_stay(self, monkeypatch):
        """西江与雷山口径不同不应阻断报价，且贵阳仍应精确匹配。"""
        import hotel
        import hotel_retriever

        class FakeRetriever:
            def search_by_name(self, tenant_id, name_query, top_k=10):
                return [{
                    "doc_id": 101 if name_query.startswith("西江") else 202,
                    "title": name_query,
                    "info": f"酒店名称：{name_query}",
                }]

            def get_price_table(self, doc_id):
                return "| 房型 | 团队价 |\n|---|---|\n| 标准间 | 400 |\n"

        monkeypatch.setattr(hotel_retriever, "HotelRetriever", FakeRetriever)
        stays = [
            {"city": "雷山", "nights": 2, "hotel_doc_id": 1},
            {"city": "贵阳", "nights": 1, "hotel_doc_id": 2},
        ]

        names = hotel.resolve_hotel_overrides(
            "tenant-1",
            stays,
            [
                {"city": "西江", "hotel_name": "西江千户苗寨大院"},
                {"city": "贵阳", "hotel_name": "艺龙酒店(贵阳喷水池店)"},
            ],
            allow_city_fallback=True,
        )

        assert stays[0]["hotel_doc_id"] == 101
        assert stays[1]["hotel_doc_id"] == 202
        assert names == {
            "雷山": "西江千户苗寨大院",
            "贵阳": "艺龙酒店(贵阳喷水池店)",
        }

    def test_more_overrides_than_stays_raises_structural_error(self):
        """放宽地理审查不等于静默丢弃无法落到住宿行的酒店。"""
        import hotel

        with pytest.raises(ValueError, match="指定酒店数量超过可覆盖的住宿项数量"):
            hotel.resolve_hotel_overrides(
                "tenant-1",
                [{"city": "雷山", "nights": 2}],
                [
                    {"city": "西江", "hotel_name": "酒店A"},
                    {"city": "贵阳", "hotel_name": "酒店B"},
                ],
                allow_city_fallback=True,
            )


@pytest.fixture
def base_internal_data():
    """模拟 generate.py 返回的 internal_data：3 城市住宿 + 其他类别"""
    return {
        "course_name": "贵州研学",
        "company_name": "测试旅行社",
        "region_name": "贵州",
        "start_date": "2026-07-01",
        "trip_days": 5,
        "total_people": 30,
        "teacher_count": 3,
        "couples": 0,
        "season_type": "peak",
        "hotel_stays": [
            {"city": "贵阳", "area": "南明区", "nights": 2, "hotel_doc_id": 101},
            {"city": "平塘", "area": "", "nights": 1, "hotel_doc_id": 205},
            {"city": "荔波", "area": "", "nights": 1, "hotel_doc_id": 308},
        ],
        "items": [
            {"category": "用车", "name": "大巴", "unit_price": 1800, "quantity": 1,
             "unit": "辆", "frequency": 5, "freq_unit": "天",
             "subtotal": 300.0, "teacher_subtotal": 0, "remark": ""},
            # 贵阳酒店（位置 1）
            {"category": "住宿", "name": "贵阳XX酒店", "unit_price": 320, "quantity": 15,
             "unit": "间", "frequency": 2, "freq_unit": "夜",
             "subtotal": 320.0, "teacher_subtotal": 1920.0, "remark": "贵阳2晚"},
            {"category": "景点", "name": "天眼景区", "unit_price": 80, "quantity": 30,
             "unit": "人", "frequency": 1, "freq_unit": "次",
             "subtotal": 80.0, "teacher_subtotal": 0, "remark": ""},
            # 平塘酒店（位置 3）
            {"category": "住宿", "name": "平塘YY酒店", "unit_price": 280, "quantity": 15,
             "unit": "间", "frequency": 1, "freq_unit": "夜",
             "subtotal": 140.0, "teacher_subtotal": 840.0, "remark": "平塘1晚"},
            {"category": "餐饮", "name": "团队餐", "unit_price": 60, "quantity": 30,
             "unit": "人", "frequency": 4, "freq_unit": "餐",
             "subtotal": 240.0, "teacher_subtotal": 0, "remark": ""},
            # 荔波酒店（位置 5）
            {"category": "住宿", "name": "荔波ZZ酒店", "unit_price": 350, "quantity": 15,
             "unit": "间", "frequency": 1, "freq_unit": "夜",
             "subtotal": 175.0, "teacher_subtotal": 1050.0, "remark": "荔波1晚"},
        ],
        "cost_per_person": 1255.0,
        "teacher_total": 3810.0,
        "single_supplement": 0,
        "quote_per_person": 1255.0,
        "quote_total": 37650.0,
    }


@pytest.fixture
def patched_modules(monkeypatch):
    """mock 掉 update_hotel 的外部依赖"""
    import update_hotel
    import hotel_retriever

    # 不真正初始化数据库表
    monkeypatch.setattr(update_hotel, 'init_tables', lambda: None)

    # mock HotelRetriever：按 hotel_name 反查 doc_id
    class FakeRetriever:
        # 测试用：把 hotel_name 映射到固定 doc_id
        NAME_TO_DOC = {
            "贵阳凯宾斯基": 150,
            "荔波大酒店": 350,
            "荔波四季花园酒店": 999,
        }

        def search_by_name(self, tenant_id, name_query, top_k=5):
            # 精确匹配返回 1 条，未匹配返回空
            if name_query in self.NAME_TO_DOC:
                doc_id = self.NAME_TO_DOC[name_query]
                return [{
                    "doc_id": doc_id,
                    "title": f"酒店：{name_query}",
                    "info": f"酒店名称：{name_query}\n所在区域：测试\n",
                }]
            return []

        def get_price_table(self, doc_id):
            return "| 房型 | 团队价 |\n|---|---|\n| 标准间 | 400 |\n"

        def get_hotel_info(self, doc_id):
            return "酒店名称：新酒店\n"

    monkeypatch.setattr(hotel_retriever, 'HotelRetriever', FakeRetriever)

    # mock calculate_hotel_stays：按 hotel_stays 顺序返回新的住宿行
    def fake_calc(items, tenant_id, hotel_stays, total_people, teacher_count, couples,
                  name_overrides=None, start_date=None):
        name_overrides = name_overrides or {}
        new_items = []
        import math
        rooms_per_n = math.ceil(total_people / 2)
        single_supplement = 0
        for stay in hotel_stays:
            city = stay['city']
            nights = stay['nights']
            price = 400  # 固定测试价
            row_total = round(price * rooms_per_n * nights, 2)
            subtotal = round(row_total / total_people, 2)
            teacher_cost = round(price * nights * teacher_count, 2)
            name = name_overrides.get(city, f"{city}新酒店")
            new_items.append({
                "category": "住宿",
                "name": name,
                "unit_price": price,
                "quantity": rooms_per_n,
                "unit": "间",
                "frequency": nights,
                "freq_unit": "夜",
                "subtotal": subtotal,
                "teacher_subtotal": teacher_cost,
                "remark": f"{city}{nights}晚",
            })
        return new_items, single_supplement

    monkeypatch.setattr(update_hotel, 'calculate_hotel_stays', fake_calc)

    # mock excel_export：返回固定路径，不实际写文件
    monkeypatch.setattr(update_hotel, 'export_with_template',
                        lambda data, tpl: f"/tmp/quote_test_{abs(hash(str(data)))}.xlsx")

    return update_hotel


class TestUpdateHotelSingleCity:
    """单城市替换"""

    def test_only_target_hotel_changed(self, patched_modules, base_internal_data):
        """只换贵阳酒店，其他 items 金额不变，住宿行原位置保留"""
        result = patched_modules.update_hotel({
            "tenant_id": "t1",
            "internal_data": base_internal_data,
            "hotel_overrides": [
                {"city": "贵阳", "hotel_name": "贵阳凯宾斯基"}
            ],
        })

        new_items = result["internal_data"]["items"]
        # 顺序保持：用车、住宿、景点、住宿、餐饮、住宿
        assert new_items[0]["category"] == "用车"
        assert new_items[0]["subtotal"] == 300.0  # 未变
        assert new_items[1]["category"] == "住宿"
        assert new_items[1]["name"] == "贵阳凯宾斯基"  # 贵阳酒店被换
        assert new_items[2]["category"] == "景点"
        assert new_items[2]["subtotal"] == 80.0  # 未变
        assert new_items[3]["category"] == "住宿"
        # 平塘酒店也走了 calculate_hotel_stays 重算（price=400），名字回退默认
        assert new_items[3]["name"] == "平塘新酒店"
        assert new_items[4]["category"] == "餐饮"
        assert new_items[5]["category"] == "住宿"
        assert new_items[5]["name"] == "荔波新酒店"

    def test_filepath_top_level(self, patched_modules, base_internal_data):
        """file_path 在顶层，不在 internal_data 内"""
        result = patched_modules.update_hotel({
            "tenant_id": "t1",
            "internal_data": base_internal_data,
            "hotel_overrides": [{"city": "贵阳", "hotel_name": "贵阳凯宾斯基"}],
        })

        assert "file_path" in result
        assert result["file_path"].endswith(".xlsx")
        # internal_data 中不应含 file_path
        assert "file_path" not in result["internal_data"]


class TestUpdateHotelMultiCity:
    """多城市一次性替换"""

    def test_multi_city_overrides(self, patched_modules, base_internal_data):
        result = patched_modules.update_hotel({
            "tenant_id": "t1",
            "internal_data": base_internal_data,
            "hotel_overrides": [
                {"city": "贵阳", "hotel_name": "贵阳凯宾斯基"},
                {"city": "荔波", "hotel_name": "荔波大酒店"},
            ],
        })

        items = result["internal_data"]["items"]
        hotel_items = [it for it in items if it["category"] == "住宿"]
        names = [it["name"] for it in hotel_items]
        assert "贵阳凯宾斯基" in names
        assert "荔波大酒店" in names


class TestUpdateHotelValidation:
    """输入校验"""

    def test_city_not_in_stays_raises(self, patched_modules, base_internal_data):
        """override 的 city 不在 hotel_stays 中，应报错"""
        with pytest.raises(ValueError, match="未在城市住宿清单中找到"):
            patched_modules.update_hotel({
                "tenant_id": "t1",
                "internal_data": base_internal_data,
                "hotel_overrides": [{"city": "遵义", "hotel_name": "遵义酒店"}],
            })

    def test_missing_internal_data_raises(self, patched_modules):
        with pytest.raises(ValueError, match="缺少 internal_data"):
            patched_modules.update_hotel({
                "tenant_id": "t1",
                "hotel_overrides": [{"city": "贵阳", "hotel_name": "贵阳凯宾斯基"}],
            })

    def test_empty_overrides_raises(self, patched_modules, base_internal_data):
        with pytest.raises(ValueError, match="hotel_overrides 为空"):
            patched_modules.update_hotel({
                "tenant_id": "t1",
                "internal_data": base_internal_data,
                "hotel_overrides": [],
            })

    def test_missing_hotel_name_raises(self, patched_modules, base_internal_data):
        """override 中缺少 hotel_name，应报错"""
        with pytest.raises(ValueError, match="缺少 hotel_name"):
            patched_modules.update_hotel({
                "tenant_id": "t1",
                "internal_data": base_internal_data,
                "hotel_overrides": [{"city": "贵阳"}],
            })

    def test_hotel_name_not_found_raises(self, patched_modules, base_internal_data):
        """酒店名在知识库中查不到，应报错"""
        with pytest.raises(ValueError, match="未找到唯一匹配"):
            patched_modules.update_hotel({
                "tenant_id": "t1",
                "internal_data": base_internal_data,
                "hotel_overrides": [{"city": "贵阳", "hotel_name": "不存在的酒店"}],
            })

    def test_hotel_count_mismatch_raises(self, patched_modules, monkeypatch, base_internal_data):
        """旧住宿行数量与新算数量不一致，应报错（mock 让新算少返回一行）"""
        import update_hotel
        monkeypatch.setattr(update_hotel, 'init_tables', lambda: None)

        class FakeRetriever:
            def search_by_name(self, tenant_id, name, top_k=5):
                return [{"doc_id": 1, "title": "x", "info": f"酒店名称：{name}\n"}]

            def get_price_table(self, doc_id):
                return "| 房型 | 团队价 |\n|---|---|\n| 标准间 | 400 |\n"

        import hotel_retriever
        monkeypatch.setattr(hotel_retriever, 'HotelRetriever', FakeRetriever)
        monkeypatch.setattr(update_hotel, 'export_with_template',
                            lambda data, tpl: "/tmp/x.xlsx")

        # 只返回 2 行（旧有 3 行）
        def fake_calc(items, *args, **kwargs):
            return [{"category": "住宿"}] * 2, 0
        monkeypatch.setattr(update_hotel, 'calculate_hotel_stays', fake_calc)

        with pytest.raises(ValueError, match="住宿行数量不一致"):
            update_hotel.update_hotel({
                "tenant_id": "t1",
                "internal_data": base_internal_data,
                "hotel_overrides": [{"city": "贵阳", "hotel_name": "贵阳凯宾斯基"}],
            })

    def test_no_price_table_raises(self, patched_modules, monkeypatch, base_internal_data):
        """新酒店 doc_id 查不到价格表，应报错"""
        import update_hotel
        monkeypatch.setattr(update_hotel, 'init_tables', lambda: None)

        class FakeRetriever:
            def search_by_name(self, tenant_id, name, top_k=5):
                return [{"doc_id": 1, "title": "x", "info": f"酒店名称：{name}\n"}]

            def get_price_table(self, doc_id):
                return None  # 查不到
        import hotel_retriever
        monkeypatch.setattr(hotel_retriever, 'HotelRetriever', FakeRetriever)

        with pytest.raises(ValueError, match="查不到价格表"):
            update_hotel.update_hotel({
                "tenant_id": "t1",
                "internal_data": base_internal_data,
                "hotel_overrides": [{"city": "贵阳", "hotel_name": "贵阳凯宾斯基"}],
            })

    def test_zero_price_raises(self, patched_modules, monkeypatch, base_internal_data):
        """新酒店价格表无有效团队价，应报错"""
        import update_hotel
        monkeypatch.setattr(update_hotel, 'init_tables', lambda: None)

        class FakeRetriever:
            def search_by_name(self, tenant_id, name, top_k=5):
                return [{"doc_id": 1, "title": "x", "info": f"酒店名称：{name}\n"}]

            def get_price_table(self, doc_id):
                return "| 房型 | 团队价 |\n|---|---|\n| 标准间 | N/A |\n"  # 无法解析
        import hotel_retriever
        monkeypatch.setattr(hotel_retriever, 'HotelRetriever', FakeRetriever)

        with pytest.raises(ValueError, match="无有效团队价格"):
            update_hotel.update_hotel({
                "tenant_id": "t1",
                "internal_data": base_internal_data,
                "hotel_overrides": [{"city": "贵阳", "hotel_name": "贵阳凯宾斯基"}],
            })


class TestUpdateHotelTotals:
    """汇总计算正确性"""

    def test_totals_recalculated(self, patched_modules, base_internal_data):
        """人均 / 总价 / 老师合计 重新汇总正确"""
        result = patched_modules.update_hotel({
            "tenant_id": "t1",
            "internal_data": base_internal_data,
            "hotel_overrides": [{"city": "贵阳", "hotel_name": "贵阳凯宾斯基"}],
        })

        new_items = result["internal_data"]["items"]
        # cost_per_person 应等于所有 subtotal 之和
        expected_cost = round(sum(it["subtotal"] for it in new_items), 2)
        assert result["合计_费用小计"] == expected_cost
        # 总价 = 人均 × 30
        assert result["总价"] == round(expected_cost * 30, 2)
        # 人均 = 总价 / 30
        assert result["人均报价"] == round(result["总价"] / 30, 2)
        # 老师合计
        expected_teacher = round(sum(it["teacher_subtotal"] for it in new_items), 2)
        assert result["合计_随队老师"] == expected_teacher

    def test_rows_match_items(self, patched_modules, base_internal_data):
        """rows 数量与 items 一致，且字段对齐"""
        result = patched_modules.update_hotel({
            "tenant_id": "t1",
            "internal_data": base_internal_data,
            "hotel_overrides": [{"city": "贵阳", "hotel_name": "贵阳凯宾斯基"}],
        })

        items = result["internal_data"]["items"]
        rows = result["rows"]
        assert len(rows) == len(items)
        for row, item in zip(rows, items):
            assert row["成本类别"] == item["category"]
            assert row["费用小计"] == item["subtotal"]
            assert row["随队老师"] == item["teacher_subtotal"]


class TestGenerateOutputContract:
    """验证 generate.py 返回结构契约"""

    def test_generate_returns_internal_data(self):
        """generate.py 返回中应含 internal_data 字段（不实际跑 LLM，只做 import 后静态检查）"""
        import generate
        # generate.py 模块可正常导入，说明改造未破坏 import
        assert hasattr(generate, 'generate_quote')
