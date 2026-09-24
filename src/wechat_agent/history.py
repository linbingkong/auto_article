"""运行历史与复盘数据模块（SQLite/MySQL）。

保存每次生成的选题、文章标题、草稿 ID、状态与输出目录；
提供最近 N 天选题去重，以及后续接入阅读数据复盘的字段。
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set

from .database import StorageTarget, connect_database, is_mysql, verify_mysql_tables


class HistoryStore:
    """SQLite/MySQL 历史记录存储。"""

    def __init__(self, db_path: StorageTarget):
        self.db_path = db_path
        if is_mysql(db_path):
            verify_mysql_tables(db_path, ["articles"])
        else:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._init_db()

    def _connect(self):
        return connect_database(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT NOT NULL,
                    topic_norm TEXT NOT NULL,
                    source TEXT,
                    score REAL,
                    article_title TEXT,
                    draft_media_id TEXT,
                    status TEXT NOT NULL,
                    output_dir TEXT,
                    read_count INTEGER,
                    like_count INTEGER,
                    share_count INTEGER,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_topic_norm_time "
                "ON articles(topic_norm, created_at)"
            )

    @staticmethod
    def normalize_topic(title: str) -> str:
        return re.sub(r"[\s\W_]+", "", title).lower()[:50]

    def recent_topic_norms(self, days: int = 7) -> Set[str]:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT topic_norm FROM articles "
                "WHERE created_at >= ? AND status IN ('draft_created', 'published')",
                (cutoff,),
            ).fetchall()
        return {row["topic_norm"] for row in rows}

    def record(
        self,
        *,
        topic: str,
        source: str,
        score: float,
        article_title: str,
        status: str,
        output_dir: str,
        draft_media_id: str = "",
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO articles
                (topic, topic_norm, source, score, article_title, draft_media_id,
                 status, output_dir, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    topic,
                    self.normalize_topic(topic),
                    source,
                    score,
                    article_title,
                    draft_media_id,
                    status,
                    output_dir,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            return int(cur.lastrowid)

    def update_metrics(
        self,
        record_id: int,
        *,
        read_count: Optional[int] = None,
        like_count: Optional[int] = None,
        share_count: Optional[int] = None,
    ) -> None:
        """回填阅读/点赞/转发数据（后续可由微信数据接口或人工录入）。"""
        fields, values = [], []
        for name, value in (
            ("read_count", read_count),
            ("like_count", like_count),
            ("share_count", share_count),
        ):
            if value is not None:
                fields.append(f"{name} = ?")
                values.append(value)
        if not fields:
            return
        values.append(record_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE articles SET {', '.join(fields)} WHERE id = ?", values
            )

    def latest(self, limit: int = 20) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM articles ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

