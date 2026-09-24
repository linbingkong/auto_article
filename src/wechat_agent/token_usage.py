"""LLM Token 用量持久化与管理员成本看板聚合。"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List

from .database import MySQLConfig, StorageTarget, connect_database, verify_mysql_tables


class TokenUsageStore:
    TABLES = ["llm_token_usage"]
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS llm_token_usage(
      id TEXT PRIMARY KEY, task_id TEXT NOT NULL, article_id TEXT, user_id TEXT NOT NULL,
      username TEXT NOT NULL DEFAULT '', generation_mode TEXT NOT NULL DEFAULT 'platform',
      stage TEXT NOT NULL DEFAULT 'unknown', provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
      prompt_tokens INTEGER NOT NULL DEFAULT 0, completion_tokens INTEGER NOT NULL DEFAULT 0,
      total_tokens INTEGER NOT NULL DEFAULT 0, cached_tokens INTEGER NOT NULL DEFAULT 0,
      reasoning_tokens INTEGER NOT NULL DEFAULT 0, estimated INTEGER NOT NULL DEFAULT 0,
      input_price_per_million REAL NOT NULL DEFAULT 0, output_price_per_million REAL NOT NULL DEFAULT 0,
      estimated_cost_micro_yuan INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_llm_token_usage_task ON llm_token_usage(task_id);
    CREATE INDEX IF NOT EXISTS idx_llm_token_usage_created ON llm_token_usage(created_at);
    CREATE INDEX IF NOT EXISTS idx_llm_token_usage_user_created ON llm_token_usage(user_id,created_at);
    """

    def __init__(self, target: StorageTarget):
        self.target = target
        if isinstance(target, MySQLConfig):
            verify_mysql_tables(target, self.TABLES)
        else:
            with connect_database(target) as conn:
                conn.executescript(self.SCHEMA)

    def record(self, *, task_id: str, article_id: str, user_id: str, username: str,
               generation_mode: str, usage: Dict[str, Any], input_price_per_million: float = 0,
               output_price_per_million: float = 0) -> Dict[str, Any]:
        prompt = max(0, int(usage.get("prompt_tokens") or 0))
        completion = max(0, int(usage.get("completion_tokens") or 0))
        total = max(0, int(usage.get("total_tokens") or prompt + completion))
        input_price, output_price = max(0.0, float(input_price_per_million or 0)), max(0.0, float(output_price_per_million or 0))
        row = {
            "id": uuid.uuid4().hex, "task_id": task_id, "article_id": article_id or None,
            "user_id": user_id, "username": username or "", "generation_mode": generation_mode or "platform",
            "stage": str(usage.get("stage") or "unknown")[:64], "provider": str(usage.get("provider") or "")[:128],
            "model": str(usage.get("model") or "")[:128], "prompt_tokens": prompt,
            "completion_tokens": completion, "total_tokens": total,
            "cached_tokens": max(0, int(usage.get("cached_tokens") or 0)),
            "reasoning_tokens": max(0, int(usage.get("reasoning_tokens") or 0)),
            "estimated": 1 if usage.get("estimated") else 0,
            "input_price_per_million": input_price, "output_price_per_million": output_price,
            "estimated_cost_micro_yuan": int(round(prompt * input_price + completion * output_price)),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        columns = list(row)
        with connect_database(self.target) as conn:
            conn.execute(f"INSERT INTO llm_token_usage({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                         tuple(row[column] for column in columns))
        return row

    def link_article(self, task_id: str, article_id: str) -> None:
        if task_id and article_id:
            with connect_database(self.target) as conn:
                conn.execute("UPDATE llm_token_usage SET article_id=? WHERE task_id=? AND (article_id IS NULL OR article_id='')", (article_id, task_id))

    def _rows(self, days: int, user_id: str = "", model: str = "") -> List[Dict[str, Any]]:
        since = (datetime.now() - timedelta(days=max(1, min(days, 3650)))).isoformat(timespec="seconds")
        clauses, params = ["created_at>=?"], [since]
        if user_id: clauses.append("user_id=?"); params.append(user_id)
        if model: clauses.append("model=?"); params.append(model)
        with connect_database(self.target) as conn:
            rows = conn.execute("SELECT * FROM llm_token_usage WHERE " + " AND ".join(clauses) + " ORDER BY created_at DESC LIMIT 20000", tuple(params)).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _finish(bucket: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(bucket)
        result["estimated_cost_yuan"] = round(int(result.pop("estimated_cost_micro_yuan", 0)) / 1_000_000, 6)
        return result

    def dashboard(self, days: int = 30, limit: int = 100, user_id: str = "", model: str = "") -> Dict[str, Any]:
        rows = self._rows(days, user_id, model)
        totals, by_day, by_model, by_stage, by_task = defaultdict(int), {}, {}, {}, {}
        fields = ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "reasoning_tokens", "estimated_cost_micro_yuan")
        def add(bucket, row):
            bucket["calls"] = bucket.get("calls", 0) + 1
            for field in fields: bucket[field] = bucket.get(field, 0) + int(row.get(field) or 0)
            bucket["has_estimated_usage"] = bool(bucket.get("has_estimated_usage") or row.get("estimated"))
        for row in rows:
            add(totals, row)
            day = str(row.get("created_at") or "")[:10] or "unknown"; add(by_day.setdefault(day, {"date": day}), row)
            model_name = str(row.get("model") or "unknown"); add(by_model.setdefault(model_name, {"model": model_name}), row)
            stage = str(row.get("stage") or "unknown"); add(by_stage.setdefault(stage, {"stage": stage}), row)
            tid = str(row.get("task_id") or "")
            task = by_task.setdefault(tid, {"task_id": tid, "article_id": row.get("article_id") or "", "user_id": row.get("user_id") or "", "username": row.get("username") or "", "generation_mode": row.get("generation_mode") or "", "model": row.get("model") or "", "provider": row.get("provider") or "", "created_at": row.get("created_at") or "", "stages": []})
            if row.get("article_id"): task["article_id"] = row["article_id"]
            task["created_at"] = min(task["created_at"], row.get("created_at") or task["created_at"])
            if stage not in task["stages"]: task["stages"].append(stage)
            add(task, row)
        tasks = sorted((self._finish(x) for x in by_task.values()), key=lambda x: x["created_at"], reverse=True)
        summary = self._finish(totals); generations = len(by_task)
        summary.update({"generations": generations,
                        "avg_tokens_per_generation": round(summary.get("total_tokens", 0) / generations) if generations else 0,
                        "avg_cost_yuan_per_generation": round(summary.get("estimated_cost_yuan", 0) / generations, 6) if generations else 0})
        return {"days": days, "summary": summary,
                "daily": [self._finish(by_day[k]) for k in sorted(by_day)],
                "by_model": sorted((self._finish(x) for x in by_model.values()), key=lambda x: x.get("total_tokens", 0), reverse=True),
                "by_stage": sorted((self._finish(x) for x in by_stage.values()), key=lambda x: x.get("total_tokens", 0), reverse=True),
                "generations": tasks[:max(1, min(limit, 500))], "filters": {"user_id": user_id, "model": model}}

    def task_detail(self, task_id: str) -> Dict[str, Any]:
        with connect_database(self.target) as conn:
            rows = conn.execute("SELECT * FROM llm_token_usage WHERE task_id=? ORDER BY created_at,id", (task_id,)).fetchall()
        items = []
        for raw in rows:
            row = dict(raw); row["estimated_cost_yuan"] = round(int(row.pop("estimated_cost_micro_yuan", 0)) / 1_000_000, 6); items.append(row)
        return {"task_id": task_id, "items": items}
