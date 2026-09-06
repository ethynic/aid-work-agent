"""用户行为审计日志单元测试

覆盖 src/services/behavior_log.py 核心逻辑与 deploy/db_update.yaml 增量格式：
- record_behavior 成功 / 写入失败不影响业务（mock DB 异常）
- record_behavior_sync 失败不影响业务
- detail 敏感字段递归过滤
- 设备解析（安卓微信 UA 先判内嵌、鸿蒙、桌面浏览器、iOS、未知）
- token_id 计算（SHA256 前 8 位，不存原文）
- X-Forwarded-For 首段取 IP
- audit_action 装饰器成功 / 异常 re-raise
- audit_action 装饰器增强（Phase 2）：_find_request 不误拿名为 request 的 Pydantic body、
  name_arg 从任意名字的 body 参数提取、同步 def 路由经 to_thread 执行、
  业务级 {"success": False} 返回记失败、UPDATE 自动记变更字段名列表
- error_msg 截断 500 字符
- 渠道事件字段语义（entry=channel、无 UA）
- db_update.yaml 新条目格式合法（datetime 唯一且严格递增、SQL 幂等）

全部 mock DB，不连真实数据库。
"""

import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml
from pydantic import BaseModel

from src.services.behavior_log import (
    ERROR_MSG_MAX_LEN,
    DEVICE_VOCABULARY,
    audit_action,
    parse_device,
    record_behavior,
    record_behavior_sync,
    sanitize_detail,
)
from src.saas.models.enums import (
    BehaviorAction,
    BehaviorDeviceType,
    BehaviorEntry,
    BehaviorResourceType,
)


def _fake_request(
    headers: dict | None = None,
    tenant_id: str | None = "tenant_1",
    user_id: str | None = "user_1",
    user_role: str | None = None,
    client_host: str | None = "10.0.0.9",
) -> SimpleNamespace:
    """构造行为日志所需的 request 形状（Starlette Request 的鸭子类型替身）"""
    return SimpleNamespace(
        headers=headers or {},
        state=SimpleNamespace(tenant_id=tenant_id, user_id=user_id, user_role=user_role),
        client=SimpleNamespace(host=client_host) if client_host else None,
    )


# ============== record_behavior 成功路径 ==============

@pytest.mark.asyncio
async def test_record_behavior_success_builds_row_from_request_state():
    """tenant_id/user_id 从 request.state 读取，IP 取 X-Forwarded-For 首段"""
    req = _fake_request(headers={
        "x-forwarded-for": "203.0.113.7, 10.0.0.1",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0",
        "authorization": "Bearer abc123",
    })
    with patch("src.services.behavior_log._insert_sync") as mock_insert:
        await record_behavior(req, BehaviorAction.LOGIN, login_method="password")

    mock_insert.assert_called_once()
    row = mock_insert.call_args[0][0]
    assert row["tenant_id"] == "tenant_1"
    assert row["user_id"] == "user_1"
    assert row["action"] == "login"
    assert row["client_ip"] == "203.0.113.7"          # XFF 首段
    assert row["entry"] == BehaviorEntry.WEB.value    # 有 request 默认 web
    assert row["login_method"] == "password"
    assert row["device_info"] == "windows_pc"         # 设备解析冻结
    assert row["device_type"] == BehaviorDeviceType.PC.value
    assert row["success"] is True


@pytest.mark.asyncio
async def test_record_behavior_explicit_override_wins():
    """显式传参优先于 request.state（登录失败时 user_id 由调用方显式传）"""
    req = _fake_request(tenant_id="state_tenant", user_id="state_user")
    with patch("src.services.behavior_log._insert_sync") as mock_insert:
        await record_behavior(
            req, BehaviorAction.LOGIN_FAILED, success=False,
            user_id="explicit_user", tenant_id="explicit_tenant",
            user_role="tenant_admin", error_msg="手机号或密码有误",
            detail={"identifier": "13800000000"},
        )

    row = mock_insert.call_args[0][0]
    assert row["tenant_id"] == "explicit_tenant"
    assert row["user_id"] == "explicit_user"
    assert row["user_role"] == "tenant_admin"
    assert row["success"] is False
    assert row["error_msg"] == "手机号或密码有误"
    assert "identifier" in row["detail"]


@pytest.mark.asyncio
async def test_record_behavior_db_failure_never_raises():
    """写入失败只记日志，绝不抛出影响业务主流程"""
    req = _fake_request()
    with patch("src.services.behavior_log._insert_sync", side_effect=RuntimeError("db down")):
        # 不应抛异常
        await record_behavior(req, BehaviorAction.LOGOUT)


def test_record_behavior_sync_db_failure_never_raises():
    """同步版本写入失败同样只记日志"""
    req = _fake_request()
    with patch("src.services.behavior_log._insert_sync", side_effect=RuntimeError("db down")):
        record_behavior_sync(req, BehaviorAction.LOGOUT)


@pytest.mark.asyncio
async def test_record_behavior_error_msg_truncated():
    """error_msg 截断 500 字符"""
    req = _fake_request()
    long_msg = "x" * (ERROR_MSG_MAX_LEN + 100)
    with patch("src.services.behavior_log._insert_sync") as mock_insert:
        await record_behavior(req, BehaviorAction.LOGIN_FAILED, success=False, error_msg=long_msg)

    row = mock_insert.call_args[0][0]
    assert len(row["error_msg"]) == ERROR_MSG_MAX_LEN


# ============== 渠道事件字段语义（设计文档 §5.6）==============

@pytest.mark.asyncio
async def test_record_behavior_channel_event_semantics():
    """渠道事件：entry=channel、无浏览器上下文（UA/设备为空/unknown）、token_id 为空"""
    with patch("src.services.behavior_log._insert_sync") as mock_insert:
        await record_behavior(
            None, BehaviorAction.CHANNEL_BIND, entry=BehaviorEntry.CHANNEL.value,
            channel="wecom_personal_rpa", channel_user_id="external_userid_1",
            tenant_id="tenant_1", user_id="user_1",
        )

    row = mock_insert.call_args[0][0]
    assert row["entry"] == BehaviorEntry.CHANNEL.value
    assert row["channel"] == "wecom_personal_rpa"
    assert row["channel_user_id"] == "external_userid_1"
    assert row["user_agent"] is None
    assert row["device_info"] == "unknown"
    assert row["device_type"] == BehaviorDeviceType.UNKNOWN.value
    assert row["token_id"] is None
    assert row["client_ip"] is None


# ============== detail 敏感字段过滤 ==============

def test_sanitize_detail_filters_sensitive_keys_recursively():
    """递归过滤 password/token/api_key/secret/code 等敏感键（不修改原 dict）"""
    original = {
        "password": "plain123",
        "New_Password": "abc",           # 大小写不敏感
        "nested": {"api_key": "sk-xxx", "name": "ok", "deep": [{"code": "8866", "keep": 1}]},
        "token": "jwt...",
    }
    result = sanitize_detail(original)
    assert result["password"] == "***"
    assert result["New_Password"] == "***"
    assert result["token"] == "***"
    assert result["nested"]["api_key"] == "***"
    assert result["nested"]["name"] == "ok"
    assert result["nested"]["deep"][0]["code"] == "***"
    assert result["nested"]["deep"][0]["keep"] == 1
    # 原 dict 不被修改
    assert original["password"] == "plain123"
    assert original["nested"]["api_key"] == "sk-xxx"


def test_sanitize_detail_empty_or_none():
    assert sanitize_detail(None) == {}
    assert sanitize_detail({}) == {}


# ============== 设备解析（先判内嵌，再判 OS）==============

def test_parse_device_android_wechat_embedded_wins_over_os():
    """安卓微信 UA 同时含 Android 与 MicroMessenger，必须先判内嵌归 android_wechat"""
    ua = ("Mozilla/5.0 (Linux; Android 14; M2012K11AC) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/86.0.4240.198 Mobile Safari/537.36 "
          "MicroMessenger/8.0.49")
    device_info, device_type = parse_device(ua)
    assert device_info == "android_wechat"
    assert device_type == BehaviorDeviceType.MOBILE.value


def test_parse_device_ios_wechat():
    ua = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.49")
    device_info, device_type = parse_device(ua)
    assert device_info == "ios_wechat"
    assert device_type == BehaviorDeviceType.MOBILE.value


def test_parse_device_harmony():
    """鸿蒙浏览器（ArkWeb/HarmonyOS UA，可能带 Android 兼容串）归 harmony_mobile"""
    ua = ("Mozilla/5.0 (Phone; OpenHarmony 5.0) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/114.0.0.0 Mobile Safari/537.36 ArkWeb/4.1.6.1 HuaweiBrowser/5.0.1.300")
    device_info, device_type = parse_device(ua)
    assert device_info == "harmony_mobile"
    assert device_type == BehaviorDeviceType.MOBILE.value


def test_parse_device_desktop_browsers():
    cases = [
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0", "windows_pc"),
        ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15", "mac_pc"),
        ("Mozilla/5.0 (X11; Linux x86_64) Firefox/127.0", "linux_pc"),
    ]
    for ua, expected in cases:
        device_info, device_type = parse_device(ua)
        assert device_info == expected, f"UA={ua}"
        assert device_type == BehaviorDeviceType.PC.value


def test_parse_device_plain_mobile():
    assert parse_device("Mozilla/5.0 (Linux; Android 13; Pixel 7) Chrome/120.0 Mobile")[0] == "android_mobile"
    assert parse_device("Mozilla/5.0 (iPhone; CPU iPhone OS 17_4) Safari/605.1.15")[0] == "ios_mobile"


def test_parse_device_unknown_and_empty():
    # 无关键词（curl 等）与空 UA 均归 unknown
    for ua in ("curl/8.5.0", ""):
        device_info, device_type = parse_device(ua)
        assert device_info == "unknown"
        assert device_type == BehaviorDeviceType.UNKNOWN.value
    assert parse_device(None) == ("unknown", "unknown")


def test_parse_device_output_always_in_vocabulary():
    """解析结果必须落在受控词表内（写入 device_info 前的约束）"""
    for ua in ("", "MicroMessenger/unknown-platform", "Mozilla/5.0 WeirdOS/9", "curl/8"):
        device_info, _ = parse_device(ua)
        assert device_info in DEVICE_VOCABULARY


# ============== token_id ==============

@pytest.mark.asyncio
async def test_token_id_is_sha256_prefix8():
    """token_id = SHA256(Authorization token) 前 8 位，不存原文"""
    token = "my-secret-token-123"
    expected = hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]
    req = _fake_request(headers={"authorization": f"Bearer {token}"})
    with patch("src.services.behavior_log._insert_sync") as mock_insert:
        await record_behavior(req, BehaviorAction.PROFILE_UPDATE)

    row = mock_insert.call_args[0][0]
    assert row["token_id"] == expected
    assert token not in (row["token_id"] or "")


@pytest.mark.asyncio
async def test_token_id_none_without_bearer():
    req = _fake_request(headers={"authorization": "Basic xxx"})
    with patch("src.services.behavior_log._insert_sync") as mock_insert:
        await record_behavior(req, BehaviorAction.PROFILE_UPDATE)
    assert mock_insert.call_args[0][0]["token_id"] is None


# ============== audit_action 装饰器 ==============

@pytest.mark.asyncio
async def test_audit_action_records_success():
    """业务成功返回后记 success=True，并自动取 http_method/path"""
    req = SimpleNamespace(
        headers={}, state=SimpleNamespace(tenant_id="t1", user_id="u1", user_role=None),
        client=SimpleNamespace(host="127.0.0.1"), method="POST",
        url=SimpleNamespace(path="/api/saas/tenants"),
    )

    @audit_action(BehaviorAction.CREATE, BehaviorResourceType.TENANT,
                  id_arg="tenant_id", name_arg="company_name")
    async def dummy_endpoint(request, tenant_id=None, body=None):
        return {"success": True}

    body = {"company_name": "测试租户"}
    with patch("src.services.behavior_log._insert_sync") as mock_insert:
        result = await dummy_endpoint(req, tenant_id="t100", body=body)

    assert result == {"success": True}
    mock_insert.assert_called_once()
    row = mock_insert.call_args[0][0]
    assert row["action"] == "create"
    assert row["resource_type"] == "tenant"
    assert row["resource_id"] == "t100"
    assert row["resource_name"] == "测试租户"
    assert row["http_method"] == "POST"
    assert row["path"] == "/api/saas/tenants"
    assert row["success"] is True


@pytest.mark.asyncio
async def test_audit_action_records_failure_and_reraises():
    """业务异常记 success=False + error_msg 后原样 re-raise"""
    req = SimpleNamespace(
        headers={}, state=SimpleNamespace(tenant_id="t1", user_id="u1", user_role=None),
        client=None, method="DELETE", url=SimpleNamespace(path="/api/saas/tenants/t1"),
    )

    @audit_action(BehaviorAction.DELETE, BehaviorResourceType.TENANT, id_arg="tenant_id")
    async def failing_endpoint(request, tenant_id=None):
        raise ValueError("租户仍有活跃用户，禁止删除")

    with patch("src.services.behavior_log._insert_sync") as mock_insert:
        with pytest.raises(ValueError, match="禁止删除"):
            await failing_endpoint(req, tenant_id="t1")

    mock_insert.assert_called_once()
    row = mock_insert.call_args[0][0]
    assert row["success"] is False
    assert "禁止删除" in row["error_msg"]
    assert row["resource_id"] == "t1"


@pytest.mark.asyncio
async def test_audit_action_wraps_positional_request():
    """Request 以位置参数传入时也能定位（兼容两种调用形态）"""

    @audit_action(BehaviorAction.UPDATE, BehaviorResourceType.CONFIG)
    async def dummy_endpoint(request):
        return "ok"

    req = SimpleNamespace(
        headers={}, state=SimpleNamespace(tenant_id=None, user_id=None, user_role=None),
        client=None, method="PUT", url=SimpleNamespace(path="/api/admin/config"),
    )
    with patch("src.services.behavior_log._insert_sync") as mock_insert:
        await dummy_endpoint(req)

    row = mock_insert.call_args[0][0]
    assert row["http_method"] == "PUT"
    assert row["path"] == "/api/admin/config"


# ============== 匿名事件 user_id 可空（登录失败/无效 token 登出）==============

@pytest.mark.asyncio
async def test_anonymous_login_failed_row_has_null_user_id():
    """登录失败无用户身份：user_id 落 NULL（DDL 允许 NULL），identifier 记 detail

    回归锁定：user_id 曾为 NOT NULL，导致所有 login_failed 行 INSERT 被
    NotNullViolation 拦截且被 record_behavior 吞掉，审计事件全部丢失。
    """
    req = _fake_request(tenant_id=None, user_id=None)
    with patch("src.services.behavior_log._insert_sync") as mock_insert:
        await record_behavior(
            req, BehaviorAction.LOGIN_FAILED, success=False,
            error_msg="手机号或密码有误", login_method="password",
            detail={"identifier": "13800000000"},
        )

    row = mock_insert.call_args[0][0]
    assert row["user_id"] is None
    assert row["detail"] is not None and "13800000000" in row["detail"]


# ============== audit_action 装饰器增强（Phase 2）==============

class _FakeBody(BaseModel):
    """模拟 FastAPI Pydantic body 模型"""
    username: str | None = None
    remark: str | None = None


class _MigrationBody(BaseModel):
    """模拟 tenant_migration.py 的 body（参数名恰好叫 request）"""
    source_tenant: str = "src_tenant"


def _audit_request(method: str = "POST", path: str = "/api/admin/x") -> SimpleNamespace:
    """构造带 method/url 形状的 request 替身（装饰器按形状兜底识别）"""
    return SimpleNamespace(
        headers={},
        state=SimpleNamespace(tenant_id="t1", user_id="u1", user_role=None),
        client=None, method=method, url=SimpleNamespace(path=path),
    )


@pytest.mark.asyncio
async def test_audit_action_pydantic_body_named_request_not_mistaken_for_request():
    """body 参数名恰为 request 时不得误当 Request（tenant_migration 场景），Request 形参名叫 req 也能定位"""
    @audit_action(BehaviorAction.UPDATE, BehaviorResourceType.TENANT,
                  id_arg="tenant_id", name_arg="source_tenant")
    async def ep(tenant_id=None, request: _MigrationBody = None, req=None):
        return {"success": True}

    req_obj = _audit_request(method="POST", path="/api/saas/tenants/t9/migration/preview")
    with patch("src.services.behavior_log._insert_sync") as mock_insert:
        await ep(tenant_id="t9", request=_MigrationBody(), req=req_obj)

    row = mock_insert.call_args[0][0]
    assert row["http_method"] == "POST"          # 从 req（真 Request）定位，而非名为 request 的 body
    assert row["path"] == "/api/saas/tenants/t9/migration/preview"
    assert row["resource_id"] == "t9"
    assert row["resource_name"] == "src_tenant"  # name_arg 从名为 request 的 Pydantic body 提取


@pytest.mark.asyncio
async def test_audit_action_name_arg_extracts_from_arbitrary_body_param_name():
    """name_arg 从非 body 名的 Pydantic 参数提取（body 参数名可能是 req/data 等任意名字）"""
    @audit_action(BehaviorAction.CREATE, BehaviorResourceType.TENANT_USER, name_arg="username")
    async def ep(request=None, data: _FakeBody = None):
        return {"success": True}

    with patch("src.services.behavior_log._insert_sync") as mock_insert:
        await ep(request=_audit_request(path="/api/saas/users"), data=_FakeBody(username="张三"))

    row = mock_insert.call_args[0][0]
    assert row["resource_name"] == "张三"


@pytest.mark.asyncio
async def test_audit_action_sync_def_route_runs_in_worker_thread():
    """同步 def 路由：装饰后可 await，原函数经 to_thread 在工作线程执行（不阻塞事件循环）"""
    caller_thread = threading.get_ident()
    seen_threads = []

    @audit_action(BehaviorAction.UPDATE, BehaviorResourceType.TENANT, id_arg="tenant_id")
    def sync_endpoint(request, tenant_id=None):
        seen_threads.append(threading.get_ident())
        return {"success": True}

    with patch("src.services.behavior_log._insert_sync") as mock_insert:
        result = await sync_endpoint(
            _audit_request(method="POST", path="/api/saas/permissions/tenant/t1/agents"),
            tenant_id="t1",
        )

    assert result == {"success": True}
    assert seen_threads[0] != caller_thread      # 在 to_thread 工作线程执行，未阻塞事件循环线程
    row = mock_insert.call_args[0][0]
    assert row["success"] is True
    assert row["resource_id"] == "t1"


@pytest.mark.asyncio
async def test_audit_action_business_failure_dict_recorded_as_failure():
    """业务级失败：返回 {"success": False} 记 success=False + error_msg（error 优先，message 兜底）"""
    @audit_action(BehaviorAction.UPDATE, BehaviorResourceType.TENANT)
    async def ep_with_error(request):
        return {"success": False, "error": "权限不足"}

    @audit_action(BehaviorAction.UPDATE, BehaviorResourceType.TENANT)
    async def ep_with_message(request):
        return {"success": False, "message": "没有需要更新的字段"}

    with patch("src.services.behavior_log._insert_sync") as mock_insert:
        await ep_with_error(_audit_request())
    row = mock_insert.call_args[0][0]
    assert row["success"] is False
    assert "权限不足" in row["error_msg"]

    with patch("src.services.behavior_log._insert_sync") as mock_insert:
        await ep_with_message(_audit_request())
    row = mock_insert.call_args[0][0]
    assert row["success"] is False
    assert "没有需要更新的字段" in row["error_msg"]


@pytest.mark.asyncio
async def test_audit_action_dict_without_success_key_not_treated_as_failure():
    """防误伤：返回 dict 但无 success 键（如导出结果）仍记 success=True"""
    @audit_action(BehaviorAction.EXPORT, BehaviorResourceType.BILLING)
    async def ep(request):
        return {"rows": [1, 2, 3]}

    with patch("src.services.behavior_log._insert_sync") as mock_insert:
        await ep(_audit_request(method="GET", path="/api/saas/reports/export_usage_report"))

    row = mock_insert.call_args[0][0]
    assert row["success"] is True
    assert row["error_msg"] is None


@pytest.mark.asyncio
async def test_audit_action_update_records_body_field_names_only():
    """UPDATE 操作自动记 detail={"fields": [...]}：仅字段名不含值，敏感值不落库"""
    @audit_action(BehaviorAction.UPDATE, BehaviorResourceType.TENANT)
    async def update_ep(request, body: _FakeBody = None):
        return {"success": True}

    @audit_action(BehaviorAction.CREATE, BehaviorResourceType.TENANT)
    async def create_ep(request, body: _FakeBody = None):
        return {"success": True}

    with patch("src.services.behavior_log._insert_sync") as mock_insert:
        await update_ep(request=_audit_request(method="PATCH"), body=_FakeBody(username="张三"))
    row = mock_insert.call_args[0][0]
    detail = json.loads(row["detail"])
    assert detail == {"fields": ["username"]}     # 只记请求实际携带的字段名
    assert "张三" not in (row["detail"] or "")     # 不含字段值

    with patch("src.services.behavior_log._insert_sync") as mock_insert:
        await create_ep(request=_audit_request(method="POST"), body=_FakeBody(username="张三"))
    row = mock_insert.call_args[0][0]
    assert row["detail"] is None                  # 非 UPDATE 动作不记 fields


# ============== db_update.yaml 增量格式 ==============

class TestDbUpdateYaml:
    """db_update.yaml 必须能被 yaml.safe_load 解析且 datetime 唯一、严格递增（fail-fast 规范）"""

    @staticmethod
    def _load():
        path = Path(__file__).parents[2] / "deploy" / "db_update.yaml"
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def test_yaml_parses_and_required_fields_present(self):
        data = self._load()
        assert isinstance(data, list) and len(data) >= 2
        for item in data:
            assert "datetime" in item and "remark" in item and "statements" in item
            assert item["remark"].strip()
            assert item["statements"].strip()

    def test_datetime_format_and_unique_and_strictly_increasing(self):
        data = self._load()
        dts = [item["datetime"] for item in data]
        # 全部是加引号的字符串格式（yaml.safe_load 后不应出现 datetime 对象）
        for dt in dts:
            assert isinstance(dt, str), f"datetime 必须是字符串: {dt!r}"
            datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
        # 唯一
        assert len(dts) == len(set(dts)), "datetime 存在重复"
        # 严格递增（sorted 允许相等，需逐对比较）
        assert all(a < b for a, b in zip(dts, dts[1:])), "datetime 必须严格递增"

    def test_behavior_log_entry_is_idempotent_sql(self):
        """user_behavior_logs 增量条目存在且 SQL 幂等（IF NOT EXISTS）"""
        data = self._load()
        entries = [i for i in data if "user_behavior_logs" in i["statements"]]
        assert entries, "缺少 user_behavior_logs 增量条目"
        sql = entries[-1]["statements"]
        assert "CREATE TABLE IF NOT EXISTS user_behavior_logs" in sql
        for idx in ("idx_ubl_tenant_time", "idx_ubl_user_time", "idx_ubl_action_time"):
            assert f"CREATE INDEX IF NOT EXISTS {idx}" in sql
        # 关键列齐全（设计文档 §3）
        for col in ("tenant_id", "user_id", "action", "detail JSONB", "client_ip",
                    "user_agent", "success", "entry", "login_method", "channel",
                    "channel_user_id", "token_id", "request_id", "http_method",
                    "path", "device_type", "device_info", "created_at"):
            assert col in sql, f"建表语句缺少列: {col}"

    def test_behavior_log_entry_is_the_latest(self):
        """新条目 datetime 必须大于文件中此前的最后一条（保证存量环境能执行到）"""
        data = self._load()
        ubl = [i for i in data if "user_behavior_logs" in i["statements"]]
        assert ubl
        ubl_dt = datetime.strptime(ubl[-1]["datetime"], "%Y-%m-%d %H:%M:%S")
        others = [datetime.strptime(i["datetime"], "%Y-%m-%d %H:%M:%S")
                  for i in data if "user_behavior_logs" not in i["statements"]]
        if others:
            assert ubl_dt > max(others)


def _extract_ubl_create_table(sql_text: str) -> str:
    """从 SQL 文本中截取 user_behavior_logs 的 CREATE TABLE 语句块"""
    idx = sql_text.find("CREATE TABLE IF NOT EXISTS user_behavior_logs")
    assert idx >= 0, "未找到 user_behavior_logs 建表语句"
    end = sql_text.find(");", idx)
    return sql_text[idx:end]


def test_ddl_user_id_nullable_in_both_files():
    """user_id 必须可空（登录失败/无效 token 登出/渠道未注册用户落 NULL）——两处 DDL 同步锁定"""
    for ddl_path in (
        Path(__file__).parents[2] / "deploy" / "db_update.yaml",
        Path(__file__).parents[2] / "deploy" / "init-postgres.sql",
    ):
        sql_text = ddl_path.read_text(encoding="utf-8")
        block = _extract_ubl_create_table(sql_text)
        user_id_line = next(l for l in block.splitlines() if l.strip().startswith("user_id "))
        assert "NOT NULL" not in user_id_line, f"{ddl_path.name}: user_id 不允许 NOT NULL（会吞掉所有登录失败审计）"
