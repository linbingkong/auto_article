"""SQLite/MySQL 通用数据库连接适配层。

本地与测试默认使用 SQLite；生产设置 DATABASE_BACKEND=mysql 后，所有 Store
通过 PyMySQL 连接外部数据库。兼容层保留现有 conn.execute(...).fetchone() 调用方式，
并把 SQLite 占位符、UPSERT、事务锁语义转换为 MySQL。
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Union


@dataclass(frozen=True)
class MySQLConfig:
    host: str
    port: int
    user: str
    password: str = field(repr=False)
    database: str = "wechat_agent"
    connect_timeout: int = 10
    ssl_ca: str = ""


StorageTarget = Union[Path, MySQLConfig]
_verified: set[tuple[str, int, str, tuple[str, ...]]] = set()
_verify_lock = threading.Lock()


def database_target_from_env(sqlite_path: Path) -> StorageTarget:
    """按环境变量选择存储；未配置时保持 SQLite。"""
    backend = os.environ.get("DATABASE_BACKEND", "sqlite").strip().lower()
    if backend in {"", "sqlite"}:
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite_path
    if backend != "mysql":
        raise RuntimeError(f"不支持的数据库后端：{backend}")

    required = {
        "MYSQL_HOST": os.environ.get("MYSQL_HOST", "").strip(),
        "MYSQL_USER": os.environ.get("MYSQL_USER", "").strip(),
        "MYSQL_PASSWORD": os.environ.get("MYSQL_PASSWORD", ""),
        "MYSQL_DATABASE": os.environ.get("MYSQL_DATABASE", "wechat_agent").strip(),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"MySQL 配置缺失：{', '.join(missing)}")
    try:
        port = int(os.environ.get("MYSQL_PORT", "3306"))
        timeout = int(os.environ.get("MYSQL_CONNECT_TIMEOUT", "10"))
    except ValueError as exc:
        raise RuntimeError("MYSQL_PORT 和 MYSQL_CONNECT_TIMEOUT 必须是整数") from exc
    return MySQLConfig(
        host=required["MYSQL_HOST"],
        port=port,
        user=required["MYSQL_USER"],
        password=required["MYSQL_PASSWORD"],
        database=required["MYSQL_DATABASE"],
        connect_timeout=max(1, timeout),
        ssl_ca=os.environ.get("MYSQL_SSL_CA", "").strip(),
    )


def is_mysql(target: StorageTarget) -> bool:
    return isinstance(target, MySQLConfig)


def backend_name(target: StorageTarget) -> str:
    return "mysql" if is_mysql(target) else "sqlite"


def _translate_mysql_sql(sql: str) -> str:
    """把当前 Store 使用的 SQLite SQL 子集转换为 MySQL。"""
    translated = sql.replace("?", "%s")
    translated = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT IGNORE INTO", translated, flags=re.I)

    conflict = re.search(
        r"\bON\s+CONFLICT\s*\([^)]*\)\s+DO\s+UPDATE\s+SET\s+(.+)$",
        translated,
        flags=re.I | re.S,
    )
    if conflict:
        assignments = re.sub(
            r"\bexcluded\.([A-Za-z_][A-Za-z0-9_]*)",
            r"VALUES(\1)",
            conflict.group(1),
            flags=re.I,
        )
        translated = translated[: conflict.start()] + "ON DUPLICATE KEY UPDATE " + assignments

    # SQLite 的 MAX(a,b) 是标量函数；MySQL 对应 GREATEST(a,b)。
    translated = re.sub(r"\bMAX\(\s*0\s*,", "GREATEST(0,", translated, flags=re.I)
    translated = re.sub(
        r"\bMAX\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*VALUES\(",
        r"GREATEST(\1,VALUES(",
        translated,
        flags=re.I,
    )
    return translated


class MySQLConnectionCompat:
    """提供 sqlite3.Connection 子集的 PyMySQL 包装器。"""

    def __init__(self, config: MySQLConfig):
        try:
            import pymysql
            from pymysql.cursors import DictCursor
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("MySQL 后端需要安装 pymysql：pip install pymysql") from exc

        options: dict[str, Any] = {
            "host": config.host,
            "port": config.port,
            "user": config.user,
            "password": config.password,
            "database": config.database,
            "charset": "utf8mb4",
            "cursorclass": DictCursor,
            "connect_timeout": config.connect_timeout,
            "read_timeout": 30,
            "write_timeout": 30,
            "autocommit": False,
        }
        if config.ssl_ca:
            options["ssl"] = {"ca": config.ssl_ca}
        self._pymysql = pymysql
        try:
            self._conn = pymysql.connect(**options)
        except pymysql.err.OperationalError:
            # 公网链路偶发握手抖动：短暂等待后重试一次连接。
            time.sleep(1.5)
            self._conn = pymysql.connect(**options)
        self._write_transaction = False

    def execute(self, sql: str, params: Iterable[Any] = ()):
        stripped = sql.strip().rstrip(";")
        if re.fullmatch(r"BEGIN\s+IMMEDIATE", stripped, flags=re.I):
            self._conn.begin()
            self._write_transaction = True
            return self._conn.cursor()
        if stripped.upper().startswith("PRAGMA "):
            return self._conn.cursor()

        translated = _translate_mysql_sql(sql)
        if (
            self._write_transaction
            and translated.lstrip().upper().startswith("SELECT ")
            and " FOR UPDATE" not in translated.upper()
        ):
            translated = translated.rstrip().rstrip(";") + " FOR UPDATE"

        cursor = self._conn.cursor()
        try:
            cursor.execute(translated, tuple(params))
        except self._pymysql.err.IntegrityError as exc:
            # 保持 UserStore 等现有 sqlite3.IntegrityError 捕获逻辑。
            raise sqlite3.IntegrityError(str(exc)) from exc
        return cursor

    def executemany(self, sql: str, params: Iterable[Iterable[Any]]):
        cursor = self._conn.cursor()
        try:
            cursor.executemany(_translate_mysql_sql(sql), params)
        except self._pymysql.err.IntegrityError as exc:
            raise sqlite3.IntegrityError(str(exc)) from exc
        return cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._write_transaction = False
            self._conn.close()
        return False


def connect_database(target: StorageTarget):
    if isinstance(target, MySQLConfig):
        return MySQLConnectionCompat(target)
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def verify_mysql_tables(target: StorageTarget, tables: Iterable[str]) -> None:
    """MySQL 模式不在应用启动时改 DDL，只验证预建表存在。

    网络抖动时最多重试 3 次，并区分"连接失败"与"表缺失"两种错误。
    """
    if not isinstance(target, MySQLConfig):
        return
    names = tuple(sorted(set(tables)))
    key = (target.host, target.port, target.database, names)
    with _verify_lock:
        if key in _verified:
            return
    for table in names:
        if not re.fullmatch(r"[A-Za-z0-9_]+", table):
            raise RuntimeError(f"非法表名：{table}")
    import time

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            with connect_database(target) as conn:
                for table in names:
                    conn.execute(f"SELECT 1 FROM `{table}` LIMIT 1").fetchone()
            with _verify_lock:
                _verified.add(key)
            return
        except Exception as exc:
            last_exc = exc
            message = str(exc)
            if "doesn't exist" in message or "1146" in message:
                raise RuntimeError(
                    f"MySQL 表结构未就绪（{target.database}）：{', '.join(names)}；"
                    "请先执行 database/schema_mysql.sql"
                ) from exc
            if attempt < 2:
                time.sleep(2)
    raise RuntimeError(
        f"MySQL 连接失败（{target.host}:{target.port}/{target.database}）：{last_exc}；"
        "已自动重试 3 次，请检查服务器与网络状态后重启服务"
    ) from last_exc
