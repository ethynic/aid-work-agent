"""travel-quote 车型组合与公里计价单元测试。"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = PROJECT_ROOT / "src" / "skills" / "travel-quote" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import vehicle


VEHICLES = [
    {
        "vehicle_type": "bus_55",
        "vehicle_type_label": "55座大巴",
        "seats_max": 55,
        "daily_rate": None,
        "per_km_rate": 5,
    },
    {
        "vehicle_type": "bus_45",
        "vehicle_type_label": "45座大巴",
        "seats_max": 45,
        "daily_rate": None,
        "per_km_rate": 4,
    },
]


def test_recommend_vehicle_uses_per_km_rate_for_large_group():
    combo = vehicle.recommend_vehicle(150, VEHICLES, "per_km_rate")

    assert [(item["vehicle"]["seats_max"], item["count"]) for item in combo] == [
        (55, 2),
        (45, 1),
    ]


def test_recommend_vehicle_prioritizes_fewer_vehicles_over_rate():
    vehicles = [
        {"vehicle_type": "large", "seats_max": 100, "daily_rate": 1000},
        {"vehicle_type": "small", "seats_max": 40, "daily_rate": 1},
    ]

    combo = vehicle.recommend_vehicle(100, vehicles)

    assert [(item["vehicle"]["vehicle_type"], item["count"]) for item in combo] == [
        ("large", 1),
    ]


def test_per_km_cost_sums_each_vehicle_type():
    items, vehicle_count = vehicle._calculate_per_km_cost(
        [], VEHICLES, total_people=150, distance_km=100
    )

    assert vehicle_count == 3
    assert [(item["name"], item["quantity"], item["unit_price"]) for item in items] == [
        ("55座大巴", 2, 500.0),
        ("45座大巴", 1, 400.0),
    ]
    assert sum(item["subtotal"] for item in items) == pytest.approx(1400 / 150, abs=0.01)
