"""Tenant agent instance profile authorization: subscription checks bind to the base agent.

Regression: a copy instance (agent_id != base_agent_id) carries no subscription
row of its own; the authorizer must resolve it to base_agent_id before the
subscriptions / user_agent_permissions checks, otherwise tenant-portal chat
submissions fail with PROFILE_FORBIDDEN (403).
"""
import uuid

import pytest

from src.services.agent_runner.authorization import RunnerAuthorizer, RunnerError
from src.services.agent_runner.profiles import ProfileCatalog

pytestmark = pytest.mark.integration


@pytest.fixture
def authorizer():
    return RunnerAuthorizer(None)


@pytest.fixture
def instance_tenant(service_database):
    suffix = uuid.uuid4().hex
    tenant_id = f"runner_inst_{suffix}"
    base = f"inst-base-{suffix}"
    copy_id = f"{base}-tenant-{suffix}-1e0s"
    direct_id = base
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO tenants (tenant_id, company_name, tenant_code, credit_balance) VALUES (%s, %s, %s, 1000)",
            (tenant_id, f"测试租户-runner-inst-{suffix}", f"T{uuid.uuid4().hex[:6].upper()}"))
        cursor.execute(
            """INSERT INTO tenant_agent_instances (tenant_id, agent_id, base_agent_id, status)
            VALUES (%s, %s, %s, 'active'), (%s, %s, %s, 'active'),
                   (%s, %s, %s, 'archived')""",
            (tenant_id, copy_id, base,
             tenant_id, direct_id, base,
             tenant_id, f"{base}-tenant-{suffix}-arch", base))
        cursor.execute(
            """INSERT INTO subscriptions (subscription_id, tenant_id, subagent_type, status, starts_at)
            VALUES (%s, %s, %s, 'active', now() - interval '1 day')""",
            (f"sub_{uuid.uuid4().hex}", tenant_id, base))
    try:
        yield tenant_id, base, copy_id, direct_id, f"{base}-tenant-{suffix}-arch"
    finally:
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM subscriptions WHERE tenant_id=%s", (tenant_id,))
            cursor.execute("DELETE FROM tenant_agent_instances WHERE tenant_id=%s", (tenant_id,))
            cursor.execute("DELETE FROM tenants WHERE tenant_id=%s", (tenant_id,))


def allowed(authorizer, service_database, tenant_id, profile_id, user=None):
    with service_database.connect() as connection, connection.cursor() as cursor:
        authorizer._profile(cursor, profile_id, user or {"role": "tenant_admin"}, tenant_id)


def rejected(authorizer, service_database, tenant_id, profile_id, user=None):
    with service_database.connect() as connection, connection.cursor() as cursor:
        with pytest.raises(RunnerError) as caught:
            authorizer._profile(cursor, profile_id, user or {"role": "tenant_admin"}, tenant_id)
    assert caught.value.code == "PROFILE_FORBIDDEN" and caught.value.status == 403


def test_copy_instance_resolves_to_base_subscription(
        authorizer, service_database, instance_tenant):
    tenant_id, base, copy_id, _, _ = instance_tenant
    # Copy instance has no subscription row of its own; the base one must match.
    allowed(authorizer, service_database, tenant_id, copy_id)


def test_platform_admin_with_target_tenant_still_goes_through_resolution(
        authorizer, service_database, instance_tenant):
    tenant_id, base, copy_id, _, _ = instance_tenant
    allowed(authorizer, service_database, tenant_id, copy_id,
            user={"role": "platform_admin"})


def test_archived_instance_still_resolves_for_history(
        authorizer, service_database, instance_tenant):
    tenant_id, base, copy_id, _, archived_id = instance_tenant
    allowed(authorizer, service_database, tenant_id, archived_id)


def test_direct_instance_row_uses_same_agent_id(
        authorizer, service_database, instance_tenant):
    tenant_id, base, copy_id, direct_id, _ = instance_tenant
    allowed(authorizer, service_database, tenant_id, direct_id)


def test_instance_without_base_subscription_is_rejected(
        authorizer, service_database, instance_tenant):
    tenant_id, base, copy_id, _, _ = instance_tenant
    service_database.rows("DELETE FROM subscriptions WHERE tenant_id=%s AND subagent_type=%s",
                          (tenant_id, base))
    rejected(authorizer, service_database, tenant_id, copy_id)
    rejected(authorizer, service_database, tenant_id, base)


def test_foreign_tenant_instance_id_is_not_resolved(
        authorizer, service_database, instance_tenant):
    suffix = uuid.uuid4().hex
    other_tenant = f"runner_inst_other_{suffix}"
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO tenants (tenant_id, company_name, tenant_code, credit_balance) VALUES (%s, %s, %s, 1000)",
            (other_tenant, f"测试租户-runner-inst-other-{suffix}", f"T{uuid.uuid4().hex[:6].upper()}"))
    try:
        _, _, copy_id, _, _ = instance_tenant
        rejected(authorizer, service_database, other_tenant, copy_id)
    finally:
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM tenants WHERE tenant_id=%s", (other_tenant,))


@pytest.fixture
def profile_instance_tenant(service_database):
    """A copy instance and an archived instance over a real builtin base agent."""
    suffix = uuid.uuid4().hex
    tenant_id = f"runner_profile_inst_{suffix}"
    base = "pre-sales"
    copy_id = f"{base}-tenant-{suffix}-c0py"
    archived_id = f"{base}-tenant-{suffix}-arch"
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO tenants (tenant_id, company_name, tenant_code, credit_balance) VALUES (%s, %s, %s, 1000)",
            (tenant_id, f"测试租户-runner-profile-{suffix}", f"T{uuid.uuid4().hex[:6].upper()}"))
        cursor.execute(
            """INSERT INTO tenant_agent_instances (tenant_id, agent_id, base_agent_id, display_name, status)
            VALUES (%s, %s, %s, %s, 'active'), (%s, %s, %s, %s, 'archived')""",
            (tenant_id, copy_id, base, "副本实例名",
             tenant_id, archived_id, base, "归档实例名"))
    try:
        yield tenant_id, base, copy_id, archived_id
    finally:
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM tenant_agent_instances WHERE tenant_id=%s", (tenant_id,))
            cursor.execute("DELETE FROM tenants WHERE tenant_id=%s", (tenant_id,))


def test_copy_instance_profile_resolves_with_tenant(profile_instance_tenant):
    """Regression: instances live outside the global registry, so submit used to
    fail PROFILE_NOT_FOUND 404 right after the profile authorization fix."""
    tenant_id, base, copy_id, _ = profile_instance_tenant
    config, fingerprint = ProfileCatalog().resolve(copy_id, tenant_id)
    assert config.dir_name == copy_id
    assert config.base_agent_id == base
    assert config.tenant_instance is True
    assert config.name == "副本实例名"
    base_config, base_fingerprint = ProfileCatalog().resolve(base, tenant_id)
    assert base_config.dir_name == base
    assert fingerprint != base_fingerprint


def test_instance_profile_fingerprint_is_stable(profile_instance_tenant):
    tenant_id, _, copy_id, _ = profile_instance_tenant
    _, first = ProfileCatalog().resolve(copy_id, tenant_id)
    _, second = ProfileCatalog().resolve(copy_id, tenant_id)
    assert first == second


def test_archived_instance_profile_still_resolves(profile_instance_tenant):
    tenant_id, base, _, archived_id = profile_instance_tenant
    config, _ = ProfileCatalog().resolve(archived_id, tenant_id)
    assert config.dir_name == archived_id
    assert config.base_agent_id == base


def test_instance_profile_is_tenant_scoped(profile_instance_tenant, service_database):
    tenant_id, _, copy_id, _ = profile_instance_tenant
    suffix = uuid.uuid4().hex
    other_tenant = f"runner_profile_other_{suffix}"
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO tenants (tenant_id, company_name, tenant_code, credit_balance) VALUES (%s, %s, %s, 1000)",
            (other_tenant, f"测试租户-runner-profile-other-{suffix}", f"T{uuid.uuid4().hex[:6].upper()}"))
    try:
        with pytest.raises(RunnerError) as caught:
            ProfileCatalog().resolve(copy_id, other_tenant)
        assert caught.value.code == "PROFILE_NOT_FOUND" and caught.value.status == 404
    finally:
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM tenants WHERE tenant_id=%s", (other_tenant,))


def test_instance_profile_without_tenant_is_not_resolved(profile_instance_tenant):
    _, _, copy_id, _ = profile_instance_tenant
    with pytest.raises(RunnerError) as caught:
        ProfileCatalog().resolve(copy_id)
    assert caught.value.code == "PROFILE_NOT_FOUND" and caught.value.status == 404


def test_user_permission_check_also_binds_to_base_agent(
        authorizer, service_database, instance_tenant):
    tenant_id, base, copy_id, _, _ = instance_tenant
    suffix = uuid.uuid4().hex
    user_id = f"runner_inst_user_{suffix}"
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (user_id, tenant_id, role, status, username) VALUES (%s, %s, 'user', 'active', %s)",
            (user_id, tenant_id, user_id))
        cursor.execute(
            "INSERT INTO user_agent_permissions (user_id, agent_id, tenant_id) VALUES (%s, %s, %s)",
            (user_id, base, tenant_id))
    try:
        allowed(authorizer, service_database, tenant_id, copy_id, user={"role": "user", "user_id": user_id})
        service_database.rows("DELETE FROM user_agent_permissions WHERE user_id=%s", (user_id,))
        rejected(authorizer, service_database, tenant_id, copy_id, user={"role": "user", "user_id": user_id})
    finally:
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM user_agent_permissions WHERE user_id=%s", (user_id,))
            cursor.execute("DELETE FROM users WHERE user_id=%s", (user_id,))
