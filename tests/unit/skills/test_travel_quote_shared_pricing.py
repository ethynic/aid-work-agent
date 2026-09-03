"""travel-quote 5 张关系型定价表跨租户共享检索单元测试。

复现修复前 bug：车辆/餐饮/导游/费用/季节 5 张定价表读取只按本租户
tenant_id=%s 过滤，租户 B 接入租户 A 共享知识库后查不到 A 的定价表，
报价缺"用车/用餐/导游/其他"费用项。

修复方案（方案 A）：聚合该 subagent 全部共享来源租户（load_shared_ranges
不传 source_type），SQL 用 tenant_id = ANY(%s)。
"""
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = PROJECT_ROOT / 'src' / 'skills' / 'travel-quote' / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import db  # noqa: E402
import vehicle  # noqa: E402
import meal  # noqa: E402
import guide  # noqa: E402
import other_fees  # noqa: E402
import season  # noqa: E402
import generate  # noqa: E402


class FakeConn:
    """模拟 DB 连接上下文：记录最后一次 execute 的 SQL/参数，返回预设行"""

    def __init__(self, rows=None):
        self.last_sql = None
        self.last_args = None
        self._rows = rows or []

    def execute(self, sql, args=None):
        self.last_sql = sql
        self.last_args = args
        return None

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_shared(*owners):
    """patch load_shared_ranges 返回给定来源租户（不限定 source_type，模拟方案 A 聚合）"""
    return patch('src.knowledge.retriever.tenant_range.load_shared_ranges',
                 return_value=[(o, 'any') for o in owners])


class TestSharedTenantIds:
    """db.shared_tenant_ids 聚合逻辑"""

    def test_with_subagent_returns_own_plus_shared(self):
        with _patch_shared('tenant_A', 'tenant_C'):
            ids = db.shared_tenant_ids('tenant_B', 'travel-consultant')
        assert ids == ['tenant_B', 'tenant_A', 'tenant_C']

    def test_without_subagent_keeps_own_only(self):
        with _patch_shared('tenant_A'):
            ids = db.shared_tenant_ids('tenant_B')
        assert ids == ['tenant_B']

    def test_load_failure_falls_back_to_own(self):
        with patch('src.knowledge.retriever.tenant_range.load_shared_ranges',
                   side_effect=RuntimeError('db down')):
            ids = db.shared_tenant_ids('tenant_B', 'travel-consultant')
        assert ids == ['tenant_B']

    def test_shared_tenants_dedup(self):
        with _patch_shared('tenant_B', 'tenant_A'):
            ids = db.shared_tenant_ids('tenant_B', 'travel-consultant')
        assert ids == ['tenant_B', 'tenant_A']


class TestQueryByRegion:
    """db.query_by_region 租户范围 + region_name 空字符串兜底"""

    def test_with_subagent_uses_any_with_shared_tenants(self):
        fake = FakeConn(rows=[{'region_name': '贵阳', 'id': 1}])
        with patch.object(db, 'get_db', return_value=fake), \
             _patch_shared('tenant_A'):
            db.query_by_region('bs_travel_quote_vehicles', 'tenant_B',
                               ['贵阳'], subagent_id='travel-consultant')

        assert 'tenant_id = ANY(%s)' in fake.last_sql
        assert fake.last_args[0] == ['tenant_B', 'tenant_A']
        assert 'ANY(' in fake.last_sql

    def test_without_subagent_keeps_own_tenant_only(self):
        fake = FakeConn(rows=[{'region_name': '贵阳', 'id': 1}])
        with patch.object(db, 'get_db', return_value=fake), \
             _patch_shared('tenant_A'):
            db.query_by_region('bs_travel_quote_vehicles', 'tenant_B', ['贵阳'])

        assert 'tenant_id = %s' in fake.last_sql
        assert 'ANY(' not in fake.last_sql
        assert fake.last_args[0] == 'tenant_B'

    def test_fallback_sql_has_empty_region_bucket(self):
        """无区域匹配走 fallback 时，region_name 空字符串兜底生效"""
        fake = FakeConn(rows=[])
        with patch.object(db, 'get_db', return_value=fake), \
             _patch_shared('tenant_A'):
            db.query_by_region('bs_travel_quote_vehicles', 'tenant_B',
                               ['贵阳'], subagent_id='travel-consultant')

        # 区域分支无结果 → 落入 fallback SQL
        assert 'tenant_id = ANY(%s)' in fake.last_sql
        assert "(region_name IS NULL OR region_name = '')" in fake.last_sql
        assert fake.last_args[0] == ['tenant_B', 'tenant_A']

    def test_region_branch_has_empty_region_bucket(self):
        fake = FakeConn(rows=[{'region_name': '贵阳', 'id': 1}])
        with patch.object(db, 'get_db', return_value=fake), \
             _patch_shared('tenant_A'):
            db.query_by_region('bs_travel_quote_vehicles', 'tenant_B',
                               ['贵阳'], subagent_id='travel-consultant')

        assert 'region_name IS NULL OR region_name = ' in fake.last_sql
        assert "region_name IN" in fake.last_sql

    def test_parameter_order_tenant_first(self):
        """区域 + 季节：参数顺序 tenant → season → region"""
        fake = FakeConn(rows=[{'region_name': '贵阳', 'id': 1}])
        with patch.object(db, 'get_db', return_value=fake), \
             _patch_shared('tenant_A'):
            db.query_by_region('bs_travel_quote_vehicles', 'tenant_B',
                               ['贵阳', '遵义'], season_type='peak',
                               subagent_id='travel-consultant')

        assert fake.last_args == (['tenant_B', 'tenant_A'], 'peak', '贵阳', '遵义')


class TestVehicleCost:
    """vehicle.calculate_vehicle_cost 透传 subagent_id"""

    def test_passes_subagent_id_to_query_by_region(self):
        with patch.object(vehicle, 'query_by_region', return_value=[]) as m_q:
            items, count = vehicle.calculate_vehicle_cost(
                [], 'tenant_B', ['贵阳'], 30, 3, 'default', None,
                subagent_id='travel-consultant')

        assert count == 0
        assert items == []
        m_q.assert_called_once()
        assert m_q.call_args.args[:2] == ('bs_travel_quote_vehicles', 'tenant_B')
        assert m_q.call_args.kwargs.get('subagent_id') == 'travel-consultant'

    def test_without_subagent_keeps_original_call(self):
        with patch.object(vehicle, 'query_by_region', return_value=[]) as m_q:
            vehicle.calculate_vehicle_cost([], 'tenant_B', ['贵阳'], 30, 3,
                                           'default', None)
        assert m_q.call_args.kwargs.get('subagent_id') == ''


class TestMealCost:
    """meal.calculate_meal_cost 接入共享 + region_name 空字符串兜底"""

    def test_with_subagent_uses_any(self):
        fake = FakeConn()
        with patch.object(meal, 'get_db', return_value=fake), \
             _patch_shared('tenant_A'):
            meal.calculate_meal_cost([], 'tenant_B', ['贵阳'], 30, 3,
                                     'standard', 'default',
                                     subagent_id='travel-consultant')

        assert 'tenant_id = ANY(%s)' in fake.last_sql
        assert fake.last_args[0] == ['tenant_B', 'tenant_A']
        # 参数顺序：tenant → meal_tier → season_type → region
        assert fake.last_args[1] == 'standard'
        assert fake.last_args[2] == 'default'
        assert fake.last_args[3] == '贵阳'
        assert "region_name IS NULL OR region_name = ''" in fake.last_sql
        # 空字符串行与 NULL 行同组排最后，保证精确区域行优先（meal_by_type 取首行）
        assert "ORDER BY (region_name IS NULL OR region_name = '')" in fake.last_sql

    def test_without_subagent_keeps_own_tenant_only(self):
        fake = FakeConn()
        with patch.object(meal, 'get_db', return_value=fake), \
             _patch_shared('tenant_A'):
            meal.calculate_meal_cost([], 'tenant_B', [], 30, 3,
                                     'standard', 'default')

        assert 'tenant_id = %s' in fake.last_sql
        assert 'ANY(' not in fake.last_sql
        assert fake.last_args[0] == 'tenant_B'


class TestGuideCost:
    """guide.calculate_guide_cost 接入共享 + region_name 空字符串兜底"""

    def test_with_subagent_uses_any(self):
        fake = FakeConn()
        with patch.object(guide, 'get_db', return_value=fake), \
             _patch_shared('tenant_A'):
            guide.calculate_guide_cost([], 'tenant_B', ['贵阳'], 'local', 3,
                                       'default', total_people=30,
                                       subagent_id='travel-consultant')

        assert 'tenant_id = ANY(%s)' in fake.last_sql
        assert fake.last_args[0] == ['tenant_B', 'tenant_A']
        assert fake.last_args[1] == 'local'
        assert "region_name IS NULL OR region_name = ''" in fake.last_sql
        # 空字符串行与 NULL 行同组排最后，保证精确区域行优先（fetchone 取第一行）
        assert "ORDER BY (region_name IS NULL OR region_name = '')" in fake.last_sql

    def test_without_subagent_keeps_own_tenant_only(self):
        fake = FakeConn()
        with patch.object(guide, 'get_db', return_value=fake), \
             _patch_shared('tenant_A'):
            guide.calculate_guide_cost([], 'tenant_B', [], 'local', 3, 'default')

        assert 'tenant_id = %s' in fake.last_sql
        assert 'ANY(' not in fake.last_sql
        assert fake.last_args[0] == 'tenant_B'


class TestOtherFees:
    """other_fees.calculate_other_fees 接入共享（无 region 过滤）"""

    def test_with_subagent_uses_any(self):
        fake = FakeConn()
        with patch.object(other_fees, 'get_db', return_value=fake), \
             _patch_shared('tenant_A'):
            other_fees.calculate_other_fees([], 'tenant_B', 30, 3, 1, True,
                                            subagent_id='travel-consultant')

        assert 'tenant_id = ANY(%s)' in fake.last_sql
        assert fake.last_args[0] == ['tenant_B', 'tenant_A']

    def test_without_subagent_keeps_own_tenant_only(self):
        fake = FakeConn()
        with patch.object(other_fees, 'get_db', return_value=fake), \
             _patch_shared('tenant_A'):
            other_fees.calculate_other_fees([], 'tenant_B', 30, 3, 1, True)

        assert 'tenant_id = %s' in fake.last_sql
        assert 'ANY(' not in fake.last_sql
        assert fake.last_args[0] == 'tenant_B'


class TestSeason:
    """season.determine_season 接入共享"""

    def test_with_subagent_uses_any(self):
        fake = FakeConn(rows=[{'season_type': 'peak', 'price_multiplier': Decimal('1.3')}])
        with patch.object(season, 'get_db', return_value=fake), \
             _patch_shared('tenant_A'):
            result = season.determine_season('tenant_B', '2026-08-01',
                                             subagent_id='travel-consultant')

        assert 'tenant_id = ANY(%s)' in fake.last_sql
        assert fake.last_args[0] == ['tenant_B', 'tenant_A']
        assert fake.last_args[1] == date(2026, 8, 1)
        assert fake.last_args[2] == date(2026, 8, 1)
        assert result[0] == 'peak'

    def test_without_subagent_keeps_own_tenant_only(self):
        fake = FakeConn()
        with patch.object(season, 'get_db', return_value=fake), \
             _patch_shared('tenant_A'):
            season.determine_season('tenant_B', '2026-08-01')

        assert 'tenant_id = %s' in fake.last_sql
        assert 'ANY(' not in fake.last_sql
        assert fake.last_args[0] == 'tenant_B'


class TestGenerateQuoteSubagentId:
    """generate.generate_quote 必须把 subagent_id 透传给 5 个定价计算入口"""

    def test_generate_quote_passes_subagent_id(self):
        params = {
            'tenant_id': 'tenant_B',
            'subagent_id': 'travel-sub',
            'region_name': '贵阳',
            'start_date': '2026-08-01',
            'total_people': 30,
            'adults': 28,
            'students': 0,
            'teacher_count': 2,
            'trip_days': 3,
            'meal_tier': 'standard',
            'guide_type': 'local',
            'vehicle_count': 1,
            'include_insurance': True,
            'course_name': '测试课程',
        }
        with patch.object(generate, 'init_tables'), \
             patch.object(generate, 'determine_season',
                          return_value=('default', Decimal('1.00'))) as m_season, \
             patch.object(generate, 'calculate_vehicle_cost',
                          return_value=([], 1)) as m_vehicle, \
             patch.object(generate, 'calculate_attraction_cost',
                          side_effect=lambda items, *a, **k: items), \
             patch.object(generate, 'calculate_hotel_cost',
                          return_value=([], 0)), \
             patch.object(generate, 'calculate_meal_cost',
                          side_effect=lambda items, *a, **k: items) as m_meal, \
             patch.object(generate, 'calculate_guide_cost',
                          side_effect=lambda items, *a, **k: items) as m_guide, \
             patch.object(generate, 'calculate_other_fees',
                          side_effect=lambda items, *a, **k: items) as m_other, \
             patch.object(generate, 'export_with_template',
                          return_value='/tmp/quote.xlsx'):
            result = generate.generate_quote(params)

        # determine_season 按位置传 subagent_id
        assert m_season.call_args.args == ('tenant_B', '2026-08-01', 'travel-sub')
        # 其余计算函数用关键字传 subagent_id
        assert m_vehicle.call_args.kwargs.get('subagent_id') == 'travel-sub'
        assert m_meal.call_args.kwargs.get('subagent_id') == 'travel-sub'
        assert m_guide.call_args.kwargs.get('subagent_id') == 'travel-sub'
        assert m_other.call_args.kwargs.get('subagent_id') == 'travel-sub'
        # 实现 9dcc9bc8 起 file_path 经 os.path.abspath（Windows 下会补盘符，Linux 原样返回）
        assert result['file_path'] == os.path.abspath('/tmp/quote.xlsx')
