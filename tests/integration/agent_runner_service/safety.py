"""Read-only checks against resolved application/SDK resource targets.

Called before an execution is allowed to reach external IO. No auth, Engine,
Gateway, worker or repository behavior is replaced by these checks.
"""
from urllib.parse import urlsplit


def verify_resource_targets(*, database_name, provider_port):
    if not database_name.startswith("aid_test_"):
        raise RuntimeError("Service verification requires an isolated database")

    from src.config.settings import settings, get_embedding_api_key
    from src.db import database
    import dashscope

    def local_endpoint(value):
        endpoint = urlsplit(value or "")
        return endpoint.scheme == "http" and endpoint.hostname == "127.0.0.1" and endpoint.port == provider_port

    # Resolve the objects actually consumed by Gateway/Provider and SDK, rather
    # than checking the input environment strings which dotenv may override.
    assert settings.llm.provider == "qwen", "Unexpected effective primary provider"
    assert not settings.llm.failover.enabled, "External failover must be disabled"
    assert settings.llm.get_lite_target() == ("qwen", "qwen-runner-lite-fixture")
    for name in ("qwen", "zhipu", "deepseek", "moonshot"):
        configuration = getattr(settings.llm, name)
        assert local_endpoint(configuration.base_url), "Provider endpoint is not fixture-local"
    assert local_endpoint(dashscope.base_http_api_url), "SDK embedding endpoint is not fixture-local"
    assert local_endpoint(dashscope.base_compatible_api_url), "SDK compatible endpoint is not fixture-local"
    assert bool(get_embedding_api_key()), "Fixture embedding key was not resolved"

    # Validate the parsed module target BEFORE opening a connection, so a bad
    # configuration cannot result in even a probe against the shared logs DB.
    assert database.DB_CONFIG["database"] == database_name
    configuration = database._get_logs_db_config()
    assert configuration and configuration["database"] == database_name, "Effective trace target is not isolated"
    owned_pool = database._logs_connection_pool is None
    try:
        if owned_pool:
            assert database.init_logs_pool(), "Isolated trace pool did not initialize"
        with database.get_logs_connection() as cursor:
            cursor.execute("SELECT current_database() AS database_name")
            row = cursor.fetchone()
            assert row["database_name"] == database_name, "Actual trace connection is not isolated"
    finally:
        if owned_pool:
            database.close_logs_pool()
    # Only fixture database name and booleans may be written to evidence.
    return {"database": database_name, "trace_connection_verified": True,
            "effective_provider_endpoints_local": True}
