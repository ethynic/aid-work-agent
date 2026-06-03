#!/usr/bin/env python3
"""在 docker compose down 之前清理数据库残留连接"""
import psycopg2
import os

apps = ['aid-work-agent', 'aid-work-agent-logs']
for key in ('DATABASE_URL', 'LOGS_DATABASE_URL'):
    url = os.environ.get(key, '')
    if not url:
        continue
    try:
        conn = psycopg2.connect(url)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE application_name = ANY(%s) AND pid <> pg_backend_pid()",
            (apps,)
        )
        cur.close()
        conn.close()
        # 从 URL 中提取 db@host 用于显示
        display = url.split('@')[1] if '@' in url else url
        print(f"OK {display}")
    except Exception as e:
        print(f"SKIP {e}")
