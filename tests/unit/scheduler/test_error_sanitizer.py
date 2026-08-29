"""定时任务错误脱敏（error_sanitizer）单元测试（纯函数，不依赖 PG/LLM）

覆盖安全加固设计「敏感错误脱敏」门禁：
- 键名 × 分隔符矩阵（=、:、=>、is、空白；单词条目与复合键 api key 等）
- 攻击性审查回归：复数键名、复合键重复分隔符（api__key）、secret_key、
  括号包裹键（args['password']=v）、分隔符标点噪声、全角 ：＝ 与弯引号
- 引号值（单/双/键名带引号）、跨行值、未闭合引号保守遮到末尾
- 前缀键（db_password、X-Api-Key）与中文前缀不因边界判断漏遮
- Bearer 头部、URL 查询参数 token 遮蔽
- 超长输入（1MB）线性性能；无秘密文本/None/空串原样返回
- 调用点：DB 日志落库前 error_message/error_trace 经过脱敏（mock 捕获 SQL 参数）
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from src.scheduler.error_sanitizer import (
    sanitize_scheduled_task_error,
    sanitize_scheduled_task_log_rows,
)

# ==================== 键名 × 分隔符矩阵 ====================

_KEY_NAMES = [
    "password", "passwd", "secret", "token",
    "api_key", "api-key", "api key", "access_key", "private_key", "auth_token",
]
_SEPARATORS = ["=", ":", "=>", " is ", " "]


@pytest.mark.parametrize("sep", _SEPARATORS)
@pytest.mark.parametrize("key", _KEY_NAMES)
def test_key_separator_matrix_masks_value(key, sep):
    """任意键名 × 任意分隔符：值被遮蔽，键名保留为 <key>=***"""
    text = f"调用失败 {key}{sep}leak-value-123 已拒绝"
    out = sanitize_scheduled_task_error(text)
    assert "leak-value-123" not in out
    assert f"{key}=***" in out
    # 可操作上下文（错误类别文字）保留
    assert "调用失败" in out and "已拒绝" in out


@pytest.mark.parametrize("text,secret", [
    # 前缀键：合并本地版子串匹配场景，不因词边界漏遮
    ("db_password=hunter2", "hunter2"),
    ("X-Api-Key: sk-front-123", "sk-front-123"),
    ("密码password=abc123错误", "abc123"),
    # 本地实现的历史缺陷回归：':' 分隔符曾被整体保留（泄漏值）
    ("认证失败 password: hunter2 已拒绝", "hunter2"),
    # Bearer 头部（对参考实现的增强）
    ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.Sig", "eyJhbGci"),
    # URL 查询参数 token：保守遮到参数串末尾（& 不作为值边界）
    ("https://api.example.com/v1/cb?token=abc123&user=1", "abc123"),
])
def test_prefix_bearer_and_url_masked(text, secret):
    assert secret not in sanitize_scheduled_task_error(text)


# ==================== 前缀键、Bearer、URL（攻击性审查补充） ====================

@pytest.mark.parametrize("text,secret", [
    # 复数键名（tokens= 等）：裸键 + s 后接分隔符，值不得泄漏
    ("invalid tokens: tkn-plural-1", "tkn-plural-1"),
    ("passwords=pass-plural-2", "pass-plural-2"),
    ("secrets: sec-plural-3", "sec-plural-3"),
    ("api keys: ['sk-a1','sk-a2']", "sk-a1"),
    ("api_keys=['sk-b1','sk-b2']", "sk-b1"),
    ('{"tokens": "tkn-json-22"}', "tkn-json-22"),
    # 复合键分隔符重复/混合（api__key、api - key、跨行）
    ("api__key=sk-under-5", "sk-under-5"),
    ("api--key=sk-dash-6", "sk-dash-6"),
    ("api - key: sk-mix-7", "sk-mix-7"),
    ("api  key: sk-two-8", "sk-two-8"),
    ("api\nkey: sk-nl-9", "sk-nl-9"),
    # secret_key 复合键（Django/Flask 常见），裸 secret 键不匹配 _key 后缀
    ("secret_key=dj-secret-10", "dj-secret-10"),
    ("SECRET_KEY=dj-secret-11", "dj-secret-11"),
    ("secretKey: dj-secret-12", "dj-secret-12"),
    # 括号/索引包裹键（参数 dump 形态）
    ("[password]=hunter2-br1", "hunter2-br1"),
    ("args['password']='hunter2-br2'", "hunter2-br2"),
    ('args["api__key"]="sk-br3"', "sk-br3"),
    # 分隔符后标点噪声（空值 + 逗号 / 双冒号），不得因空值漏遮后续真值
    ("invalid fields password:, hunter2-px", "hunter2-px"),
    ("password :: hunter2-px2", "hunter2-px2"),
    # 前缀键组合
    ("X_API_KEY：sk-mix-20", "sk-mix-20"),
])
def test_attack_review_leak_vectors_masked(text, secret):
    """攻击性审查发现的 19 个真实绕过（值可完整恢复）回归：全部遮蔽"""
    assert secret not in sanitize_scheduled_task_error(text)


@pytest.mark.parametrize("text,secret", [
    # 全角冒号/等号 + ASCII 键
    ("认证失败 password：hunter2-cjk1", "hunter2-cjk1"),
    ("password＝hunter2-cjk2", "hunter2-cjk2"),
    ("tokens：tkn-fw-21", "tkn-fw-21"),
    # 全角弯引号包裹键与值（开/闭字符不同，需配对扫描）
    ("\u201cpassword\u201d\uff1a\u201chunter2-cq\u201d", "hunter2-cq"),
    ("\u201cpassword\u201d\uff1a\u201cmy hunter2 word\u201d tail", "hunter2"),
    ("\u2018token\u2019\uff1a\u2018tkn-cq2\u2019", "tkn-cq2"),
    ("\u201csecret\u201d\uff1d\u201csec-cq3\u201d", "sec-cq3"),
    ("\u201cpassword\u201d\uff1a\u201cunclosed-cq4", "unclosed-cq4"),
])
def test_fullwidth_separators_and_quotes_masked(text, secret):
    """全角分隔符（：＝）与全角弯引号（“”‘’）形态不泄漏"""
    assert secret not in sanitize_scheduled_task_error(text)


@pytest.mark.parametrize("text,secret", [
    # 凭据列表值：第二项及以后没有键名认领，停在逗号会泄漏
    ("tokens：['sk-live-11','sk-live-22']", "sk-live-22"),
    ("api keys: ['sk-a1', 'sk-b2', 'sk-c3']", "sk-c3"),
    ("tokens: 't1', 't2', 't3'", "t3"),
    ("secrets: {'a': 's1', 'b': ['s2', 's3']}", "s3"),
    ("api_keys: ['sk-1', 'sk-2", "sk-2"),  # 未闭合容器 fail-safe
])
def test_credential_list_values_fully_masked(text, secret):
    """凭据列表（容器/引号链）整体遮蔽：任意一项都不得残留"""
    assert secret not in sanitize_scheduled_task_error(text)


def test_list_masking_keeps_next_dict_key_readable():
    """dict 中敏感值遮蔽后不吞并下一个键值对（可操作上下文保留）"""
    out = sanitize_scheduled_task_error("{'api_key': 'sk-xyz-987', 'host': 'db.prod'}")
    assert "sk-xyz-987" not in out
    assert "'host': 'db.prod'" in out


def test_known_limit_unquoted_bare_list_tail_not_masked():
    """已知限制（记录不修）：无引号裸词列表 tokens: aa, bb 只遮首项。
    吞并逗号链会把 "password: wrong, please contact admin" 整句遮蔽，
    违背保留可操作上下文的目标；带引号/括号的真实 dump 形态已覆盖。"""
    out = sanitize_scheduled_task_error("tokens: aa, bb, cc")
    assert "aa" not in out  # 首项仍被遮蔽


# ==================== 引号值 ====================


@pytest.mark.parametrize("quote", ["'", '"'])
def test_quoted_value_with_inner_spaces_masked(quote):
    """带空格的引号值整体遮蔽（旧实现的非空白匹配只能遮到第一个空格）"""
    text = f"auth failed: password={quote}p@ss w0rd 42{quote} retry"
    out = sanitize_scheduled_task_error(text)
    assert "p@ss" not in out and "w0rd" not in out
    assert "retry" in out


def test_quoted_key_and_value_json_style():
    """JSON/YAML 形态：键名成对引号可解析，非敏感键值保留"""
    text = "{'api_key': 'sk-xyz-987', 'host': 'db.prod'}"
    out = sanitize_scheduled_task_error(text)
    assert "sk-xyz-987" not in out
    assert "'host': 'db.prod'" in out


def test_quoted_value_with_escaped_quote():
    """转义引号不提前结束值扫描"""
    text = 'password="ab\\"cd" tail'
    out = sanitize_scheduled_task_error(text)
    assert 'ab' not in out and 'cd' not in out
    assert "tail" in out


def test_multiline_quoted_value_masked():
    """跨行引号值整体遮蔽"""
    text = 'password="line-one\nline-two" 尾部'
    out = sanitize_scheduled_task_error(text)
    assert "line-one" not in out and "line-two" not in out
    assert "尾部" in out


def test_unclosed_quote_masks_to_end():
    """未闭合引号：保守遮到文本末尾，不泄漏值的任何后半段"""
    text = "request failed: token=\"unclosed-secret\nmore-context-lines"
    out = sanitize_scheduled_task_error(text)
    assert "unclosed-secret" not in out
    assert "more-context-lines" not in out
    assert out == "request failed: token=***"


def test_truncated_value_at_text_end_masked():
    """文本在值中间截断（无引号）：遮到末尾"""
    assert sanitize_scheduled_task_error("boom password=trunc") == "boom password=***"


# ==================== 非凭据文本不受影响 ====================


@pytest.mark.parametrize("text", [
    "连接超时：db.prod:5432 unreachable",
    "普通错误：文件不存在 /tmp/a.txt",
    "monkey = funny",
    "tokenization = True",
])
def test_non_credential_text_unchanged(text):
    assert sanitize_scheduled_task_error(text) == text


def test_none_and_empty_passthrough():
    assert sanitize_scheduled_task_error(None) is None
    assert sanitize_scheduled_task_error("") == ""


# ==================== 超长输入性能 ====================


def test_1mb_plain_text_linear_performance():
    text = ("x" * 99 + "\n") * 10000 + "password=leak-me-now"
    assert len(text) >= 1_000_000
    start = time.perf_counter()
    out = sanitize_scheduled_task_error(text)
    elapsed = time.perf_counter() - start
    assert "leak-me-now" not in out
    assert out.endswith("password=***")
    # 实测约 0.06s；5s 上限只拦截灾难性回退（回溯爆炸），避免慢机抖动
    assert elapsed < 5.0


def test_1mb_credential_heavy_text_performance():
    """大量键名干扰（无分隔符不匹配）仍为线性"""
    text = "password token api key secret\n" * 50_000
    start = time.perf_counter()
    sanitize_scheduled_task_error(text)
    assert time.perf_counter() - start < 5.0


def test_100kb_dense_credentials_and_punct_noise_performance():
    """100KB 密集真实键值 + 分隔符标点噪声（:, :: => is）：仍为线性"""
    dense = "password=hunter token=abc api_key=sk secret=xyz; " * 2300
    noise = "password:, :: => is , " * 5000
    for text in (dense, noise):
        assert len(text) >= 100_000
        start = time.perf_counter()
        sanitize_scheduled_task_error(text)
        # 实测 <0.01s/100KB；1s 上限拦截回溯爆炸类回退
        assert time.perf_counter() - start < 1.0


# ==================== 调用点：DB 日志落库边界 ====================


@patch("src.scheduler.db.get_db_connection")
def test_log_create_sanitizes_error_fields_before_insert(mock_conn):
    """ScheduledTaskLogDB.create：error_message/error_trace 落库前经过脱敏，
    result_summary 等非错误字段不经过此逻辑（维持既有语义）"""
    from src.scheduler.db import ScheduledTaskLogDB

    cursor = MagicMock()
    cursor.fetchone.return_value = None  # 落库后 get_by_id 返回 None，聚焦 SQL 参数
    mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor

    ScheduledTaskLogDB.create(
        task_id="t1", user_id="u1", status="failed",
        result_summary="失败: 上游 401",
        error_message="认证失败 password=hunter2 已拒绝",
        error_trace='Traceback: token="unclosed-tail',
        tenant_id="tenant-1",
    )

    insert_call = next(
        c for c in cursor.execute.call_args_list
        if "INSERT INTO scheduled_task_logs" in c.args[0]
    )
    params = insert_call.args[1]
    # INSERT 列序：error_message=索引 9，error_trace=索引 10
    assert "hunter2" not in params[9]
    assert "password=***" in params[9]
    assert params[10].endswith("token=***")
    assert "unclosed-tail" not in params[10]
    # 非错误字段原样落库
    assert params[7] == "失败: 上游 401"
    assert params[1] == "tenant-1"


def test_api_boundary_wrapper_delegates():
    """API 边界 _sanitize_error 委托统一脱敏模块"""
    from src.api.scheduled_task import _sanitize_error

    out = _sanitize_error("调用第三方失败 Authorization: Bearer tkn-leak-88")
    assert "tkn-leak-88" not in out
    assert "Bearer=***" in out


# ==================== 日志行级读取边界兜底脱敏 ====================


def test_log_rows_historical_plaintext_masked():
    """历史遗留行（未脱敏明文）：行级 helper 遮蔽 error_message/error_trace"""
    rows = [
        {
            "log_id": "slog_old1",
            "status": "failed",
            "error_message": "登录失败 password=hunter2@prod x",
            "error_trace": 'Traceback: requests.HTTPError api_key="sk-live-42"',
        },
        {"log_id": "slog_old2", "status": "success", "error_message": None},
    ]
    out = sanitize_scheduled_task_log_rows(rows)
    assert "hunter2@prod" not in out[0]["error_message"]
    assert "password=***" in out[0]["error_message"]
    assert "sk-live-42" not in out[0]["error_trace"]
    assert "api_key=***" in out[0]["error_trace"]
    # 无错误字段/空值行不受影响
    assert out[1]["error_message"] is None


def test_log_rows_sanitization_idempotent():
    """幂等：新写入已脱敏（password=***）的行经边界再脱敏保持不变"""
    rows = [{
        "log_id": "slog_new1",
        "status": "failed",
        "error_message": "登录失败 password=*** x",
        "error_trace": "Traceback: password=***",
    }]
    before = dict(rows[0])
    out = sanitize_scheduled_task_log_rows(rows)
    assert out[0]["error_message"] == before["error_message"]
    assert out[0]["error_trace"] == before["error_trace"]


def test_log_rows_missing_fields_untouched():
    """不含 error 字段的行（如统计行/mock 缺列）原样返回，不抛错"""
    rows = [{"log_id": "slog_x", "status": "success"},
            {"log_id": "slog_y", "error_message": "", "error_trace": None}]
    out = sanitize_scheduled_task_log_rows(rows)
    assert out == rows
    assert sanitize_scheduled_task_log_rows([]) == []
