"""验证子进程隔离方案是否能解决 gunicorn worker 静默退出问题。

背景：
- 容器内独立 python 进程调 verifier.verify_archive_server_mode() 成功
- HTTP 请求 verify 时 gunicorn worker 静默退出（RemoteDisconnected）
- 推测：worker 主进程已加载 cryptography/psycopg 等 C 扩展，
  后加载 SDK 的 .so 跟它们在同一地址空间冲突

本脚本模拟 gunicorn worker 主进程行为：
1. 主进程加载 cryptography/psycopg（模拟 worker 启动）
2. fork spawn 子进程调 verifier
3. 子进程结束 → C 堆污染销毁，主进程干净

用法：
    docker cp scripts/test_archive_subprocess.py aid-agent-api2:/tmp/
    docker exec aid-agent-api2 python /tmp/test_archive_subprocess.py
"""

import asyncio
import multiprocessing as mp
import os
import sys


def child_verify(config_data: dict, tenant_id: str) -> dict:
    """子进程入口：独立加载 .so，独立 C 堆。"""
    import asyncio
    from src.channels.wecom_personal_rpa.archive import verifier
    return asyncio.run(
        verifier.verify_archive_server_mode(config_data, tenant_id)
    )


def main():
    # === 步骤 1：主进程加载 C 扩展（模拟 gunicorn worker 启动） ===
    from cryptography.hazmat.primitives.ciphers import Cipher  # noqa
    import psycopg2  # noqa
    from src.db.database import init_postgres_pool
    init_postgres_pool()
    print(f"主进程已加载 C 扩展，PID = {os.getpid()}")

    # === 步骤 2：拿配置 ===
    from src.saas.db.channel_config_db import ChannelConfigDB
    cfg = ChannelConfigDB.get_by_id_decrypted("chan_616337ad19f6")
    config_data = cfg.get("config") or {}
    print(f"config 加载完成 listen_mode={config_data.get('listen_mode')}")

    # === 步骤 3：spawn 子进程调 verifier ===
    ctx = mp.get_context("spawn")
    with ctx.Pool(1) as pool:
        result = pool.apply(child_verify, (config_data, "tenant_9eb3e45cab83"))
    print(f"子进程返回: {result}")

    # === 步骤 4：主进程继续存活，证明没崩 ===
    print("主进程仍然存活，PID =", os.getpid())


if __name__ == "__main__":
    main()
