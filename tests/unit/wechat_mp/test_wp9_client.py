"""WP9 freepublish 接口客户端单元测试（纯逻辑，httpx.MockTransport，不打真实接口）。

覆盖（计划 WP9 节 + 设计 §5.3）：
- stable_token：Redis 缓存命中/TTL=expires_in-300、secret 摘要换键、账号级单飞单次刷新
- 40001/42001 失效：删缓存刷一次 token、业务请求重试一次；二次失效立即抛错
- batchget_all：offset 按返回 item_count 推进、重复页/无进展/总数漂移置 incomplete、
  结构异常抛错
- 53600 独立信号（ArticleNotFoundError）、40164 出口 IP 解析（IPWhitelistError）
- 瞬时错误（-1/45009）指数退避重试；权限/参数错误不盲重试
- 日志纪律：token/secret/appid/URL/响应正文不进日志
"""

import hashlib
import json
import threading

import httpx
import pytest
from loguru import logger

from src.wechat_mp.client import (
    ArticleNotFoundError,
    IPWhitelistError,
    WeChatMPAPIError,
    WeChatMPAPIClient,
    friendly_api_error,
    is_truthy_flag,
    parse_whitelist_ip,
)

APPID = "wx1234567890abcdef"
SECRET = "wp9-client-test-secret"
TENANT = "t_wp9_client"
CONFIG = "chan_wp9client01"
TOKEN_PATH = "/cgi-bin/stable_token"
BATCHGET_PATH = "/cgi-bin/freepublish/batchget"
GETARTICLE_PATH = "/cgi-bin/freepublish/getarticle"


# ------------------------------- 测试替身 -------------------------------


class FakeRedis:
    """进程内 Redis 替身（redis_client 接口子集：get/set/delete/锁；JSON 序列化口径一致）。"""

    def __init__(self, available: bool = True):
        self._available = available
        self._store = {}
        self._locks = {}
        self._mu = threading.Lock()
        self.sets = []  # (key, value, ex)
        self.deletes = []

    def is_available(self) -> bool:
        return self._available

    def get(self, key):
        with self._mu:
            raw = self._store.get(key)
        return json.loads(raw) if raw is not None else None

    def set(self, key, value, ex=None):
        with self._mu:
            self._store[key] = json.dumps(value)
            self.sets.append((key, value, ex))

    def delete(self, key):
        with self._mu:
            self.deletes.append(key)
            return 1 if self._store.pop(key, None) is not None else 0

    def acquire_lock(self, key, value, ex=60) -> bool:
        with self._mu:
            if key in self._locks:
                return False
            self._locks[key] = value
            return True

    def release_lock(self, key, value) -> bool:
        with self._mu:
            if self._locks.get(key) == value:
                del self._locks[key]
                return True
            return False


class Clock:
    """可控时钟：monotonic 读数、sleep 记录并推进（免真实等待，退避断言用）。

    step > 0 时每次 monotonic() 调用自动推进（模拟时间流逝，超时预算测试用）。
    """

    def __init__(self, t: float = 1000.0, step: float = 0.0):
        self.t = t
        self.step = step
        self.sleeps = []

    def monotonic(self) -> float:
        now = self.t
        self.t += self.step
        return now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class Handler:
    """MockTransport 处理器：脚本化响应 + 调用记录 (path, access_token, payload)。"""

    def __init__(self):
        self.mu = threading.Lock()
        self.calls = []
        self.token_script = [{"access_token": "TOKEN_A", "expires_in": 7200}]
        self.business_script = {}  # path -> [响应dict]（末元素重复返回）
        self.token_delay = 0.0  # 单飞并发测试用（真实 sleep，仅毫秒级）

    def add(self, path: str, resp: dict) -> None:
        self.business_script.setdefault(path, []).append(resp)

    def _next(self, script: list):
        return script.pop(0) if len(script) > 1 else script[0]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        with self.mu:
            self.calls.append(
                (path, request.url.params.get("access_token"), json.loads(request.content or b"{}"))
            )
        if path == TOKEN_PATH:
            if self.token_delay:
                import time

                time.sleep(self.token_delay)
            return httpx.Response(200, json=self._next(self.token_script))
        return httpx.Response(200, json=self._next(self.business_script.get(path) or [{}]))


def make_client(handler: Handler, redis=None, clock=None, real_sleep: bool = False):
    import time as _time

    clock = clock or Clock()
    client = WeChatMPAPIClient(
        tenant_id=TENANT,
        config_id=CONFIG,
        appid=APPID,
        secret=SECRET,
        redis=redis or FakeRedis(),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=_time.sleep if real_sleep else clock.sleep,
        monotonic=clock.monotonic,
    )
    return client, clock


def _batch_msg(idx: int) -> dict:
    """batchget 消息项（article_id 形态对齐实测：64 字符 token；含首子篇元数据）。"""
    return {
        "article_id": f"ART{idx:05d}" + "x" * 58,
        "update_time": 1789374300 + idx,
        "content": {
            "news_item": [
                {
                    "title": f"标题{idx}",
                    "url": f"https://mp.weixin.qq.com/s?__biz=MP9&mid={idx}&idx=1&sn=sn{idx}",
                }
            ],
            "create_time": 1789374300 + idx - 60,
            "update_time": 1789374300 + idx,
        },
    }


def _page(offset: int, total: int, items: list) -> dict:
    return {"total_count": total, "item_count": len(items), "item": items}


def _expected_cache_key() -> str:
    digest = hashlib.sha256(SECRET.encode("utf-8")).hexdigest()[:8]
    return f"wechat_mp_api:token:{TENANT}:{CONFIG}:{digest}"


# ------------------------------- token 缓存与单飞 -------------------------------


def test_token_cached_with_ttl():
    handler = Handler()
    redis = FakeRedis()
    client, clock = make_client(handler, redis=redis)
    assert client.get_access_token() == "TOKEN_A"
    assert client.get_access_token() == "TOKEN_A"
    token_calls = [c for c in handler.calls if c[0] == TOKEN_PATH]
    assert len(token_calls) == 1  # 第二次命中缓存
    assert len(redis.sets) == 1
    key, value, ex = redis.sets[0]
    assert key == _expected_cache_key()
    assert value == "TOKEN_A"
    assert ex == 7200 - 300  # TTL = expires_in - 300s 安全余量


def test_token_cache_key_changes_with_secret():
    handler = Handler()
    redis = FakeRedis()
    client, _ = make_client(handler, redis=redis)
    other = WeChatMPAPIClient(
        tenant_id=TENANT,
        config_id=CONFIG,
        appid=APPID,
        secret=SECRET + "-rotated",
        redis=redis,
        http_client=httpx.Client(transport=httpx.MockTransport(Handler())),
        sleep=Clock().sleep,
        monotonic=Clock().monotonic,
    )
    assert client._token_cache_key() != other._token_cache_key()


def test_singleflight_single_fetch():
    handler = Handler()
    handler.token_delay = 0.15  # 毫秒级真实延迟，制造并发窗口
    redis = FakeRedis()
    # 单飞等待依赖真实时间（注入 sleep 在其他用例用于退避断言），此处用 real_sleep
    client, _ = make_client(handler, redis=redis, real_sleep=True)
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(client.get_access_token()))
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r == "TOKEN_A" for r in results)
    token_calls = [c for c in handler.calls if c[0] == TOKEN_PATH]
    assert len(token_calls) == 1  # 单飞锁保证账号级仅一次刷新


def test_token_refresh_waits_for_other_holder_cache():
    """未抢到刷新锁：等待其他持有者写缓存后直接命中，不自行发起请求。"""
    handler = Handler()
    redis = FakeRedis()
    lock_key = f"wechat_mp_api:token_lock:{TENANT}:{CONFIG}:" + hashlib.sha256(
        SECRET.encode("utf-8")
    ).hexdigest()[:8]
    assert redis.acquire_lock(lock_key, "other-owner")  # 预占单飞锁
    client, _ = make_client(handler, redis=redis, real_sleep=True)

    import threading as _t

    def _release_soon():
        redis.set(_expected_cache_key(), "TOKEN_FROM_PEER", ex=3600)
        redis.release_lock(lock_key, "other-owner")

    timer = _t.Timer(0.05, _release_soon)
    timer.start()
    assert client.get_access_token() == "TOKEN_FROM_PEER"
    assert handler.calls == []  # 未触达 HTTP


def test_token_unavailable_redis_degrades_to_direct_fetch():
    handler = Handler()
    client, _ = make_client(handler, redis=FakeRedis(available=False))
    assert client.get_access_token() == "TOKEN_A"
    assert len([c for c in handler.calls if c[0] == TOKEN_PATH]) == 1


# ------------------------------- token 失效重试 -------------------------------


def test_invalid_token_refreshes_once_and_retries():
    handler = Handler()
    handler.token_script = [
        {"access_token": "TOKEN_A", "expires_in": 7200},
        {"access_token": "TOKEN_B", "expires_in": 7200},
    ]
    handler.add(GETARTICLE_PATH, {"errcode": 40001, "errmsg": "invalid credential"})
    handler.add(GETARTICLE_PATH, {"news_item": [{"title": "ok"}]})
    redis = FakeRedis()
    client, _ = make_client(handler, redis=redis)
    body = client.getarticle("ART1")
    assert body["news_item"][0]["title"] == "ok"
    biz = [c for c in handler.calls if c[0] == GETARTICLE_PATH]
    assert len(biz) == 2  # 业务请求仅重试一次
    assert biz[0][1] == "TOKEN_A" and biz[1][1] == "TOKEN_B"
    assert redis.get(_expected_cache_key()) == "TOKEN_B"  # 缓存换新


def test_invalid_token_twice_raises_without_infinite_retry():
    handler = Handler()
    handler.add(GETARTICLE_PATH, {"errcode": 42001, "errmsg": "access_token expired"})
    handler.add(GETARTICLE_PATH, {"errcode": 42001, "errmsg": "access_token expired"})
    client, clock = make_client(handler)
    with pytest.raises(WeChatMPAPIError) as exc_info:
        client.getarticle("ART1")
    assert exc_info.value.errcode == 42001
    biz = [c for c in handler.calls if c[0] == GETARTICLE_PATH]
    assert len(biz) == 2  # 刷一次重试一次，不无限循环


# ------------------------------- batchget_all 分页 -------------------------------


def test_batchget_pagination_advances_by_item_count():
    handler = Handler()
    handler.add(BATCHGET_PATH, _page(0, 45, [_batch_msg(i) for i in range(20)]))
    handler.add(BATCHGET_PATH, _page(20, 45, [_batch_msg(i) for i in range(20, 40)]))
    handler.add(BATCHGET_PATH, _page(40, 45, [_batch_msg(i) for i in range(40, 45)]))
    client, _ = make_client(handler)
    result = client.batchget_all()
    assert result.fetched == 45
    assert result.total_count == 45
    assert result.complete and result.reliable
    offsets = [c[2]["offset"] for c in handler.calls if c[0] == BATCHGET_PATH]
    assert offsets == [0, 20, 40]
    # 消息含首子篇元数据（对账建 articles 行预填用）
    assert result.messages[0]["first_title"] == "标题0"
    assert result.messages[0]["create_time"] == 1789374300 - 60


def test_batchget_duplicate_page_marks_incomplete():
    handler = Handler()
    items = [_batch_msg(1), _batch_msg(2)]
    handler.add(BATCHGET_PATH, _page(0, 4, items))
    handler.add(BATCHGET_PATH, _page(2, 4, [_batch_msg(1), _batch_msg(2)]))  # 重复页
    client, _ = make_client(handler)
    result = client.batchget_all()
    assert result.fetched == 2  # 重复项去重
    assert not result.complete and not result.reliable  # 缺失迁移门禁据此不迁移


def test_batchget_empty_page_before_total_marks_incomplete():
    handler = Handler()
    handler.add(BATCHGET_PATH, {"total_count": 10, "item_count": 0, "item": []})
    client, _ = make_client(handler)
    result = client.batchget_all()
    assert result.fetched == 0
    assert not result.complete  # 无进展：宁可不删不可误删


def test_batchget_total_drift_marks_incomplete():
    handler = Handler()
    handler.add(BATCHGET_PATH, _page(0, 2, [_batch_msg(1), _batch_msg(2), _batch_msg(3)]))
    client, _ = make_client(handler)
    result = client.batchget_all()
    assert result.fetched == 3 and result.total_count == 2
    assert not result.complete  # 总数漂移


def test_batchget_empty_collection_is_reliable():
    """空集合（total=0）走相同门禁且视为完整可靠（设计 §5.2.4）。"""
    handler = Handler()
    handler.add(BATCHGET_PATH, {"total_count": 0, "item_count": 0, "item": []})
    client, _ = make_client(handler)
    result = client.batchget_all()
    assert result.reliable and result.fetched == 0


def test_batchget_structure_anomaly_raises():
    client, _ = make_client(Handler())
    for bad in (
        {"item_count": 1, "item": []},  # 缺 total_count
        {"total_count": 1, "item": []},  # 缺 item_count
        {"total_count": 1, "item_count": 1, "item": [{"update_time": 1}]},  # 缺 article_id
        {"total_count": 1, "item_count": 1, "item": [{"article_id": "A"}]},  # 缺 update_time
    ):
        handler = Handler()
        handler.add(BATCHGET_PATH, bad)
        client, _ = make_client(handler)
        with pytest.raises(WeChatMPAPIError):
            client.batchget_all()


def test_batchget_timeout_budget_marks_incomplete():
    """单轮总超时：预算耗尽即截断置 incomplete，不无限分页。"""
    handler = Handler()
    handler.add(BATCHGET_PATH, _page(0, 100, [_batch_msg(1)]))
    handler.add(BATCHGET_PATH, _page(1, 100, [_batch_msg(2)]))
    handler.add(BATCHGET_PATH, _page(2, 100, [_batch_msg(3)]))
    # 每次读钟推进 400s：deadline 判定在第 3 轮循环前越过 600s 预算
    client, _ = make_client(handler, clock=Clock(t=10.0, step=400.0))
    result = client.batchget_all()
    assert not result.complete and result.fetched < 3


# ------------------------------- 错误分类 -------------------------------


def test_getarticle_53600_is_dedicated_signal():
    handler = Handler()
    handler.add(GETARTICLE_PATH, {"errcode": 53600, "errmsg": "article not exist"})
    client, _ = make_client(handler)
    with pytest.raises(ArticleNotFoundError):
        client.getarticle("GONE")


def test_40164_parses_exit_ip():
    handler = Handler()
    handler.add(
        GETARTICLE_PATH,
        {
            "errcode": 40164,
            "errmsg": "invalid ip 203.0.113.9 ipv6 ::ffff:203.0.113.9, not in whitelist, hints: [req_id=AbCd]",
        },
    )
    client, _ = make_client(handler)
    with pytest.raises(IPWhitelistError) as exc_info:
        client.getarticle("ART1")
    assert exc_info.value.ip == "203.0.113.9"
    assert "203.0.113.9" in exc_info.value.message
    assert "IP 白名单" in exc_info.value.message
    # errmsg 原文（req_id 等）不进异常消息
    assert "req_id" not in exc_info.value.message


def test_transient_errcode_retries_with_backoff():
    handler = Handler()
    handler.add(GETARTICLE_PATH, {"errcode": -1, "errmsg": "system busy"})
    handler.add(GETARTICLE_PATH, {"errcode": 45009, "errmsg": "reach max api daily quota"})
    handler.add(GETARTICLE_PATH, {"news_item": []})
    client, clock = make_client(handler)
    body = client.getarticle("ART1")
    assert body == {"news_item": []}
    biz = [c for c in handler.calls if c[0] == GETARTICLE_PATH]
    assert len(biz) == 3
    assert len(clock.sleeps) >= 2  # 退避发生过


def test_permission_error_no_blind_retry():
    handler = Handler()
    handler.add(GETARTICLE_PATH, {"errcode": 48001, "errmsg": "api unauthorized"})
    client, clock = make_client(handler)
    with pytest.raises(WeChatMPAPIError) as exc_info:
        client.getarticle("ART1")
    assert exc_info.value.errcode == 48001
    assert len([c for c in handler.calls if c[0] == GETARTICLE_PATH]) == 1  # 不盲重试
    assert "freepublish" in friendly_api_error(exc_info.value)


def test_friendly_error_never_leaks_transport_details():
    assert friendly_api_error(RuntimeError("https://api.weixin.qq.com?access_token=x")) == (
        "微信接口调用失败（RuntimeError）"
    )


# ------------------------------- 日志纪律 -------------------------------


def test_logs_never_contain_credentials(monkeypatch):
    """token/secret/appid/URL/响应正文不进日志（设计 §5.3）。"""
    records = []

    sink_id = logger.add(lambda m: records.append(str(m)), level="DEBUG")
    try:
        handler = Handler()
        handler.add(GETARTICLE_PATH, {"errcode": 40001, "errmsg": "invalid credential"})
        handler.add(GETARTICLE_PATH, {"errcode": 40001, "errmsg": "invalid credential"})
        client, _ = make_client(handler)
        with pytest.raises(WeChatMPAPIError):
            client.getarticle("ART1")
    finally:
        logger.remove(sink_id)

    joined = "\n".join(records)
    assert SECRET not in joined
    assert "TOKEN_A" not in joined and "TOKEN_B" not in joined
    assert "access_token=" not in joined
    assert APPID not in joined
    assert "invalid credential" not in joined  # 响应正文不透传
    assert "api.weixin.qq.com" not in joined


# ------------------------------- 小工具 -------------------------------


def test_is_truthy_flag_tolerant():
    assert is_truthy_flag(True) and is_truthy_flag(1) and is_truthy_flag("true")
    assert not is_truthy_flag(False) and not is_truthy_flag(0) and not is_truthy_flag("false")
    assert not is_truthy_flag(None) and not is_truthy_flag({"x": 1})  # 未知结构不当删除


def test_parse_whitelist_ip():
    assert parse_whitelist_ip("invalid ip 9.9.9.9, not in whitelist") == "9.9.9.9"
    assert parse_whitelist_ip("no ip here") is None
    assert parse_whitelist_ip("") is None
