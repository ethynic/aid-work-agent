"""Phase 2：轻重负载判型与领取角色过滤单测。

纯函数测试覆盖判型三分支与信号扫描；SQL 测试捕获 execute 的语句与参数，
断言 light 角色过滤条件存在（general 不过滤、heavy 行对轻池不可见）。
"""

from types import SimpleNamespace

import pytest

from src.services.agent_runner import load_class


def _patch_heavy_sets(monkeypatch, tools, skills):
    monkeypatch.setattr(load_class, 'heavy_sets', lambda: (set(tools), set(skills)))


class _ProfileConfig:
    def __init__(self, tools=None, skills=None):
        self._tools, self._skills = tools or [], skills or []
    def get_allowed_tools(self):
        return list(self._tools)
    def get_allowed_skills(self):
        return list(self._skills)


class TestSkillBaseName:
    def test_strips_version_suffix(self):
        assert load_class.skill_base_name('interior-render-1.0.0') == 'interior-render'
        assert load_class.skill_base_name('pptx-vi-gen-2.2.0') == 'pptx-vi-gen'

    def test_plain_name_unchanged(self):
        assert load_class.skill_base_name('interior-render') == 'interior-render'

    def test_hyphenated_name_without_version_unchanged(self):
        # 尾段非纯数字/点，视为名称本身的一部分（如 'quote-price-comparison'）
        assert load_class.skill_base_name('quote-price-comparison') == 'quote-price-comparison'

    def test_numeric_only_tail_treated_as_version(self):
        assert load_class.skill_base_name('demo-2') == 'demo'

    def test_single_token_unchanged(self):
        assert load_class.skill_base_name('single') == 'single'


class TestClassifyProfile:
    def test_main_profile_without_config_is_light(self, monkeypatch):
        _patch_heavy_sets(monkeypatch, ['excel_process'], ['interior-render'])
        assert load_class.classify_profile('main', None) == 'light'

    def test_empty_lists_default_to_light(self, monkeypatch):
        _patch_heavy_sets(monkeypatch, [], [])
        assert load_class.classify_profile('sales', _ProfileConfig(tools=['excel_process'])) == 'light'

    def test_allowed_tool_intersection_is_heavy(self, monkeypatch):
        _patch_heavy_sets(monkeypatch, ['excel_process'], [])
        config = _ProfileConfig(tools=['excel_process', 'email_send'])
        assert load_class.classify_profile('report', config) == 'heavy'

    def test_allowed_skill_base_name_intersection_is_heavy(self, monkeypatch):
        _patch_heavy_sets(monkeypatch, [], ['interior-render'])
        config = _ProfileConfig(skills=['interior-render-1.0.0', 'followup-tracking-1.0.0'])
        assert load_class.classify_profile('designer', config) == 'heavy'

    def test_disjoint_allowlist_is_light(self, monkeypatch):
        _patch_heavy_sets(monkeypatch, ['excel_process'], ['interior-render'])
        config = _ProfileConfig(tools=['email_send'], skills=['followup-tracking-1.0.0'])
        assert load_class.classify_profile('clerk', config) == 'light'

    def test_config_error_fails_open_to_light(self, monkeypatch):
        _patch_heavy_sets(monkeypatch, ['excel_process'], [])
        class Broken:
            def get_allowed_tools(self):
                raise RuntimeError('fixture')
        assert load_class.classify_profile('x', Broken()) == 'light'


class TestStateUsesHeavy:
    def _state(self, facts):
        return SimpleNamespace(tools=facts)

    def test_heavy_tool_signal(self, monkeypatch):
        _patch_heavy_sets(monkeypatch, ['excel_process'], [])
        fact = SimpleNamespace(call=SimpleNamespace(name='excel_process', arguments={}))
        assert load_class.state_uses_heavy(self._state({'c1': fact})) is True

    def test_plain_tool_signal_is_light(self, monkeypatch):
        _patch_heavy_sets(monkeypatch, ['excel_process'], [])
        fact = SimpleNamespace(call=SimpleNamespace(name='email_send', arguments={}))
        assert load_class.state_uses_heavy(self._state({'c1': fact})) is False

    def test_skill_execute_with_heavy_skill(self, monkeypatch):
        _patch_heavy_sets(monkeypatch, [], ['interior-render'])
        fact = SimpleNamespace(call=SimpleNamespace(name='skill_execute',
            arguments={'skill': 'interior-render-1.0.0'}))
        assert load_class.state_uses_heavy(self._state({'c1': fact})) is True

    def test_skill_execute_with_plain_skill(self, monkeypatch):
        _patch_heavy_sets(monkeypatch, [], ['interior-render'])
        fact = SimpleNamespace(call=SimpleNamespace(name='skill_execute',
            arguments={'skill': 'followup-tracking-1.0.0'}))
        assert load_class.state_uses_heavy(self._state({'c1': fact})) is False

    def test_empty_state_is_light(self, monkeypatch):
        _patch_heavy_sets(monkeypatch, ['excel_process'], ['interior-render'])
        assert load_class.state_uses_heavy(self._state({})) is False
        assert load_class.state_uses_heavy(SimpleNamespace(tools=None)) is False


class TestAcquireRoleFilter:
    """light 角色领取必须带 load_class 过滤；general 角色不过滤。"""

    class _RecordingConnection:
        def __init__(self, recorder):
            self.recorder = recorder
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def cursor(self):
            return self.recorder

    class _Recorder:
        def __init__(self):
            self.calls = []
        def execute(self, sql, params=None):
            self.calls.append((sql, params))
        def fetchone(self):
            return None
        def fetchall(self):
            return []

    def _acquire(self, role, *, priority_only=False):
        recorder = self._Recorder()
        from src.services.agent_runner.execution_repository import ExecutionRepository
        repository = ExecutionRepository(
            connection_factory=lambda: self._RecordingConnection(recorder))
        repository.acquire('worker-a', 30, priority_only=priority_only, role=role)
        return recorder.calls[0]

    @pytest.mark.parametrize('priority_only', [False, True])
    def test_light_role_filters_heavy_rows(self, priority_only):
        sql, params = self._acquire('light', priority_only=priority_only)
        assert "(%s <> 'light' OR r.load_class='light')" in sql
        assert params[0] is priority_only
        assert params[1] == 'light'

    @pytest.mark.parametrize('priority_only', [False, True])
    def test_general_role_sees_every_load_class(self, priority_only):
        sql, params = self._acquire('general', priority_only=priority_only)
        assert "(%s <> 'light' OR r.load_class='light')" in sql
        assert params[1] == 'general'


class TestCandidateRoleFilter:
    """timer/恢复扫描（continuous/resume）同样按角色过滤。"""

    class _Connection:
        def __init__(self, recorder):
            self.recorder = recorder
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def cursor(self):
            return self.recorder

    class _Recorder:
        def __init__(self):
            self.calls = []
        def execute(self, sql, params=None):
            self.calls.append((sql, params))
        def fetchone(self):
            return None
        def fetchall(self):
            return []

    def test_timer_candidates_light_role(self):
        recorder = self._Recorder()
        from src.services.agent_runner.timer_repository import TimerRepository
        TimerRepository(connection_factory=lambda: self._Connection(recorder)).next_candidates(role='light')
        sql, params = recorder.calls[0]
        assert "(%s <> 'light' OR r.load_class='light')" in sql
        assert params[0] == 'light'

    def test_recovery_continuous_light_role(self):
        recorder = self._Recorder()
        from src.services.agent_runner.recovery_repository import RecoveryRepository
        repo = RecoveryRepository(connection_factory=lambda: self._Connection(recorder))
        repo.next_continuous_candidates(role='light')
        sql, params = recorder.calls[0]
        assert "(%s <> 'light' OR r.load_class='light')" in sql
        assert params[0] == 'light'

    def test_recovery_resume_light_role(self):
        recorder = self._Recorder()
        from src.services.agent_runner.recovery_repository import RecoveryRepository
        RecoveryRepository(connection_factory=lambda: self._Connection(recorder)).next_candidates(role='light')
        sql, params = recorder.calls[0]
        assert "(%s <> 'light' OR r.load_class='light')" in sql
        assert params[0] == 'light'


def test_save_checkpoint_promotes_load_class_to_heavy():
    """运行时重判：save_checkpoint 在同事务把 load_class 提升为 heavy（防退化）。"""
    import inspect
    from src.services.agent_runner.execution_repository import ExecutionRepository
    source = inspect.getsource(ExecutionRepository.save_checkpoint)
    assert "load_class=CASE WHEN %s THEN 'heavy' ELSE load_class END" in source
    assert 'heavy_used' in source


def test_worker_declares_role_from_environment():
    """领取角色来自 AGENT_RUNNER_ROLE，五个领取调用点均传 role。"""
    import inspect
    from src.services.agent_runner import worker as worker_module
    source = inspect.getsource(worker_module)
    assert "os.environ.get('AGENT_RUNNER_ROLE', 'general')" in source
    assert source.count('role=self.role') >= 5
