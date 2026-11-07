#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""efficiency.py —— 请求节流 / 风控冷却 / LLM 响应缓存。

供 web_search.py 与 pipeline.py 共用。三条设计约束：

1. **绝不加速**：对外部网站的请求频率只会降低或持平。效率优化只作用于
   「重复解析 / 重复请求 / 重复调用大模型」，不引入并发、不缩短既有间隔。
2. **按厂商组限速**：baidu.com 与 m.baidu.com 属同一组。PC 端刚触发验证码，
   不能立刻换移动端继续加压；搜狗 / Bing 属不同组，仍可正常降级。
3. **跨进程生效**：CLI 每次运行都是新进程，节流状态与 LLM 缓存落盘，
   避免连续多次调用把同一站点打爆，或为同一份内容重复付 LLM 的钱。

环境变量（全部可选，默认值即「更保守」）
------------------------------------------
    WSP_CACHE_DIR            缓存目录，默认技能目录下 .cache/
    WSP_MIN_REQUEST_INTERVAL 同组两次请求最小间隔（秒），默认 0（仅登记时间戳）
    WSP_REDIRECT_INTERVAL    解析 SERP 跳转链接的最小间隔，默认 0.35（原来为 0）
    WSP_MAX_REDIRECTS        单次搜索最多解析多少个跳转链接，默认 40，0=不限
    WSP_BLOCK_COOLDOWN       命中风控后同组冷却秒数，默认 20；0=恢复旧的移动端降级
    WSP_DOMAIN_MIN_INTERVAL  目标站按域名最小间隔，默认 0（不改变既有 batch 行为）
    WSP_LLM_CACHE_TTL        LLM 响应缓存有效期（秒），默认 86400；0=禁用缓存
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(os.environ.get("WSP_CACHE_DIR") or str(_ROOT / ".cache"))
LLM_CACHE_DIR = CACHE_DIR / "llm"
THROTTLE_FILE = CACHE_DIR / "throttle.json"


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


MIN_REQUEST_INTERVAL = _env_float("WSP_MIN_REQUEST_INTERVAL", 0.0)
REDIRECT_MIN_INTERVAL = _env_float("WSP_REDIRECT_INTERVAL", 0.35)
MAX_REDIRECT_RESOLVES = int(_env_float("WSP_MAX_REDIRECTS", 40))
BLOCK_COOLDOWN = _env_float("WSP_BLOCK_COOLDOWN", 20.0)
DOMAIN_MIN_INTERVAL = _env_float("WSP_DOMAIN_MIN_INTERVAL", 0.0)
LLM_CACHE_TTL = _env_float("WSP_LLM_CACHE_TTL", 86400.0)


# 引擎名 -> 厂商组
_ENGINE_GROUP = {
    "baidu": "baidu", "baidu_m": "baidu",
    "sogou": "sogou", "so": "so",
    "naver": "naver", "daum": "daum",
    "yahoojp": "yahoojp", "goo": "goo",
    "yandex": "yandex", "seznam": "seznam", "qwant": "qwant",
    "parsijoo": "parsijoo", "coccoc": "coccoc",
    "swisscows": "swisscows", "startpage": "startpage", "brave": "brave",
    "google": "google", "bing": "bing", "ddg": "ddg",
}

# 域名后缀 -> 厂商组。hostname 会先去掉 www./m./mobile. 前缀，
# 因此 m.baidu.com 与 www.baidu.com 同组，aiqicha.baidu.com 也归入百度系。
_DOMAIN_GROUP = (
    ("baidu.com", "baidu"), ("sogou.com", "sogou"),
    ("so.com", "so"), ("360.cn", "so"),
    ("bing.com", "bing"), ("google.com", "google"),
    ("duckduckgo.com", "ddg"), ("naver.com", "naver"),
    ("daum.net", "daum"), ("yahoo.co.jp", "yahoojp"),
    ("goo.ne.jp", "goo"), ("yandex.ru", "yandex"),
    ("yandex.com", "yandex"), ("qwant.com", "qwant"),
    ("brave.com", "brave"), ("startpage.com", "startpage"),
    ("seznam.cz", "seznam"),
)


def group_key(value):
    """把引擎名或 URL 归一化成「共享风控画像」的组键。

    搜索域名按厂商归并；其它目标站按 hostname 归组，便于对同一站点的
    多个 tab / 多个页面做限速（用户反馈过「访问太快触发验证或禁用」）。
    """
    if not value:
        return "unknown"
    v = str(value).strip().lower()
    if v in _ENGINE_GROUP:
        return _ENGINE_GROUP[v]
    if "://" in v:
        host = (urlparse(v).hostname or "").lower()
    else:
        host = v.split("/")[0].split(":")[0].lower()
    for prefix in ("www.", "m.", "mobile."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    for suffix, grp in _DOMAIN_GROUP:
        if host == suffix or host.endswith("." + suffix):
            return grp
    return host or "unknown"


def _write_json(path, data):
    """原子写，避免多进程并发时读到半截 JSON。失败静默——缓存不是关键路径。"""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, str(path))
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except Exception:
        pass


class Throttle:
    """基于文件的按组节流器（含进程内兜底，防止多进程互相覆盖导致漏限速）。"""

    def __init__(self, path=None):
        self.path = Path(path or THROTTLE_FILE)
        self._mem = {"last": {}, "cooldown_until": {}}

    def _load(self):
        st = {"last": {}, "cooldown_until": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            st["last"] = {k: float(v) for k, v in (raw.get("last") or {}).items()}
            st["cooldown_until"] = {
                k: float(v) for k, v in (raw.get("cooldown_until") or {}).items()
            }
        except Exception:
            pass
        for k, v in self._mem["last"].items():
            st["last"][k] = max(st["last"].get(k, 0.0), v)
        for k, v in self._mem["cooldown_until"].items():
            st["cooldown_until"][k] = max(st["cooldown_until"].get(k, 0.0), v)
        return st

    def is_cooling(self, key):
        return self._load()["cooldown_until"].get(key, 0.0) > time.time()

    def cooldown(self, key, seconds=None):
        """命中风控后给整组降温和。0 秒 = 不设置（恢复旧行为）。"""
        if seconds is None:
            seconds = BLOCK_COOLDOWN
        if seconds <= 0:
            return
        until = time.time() + seconds
        self._mem["cooldown_until"][key] = until
        st = self._load()
        st["cooldown_until"][key] = max(st["cooldown_until"].get(key, 0.0), until)
        _write_json(self.path, st)

    def wait(self, key, min_interval=None):
        """登记一次外部请求；必要时 sleep 补足间隔。

        刻意**不**在这里检查冷却：冷却只该拦住「搜索引擎通道」的重试与降级，
        不该误伤用户明确给定的目标 URL（例如爱企查与百度同属一组，
        百度搜索被拦不应导致用户指定的详情页也抓不了）。
        需要冷却判断的调用方请显式先调 is_cooling()。
        """
        if min_interval is None:
            min_interval = MIN_REQUEST_INTERVAL
        st = self._load()
        now = time.time()
        gap = min_interval - (now - st["last"].get(key, 0.0))
        if gap > 0:
            time.sleep(gap)
        ts = time.time()
        self._mem["last"][key] = ts
        st = self._load()
        st["last"][key] = max(st["last"].get(key, 0.0), ts)
        _write_json(self.path, st)
        return True


throttle = Throttle()


# ---------------------------------------------------------------- LLM 缓存

def llm_cache_key(cfg, prompt, context):
    """缓存键绑定 接口地址 + 模型 + 指令 + 正文，任一变化都视为不同请求。

    刻意不包含 API key：缓存文件里不落任何凭据。
    """
    h = hashlib.sha256()
    for part in (cfg.get("base", ""), cfg.get("model", ""), prompt, context):
        h.update(part.encode("utf-8", errors="replace"))
        h.update(b"\x00")
    return h.hexdigest()[:40]


def llm_cache_get(key):
    if LLM_CACHE_TTL <= 0:
        return None
    try:
        d = json.loads((LLM_CACHE_DIR / (key + ".json")).read_text(encoding="utf-8"))
        if time.time() - float(d.get("ts", 0)) > LLM_CACHE_TTL:
            return None
        return d.get("text")
    except Exception:
        return None


def llm_cache_put(key, text):
    if LLM_CACHE_TTL <= 0:
        return
    _write_json(LLM_CACHE_DIR / (key + ".json"), {"ts": time.time(), "text": text})


def llm_cache_clear():
    """清空 LLM 缓存，返回删除数量。"""
    n = 0
    try:
        for p in LLM_CACHE_DIR.glob("*.json"):
            try:
                p.unlink()
                n += 1
            except Exception:
                pass
    except Exception:
        pass
    return n
