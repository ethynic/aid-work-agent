"""Small, bounded end-to-end Runner load probe against agent3's public Nginx URL.

Only dedicated test tenants are used. The script never prints tokens, prompts,
model replies, or uploaded data. It leaves labelled sessions and a synthetic
data source for inspection after the run.
"""

import argparse
import concurrent.futures
import csv
import io
import json
import re
import secrets
import subprocess
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests


BASE_URL = "https://agent3.aidingyi.cn"
SSH_HOST = "ubuntu@124.222.3.254"
DB_COMMAND = "docker exec -i aid-postgres psql -U aid_user -d aid_work_agent3 -X -q -A -t -v ON_ERROR_STOP=1"
DATA_TENANT = ("tenant_d6c6ed2aadd6", "user_5e891c1a3f3a")
OTHER_TENANT = ("tenant_1dc997a1806b", "user_1dc51d7027fe")

CASES = {
    "data_trend": (DATA_TENANT, "data-analysis",
        "只使用 loadtest_orders_20261106 合成订单表，按月和渠道汇总 2024 与 2025 年的订单量、销售额和毛利率，计算同比与环比，说明计算口径并给出趋势图。不要使用其他数据源或人员数据。"),
    "data_quality": (DATA_TENANT, "data-analysis",
        "只分析 loadtest_orders_20261106 合成订单表：按渠道和地区比较销售额、成本占比，找出最近两期的缺失值与异常波动，并说明判断依据。不要使用其他数据源或人员数据。"),
    "main_short": (OTHER_TENANT, "main",
        "只用文字说明 Runner 执行槽和数据库租约各自解决什么问题，控制在三句话内，不调用外部工具。"),
    "main_reasoning": (OTHER_TENANT, "main",
        "假设两个独立执行槽处理三个只读任务，其中一个任务耗时较长。用不超过五句话说明预期排队和完成顺序，不调用外部工具。"),
    "travel_short": (OTHER_TENANT, "travel-consultant",
        "为第一次来上海的游客给出一天的步行与地铁行程建议，只给文字建议，不订票、不联系商家、不生成文件。"),
}


def run_sql(ssh_key: Path, sql: str) -> str:
    result = subprocess.run(
        ["ssh", "-i", str(ssh_key), "-o", "BatchMode=yes", SSH_HOST, DB_COMMAND],
        input=sql, text=True, capture_output=True, timeout=30, check=False,
    )
    if result.returncode:
        raise RuntimeError("agent3 test database helper failed")
    return result.stdout.strip()


def temporary_token(ssh_key: Path, tenant_id: str, user_id: str) -> str:
    if not all(re.fullmatch(r"[a-zA-Z0-9_-]+", value) for value in (tenant_id, user_id)):
        raise ValueError("invalid test identity")
    token = "agent3_load_" + secrets.token_hex(24)
    count = run_sql(ssh_key, f"""
        WITH inserted AS (
            INSERT INTO tokens (token, user_id, expires_at)
            SELECT '{token}', u.user_id, NOW() + interval '2 hours'
            FROM users u JOIN tenants t ON t.tenant_id = u.tenant_id
            WHERE u.user_id = '{user_id}' AND u.tenant_id = '{tenant_id}'
              AND u.status = 'active' AND t.status = 'active' AND t.tenant_type = 'test'
            RETURNING 1
        ) SELECT count(*) FROM inserted;
    """)
    if count != "1":
        raise RuntimeError("dedicated test user is unavailable")
    return token


def call(session: requests.Session, method: str, path: str, **kwargs) -> dict:
    response = session.request(method, BASE_URL + path, timeout=(10, 40), **kwargs)
    try:
        value = response.json()
    except ValueError:
        raise RuntimeError(f"{path}: HTTP {response.status_code}, invalid JSON") from None
    if not response.ok or value.get("success") is False:
        code = value.get("code") or value.get("error") or "UNKNOWN"
        if not isinstance(code, str) or not re.fullmatch(r"[A-Z0-9_]{1,80}", code):
            code = "UNSPECIFIED"
        raise RuntimeError(f"{path}: HTTP {response.status_code}, code={code}")
    return value


def make_session(token: str, tenant_id: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({"Authorization": "Bearer " + token,
                            "X-Tenant-Id": tenant_id, "Accept": "application/json"})
    return session


def synthetic_csv() -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(("order_id", "order_date", "channel", "region", "category",
                     "quantity", "sales_amount", "cost_amount"))
    channels = ("web", "store", "partner", "app")
    regions = ("east", "north", "south", "west")
    categories = ("office", "home", "outdoor")
    for i in range(20_000):
        day = date(2024, 1, 1) + timedelta(days=i % 730)
        quantity = i % 5 + 1
        sales = quantity * (80 + i % 31) + (i // 730) % 13
        cost = round(sales * (0.57 + (i % 9) / 100), 2)
        writer.writerow((f"SYN{i:06d}", day.isoformat(), channels[i % 4],
                         regions[(i // 4) % 4], categories[(i // 16) % 3],
                         quantity, sales, cost))
    return output.getvalue().encode("utf-8-sig")


def prepare_data(session: requests.Session) -> int:
    schema = None
    with session.post(BASE_URL + "/api/data-analysis/upload",
            files={"file": ("loadtest_orders_20261106.csv", synthetic_csv(), "text/csv")},
            timeout=(10, 600), stream=True) as response:
        response.raise_for_status()
        response.encoding = "utf-8"
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            if event.get("type") == "error":
                raise RuntimeError("synthetic data upload failed")
            if event.get("type") == "complete":
                schemas = event.get("schemas") or []
                if len(schemas) != 1:
                    raise RuntimeError("synthetic data upload returned unexpected schema count")
                schema = schemas[0]
    if schema is None:
        raise RuntimeError("synthetic data upload did not finish")
    payload = {key: schema[key] for key in ("columns", "source")}
    payload.update(table_name="loadtest_orders_20261106",
                   description="压测专用合成订单数据，无客户或人员信息",
                   source_info="loadtest_orders_20261106.csv")
    saved = call(session, "POST", "/api/data-analysis/schemas", json=payload)
    doc_id = int(saved["doc_id"])
    listed = call(session, "GET", "/api/data-analysis/schemas",
                  params={"keyword": "loadtest_orders_20261106"})
    if not any(item.get("id") == doc_id and item.get("source_status") == "available"
               for item in listed.get("schemas", [])):
        raise RuntimeError("synthetic data source is not available to the test tenant")
    return doc_id


def sample_state(ssh_key: Path, run_id: str) -> dict:
    lines = run_sql(ssh_key, f"""
        SELECT 'R|' || runner_id || '|' || status || '|' || attempt || '|' ||
               coalesce(worker_id, '') || '|' || coalesce(execution_started_at::text, '')
        FROM agent_runners WHERE client_request_id LIKE 'agent3-load-{run_id}-%';
        SELECT 'C|' || count(*) FROM pg_stat_activity WHERE datname = 'aid_work_agent3';
    """).splitlines()
    sample = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "runners": [], "db_connections": None, "containers": {}}
    for line in lines:
        parts = line.split("|")
        if parts[0] == "R" and len(parts) == 6:
            sample["runners"].append(dict(zip(
                ("runner_id", "status", "attempt", "worker_id", "execution_started_at"), parts[1:])))
        elif parts[0] == "C" and len(parts) == 2:
            sample["db_connections"] = int(parts[1])
    command = ("docker stats --no-stream --format '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}' "
               "agent3-runner-worker-1 agent3-runner-worker-2 agent3-runner-monitor-1 "
               "aid-runner-api3 aid-agent-api3 aid-agent-background3 aid-postgres")
    result = subprocess.run(["ssh", "-i", str(ssh_key), "-o", "BatchMode=yes",
                             SSH_HOST, command], text=True, capture_output=True,
                            timeout=30, check=False)
    if result.returncode:
        raise RuntimeError("agent3 resource sampler failed")
    for line in result.stdout.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            sample["containers"][parts[0]] = {"cpu": parts[1], "memory": parts[2]}
    return sample


def watch_state(ssh_key: Path, run_id: str, stop: threading.Event,
                samples: list, errors: list) -> None:
    while not stop.is_set():
        try:
            samples.append(sample_state(ssh_key, run_id))
        except (RuntimeError, subprocess.TimeoutExpired, ValueError):
            errors.append(datetime.now(timezone.utc).isoformat(timespec="seconds"))
        stop.wait(5)


def cancel_and_observe(session: requests.Session, runner_id: str) -> dict:
    observed = {"cancel_requested": False, "post_cancel_status": None,
                "post_cancel_settlement": None}
    try:
        call(session, "POST", f"/api/chat/runners/{runner_id}/cancel")
        observed["cancel_requested"] = True
    except (requests.RequestException, RuntimeError):
        return observed
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            runner = call(session, "GET", f"/api/chat/runners/{runner_id}")["runner"]
            observed["post_cancel_status"] = runner["status"]
            observed["post_cancel_settlement"] = runner.get("settlement_status")
            if runner["status"] in {"completed", "failed", "cancelled"} and runner.get("settlement_status") == "settled":
                break
        except (requests.RequestException, RuntimeError, KeyError):
            pass
        time.sleep(3)
    return observed


def run_case(name: str, tokens: dict, deadline_seconds: int, run_id: str) -> dict:
    identity, profile_id, prompt = CASES[name]
    tenant_id, _ = identity
    session = make_session(tokens[identity], tenant_id)
    started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    runner_id = None
    session_id = None
    status = "not_submitted"
    settlement = None
    attempt = None
    error_code = None
    accepted_at = None
    finished_at = None
    poll_errors = 0
    poll_errors_total = 0
    cancel_observation = None
    try:
        chat = call(session, "POST", "/api/sessions",
                    json={"title": f"LOADTEST {run_id} {name}", "subagent_id": profile_id})
        session_id = chat["session_id"]
        accepted = call(session, "POST", "/api/chat/runners", json={
            "client_request_id": f"agent3-load-{run_id}-{name}-{uuid.uuid4().hex[:8]}",
            "message": prompt, "session_id": session_id, "files": [], "subagent": profile_id,
        })
        runner_id = accepted["runner"]["runner_id"]
        accepted_at = accepted["runner"].get("accepted_at")
        accepted_seconds = round(time.monotonic() - started, 2)
        deadline = started + deadline_seconds
        while time.monotonic() < deadline:
            try:
                runner = call(session, "GET", f"/api/chat/runners/{runner_id}")["runner"]
            except (requests.RequestException, RuntimeError):
                poll_errors += 1
                poll_errors_total += 1
                time.sleep(min(3 * poll_errors, 15))
                continue
            poll_errors = 0
            status = runner["status"]
            settlement = runner.get("settlement_status")
            attempt = runner.get("attempt")
            finished_at = runner.get("finished_at")
            result = runner.get("result") or {}
            code = result.get("error_code") if isinstance(result, dict) else None
            error_code = code if isinstance(code, str) and re.fullmatch(r"[A-Z0-9_]{1,80}", code) else None
            if status in {"completed", "failed", "cancelled"} and settlement == "settled":
                break
            time.sleep(3)
        else:
            status = "timeout"
            cancel_observation = cancel_and_observe(session, runner_id)
        return {"case": name, "profile": profile_id, "session_id": session_id,
                "runner_id": runner_id,
                "status": status, "settlement_status": settlement, "attempt": attempt,
                "error_code": error_code,
                "started_at": started_at, "accepted_at": accepted_at,
                "finished_at": finished_at, "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "poll_errors": poll_errors_total, "cancel_observation": cancel_observation,
                "accept_seconds": accepted_seconds,
                "elapsed_seconds": round(time.monotonic() - started, 2)}
    except (requests.RequestException, RuntimeError, KeyError) as error:
        if runner_id:
            cancel_observation = cancel_and_observe(session, runner_id)
        return {"case": name, "profile": profile_id, "session_id": session_id,
                "runner_id": runner_id,
                "status": "probe_error", "error_type": type(error).__name__,
                "error": str(error)[:160], "started_at": started_at,
                "cancel_observation": cancel_observation,
                "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "elapsed_seconds": round(time.monotonic() - started, 2)}
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-key", type=Path, required=True)
    parser.add_argument("--cases", required=True, help="Comma-separated case names: " + ",".join(CASES))
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--prepare-data", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    names = [name.strip() for name in args.cases.split(",")]
    if not names or any(name not in CASES for name in names) or len(set(names)) != len(names):
        parser.error("unknown or duplicate case")
    if not 1 <= args.concurrency <= 4 or not 60 <= args.timeout_seconds <= 1800:
        parser.error("concurrency must be 1..4 and timeout 60..1800 seconds")
    if not args.ssh_key.is_file():
        parser.error("SSH key does not exist")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    identities = {CASES[name][0] for name in names}
    if args.prepare_data:
        identities.add(DATA_TENANT)
    tokens = {}
    data_doc_id = None
    samples = []
    watch_errors = []
    cleanup_errors = []
    results = []
    try:
        for identity in identities:
            tokens[identity] = temporary_token(args.ssh_key, *identity)
            with make_session(tokens[identity], identity[0]) as session:
                capabilities = call(session, "GET", "/api/chat/runners/capabilities")
                if not capabilities.get("web_enabled"):
                    raise RuntimeError("agent3 Web Runner is disabled")
        if args.prepare_data:
            with make_session(tokens[DATA_TENANT], DATA_TENANT[0]) as session:
                data_doc_id = prepare_data(session)
        stop = threading.Event()
        watcher = threading.Thread(target=watch_state,
            args=(args.ssh_key, run_id, stop, samples, watch_errors), daemon=True)
        watcher.start()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                futures = [pool.submit(run_case, name, tokens, args.timeout_seconds, run_id)
                           for name in names]
                results = [future.result() for future in futures]
        finally:
            stop.set()
            watcher.join(timeout=35)
    finally:
        for identity, token in tokens.items():
            try:
                if run_sql(args.ssh_key, f"DELETE FROM tokens WHERE token = '{token}' RETURNING 1;") != "1":
                    cleanup_errors.append(identity[0])
            except (RuntimeError, subprocess.TimeoutExpired):
                cleanup_errors.append(identity[0])

    report = {"run_id": run_id, "base_url": BASE_URL, "concurrency": args.concurrency,
              "data_doc_id": data_doc_id, "results": results, "samples": samples,
              "sample_errors": watch_errors, "token_cleanup_failed_tenants": cleanup_errors}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "samples"}
                     | {"sample_count": len(samples)}, ensure_ascii=False))
    return 0 if not cleanup_errors and not watch_errors and all(
        item["status"] == "completed" and item["settlement_status"] == "settled"
        for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
