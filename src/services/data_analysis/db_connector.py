"""
数据分析 - 数据库连接器

支持 MySQL 和 PostgreSQL 的远程数据库连接、表结构探测和数据采样。
"""

from typing import Any, Dict, List

from loguru import logger
from sqlalchemy import create_engine, inspect, text


class DatabaseConnector:
    """远程数据库连接器"""

    # 连接超时（秒）
    CONNECT_TIMEOUT = 10

    def test_connection(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        测试数据库连接。

        Args:
            config: {
                db_type, host, port, database_name, username, password
            }

        Returns:
            {"success": True/False, "version": "...", "error": "..."}
        """
        engine = None
        try:
            url = self._build_url(config)
            engine = create_engine(
                url,
                connect_args={"connect_timeout": self.CONNECT_TIMEOUT},
                pool_pre_ping=True,
            )
            with engine.connect() as conn:
                result = conn.execute(text("SELECT version()"))
                version_info = result.scalar()
                return {"success": True, "version": str(version_info)}
        except Exception as e:
            logger.warning(f"数据库连接测试失败: {e}")
            return {"success": False, "error": str(e)}
        finally:
            if engine:
                engine.dispose()

    def list_tables(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        列出远程数据库的所有表及其列信息。

        Args:
            config: 连接配置字典

        Returns:
            [{"table_name": str, "row_count": int, "columns": [{"name": str, "type": str}]}]
        """
        engine = None
        try:
            url = self._build_url(config)
            engine = create_engine(
                url,
                connect_args={"connect_timeout": self.CONNECT_TIMEOUT},
                pool_pre_ping=True,
            )
            inspector = inspect(engine)
            table_names = inspector.get_table_names()

            results = []
            with engine.connect() as conn:
                for table_name in table_names:
                    # 获取列信息
                    columns_info = inspector.get_columns(table_name)
                    columns = [
                        {"name": col["name"], "type": str(col["type"])}
                        for col in columns_info
                    ]

                    # 获取行数估算
                    row_count = self._get_row_count(conn, table_name, config["db_type"])

                    results.append({
                        "table_name": table_name,
                        "row_count": row_count,
                        "columns": columns,
                    })

            return results
        except Exception as e:
            logger.opt(exception=True).error(f"列出远程表失败: {e}")
            raise
        finally:
            if engine:
                engine.dispose()

    def get_table_ddl(
        self, config: Dict[str, Any], table_name: str
    ) -> str:
        """
        获取远程表的 DDL（CREATE TABLE 语句）。

        Args:
            config: 连接配置字典
            table_name: 表名

        Returns:
            DDL 字符串
        """
        engine = None
        try:
            url = self._build_url(config)
            engine = create_engine(
                url,
                connect_args={"connect_timeout": self.CONNECT_TIMEOUT},
                pool_pre_ping=True,
            )
            db_type = config["db_type"]

            with engine.connect() as conn:
                if db_type == "postgresql":
                    result = conn.execute(
                        text(
                            "SELECT pg_get_tabledef(CAST(:t AS text))"
                        ),
                        {"t": table_name},
                    )
                    row = result.fetchone()
                    if row:
                        return str(row[0])

                elif db_type == "mysql":
                    result = conn.execute(
                        text("SHOW CREATE TABLE :t"),
                        {"t": table_name},
                    )
                    row = result.fetchone()
                    if row:
                        return str(row[1])  # 第二列是 CREATE TABLE 语句

                # 通用 fallback: 用 inspector 构建
                return self._build_ddl_from_inspector(engine, table_name)

        except Exception as e:
            logger.warning(f"获取 DDL 失败，使用 inspector 构建: {e}")
            try:
                return self._build_ddl_from_inspector(engine, table_name)
            except Exception as e2:
                logger.error(f"构建 DDL 也失败: {e2}")
                return f"-- 无法获取 {table_name} 的 DDL: {e}"
        finally:
            if engine:
                engine.dispose()

    def get_table_sample(
        self, config: Dict[str, Any], table_name: str, limit: int = 20
    ) -> Dict[str, Any]:
        """
        获取远程表的采样数据。

        Args:
            config: 连接配置字典
            table_name: 表名
            limit: 采样行数

        Returns:
            {"columns": [{"name": str, "type": str}], "sample_data": List[List], "rows": int}
        """
        engine = None
        try:
            url = self._build_url(config)
            engine = create_engine(
                url,
                connect_args={"connect_timeout": self.CONNECT_TIMEOUT},
                pool_pre_ping=True,
            )
            inspector = inspect(engine)
            columns_info = inspector.get_columns(table_name)
            columns = [
                {"name": col["name"], "type": str(col["type"])}
                for col in columns_info
            ]

            with engine.connect() as conn:
                # 采样数据
                result = conn.execute(
                    text(f"SELECT * FROM {table_name} LIMIT :lim"),
                    {"lim": limit},
                )
                rows = result.fetchall()
                sample_data = []
                for row in rows:
                    sample_data.append([self._clean_value(v) for v in row])

                # 行数
                row_count = self._get_row_count(conn, table_name, config["db_type"])

            return {
                "columns": columns,
                "sample_data": sample_data,
                "rows": row_count,
            }
        except Exception as e:
            logger.opt(exception=True).error(f"获取表采样数据失败: {e}")
            raise
        finally:
            if engine:
                engine.dispose()

    def _build_url(self, config: Dict[str, Any]) -> str:
        """构建 SQLAlchemy 连接 URL"""
        db_type = config["db_type"]
        host = config.get("host", "localhost")
        port = config.get("port", 5432)
        database = config.get("database_name", "")
        username = config.get("username", "")
        password = config.get("password", "")

        if db_type == "postgresql":
            default_port = 5432
            driver = "postgresql+psycopg2"
        elif db_type == "mysql":
            default_port = 3306
            driver = "mysql+pymysql"
        else:
            raise ValueError(f"不支持的数据库类型: {db_type}")

        port = port or default_port

        # URL encode 密码中的特殊字符
        from urllib.parse import quote_plus
        encoded_password = quote_plus(password)

        return f"{driver}://{username}:{encoded_password}@{host}:{port}/{database}"

    def _get_row_count(self, conn, table_name: str, db_type: str) -> int:
        """获取表的行数估算"""
        try:
            if db_type == "postgresql":
                # 使用 pg_class 估算（快，不扫描全表）
                result = conn.execute(
                    text(
                        "SELECT reltuples::bigint FROM pg_class "
                        "WHERE relname = :t"
                    ),
                    {"t": table_name},
                )
                row = result.fetchone()
                if row and row[0] is not None and row[0] >= 0:
                    return int(row[0])

            # fallback: COUNT(*)
            result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
            return result.scalar()
        except Exception:
            return -1

    def _build_ddl_from_inspector(self, engine, table_name: str) -> str:
        """用 SQLAlchemy inspector 构建 CREATE TABLE DDL"""
        inspector = inspect(engine)
        columns_info = inspector.get_columns(table_name)
        pk_info = inspector.get_pk_constraint(table_name)

        pk_columns = pk_info.get("constrained_columns", []) if pk_info else []

        lines = [f"CREATE TABLE {table_name} ("]
        col_lines = []
        for col in columns_info:
            col_def = f"  {col['name']} {col['type']}"
            if col.get("primary_key") or col["name"] in pk_columns:
                col_def += " PRIMARY KEY"
            if not col.get("nullable", True):
                col_def += " NOT NULL"
            col_lines.append(col_def)

        lines.append(",\n".join(col_lines))
        lines.append(");")
        return "\n".join(lines)

    @staticmethod
    def _clean_value(value) -> Any:
        """清洗值为 JSON 可序列化格式"""
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value
