"""操作审计模块：记录关键操作日志。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .database import StorageTarget, connect_database, is_mysql, verify_mysql_tables


class AuditStore:
    """SQLite/MySQL 审计日志存储。"""

    def __init__(self, db_path: StorageTarget):
        self.db_path = db_path
        if is_mysql(db_path):
            verify_mysql_tables(db_path, ["audit_logs"])
        else:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._init_db()

    def _connect(self) -> Any:
        return connect_database(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    username TEXT,
                    action TEXT NOT NULL,
                    resource TEXT,
                    resource_id TEXT,
                    detail TEXT,
                    ip TEXT,
                    user_agent TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_user_time ON audit_logs(user_id, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action)"
            )

    def record(
        self,
        *,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        action: str,
        resource: Optional[str] = None,
        resource_id: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> str:
        """记录审计日志，返回日志 ID。"""
        log_id = uuid.uuid4().hex
        now = datetime.now().isoformat(timespec="seconds")
        detail_json = json.dumps(detail, ensure_ascii=False) if detail else None

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_logs
                (id, user_id, username, action, resource, resource_id, detail, ip, user_agent, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (log_id, user_id, username, action, resource, resource_id, detail_json, ip, user_agent, now),
            )
        return log_id

    def list_logs(
        self,
        *,
        user_id: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict]:
        """查询审计日志。"""
        conditions = []
        values = []

        if user_id:
            conditions.append("user_id = ?")
            values.append(user_id)
        if action:
            conditions.append("action = ?")
            values.append(action)

        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        values.extend([limit, offset])

        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM audit_logs{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                values,
            ).fetchall()

        results = []
        for row in rows:
            item = dict(row)
            if item.get("detail"):
                try:
                    item["detail"] = json.loads(item["detail"])
                except Exception:
                    pass
            results.append(item)
        return results

    def count_logs(self, *, user_id: Optional[str] = None, action: Optional[str] = None) -> int:
        """统计日志总数。"""
        conditions = []
        values = []

        if user_id:
            conditions.append("user_id = ?")
            values.append(user_id)
        if action:
            conditions.append("action = ?")
            values.append(action)

        where = " WHERE " + " AND ".join(conditions) if conditions else ""

        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) as cnt FROM audit_logs{where}",
                values,
            ).fetchone()
        return row["cnt"]