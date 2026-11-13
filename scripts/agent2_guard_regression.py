"""agent2 对话级回归：1M 行 Excel 附件走「分块读取」意图（2026-11-12 to_md 守门整改验证）。

复用 agent2_runner_load.py 的上传/提交逻辑；采样宿主机 free 与 runner 容器
docker stats / cgroup memory.events（v2）。不打印 token、提示词原文与模型回复。
"""

import json
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent2_runner_load import (  # noqa: E402
    SSH_HOST, DATA_TENANT, call, make_session, prepare_excel, run_sql,
    temporary_token,
)

SSH_KEY = Path.home() / ".ssh" / "id_ed25519"
RUN_ID = "guard-regr"
CONTAINERS = (
    "agent2-runner-worker-light-4", "agent2-runner-worker-light-5",
    "agent2-runner-worker-general-1", "agent2-runner-worker-general-2",
    "agent2-runner-worker-general-3",
)

# 事故同款自然语言意图：含「分块读取」，不写死 range，验证路由不再劫持为 to_md
PROMPT = (
    "我上传的 Excel 文件约有一百万行订单数据。请分块读取文件内容，统计各 channel 的订单数量"
    "和 sales_amount 合计（表格形式），并说明你实际读取了哪些范围。"
)


def host_sample() -> dict:
    # 目标机为 cgroup v1（/proc/cgroups memory enabled）；v2 路径仅作回退
    command = "free -m; for c in " + " ".join(CONTAINERS) + (
        "; do id=$(docker inspect --format '{{.Id}}' $c); "
        "mem=$(docker stats --no-stream --format '{{.MemUsage}}' $c); "
        "d1=/sys/fs/cgroup/memory/docker/$id; "
        "d2=/sys/fs/cgroup/unified/docker/$id; "
        "fc=$(cat $d1/memory.failcnt 2>/dev/null || cat $d2/memory.events 2>/dev/null | grep '^max' | awk '{print $2}'); "
        "ok=$(grep oom_kill $d1/memory.oom_control 2>/dev/null | awk '{print $2}' || cat $d2/memory.events 2>/dev/null | grep '^oom' | tr '\\n' ' '); "
        "echo \"$c|$mem|failcnt=${fc:-na} oom=${ok:-na}\"; done"
    )
    result = subprocess.run(
        ["ssh", "-i", str(SSH_KEY), "-o", "BatchMode=yes", SSH_HOST, command],
        text=True, capture_output=True, timeout=40, check=False,
    )
    sample = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "host_mem": "", "containers": {}}
    lines = result.stdout.splitlines()
    for line in lines[:3]:  # free -m 头两行 + Mem 行
        if line.startswith("Mem:"):
            sample["host_mem"] = line
    for line in lines:
        parts = line.split("|", 2)
        if len(parts) == 3 and parts[0].startswith("agent2"):
            sample["containers"][parts[0]] = {"mem": parts[1], "events": parts[2].strip()}
    return sample


def summarize_samples(samples: list) -> str:
    # free -m 的 Mem 行：Mem: total used free shared buff/cache available
    host_used, host_avail = [], []
    peaks = {c: [] for c in CONTAINERS}
    for s in samples:
        try:
            fields = s["host_mem"].split()
            host_used.append(int(fields[2]))
            host_avail.append(int(fields[6]))
        except Exception:
            pass
        for name, data in s["containers"].items():
            try:
                num, unit = data.get("mem", "").split("/")[0].split()
                value = float(num) * (1024**3 if "GiB" in unit else 1024**2 if "MiB" in unit else 1)
                peaks[name].append(value / 1024 / 1024)  # MB
            except Exception:
                pass
    out = [f"宿主机 used 峰值 {max(host_used)} MB / available 最低 {min(host_avail)} MB"]
    for name, series in peaks.items():
        if series:
            out.append(f"{name}: 内存峰值 {max(series):.0f} MB")
    return "; ".join(out)


def main() -> int:
    tenant_id, user_id = DATA_TENANT
    token = temporary_token(SSH_KEY, tenant_id, user_id)
    session = make_session(token, tenant_id)
    try:
        attachment = prepare_excel(session, 1_000_000)
        print(f"[upload] file_id={attachment['file_id']} size={attachment['size']}")

        samples, stop = [], threading.Event()

        def sampler():
            while not stop.is_set():
                try:
                    samples.append(host_sample())
                except Exception:
                    pass
                stop.wait(5)

        threading.Thread(target=sampler, daemon=True).start()

        chat = call(session, "POST", "/api/sessions",
                    json={"title": f"LOADTEST {RUN_ID} guard", "subagent_id": "main"})
        accepted = call(session, "POST", "/api/chat/runners", json={
            "client_request_id": f"agent2-load-{RUN_ID}-{uuid.uuid4().hex[:8]}",
            "message": PROMPT, "session_id": chat["session_id"],
            "files": [attachment], "subagent": "main",
        })
        runner_id = accepted["runner"]["runner_id"]
        print(f"[runner] {runner_id}")

        deadline = time.monotonic() + 900
        runner = {}
        while time.monotonic() < deadline:
            runner = call(session, "GET", f"/api/chat/runners/{runner_id}")["runner"]
            if runner["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                break
            time.sleep(5)
        stop.set()
        time.sleep(6)

        status = runner["status"]
        result = runner.get("result") or {}
        print(f"[status] {status} error_code={result.get('error_code')}")

        payload_like = run_sql(SSH_KEY, f"""
            SELECT payload::text FROM agent_runner_events
            WHERE runner_id = '{runner_id}';
        """)
        blob = payload_like.replace("\n", " ")
        guard_hits = blob.count("整簿转换上限")
        print(f"[guard] 拦截文案出现 {guard_hits} 次")
        ranges = sorted(set(
            seg[:16] for seg in __import__("re").findall(
                r"[A-J]\d{4,7}:[A-J]\d{4,7}", blob)))
        print(f"[read] 事件流中出现的 range 引用数: {len(ranges)}（示例前 5: {ranges[:5]}）")
        tool_names = sorted(set(__import__("re").findall(
            r'"(?:tool_name|name)"\s*:\s*"(excel_\w+|cp|read\w*)"', blob)))
        print(f"[tools] excel 相关工具调用名: {tool_names}")

        summary = summarize_samples(samples)
        print(f"[mem] {summary}")
        Path("/tmp/agent2_guard_regr_samples.json").write_text(
            json.dumps({"runner_id": runner_id, "status": status,
                        "samples": samples}, ensure_ascii=False, indent=1))
        return 0 if status == "completed" and guard_hits >= 1 else 1
    finally:
        run_sql(SSH_KEY, f"DELETE FROM tokens WHERE token = '{token}' RETURNING 1;")


if __name__ == "__main__":
    sys.exit(main())
