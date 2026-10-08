"""Canonical shared owner boundaries, real filesystem and production helpers."""
from pathlib import Path

import pytest

from src.core.storage import get_conversation_dir, resolve_scoped_uploaded_file

pytestmark = pytest.mark.integration


@pytest.mark.parametrize('tenant', ['current-owner', None])
def test_scoped_owner_root_symlink_cannot_read_foreign_owner_from_cache_or_disk(tmp_path, monkeypatch, tenant):
    base = tmp_path / 'shared'
    foreign = base / 'tenants' / 'foreign-owner'
    conversation = foreign / 'conversation'
    conversation.mkdir(parents=True)
    fixture = conversation / 'file_fixture_anchor.txt'
    fixture.write_text('fictional other owner bytes')
    (base / 'tenants' / (tenant or '_anonymous')).symlink_to(foreign, target_is_directory=True)
    monkeypatch.setenv('AGENT_RUNNER_STORAGE_ROOT', str(base))
    disk = resolve_scoped_uploaded_file(fixture.name, tenant)
    cache = resolve_scoped_uploaded_file(fixture.name, tenant, metadata={'path': str(fixture)})
    assert disk is None and cache is None
    assert fixture.read_text() == 'fictional other owner bytes'


def test_owner_root_symlink_rejected_before_mkdir_materializes_external_conversation(tmp_path, monkeypatch):
    base = tmp_path / 'shared'
    external = tmp_path / 'external-fixture'
    external.mkdir()
    (base / 'tenants').mkdir(parents=True)
    (base / 'tenants' / '_anonymous').symlink_to(external, target_is_directory=True)
    monkeypatch.setenv('AGENT_RUNNER_STORAGE_ROOT', str(base))
    with pytest.raises(ValueError):
        get_conversation_dir(None)
    assert not (external / 'conversation').exists()


def test_trusted_mount_alias_itself_is_supported_without_following_descendant_scene_links(tmp_path, monkeypatch):
    mount = tmp_path / 'actual-mounted-root'
    owner = mount / 'tenants' / 'current-owner'
    conversation = owner / 'conversation'
    conversation.mkdir(parents=True)
    good = conversation / 'file_fixture_mount.txt'
    good.write_text('fictional own bytes')
    configured = tmp_path / 'configured-mount-alias'
    configured.symlink_to(mount, target_is_directory=True)
    monkeypatch.setenv('AGENT_RUNNER_STORAGE_ROOT', str(configured))
    found = resolve_scoped_uploaded_file(good.name, 'current-owner')
    assert found and Path(found['path']) == good and get_conversation_dir('current-owner') == conversation
    outside = tmp_path / 'external-scene'
    outside.mkdir()
    bad = outside / 'file_fixture_external.txt'
    bad.write_text('fictional external bytes')
    (owner / 'external-scene').symlink_to(outside, target_is_directory=True)
    assert resolve_scoped_uploaded_file(bad.name, 'current-owner', metadata={'path': str(bad)}) is None


@pytest.mark.parametrize('component', ['tenants', 'conversation'])
def test_owner_parent_or_conversation_symlink_cannot_materialize_foreign_write_namespace(tmp_path, monkeypatch, component):
    base = tmp_path / 'shared'
    external = tmp_path / 'external-root'
    external.mkdir()
    base.mkdir()
    if component == 'tenants':
        (base / 'tenants').symlink_to(external, target_is_directory=True)
    else:
        own = base / 'tenants' / 'current-owner'
        own.mkdir(parents=True)
        (own / 'conversation').symlink_to(external, target_is_directory=True)
    monkeypatch.setenv('AGENT_RUNNER_STORAGE_ROOT', str(base))
    with pytest.raises(ValueError):
        get_conversation_dir('current-owner')
    assert list(external.iterdir()) == []
