"""重建知识库向量表（删除旧的 chunks_vec 表）"""
import sqlite_vec
import sqlite3

DB_PATH = "/app/aid_work_agent.db"

conn = sqlite3.connect(DB_PATH)
sqlite_vec.load(conn)
cursor = conn.cursor()

# 查看现有表结构
cursor.execute("SELECT sql FROM sqlite_master WHERE name='chunks_vec'")
row = cursor.fetchone()
if row:
    print(f"当前表结构: {row[0]}")
else:
    print("chunks_vec 表不存在，无需操作")

# 删除旧向量表
cursor.execute("DROP TABLE IF EXISTS chunks_vec")
conn.commit()
print("已删除 chunks_vec 表，下次上传文档时会自动用 1024 维重建")

conn.close()
