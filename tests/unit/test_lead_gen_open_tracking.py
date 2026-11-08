"""ai-lead-gen 邮件打开追踪（D 项）单元测试。

覆盖：tracking_key 生成/持久化/手动覆盖校验、fetch_opens 游标护栏与聚合、
服务端 /api/track 端点的 key 解析与响应逻辑。数据库一律 mock 或走尽力而为路径。
"""

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

SKILL_DIR = Path(__file__).resolve().parents[2] / "src" / "skills" / "ai-lead-gen-1.0.0" / "scripts"
sys.path.insert(0, str(SKILL_DIR))

import tracking_key  # noqa: E402
import fetch_opens  # noqa: E402
from src.api.track import parse_tracking_key, _clean, router as track_router  # noqa: E402


# ---------- tracking_key ----------

@pytest.fixture()
def key_store(tmp_path, monkeypatch):
    monkeypatch.setattr(tracking_key, "_tenant_dir", lambda: tmp_path)
    monkeypatch.setattr(tracking_key, "_cache", {})
    return tmp_path


class TestTrackingKey:
    def test_generate_and_persist(self, key_store):
        t, s, key = tracking_key.get_or_create_key("Outreach@Example.com ")
        assert len(t) == 8 and len(s) == 32
        assert key == f"{t}.{s}"
        # 同一邮箱重复取：同一把 key（持久化 + 缓存）
        t2, s2, key2 = tracking_key.get_or_create_key("outreach@example.com")
        assert (t, s, key) == (t2, s2, key2)
        # 落盘在租户目录
        assert (key_store / "tracking_keys.json").exists()

    def test_override_requires_hex_format(self):
        # 服务端校验口径：hex8.hex32
        t, s, key = tracking_key.get_or_create_key(
            "a@b.com", override="abc123de." + "0" * 32)
        assert t == "abc123de" and s == "0" * 32 and key == f"{t}.{s}"
        with pytest.raises(SystemExit):
            tracking_key.get_or_create_key("a@b.com", override="mytenant.mysecret")
        with pytest.raises(SystemExit):
            tracking_key.get_or_create_key("a@b.com", override="abc123de.short")

    def test_missing_from_email_fail_fast(self):
        with pytest.raises(SystemExit):
            tracking_key.get_or_create_key("", None)

    def test_tenant_dir_requires_identity(self, monkeypatch):
        monkeypatch.delenv("AID_TENANT_ID", raising=False)
        monkeypatch.delenv("CURRENT_TENANT_ID", raising=False)
        with pytest.raises(SystemExit):
            tracking_key._tenant_dir()


# ---------- fetch_opens ----------

class TestFetchOpens:
    def test_split_key(self):
        assert fetch_opens.split_key("abcd1234." + "f" * 32) == ("abcd1234", "f" * 32)
        with pytest.raises(SystemExit):
            fetch_opens.split_key("no-dot")
        with pytest.raises(SystemExit):
            fetch_opens.split_key("")

    def test_norm_message_id(self):
        assert fetch_opens.norm_message_id("<abc@x.com>") == "abc@x.com"
        assert fetch_opens.norm_message_id("  <a@b>  ") == "a@b"
        assert fetch_opens.norm_message_id(None) == ""

    def test_aggregate_counts_and_latest(self):
        records = [
            {"message_id": "<m1@x>", "opened_at": "2026-11-08T10:00:00"},
            {"message_id": "m1@x", "opened_at": "2026-11-08T11:00:00"},
            {"message_id": "<m2@x>", "opened_at": "2026-11-07T09:00:00"},
            {"message_id": "", "opened_at": "2026-11-07T09:00:00"},  # 缺 id 忽略
        ]
        agg = fetch_opens.aggregate(records)
        assert agg["m1@x"]["open_count"] == 2
        assert agg["m1@x"]["opens_at"] == "2026-11-08T11:00:00"
        assert agg["m2@x"]["open_count"] == 1
        assert len(agg) == 2

    def test_fetch_all_cursor_guards(self, monkeypatch):
        # 游标不推进 -> 立即停止（防服务端异常时无限翻页）
        pages = [{"opens": [{"id": 1}], "next_cursor": "5"},
                 {"opens": [{"id": 2}], "next_cursor": "5"}]
        calls = []

        def fake_fetch_page(endpoint, key, limit, cursor, campaign_filter=""):
            calls.append(cursor)
            return pages[len(calls) - 1]

        monkeypatch.setattr(fetch_opens, "fetch_page", fake_fetch_page)
        rows = fetch_opens.fetch_all("http://x", "abcd1234." + "f" * 32)
        assert len(rows) == 2
        assert len(calls) == 2  # 第二页游标未推进即停

    def test_fetch_all_full_pagination(self, monkeypatch):
        pages = [{"opens": [{"id": 1}], "next_cursor": "1"},
                 {"opens": [{"id": 2}], "next_cursor": "2"},
                 {"opens": [], "next_cursor": ""}]

        def fake_fetch_page(endpoint, key, limit, cursor, campaign_filter=""):
            return pages[cursor]

        monkeypatch.setattr(fetch_opens, "fetch_page", fake_fetch_page)
        rows = fetch_opens.fetch_all("http://x", "abcd1234." + "f" * 32)
        assert [r["id"] for r in rows] == [1, 2]


# ---------- 服务端 /api/track ----------

client = TestClient(FastAPI())
client.app.include_router(track_router)


class TestTrackApi:
    def test_parse_tracking_key(self):
        assert parse_tracking_key("abcd1234." + "f" * 32) == ("abcd1234", "f" * 32)
        assert parse_tracking_key("ABCD1234." + "F" * 32) == ("abcd1234", "f" * 32)
        assert parse_tracking_key("short.1234") is None
        assert parse_tracking_key("mytenant.mysecret") is None
        assert parse_tracking_key("") is None
        assert parse_tracking_key("x" * 100) is None

    def test_clean_truncates(self):
        assert _clean("  ab ", 10) == "ab"
        assert _clean("x" * 300, 255) == "x" * 255
        assert _clean(None, 10) == ""

    def test_pixel_invalid_key_still_gif(self):
        # 非法 key 静默丢弃（不写库），仍返回图片避免收件端坏图重试
        resp = client.get("/api/track/p.gif", params={"k": "bad", "m": "m1"})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/gif"
        assert resp.headers["cache-control"] == "no-store"
        assert len(resp.content) == 42

    def test_pixel_missing_message_id_still_gif(self):
        resp = client.get("/api/track/p.gif",
                          params={"k": "abcd1234." + "f" * 32})
        assert resp.status_code == 200
        assert len(resp.content) == 42

    def test_pixel_valid_params_insert(self, monkeypatch):
        seen = {}
        monkeypatch.setattr("src.api.track._insert_open",
                            lambda *args: seen.update(args=args))
        resp = client.get("/api/track/p.gif", params={
            "k": "abcd1234." + "f" * 32, "c": "camp1", "m": "m1@x.com",
            "r": "a@b.com", "t": "erp11095",
        }, headers={"user-agent": "TestUA"})
        assert resp.status_code == 200 and len(resp.content) == 42
        assert seen["args"] == ("abcd1234", "erp11095", "camp1", "m1@x.com",
                                "a@b.com", "TestUA",
                                seen["args"][6])  # ip 位由运行环境决定
        # 非法 t 参数被忽略（不写库路径走 None 归属）
        monkeypatch.setattr("src.api.track._insert_open",
                            lambda *args: seen.update(args=args))
        client.get("/api/track/p.gif", params={
            "k": "abcd1234." + "f" * 32, "m": "m1@x.com", "t": "bad tenant!",
        })
        assert seen["args"][1] is None

    def test_opens_auth_rejects_secret_mismatch(self):
        resp = client.get("/api/track/opens",
                          params={"k": "abcd1234." + "f" * 32, "secret": "wrong"})
        assert resp.status_code == 403
        resp = client.get("/api/track/opens",
                          params={"k": "bad", "secret": "bad"})
        assert resp.status_code == 403
        resp = client.get("/api/track/opens",
                          params={"k": "abcd1234." + "f" * 32})
        assert resp.status_code == 403  # 缺 secret 一律拒绝

    def test_opens_cursor_pagination(self, monkeypatch):
        rows = [{"id": i, "campaign": "c1", "message_id": f"m{i}",
                 "recipient": "a@b.com", "opened_at": None, "user_agent": "UA"}
                for i in range(1, 6)]
        monkeypatch.setattr("src.api.track._fetch_opens",
                            lambda t, c, cursor, limit: rows[cursor:cursor + limit])
        key = "abcd1234." + "f" * 32
        # 满页：next_cursor 单调递增
        data = client.get("/api/track/opens",
                          params={"k": key, "secret": "f" * 32, "limit": 3}).json()
        assert data["count"] == 3
        assert data["next_cursor"] == "3"
        assert data["opens"][0]["id"] == 1
        # 不足一页：next_cursor 为空串（客户端停止翻页）
        data = client.get("/api/track/opens",
                          params={"k": key, "secret": "f" * 32, "limit": 10}).json()
        assert data["count"] == 5
        assert data["next_cursor"] == ""
        # campaign 过滤参数透传
        seen = {}
        def spy(t, c, cursor, limit):
            seen["campaign"] = c
            return []
        monkeypatch.setattr("src.api.track._fetch_opens", spy)
        client.get("/api/track/opens",
                   params={"k": key, "secret": "f" * 32, "c": "camp1"})
        assert seen["campaign"] == "camp1"
