"""跨轮 workspace 旧路径改写（workspace_path_rewriter）单元测试。

覆盖：
- 命中反查索引改写为 file_id / 未命中失效标记
- Redis 异常全降级、tenant 不一致按未命中降级
- parts list 改写且 runner_image 部件不动
- tool JSON content 改写后仍可 json.loads
- 原消息 dict 不被就地修改（ShortTermMemory 缓存共享引用保护）
- 快筛未命中零 Redis 调用
- 失效标记文件名危险字符过滤
- is_workspace_path 判定

所有外部依赖 mock：get_cached/set_cached 直接 monkeypatch 模块引用。
"""

import json
from unittest.mock import MagicMock

import pytest

from src.services.agent_runner.runtime import workspace_path_rewriter as rew

_NSHA = "a" * 64
WS_DIR = f"/app/storage/agent_runner/workspaces/{_NSHA}/skill_ws_abc123"
WS_PATH = f"{WS_DIR}/餐桌.png"
WS_PATH2 = f"{WS_DIR}/空餐厅.png"


def _hit(file_id="file_hit12345678", tenant="tenant-1"):
    return json.dumps({"file_id": file_id, "name": "餐桌.png",
                       "tenant_id": tenant, "registered_at": "2026-11-10T10:00:00"})


# ============================================================
# is_workspace_path
# ============================================================

class TestIsWorkspacePath:
    def test_absolute_workspace_path(self):
        assert rew.is_workspace_path(WS_PATH) is True

    def test_workspace_path_with_backtick_wrapping(self):
        assert rew.is_workspace_path(f"Full path: `{WS_PATH}`") is True

    def test_normal_path(self):
        assert rew.is_workspace_path("/app/storage/tenants/t1/conversation/a.png") is False

    def test_relative_workspaces_fragment(self):
        assert rew.is_workspace_path("workspaces/aaa/skill_ws_x/a.png") is False

    def test_empty(self):
        assert rew.is_workspace_path("") is False


# ============================================================
# rewrite_history_workspace_paths
# ============================================================

class TestRewriteHistory:
    @pytest.mark.asyncio
    async def test_index_hit_rewrites_to_file_id(self, monkeypatch):
        monkeypatch.setattr(rew, "get_cached", MagicMock(return_value=_hit()))
        msgs = [{"role": "user", "content": f"Full path: `{WS_PATH}`"}]
        out = await rew.rewrite_history_workspace_paths(msgs, "tenant-1")
        assert out[0]["content"] == "Full path: `file_hit12345678`"

    @pytest.mark.asyncio
    async def test_index_miss_marks_stale(self, monkeypatch):
        monkeypatch.setattr(rew, "get_cached", MagicMock(return_value=None))
        msgs = [{"role": "user", "content": f"参考 {WS_PATH} 与 {WS_PATH2}"}]
        out = await rew.rewrite_history_workspace_paths(msgs, "tenant-1")
        content = out[0]["content"]
        assert WS_PATH not in content and WS_PATH2 not in content
        assert "[已失效:临时文件 餐桌.png 随上一轮工作区清理删除" in content
        assert "[已失效:临时文件 空餐厅.png 随上一轮工作区清理删除" in content

    @pytest.mark.asyncio
    async def test_redis_error_degrades_to_stale(self, monkeypatch):
        boom = MagicMock(side_effect=RuntimeError("redis down"))
        monkeypatch.setattr(rew, "get_cached", boom)
        msgs = [{"role": "user", "content": WS_PATH}]
        out = await rew.rewrite_history_workspace_paths(msgs, "tenant-1")
        assert "[已失效:" in out[0]["content"]

    @pytest.mark.asyncio
    async def test_tenant_mismatch_degrades_to_stale(self, monkeypatch):
        monkeypatch.setattr(rew, "get_cached", MagicMock(return_value=_hit(tenant="tenant-other")))
        msgs = [{"role": "user", "content": WS_PATH}]
        out = await rew.rewrite_history_workspace_paths(msgs, "tenant-1")
        assert "[已失效:" in out[0]["content"]

    @pytest.mark.asyncio
    async def test_parts_list_rewrites_text_keeps_runner_image(self, monkeypatch):
        monkeypatch.setattr(rew, "get_cached", MagicMock(return_value=_hit()))
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": f"路径 {WS_PATH}"},
            {"type": "runner_image", "name": "x.png", "mime_type": "image/png",
             "size_bytes": 10, "sha256": "deadbeef"},
        ]}]
        out = await rew.rewrite_history_workspace_paths(msgs, "tenant-1")
        parts = out[0]["content"]
        assert parts[0]["text"] == "路径 file_hit12345678"
        assert parts[1]["type"] == "runner_image"
        assert parts[1]["sha256"] == "deadbeef"

    @pytest.mark.asyncio
    async def test_tool_json_content_stays_parseable(self, monkeypatch):
        monkeypatch.setattr(rew, "get_cached", MagicMock(return_value=None))
        payload = json.dumps({"success": False,
                              "error": f"参考图文件不存在: {WS_PATH}"}, ensure_ascii=False)
        msgs = [{"role": "tool", "tool_call_id": "call1", "content": payload}]
        out = await rew.rewrite_history_workspace_paths(msgs, "tenant-1")
        parsed = json.loads(out[0]["content"])
        assert parsed["success"] is False
        assert WS_PATH not in parsed["error"]
        assert "[已失效:临时文件" in parsed["error"]

    @pytest.mark.asyncio
    async def test_original_message_not_mutated(self, monkeypatch):
        monkeypatch.setattr(rew, "get_cached", MagicMock(return_value=None))
        content = [
            {"type": "text", "text": f"路径 {WS_PATH}"},
            {"type": "runner_image", "sha256": "ff"},
        ]
        msgs = [{"role": "user", "content": content}]
        await rew.rewrite_history_workspace_paths(msgs, "tenant-1")
        assert content[0]["text"] == f"路径 {WS_PATH}"  # 原对象不变
        assert msgs[0]["content"] is content

    @pytest.mark.asyncio
    async def test_fast_filter_miss_zero_redis_calls(self, monkeypatch):
        get_cached = MagicMock()
        monkeypatch.setattr(rew, "get_cached", get_cached)
        msgs = [
            {"role": "user", "content": "普通对话，无任何路径"},
            {"role": "assistant", "content": [{"type": "text", "text": "回复内容"}]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'},
        ]
        out = await rew.rewrite_history_workspace_paths(msgs, "tenant-1")
        assert out is msgs  # 未命中原样返回同一 list
        get_cached.assert_not_called()

    @pytest.mark.asyncio
    async def test_unsafe_filename_sanitized(self, monkeypatch):
        # 路径提取在引号处截断（引号是路径 token 边界），失效标记内不含危险字符
        monkeypatch.setattr(rew, "get_cached", MagicMock(return_value=None))
        evil = f"{WS_DIR}/evil\"quote\\back.png"
        msgs = [{"role": "user", "content": evil}]
        out = await rew.rewrite_history_workspace_paths(msgs, "tenant-1")
        content = out[0]["content"]
        start = content.index("[已失效:")
        end = content.index("]", start)
        marker = content[start:end + 1]
        assert '"' not in marker and "\\" not in marker
        assert "[已失效:临时文件 evil " in marker
        # JSON 上下文整体可解析
        json.loads(json.dumps({"e": content}, ensure_ascii=False))

    @pytest.mark.asyncio
    async def test_empty_history_returns_as_is(self, monkeypatch):
        get_cached = MagicMock()
        monkeypatch.setattr(rew, "get_cached", get_cached)
        assert await rew.rewrite_history_workspace_paths([], "t1") == []
        get_cached.assert_not_called()

    @pytest.mark.asyncio
    async def test_windows_style_path_also_matched(self, monkeypatch):
        monkeypatch.setattr(rew, "get_cached", MagicMock(return_value=None))
        win = f"D:\\storage\\workspaces\\{_NSHA}\\skill_ws_x\\plan.png"
        msgs = [{"role": "user", "content": win}]
        out = await rew.rewrite_history_workspace_paths(msgs, "tenant-1")
        assert "[已失效:" in out[0]["content"]


# ============================================================
# index_workspace_file
# ============================================================

class TestIndexWorkspaceFile:
    def test_write_index(self, monkeypatch):
        recorded = {}

        def fake_set(prefix, ident, value, ttl):
            recorded["prefix"], recorded["ident"], recorded["value"], recorded["ttl"] = \
                prefix, ident, value, ttl

        monkeypatch.setattr(rew, "set_cached", fake_set)
        rew.index_workspace_file(WS_PATH, "file_abc", "餐桌.png", "tenant-1")
        import hashlib
        assert recorded["prefix"] == rew.CacheKeys.WORKSPACE_FILE_INDEX
        assert recorded["ident"] == hashlib.sha256(WS_PATH.encode()).hexdigest()
        assert recorded["ttl"] == 86400
        data = json.loads(recorded["value"])
        assert data["file_id"] == "file_abc"
        assert data["tenant_id"] == "tenant-1"

    def test_write_failure_not_raised(self, monkeypatch):
        monkeypatch.setattr(rew, "set_cached", MagicMock(side_effect=RuntimeError("down")))
        rew.index_workspace_file(WS_PATH, "file_abc", "餐桌.png", "tenant-1")  # 不抛
