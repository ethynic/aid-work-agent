"""筛选模板真实清洗及 CRUD 参数化边界；数据库全 mock，禁止真实库连接。"""
from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from src.services import recruiting_job_service as service

pytestmark = pytest.mark.unit
JOB_ID = "11278c92-1c62-4aed-a032-a58fe29c79d2"


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    connection = MagicMock()
    connection.__enter__.return_value = connection
    get_connection = MagicMock(return_value=connection)
    monkeypatch.setattr(service, "get_db_connection", get_connection)
    return connection


def full_requirements():
    return {
        "experiences": ["在校/应届", "26年毕业", "1年以内"],
        "educations": ["高中", "本科"],
        "job_statuses": ["离职-随时到岗", "在职-考虑机会"],
        "salary_range": {"min": 15, "max": 25.5},
        "vip_filters": {
            "age": {"min": 25, "max": None}, "activity": "3日内活跃", "gender": "男",
            "not_viewed": "近14天没有", "not_exchanged": "近一个月没有",
            "schools": ["985", "QS 100"], "first_degree_only": True,
            "job_hopping": "5年少于3份", "boss_keywords": ["HTML", "数据库设计"],
        },
        "keywords": ["Linux"], "notes": "测试要求",
    }


def test_full_template_is_cleaned_without_mutation():
    original = full_requirements()
    original["vip_filters"]["schools"] += ["985"]
    before = deepcopy(original)
    cleaned = service._validate_job_requirements(original)
    assert cleaned == full_requirements()
    assert original == before


@pytest.mark.parametrize("requirements", [
    {"experience": "3-5年", "salary": "15-25K", "educations": ["本科"]},
    {"salary_range": {"min": 20}}, {"salary_range": {"max": 50}},
    {"vip_filters": {"age": {"max": 45}}},
    {"vip_filters": {"age": {"min": 16, "max": 45}}},
])
def test_legacy_and_one_sided_ranges_are_supported(requirements):
    assert service._validate_job_requirements(requirements) == requirements


@pytest.mark.parametrize("requirements", [
    {"salary_range": {}}, {"salary_range": {"min": None}},
    {"salary_range": {"min": True}}, {"salary_range": {"min": "15"}},
    {"salary_range": {"max": float("inf")}}, {"salary_range": {"max": float("nan")}},
    {"salary_range": {"min": 10 ** 400}},
    {"salary_range": {"min": -1}}, {"salary_range": {"min": 0}},
    {"salary_range": {"min": 25, "max": 15}}, {"salary_range": {"foo": 15}},
    {"vip_filters": {"age": {"min": 15}}}, {"vip_filters": {"age": {"max": 46}}},
    {"vip_filters": {"age": {"min": 25.5}}}, {"vip_filters": {"age": {"min": False}}},
    {"vip_filters": {"age": {"max": None}}}, {"vip_filters": {"age": {"min": 40, "max": 20}}},
    {"vip_filters": {"first_degree_only": True}},
    {"vip_filters": {"schools": ["不限"], "first_degree_only": True}},
    {"vip_filters": {"first_degree_only": "true"}},
    {"vip_filters": {"schools": ["985", "不限"]}}, {"vip_filters": {"schools": [985]}},
    {"vip_filters": {"schools": ["其他"]}}, {"vip_filters": {"boss_keywords": ["Linux"]}},
    {"vip_filters": {"gender": ["男"]}}, {"vip_filters": {"gender": "男性"}},
    {"vip_filters": {"major": "计算机"}}, {"vip_filters": []},
    {"experiences": "3-5年"}, {"experiences": ["不限", "1年以内"]},
    {"job_statuses": ["马上到岗"]},
    {"experience": "3-5年", "experiences": ["1年以内"]},
    {"experience": "3-5年", "experiences": []},
    {"salary": "15-25K", "salary_range": {"min": 15, "max": 25}},
])
def test_invalid_template_is_rejected_before_db_access(requirements, no_database):
    with pytest.raises(service.JobServiceError):
        service.create_job("tenant-a", job_name="职位", job_requirements=requirements)
    no_database.cursor.assert_not_called()


def test_unlimited_and_equivalent_legacy_experience_are_normalized():
    assert service._validate_job_requirements({
        "experience": "1年以内", "experiences": ["1年以内", "1年以内"],
        "vip_filters": {"gender": "不限", "schools": [], "first_degree_only": False},
    }) == {"experiences": ["1年以内"]}
    assert service._validate_job_requirements({"experiences": ["不限"], "vip_filters": {"activity": " "}}) == {}
    assert service._validate_job_requirements({"keywords": ["不限"]}) == {"keywords": ["不限"]}


def test_create_stores_and_returns_vip_json(no_database):
    cursor = no_database.cursor.return_value
    row = {"id": JOB_ID, "tenant_id": "tenant-a", "job_name": "职位", "job_requirements": full_requirements()}
    cursor.fetchone.side_effect = [None, row]
    result = service.create_job("tenant-a", job_name="职位", job_requirements=full_requirements())
    insert_args = cursor.execute.call_args_list[1].args
    assert insert_args[1][0] == "tenant-a"
    assert insert_args[1][-1].adapted == full_requirements()
    assert result["job_requirements"] == full_requirements()
    assert result["script_count"] == 0
    no_database.commit.assert_called_once()


def test_update_preserves_tenant_boundary_and_empty_template_clears(no_database):
    cursor = no_database.cursor.return_value
    cursor.fetchone.return_value = {"id": JOB_ID, "job_requirements": None}
    cursor.fetchall.return_value = []
    result = service.update_job("tenant-a", JOB_ID, job_requirements={})
    sql, params = cursor.execute.call_args_list[0].args
    assert "WHERE id = %s AND tenant_id = %s" in sql
    assert params == [None, JOB_ID, "tenant-a"]
    assert result["job_requirements"] is None


def test_update_other_tenant_returns_none(no_database):
    cursor = no_database.cursor.return_value
    cursor.fetchone.return_value = None
    assert service.update_job("tenant-b", JOB_ID, job_requirements=full_requirements()) is None
    assert cursor.execute.call_args.args[1][-2:] == [JOB_ID, "tenant-b"]
    no_database.commit.assert_not_called()


@pytest.mark.asyncio
async def test_api_uses_real_service_validation_and_candidates(monkeypatch, no_database):
    from src.api import recruiting_operator as api
    monkeypatch.setattr(api, "_require_tenant", lambda: "tenant-a")
    invalid = api.CreateJobRequest(job_name="职位", job_requirements={"vip_filters": {"age": {"min": 99}}})
    response = await api.create_job(invalid, None)
    assert response.status_code == 400
    assert "16-45" in response.body.decode()
    no_database.cursor.assert_not_called()
    options = (await api.get_requirement_options(None))["data"]
    assert "26年毕业" in options["experiences"]
    assert "初中及以下" in options["educations"]
    assert options["vip_filters"]["schools"][-1] == "QS 500"


@pytest.mark.asyncio
async def test_api_create_and_detail_preserve_full_template(monkeypatch, no_database):
    from src.api import recruiting_operator as api
    monkeypatch.setattr(api, "_require_tenant", lambda: "tenant-a")
    cursor = no_database.cursor.return_value
    row = {"id": JOB_ID, "tenant_id": "tenant-a", "job_name": "职位", "job_requirements": full_requirements()}
    cursor.fetchone.side_effect = [None, row, row]
    cursor.fetchall.return_value = []
    created = await api.create_job(api.CreateJobRequest(job_name="职位", job_requirements=full_requirements()), None)
    loaded = await api.get_job(JOB_ID, None)
    assert created["data"]["job_requirements"] == loaded["data"]["job_requirements"] == full_requirements()
    assert cursor.execute.call_args_list[1].args[1][-1].adapted == full_requirements()
    assert cursor.execute.call_args_list[2].args[1] == (JOB_ID, "tenant-a")
