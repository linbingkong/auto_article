#!/usr/bin/env python3
"""SQLite → MySQL 一次性数据迁移脚本。

把 output/auth.db（用户/额度/支付/审计/权限）和 output/history.db
（生成与发布记录）迁移到生产 MySQL（先执行 database/schema_mysql.sql）。

用法示例：
    pip install pymysql
    python database/migrate_sqlite_to_mysql.py --sqlite output/auth.db \
        --host 127.0.0.1 --user root --password '***' --database wechat_agent
    python database/migrate_sqlite_to_mysql.py --sqlite output/history.db \
        --host 127.0.0.1 --user root --password '***' --database wechat_agent

支持 --dry-run 只统计不写入。脚本可重复执行：目标表已有同主键数据时跳过。
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import pymysql
except ImportError:  # pragma: no cover
    sys.exit("请先安装 pymysql：pip install pymysql")

# auth.db 的 10 张表 + history.db 的 articles
TABLES = [
    "users",
    "user_api_configs",
    "user_generation_accounts",
    "trial_identities",
    "generation_usage",
    "credit_adjustments",
    "payment_orders",
    "payment_events",
    "article_permissions",
    "audit_logs",
    "articles",
]


def convert(value):
    """SQLite 值 → MySQL 值；仅处理 bytes（理论上不出现）。"""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def run_schema(cur, schema_path: Path, database: str, include_database: bool = True) -> None:
    """执行建库脚本，并安全替换目标数据库名称。"""
    if not re.fullmatch(r"[A-Za-z0-9_]+", database):
        raise ValueError("数据库名只能包含字母、数字和下划线")
    sql = schema_path.read_text(encoding="utf-8-sig")
    sql = sql.replace(
        "CREATE DATABASE IF NOT EXISTS wechat_agent",
        f"CREATE DATABASE IF NOT EXISTS `{database}`",
    ).replace("USE wechat_agent", f"USE `{database}`")
    for statement in sql.split(";"):
        statement = statement.strip()
        lines = [ln for ln in statement.splitlines() if ln.strip() and not ln.strip().startswith("--")]
        if not lines:
            continue
        first_sql = lines[0].strip().upper()
        if not include_database and (first_sql.startswith("CREATE DATABASE") or first_sql.startswith("USE ")):
            continue
        cur.execute(statement)


def main() -> None:
    ap = argparse.ArgumentParser(description="SQLite → MySQL 数据迁移")
    ap.add_argument("--sqlite", required=True, help="源 SQLite 文件，如 output/auth.db")
    ap.add_argument("--schema", default=str(Path(__file__).with_name("schema_mysql.sql")),
                    help="MySQL 建库脚本（默认同目录 schema_mysql.sql）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3306)
    ap.add_argument("--user", default="root")
    ap.add_argument("--password", default=os.environ.get("MYSQL_PASSWORD", ""),
                    help="MySQL 密码；建议通过 MYSQL_PASSWORD 环境变量传入")
    ap.add_argument("--database", default="wechat_agent")
    ap.add_argument("--database-exists", action="store_true",
                    help="数据库已由管理员创建；跳过 CREATE DATABASE/USE，适合受限迁移账号")
    ap.add_argument("--batch", type=int, default=500)
    ap.add_argument("--dry-run", action="store_true", help="只统计行数，不写入")
    args = ap.parse_args()

    src = sqlite3.connect(args.sqlite)
    src.row_factory = sqlite3.Row

    conn = None
    cur = None
    if not args.dry_run:
        connect_options = dict(
            host=args.host, port=args.port, user=args.user, password=args.password,
            charset="utf8mb4", autocommit=False,
        )
        if args.database_exists:
            connect_options["database"] = args.database
        conn = pymysql.connect(**connect_options)
        cur = conn.cursor()
        print(f"执行建库脚本 {args.schema} ...")
        run_schema(cur, Path(args.schema), args.database, include_database=not args.database_exists)
        conn.commit()

    total = 0
    for table in TABLES:
        try:
            rows = src.execute(f"SELECT * FROM {table}").fetchall()
        except sqlite3.OperationalError:
            print(f"[跳过] {table}：源库中不存在")
            continue
        if not rows:
            print(f"[空表] {table}")
            continue

        columns = list(rows[0].keys())
        primary_column = columns[0]
        insert_sql = (
            f"INSERT INTO `{table}` ({','.join(f'`{c}`' for c in columns)}) "
            f"VALUES ({','.join(['%s'] * len(columns))}) "
            f"ON DUPLICATE KEY UPDATE `{primary_column}`=VALUES(`{primary_column}`)"
        )
        payload = [[convert(v) for v in row] for row in rows]

        if args.dry_run:
            print(f"[统计] {table}: {len(payload)} 行")
            total += len(payload)
            continue

        inserted = 0
        for i in range(0, len(payload), args.batch):
            chunk = payload[i : i + args.batch]
            inserted += cur.executemany(insert_sql, chunk) or 0
        conn.commit()
        cur.execute(f"SELECT COUNT(*) FROM `{table}`")
        target_count = int(cur.fetchone()[0])
        print(f"[完成] {table}: 源 {len(payload)} 行，新增 {inserted} 行，目标共 {target_count} 行")
        total += inserted

    # articles 使用自增主键，迁移后校正 AUTO_INCREMENT 起点
    if not args.dry_run:
        cur.execute("SELECT COALESCE(MAX(id), 0) FROM articles")
        max_id = int(cur.fetchone()[0])
        if max_id:
            cur.execute(f"ALTER TABLE articles AUTO_INCREMENT = {max_id + 1}")
            conn.commit()

    print(f"合计迁移 {total} 行" + ("（dry-run）" if args.dry_run else ""))
    src.close()
    if conn:
        conn.close()


if __name__ == "__main__":
    main()
