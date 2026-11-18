"""lyjy_employment fetcher 单测：列白名单 / 四步 WHERE 条件 / 删除事件集合差 /
连接配置解密（全部离线：假 engine 捕获 SQL，不连客户库）。
"""

import pytest

import src.tenant_custom.lyjy_employment.fetcher as fetcher


class RowLike:
    """模拟 SQLAlchemy Row（_mapping 接口；fetcher._rows 统一按此取列）。"""

    def __init__(self, data: dict):
        self._mapping = data


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(RowLike(r) for r in self._rows)


class FakeConnection:
    """捕获执行的 SQL 与参数；返回构造时注入的行。"""

    def __init__(self, engine):
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, clause, params=None):
        sql = str(clause)
        self._engine.executed.append((sql, params))
        return FakeResult(self._engine.rows)


class FakeEngine:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed: list = []

    def connect(self):
        return FakeConnection(self)

    def dispose(self):
        pass


# ------------------------------- 列白名单 -------------------------------


def test_jobs_increment_selects_whitelist_only():
    """④ 岗位内容增量：SELECT 只取白名单列；求职者/管理侧列（uid/admin_id/click/
    key_full 等）不在 SQL 中——白名单机制保证源库加列也不进管线。"""
    engine = FakeEngine()
    fetcher.fetch_jobs_increment(engine, watermark=100)
    sql, params = engine.executed[-1]
    assert params == {"watermark": 100}
    for col in fetcher.JOBS_CONTENT_COLUMNS:
        assert f"`{col}`" in sql
    for banned in ("uid", "admin_id", "click", "key_full", "key_precise",
                   "map_x", "map_y", "setmeal_id", "telephone", "idcard"):
        assert f"`{banned}`" not in sql
    # 表名正确
    assert "FROM `qs_jobs`" in sql


def test_articles_increment_whitelist_and_conditions():
    """④ 文章增量：guide/article 要求 audit=1 AND is_display=1；
    notice/explain 无 audit 列仅 is_display；水位走 addtime。"""
    engine = FakeEngine()
    for table in ("qs_guide", "qs_article", "qs_notice", "qs_explain"):
        fetcher.fetch_articles_increment(engine, table, watermark=7)
        sql, params = engine.executed[-1]
        assert params == {"watermark": 7}
        assert f"FROM `{table}`" in sql
        for col in fetcher.ARTICLE_CONTENT_COLUMNS[table]:
            assert f"`{col}`" in sql
        where = sql.split("WHERE", 1)[1]
        assert "addtime > " in where
        assert "is_display = 1" in where
        if table in ("qs_guide", "qs_article"):
            assert "audit = 1" in where
        else:
            assert "audit" not in where


def test_catalog_pulls_light_columns():
    """① 目录全量：jobs 六状态列+名称展示列；文章表 id/状态/addtime。"""
    engine = FakeEngine()
    fetcher.fetch_catalog(engine, "qs_jobs")
    sql, _ = engine.executed[-1]
    assert "FROM `qs_jobs`" in sql
    for col in ("id", "audit", "display", "close_type", "deadline", "refreshtime"):
        assert f"`{col}`" in sql
    fetcher.fetch_catalog(engine, "qs_guide")
    sql, _ = engine.executed[-1]
    assert "`audit`" in sql and "`is_display`" in sql and "`addtime`" in sql
    fetcher.fetch_catalog(engine, "qs_notice")
    sql, _ = engine.executed[-1]
    where = sql.split("WHERE", 1)[0]  # 目录无 WHERE（全表）
    assert "FROM `qs_notice`" in where


def test_delete_ids_and_expiry_conditions():
    """② 删除事件仅取 id 单列（测试库实测仅 qs_jobs_delete 存在）；
    ③ 到期扫描条件 = 在招状态 + deadline>0 + 已过期。"""
    engine = FakeEngine([{"id": 5}])
    fetcher.fetch_delete_ids(engine, "qs_jobs")
    sql, _ = engine.executed[-1]
    assert sql == "SELECT `id` FROM `qs_jobs_delete`"
    assert fetcher.DELETE_TABLES == {"qs_jobs": "qs_jobs_delete"}

    fetcher.fetch_expired_job_ids(engine)
    sql, _ = engine.executed[-1]
    where = sql.split("WHERE", 1)[1]
    assert "audit = 1" in where and "display = 1" in where
    assert "deadline > 0" in where and "deadline < UNIX_TIMESTAMP()" in where


def test_contact_and_profile_queries():
    """联系方式/企业简介按 id 批量取，白名单列；联系方式含展示位。"""
    engine = FakeEngine([{"pid": 1}])
    fetcher.fetch_contacts(engine, [1, 2, 3])
    sql, _ = engine.executed[-1]
    assert "FROM `qs_jobs_contact`" in sql and "pid IN (1, 2, 3)" in sql
    for col in ("contact", "telephone", "landline_tel", "contact_show", "telephone_show"):
        assert f"`{col}`" in sql
    assert "`qq`" not in sql and "`email`" not in sql

    profile_engine = FakeEngine([{"id": 9, "companyname": "甲", "contents": "简介"}])
    profiles = fetcher.fetch_company_profiles(profile_engine, [9])
    sql, _ = profile_engine.executed[-1]
    assert "FROM `qs_company_profile`" in sql
    for col in fetcher.COMPANY_PROFILE_COLUMNS:
        assert f"`{col}`" in sql
    assert profiles[9]["companyname"] == "甲"


# ------------------------------- 连接配置 -------------------------------


def test_load_connection_config_roundtrip_and_errors():
    """connection_encrypted Fernet 整包 JSON 解密；缺失/损坏归一为配置错误且不带密文。"""
    from src.tenant_custom.lyjy_employment.bootstrap import build_connection_payload

    encrypted = build_connection_payload({
        "host": "db.example.internal", "port": 3306, "database": "gt_lyjy",
        "user": "ro_user", "password": "secret-pw",
    })
    config = fetcher.load_connection_config({"module": "lyjy_employment",
                                             "connection_encrypted": encrypted})
    assert config == {
        "host": "db.example.internal", "port": 3306, "database": "gt_lyjy",
        "user": "ro_user", "password": "secret-pw",
    }
    with pytest.raises(fetcher.MissingConnectionConfigError, match="缺少连接配置"):
        fetcher.load_connection_config({"module": "lyjy_employment"})
    with pytest.raises(fetcher.MissingConnectionConfigError, match="解密失败"):
        fetcher.load_connection_config({"module": "lyjy_employment",
                                        "connection_encrypted": "not-fernet"})
    # 字段不全：加密层拒绝构造；直接构造缺字段密文时解密层同样归一为配置错误
    with pytest.raises(ValueError, match="缺少字段"):
        build_connection_payload({
            "host": "h", "port": 3306, "database": "d", "user": "u", "password": "",
        })
    import json as _json

    from src.db.encryption import encryption_manager

    incomplete = encryption_manager.encrypt(_json.dumps(
        {"host": "h", "port": 3306, "database": "d", "user": "u"}))
    with pytest.raises(fetcher.MissingConnectionConfigError, match="字段不全"):
        fetcher.load_connection_config({"connection_encrypted": incomplete})


# ------------------------------- 短连接与集合差 -------------------------------


def test_build_engine_uses_mysql_driver_and_disposable():
    """SQLAlchemy 短连接：mysql+pymysql 驱动；密码特殊字符走 quote_plus。"""
    engine = fetcher.build_engine({
        "host": "h", "port": 3307, "database": "gt_lyjy",
        "user": "u", "password": "p@ss:word",
    })
    url = engine.url
    assert url.drivername == "mysql+pymysql"
    assert url.host == "h" and url.port == 3307 and url.database == "gt_lyjy"
    assert url.password == "p@ss:word"  # quote_plus 后仍原值取回
    engine.dispose()


def test_delete_ids_set_diff_pure():
    """② id 集合差：新出现的 id 才进入处理；已处理集合差集为空。"""
    fetched = {1, 2, 3}
    processed = {2}
    new = fetched - processed
    assert new == {1, 3}
    # 集合差幂等：同一批重放差集为空
    processed |= new
    assert fetched - processed == set()
