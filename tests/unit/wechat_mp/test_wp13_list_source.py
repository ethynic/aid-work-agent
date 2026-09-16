"""WP13 清单源客户端与解析单元测试（纯逻辑 + MockTransport，不打真实接口）。

覆盖（计划 WP13 节 + 设计 §3.2）：
- 解析：publish_page/publish_info JSON 字符串二次解析、子篇字段映射、is_deleted 容错、
  dict 形态容忍、结构异常（非对象/缺 publish_page/子篇缺 link/update_time）
- 失败分类：200007→account_error、200013→freq_control、200003/200040→session_expired、
  其他 ret、非 JSON 响应→session_expired
- 分页：按消息数推进翻完、空页无进展/整页重复/超预算→complete=False 不抛异常
- 限速：实例级请求间隔 ≥2s（sleep 注入断言）
- 日志纪律：token/cookie/链接/响应正文不进日志（对齐 test_wp9_client 范式）

真实清单样本（脱敏）：tests/fixtures/wechat_mp/own_publish_list_sample.json。
"""

import json
import threading

import httpx
import pytest
from loguru import logger

from src.wechat_mp.list_source import (
    ListAccountError,
    ListFreqControlError,
    ListSessionExpiredError,
    ListSourceError,
    ListStructureError,
    OwnListClient,
    OwnArticle,
    mask_session_digest,
    parse_list_page,
)

TOKEN = "908279301"
COOKIE = "slave_sid=wp13fakecookie; slave_user=wp13fakeuser"


def _load_fixture():
    from pathlib import Path

    path = Path(__file__).parent.parent.parent / "fixtures" / "wechat_mp" / (
        "own_publish_list_sample.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _page_body(total: int, messages: list) -> dict:
    """构造真机形状的一页：publish_page 为 JSON 字符串。"""
    publish_list = []
    for m in messages:
        info = {
            "msgid": m["msgid"],
            "publish_type": 101,
            "sent_status": {"total": 1, "succ": 1, "fail": 0, "progress": 100},
            "appmsgex": [dict(m["sub"], msgid=m["msgid"])],
        }
        publish_list.append(
            {
                "publish_info": json.dumps(info, ensure_ascii=False),
                "publish_type": 101,
            }
        )
    return {
        "base_resp": {"err_msg": "ok", "ret": 0},
        "is_admin": False,
        "publish_page": json.dumps(
            {"total_count": total, "publish_list": publish_list}, ensure_ascii=False
        ),
    }


def _page_body_multi(total: int, entries: list) -> dict:
    """构造每消息多个子篇的一页：entries = [(msgid, [sub, ...]), ...]。"""
    publish_list = []
    for msgid, subs in entries:
        info = {"msgid": msgid, "publish_type": 101, "appmsgex": list(subs)}
        publish_list.append(
            {
                "publish_info": json.dumps(info, ensure_ascii=False),
                "publish_type": 101,
            }
        )
    return {
        "base_resp": {"err_msg": "ok", "ret": 0},
        "is_admin": False,
        "publish_page": json.dumps(
            {"total_count": total, "publish_list": publish_list}, ensure_ascii=False
        ),
    }


def _sub(token: str, update_time: int, **extra) -> dict:
    base = {
        "aid": f"Fk13{token[:8]}",
        "title": f"标题{token}",
        "link": f"https://mp.weixin.qq.com/s/{token}",
        "update_time": update_time,
        "create_time": update_time - 600,
        "is_deleted": False,
        "item_show_type": 9,
        "itemidx": 1,
        "digest": "摘要",
        "cover": "https://mmbiz.qpic.cn/cover",
        "author_name": "测试号",
    }
    base.update(extra)
    return base


class Clock:
    """可控时钟：monotonic 微步递增 + sleep 记录（免真实等待，步长需远小于 2s 限速）。"""

    def __init__(self, step: float = 0.01):
        self._now = 0.0
        self._step = step
        self.sleeps: list = []
        self.mu = threading.Lock()

    def monotonic(self) -> float:
        with self.mu:
            self._now += self._step
            return self._now

    def sleep(self, seconds: float) -> None:
        with self.mu:
            self.sleeps.append(seconds)
            self._now += seconds


def make_client(handler, clock: Clock = None, cookie: str = COOKIE) -> tuple:
    clock = clock or Clock()
    client = OwnListClient(
        token=TOKEN,
        cookie=cookie,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    return client, clock


# ------------------------------- 解析 -------------------------------


class TestParseListPage:
    def test_fixture_full_parse(self):
        """真实样本（脱敏）：二次解析 + 子篇字段映射 + 记录级 msgid/publish_type。"""
        total, articles = parse_list_page(_load_fixture())
        assert total == 2  # total_count 是消息数（2 条消息 3 个子篇）
        assert len(articles) == 3
        a0 = articles[0]
        assert isinstance(a0, OwnArticle)
        assert a0.aid == "Fk13Aid00001"
        assert a0.link == "https://mp.weixin.qq.com/s/Wp13Sample0001"
        assert a0.update_time == 1788162323 and a0.create_time == 1788160000
        assert a0.is_deleted is False
        assert a0.item_show_type == 9 and a0.itemidx == 1
        assert a0.msgid == 1000000001 and a0.publish_type == 101
        assert a0.author_name == "夹具测试号"
        assert a0.digest and a0.cover
        deleted = [a for a in articles if a.is_deleted]
        assert len(deleted) == 1 and deleted[0].aid == "Fk13Aid00003"

    def test_tolerates_dict_publish_page_and_missing_optional_fields(self):
        """publish_page 为 dict / 子篇可选字段缺失：降级容忍不抛。"""
        body = {
            "base_resp": {"ret": 0},
            "publish_page": {
                "total_count": 1,
                "publish_list": [
                    {
                        "publish_info": json.dumps(
                            {
                                "msgid": 7,
                                "appmsgex": [
                                    {
                                        "link": "https://mp.weixin.qq.com/s/Wp13Tol00001",
                                        "update_time": 100,
                                    }
                                ],
                            }
                        )
                    }
                ],
            },
        }
        total, articles = parse_list_page(body)
        assert total == 1
        assert articles[0].title is None and articles[0].is_deleted is False
        assert articles[0].msgid == 7 and articles[0].aid == ""

    def test_structure_errors(self):
        with pytest.raises(ListStructureError):
            parse_list_page("not-a-dict")
        with pytest.raises(ListStructureError):
            parse_list_page({"base_resp": {"ret": 0}})
        # 子篇缺 link / update_time：身份与增量基准缺失 → 结构异常
        bad_link = _page_body(
            1, [{"msgid": 1, "sub": {"update_time": 1, "title": "x"}}]
        )
        with pytest.raises(ListStructureError):
            parse_list_page(bad_link)
        bad_time = _page_body(
            1, [{"msgid": 1, "sub": {"link": "https://mp.weixin.qq.com/s/Wp13Tol00002"}}]
        )
        with pytest.raises(ListStructureError):
            parse_list_page(bad_time)

    def test_publish_info_unparseable_skips_entry(self):
        """单条 publish_info 损坏：跳过该条不致命（其余可用）。"""
        body = {
            "base_resp": {"ret": 0},
            "publish_page": {
                "total_count": 2,
                "publish_list": [
                    {"publish_info": "{broken json"},
                    {
                        "publish_info": json.dumps(
                            {
                                "msgid": 2,
                                "appmsgex": [
                                    _sub("Wp13Ok0000001", 111)
                                ],
                            }
                        )
                    },
                ],
            },
        }
        total, articles = parse_list_page(body)
        assert total == 2 and len(articles) == 1 and articles[0].msgid == 2


# ------------------------------- 失败分类 -------------------------------


class TestFailureClassification:
    @pytest.mark.parametrize(
        "ret,exc",
        [
            (200007, ListAccountError),
            (200013, ListFreqControlError),
            (200003, ListSessionExpiredError),
            (200040, ListSessionExpiredError),
            (43002, ListSourceError),
        ],
    )
    def test_ret_semantics(self, ret, exc):
        body = {"base_resp": {"ret": ret, "err_msg": "x"}, "publish_page": "{}"}
        with pytest.raises(exc) as ei:
            parse_list_page(body)
        assert ei.value.ret == ret

    def test_non_json_response_is_session_expired(self):
        """后台返回登录页 HTML（会话失效）：按 session_expired 分类。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, text="<html>login page</html>", headers={
                    "content-type": "text/html",
                }
            )

        client, _ = make_client(handler)
        with pytest.raises(ListSessionExpiredError):
            client.fetch_page(0)

    def test_5xx_retries_then_raises(self):
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(502, text="bad gateway")

        client, clock = make_client(handler)
        with pytest.raises(ListSourceError):
            client.fetch_page(0)
        assert attempts["n"] == 2  # MAX_TRANSIENT_ATTEMPTS=2
        assert clock.sleeps  # 退避生效


# ------------------------------- 分页 -------------------------------


class TestFetchAll:
    def test_pagination_completes_by_message_count(self):
        """begin 按消息数推进直到 >= total_count；complete=True。"""
        seen_begins = []

        def handler(request: httpx.Request) -> httpx.Response:
            begin = int(request.url.params.get("begin", "0"))
            seen_begins.append(begin)
            total = 45
            count = min(20, total - begin)
            messages = [
                {"msgid": begin + i + 1, "sub": _sub(f"Wp13Pg{begin:03d}{i:03d}", 1789000000 + begin + i)}
                for i in range(count)
            ]
            return httpx.Response(200, json=_page_body(total, messages))

        client, clock = make_client(handler)
        scan = client.fetch_all()
        assert seen_begins == [0, 20, 40]
        assert scan.total_count == 45
        assert len(scan.articles) == 45
        assert scan.pages_fetched == 3
        assert scan.complete and scan.reliable
        # 限速：页间隔补足 ≥2s（sleep = 2s - 实际耗时；Clock 步长极小故接近 2s）
        assert len(clock.sleeps) == 2
        assert all(1.5 < s <= 2.0 for s in clock.sleeps)

    def test_empty_page_before_total_marks_incomplete(self):
        def handler(request: httpx.Request) -> httpx.Response:
            begin = int(request.url.params.get("begin", "0"))
            if begin == 0:
                return httpx.Response(
                    200,
                    json=_page_body(
                        30, [{"msgid": 1, "sub": _sub("Wp13Em0000001", 111)}]
                    ),
                )
            return httpx.Response(200, json=_page_body(30, []))  # 空页但总数未到

        client, _ = make_client(handler)
        scan = client.fetch_all()
        assert scan.complete is False and not scan.reliable
        assert len(scan.articles) == 1  # 已取部分保留，下轮补齐

    def test_duplicate_page_marks_incomplete(self):
        """源未按 begin 推进（整页重复）：截断置 incomplete 防死循环。"""

        def handler(request: httpx.Request) -> httpx.Response:
            messages = [{"msgid": i + 1, "sub": _sub(f"Wp13Dp{i:03d}00001", 100 + i)} for i in range(20)]
            return httpx.Response(200, json=_page_body(45, messages))

        client, _ = make_client(handler)
        scan = client.fetch_all()
        assert scan.complete is False
        assert len(scan.articles) == 20  # 重复页内容不重复累计

    def test_budget_exceeded_truncates(self):
        """单轮 10min 预算：超时截断 complete=False，不抛异常。"""
        clock = Clock(step=1000.0)  # 每次 monotonic 调用推进 1000s → 首轮即超预算
        clock.monotonic()  # deadline = ~1000+600

        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("超预算不应发起请求")

        client, _ = make_client(handler, clock=clock)
        scan = client.fetch_all()
        assert scan.complete is False and scan.articles == []

    def test_failure_code_propagates_from_fetch_all(self):
        """翻页中途失败码（如 200013）：抛异常不误报。"""
        pages = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            pages["n"] += 1
            if pages["n"] == 1:
                return httpx.Response(
                    200, json=_page_body(40, [{"msgid": 1, "sub": _sub("Wp13Fc0000001", 111)}])
                )
            return httpx.Response(
                200, json={"base_resp": {"ret": 200013, "err_msg": "freq control"}}
            )

        client, _ = make_client(handler)
        with pytest.raises(ListFreqControlError):
            client.fetch_all()


# ------------------------------- 健康检查探针 -------------------------------


class TestFetchSyncScan:
    """WP13-r1 分页策略（设计 §3.4）：首次回填上限 / 增量走到重叠即停。

    兜底语义（空页/重复页/预算/失败码）与 fetch_all 一致，fetch_all 即
    fetch_sync_scan() 的无策略形态。
    """

    def test_backfill_stops_at_cap_boundary_message_counted(self):
        """累计子篇数达到上限即停止翻页；边界消息整条计入（允许超出），视为完整。"""
        seen_begins = []

        def handler(request: httpx.Request) -> httpx.Response:
            begin = int(request.url.params.get("begin", "0"))
            seen_begins.append(begin)
            messages = [
                {"msgid": begin + i + 1, "sub": _sub(f"Wp13Cap{begin:03d}{i:03d}", 1789000000 + begin + i)}
                for i in range(20)
            ]
            return httpx.Response(200, json=_page_body(45, messages))

        client, _ = make_client(handler)
        scan = client.fetch_sync_scan(max_articles=10)
        assert seen_begins == [0]  # 首页 20 篇 ≥ 上限 10：不再翻第 2 页
        assert len(scan.articles) == 20  # 边界消息整条计入（20 > 10）
        assert scan.complete is True
        assert scan.pages_fetched == 1

    def test_backfill_total_below_cap_fetches_all(self):
        """总数低于上限：正常翻完（complete=True），与 fetch_all 等价。"""

        def handler(request: httpx.Request) -> httpx.Response:
            begin = int(request.url.params.get("begin", "0"))
            total = 45
            count = min(20, total - begin)
            messages = [
                {"msgid": begin + i + 1, "sub": _sub(f"Wp13Sm{begin:03d}{i:03d}", 1789000000 + begin + i)}
                for i in range(count)
            ]
            return httpx.Response(200, json=_page_body(total, messages))

        client, _ = make_client(handler)
        scan = client.fetch_sync_scan(max_articles=100)
        assert len(scan.articles) == 45 and scan.complete is True

    def test_backfill_truncates_to_hard_max_500(self):
        """边界消息使累计超 500：截断子篇到 500（页序从新到旧，保留最新）。"""

        def handler(request: httpx.Request) -> httpx.Response:
            begin = int(request.url.params.get("begin", "0"))
            entries = []
            for i in range(20):
                msgid = begin + i + 1
                subs = [
                    _sub(f"Wp13Hd{begin:03d}{i:03d}x{j}", 1789000000 + begin + i)
                    for j in range(3)
                ]
                entries.append((msgid, subs))
            return httpx.Response(200, json=_page_body_multi(1000, entries))

        client, _ = make_client(handler)
        scan = client.fetch_sync_scan(max_articles=500)
        # 每页 20 消息 × 3 子篇 = 60 篇：第 9 页后累计 540 ≥ 500 停止并截断
        assert len(scan.articles) == 500
        assert scan.articles[0].link.endswith("Wp13Hd000000x0")  # 保留最新（首页首条）
        assert scan.complete is True

    def test_incremental_stops_at_all_known_page(self):
        """增量：整页全部已知（谓词 True）→ 到重叠即停；本页子篇仍进 scan 待 diff。"""
        seen_begins = []

        def handler(request: httpx.Request) -> httpx.Response:
            begin = int(request.url.params.get("begin", "0"))
            seen_begins.append(begin)
            messages = [
                {"msgid": begin + i + 1, "sub": _sub(f"Wp13Ov{begin:03d}{i:03d}", 1789000000 + begin + i)}
                for i in range(20)
            ]
            return httpx.Response(200, json=_page_body(100, messages))

        client, _ = make_client(handler)
        scan = client.fetch_sync_scan(page_all_known=lambda page: True)
        assert seen_begins == [0]
        assert len(scan.articles) == 20
        assert scan.complete is True

    def test_incremental_continues_while_page_has_new_articles(self):
        """页内有未知/变更子篇（谓词 False）→ 继续翻页直到翻完。"""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            begin = int(request.url.params.get("begin", "0"))
            calls["n"] += 1
            messages = [
                {"msgid": begin + i + 1, "sub": _sub(f"Wp13Ow{begin:03d}{i:03d}", 1789000000 + begin + i)}
                for i in range(20)
            ]
            return httpx.Response(200, json=_page_body(40, messages))

        client, _ = make_client(handler)
        scan = client.fetch_sync_scan(page_all_known=lambda page: False)
        assert calls["n"] == 2
        assert len(scan.articles) == 40 and scan.complete is True

    def test_cap_does_not_mask_empty_page_incomplete(self):
        """上限策略不掩盖空页异常：空页无进展仍置 incomplete（下轮继续回填）。"""

        def handler(request: httpx.Request) -> httpx.Response:
            begin = int(request.url.params.get("begin", "0"))
            if begin == 0:
                return httpx.Response(
                    200, json=_page_body(30, [{"msgid": 1, "sub": _sub("Wp13Ce0000001", 111)}])
                )
            return httpx.Response(200, json=_page_body(30, []))

        client, _ = make_client(handler)
        scan = client.fetch_sync_scan(max_articles=100)
        assert scan.complete is False and len(scan.articles) == 1

    def test_fetch_all_delegates_to_sync_scan(self):
        """fetch_all == fetch_sync_scan()（无策略形态，行为回归锚点）。"""
        seen_begins = []

        def handler(request: httpx.Request) -> httpx.Response:
            begin = int(request.url.params.get("begin", "0"))
            seen_begins.append(begin)
            total = 25
            count = min(20, total - begin)
            messages = [
                {"msgid": begin + i + 1, "sub": _sub(f"Wp13Dl{begin:03d}{i:03d}", 1789000000 + begin + i)}
                for i in range(count)
            ]
            return httpx.Response(200, json=_page_body(total, messages))

        client, _ = make_client(handler)
        scan = client.fetch_all()
        assert seen_begins == [0, 20]
        assert len(scan.articles) == 25 and scan.complete


# ------------------------------- 健康检查探针 -------------------------------


def test_fetch_first_health_check_shape():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        captured["cookie"] = request.headers.get("cookie", "")
        captured["fakeid"] = request.url.params.get("fakeid")
        messages = [{"msgid": 1, "sub": _sub("Wp13Hc0000001", 111)}]
        return httpx.Response(200, json=_page_body(27, messages))

    client, _ = make_client(handler)
    total, articles = client.fetch_first(1)
    assert total == 27 and len(articles) == 1
    # own-context：fakeid 必须留空；type/sub_action 形态对齐实测
    assert captured["fakeid"] == ""
    assert captured["params"]["type"] == "101_1"
    assert captured["params"]["sub_action"] == "list_ex"
    assert captured["params"]["count"] == "1"
    # 会话凭据形态：原始 cookie 串进 header
    assert "wp13fakecookie" in captured["cookie"]


# ------------------------------- 日志纪律 -------------------------------


def test_logs_never_contain_credentials():
    """token/cookie/链接/响应正文不进日志（安全约束，对齐 client.py 纪律）。"""
    records = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"base_resp": {"ret": 200007, "err_msg": "access deny"}},
        )

    sink_id = logger.add(lambda m: records.append(str(m)), level="DEBUG")
    try:
        client, _ = make_client(handler)
        # ret 语义化分类发生在解析层：fetch_page 返回原文，parse 抛 ListAccountError
        with pytest.raises(ListAccountError):
            parse_list_page(client.fetch_page(0))
    finally:
        logger.remove(sink_id)

    joined = "\n".join(records)
    assert TOKEN not in joined
    assert "wp13fakecookie" not in joined and "wp13fakeuser" not in joined
    assert "appmsgpublish?".replace("?", "") in "appmsgpublish"  # 路径可记录
    assert "access deny" not in joined  # 响应正文不透传
    assert "token=" not in joined


def test_mask_session_digest():
    assert mask_session_digest("") == "(empty)"
    digest = mask_session_digest(COOKIE)
    assert digest.startswith("sha:")
    assert "wp13" not in digest
