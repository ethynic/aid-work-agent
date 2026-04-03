"""
修复向量表维度不一致问题

使用方式:
    python scripts/fix_vector_table.py

注意：此脚本会删除旧的向量数据！
"""

import sqlite3
import sys
import os
import shutil
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite_vec


def get_db_path():
    database_url = os.environ.get("DATABASE_URL", "sqlite:///./aid_work_agent.db")
    return database_url.replace("sqlite:///", "")


def fix_vector_table(db_path: str, dimension: int = 1024):
    """修复向量表维度"""
    print(f"原始数据库路径: {db_path}")

    if not os.path.exists(db_path):
        print(f"错误：数据库文件不存在: {db_path}")
        sys.exit(1)

    # 检查是否挂载目录（容易出现 I/O 问题）
    is_docker_mount = os.path.exists("/.dockerenv") or os.environ.get("DOCKER_CONTAINER")
    if is_docker_mount and not db_path.startswith("/app"):
        print("检测到 Docker 容器环境，将数据库复制到临时目录操作...")

        # 复制到临时文件
        temp_dir = tempfile.mkdtemp()
        temp_db_path = os.path.join(temp_dir, "temp.db")
        shutil.copy2(db_path, temp_db_path)
        print(f"数据库已复制到临时文件: {temp_db_path}")

        conn = sqlite3.connect(temp_db_path)
    else:
        temp_db_path = None
        conn = sqlite3.connect(db_path)

    sqlite_vec.load(conn)
    cursor = conn.cursor()

    # 检查向量表是否存在
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='chunks_vec'
    """)
    table_exists = cursor.fetchone() is not None

    if table_exists:
        print("删除旧的 chunks_vec 表...")
        cursor.execute("DROP TABLE IF EXISTS chunks_vec")
        print("已删除旧的 chunks_vec 表")
    else:
        print("chunks_vec 表不存在，无需删除")

    # 创建新的向量表
    print(f"创建新的 chunks_vec 表 (dimension={dimension})...")
    cursor.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
            chunk_id INTEGER PRIMARY KEY,
            embedding float[{dimension}]
        )
    """)
    conn.commit()
    print("向量表重建成功!")

    # 验证
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='chunks_vec'
    """)
    result = cursor.fetchone()
    if result:
        print("验证：chunks_vec 表已创建")
    else:
        print("验证失败：chunks_vec 表未找到")

    conn.close()

    # 如果是临时文件，复制回原位置
    if temp_db_path:
        print(f"复制回原始位置: {db_path}")
        shutil.copy2(temp_db_path, db_path)
        os.remove(temp_db_path)
        os.rmdir(temp_dir)
        print("数据库已更新!")


if __name__ == "__main__":
    db_path = get_db_path()
    dimension = 1024
    if len(sys.argv) > 1:
        dimension = int(sys.argv[1])

    fix_vector_table(db_path, dimension)