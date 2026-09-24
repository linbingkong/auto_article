"""文章归属与按文章共享权限。"""
from __future__ import annotations
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from .database import StorageTarget, connect_database, is_mysql, verify_mysql_tables

class ArticleAccessStore:
    def __init__(self, db_path: StorageTarget):
        self.db_path=db_path
        if is_mysql(db_path): verify_mysql_tables(db_path,["article_permissions"])
        else: self._init()
    def _connect(self):
        return connect_database(self.db_path)
    def _init(self):
        with self._connect() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS article_permissions(
            article_id TEXT NOT NULL,user_id TEXT NOT NULL,can_view INTEGER NOT NULL DEFAULT 1,
            can_edit INTEGER NOT NULL DEFAULT 0,can_push INTEGER NOT NULL DEFAULT 0,
            granted_by TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
            PRIMARY KEY(article_id,user_id))""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_article_permissions_user ON article_permissions(user_id)")
    def grants(self, article_id:str)->List[Dict]:
        with self._connect() as c: rows=c.execute("SELECT * FROM article_permissions WHERE article_id=?",(article_id,)).fetchall()
        return [dict(r) for r in rows]
    def user_grant(self,article_id:str,user_id:str)->Dict:
        with self._connect() as c:r=c.execute("SELECT * FROM article_permissions WHERE article_id=? AND user_id=?",(article_id,user_id)).fetchone()
        return dict(r) if r else {}
    def set_grant(self,article_id:str,user_id:str,permissions:List[str],granted_by:str)->Dict:
        view=int(bool(set(permissions)&{'view','edit','push'}));edit=int('edit' in permissions);push=int('push' in permissions);now=datetime.now().isoformat(timespec='seconds')
        with self._connect() as c:c.execute("""INSERT INTO article_permissions(article_id,user_id,can_view,can_edit,can_push,granted_by,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(article_id,user_id) DO UPDATE SET can_view=excluded.can_view,can_edit=excluded.can_edit,can_push=excluded.can_push,granted_by=excluded.granted_by,updated_at=excluded.updated_at""",(article_id,user_id,view,edit,push,granted_by,now,now))
        return self.user_grant(article_id,user_id)
    def revoke(self,article_id:str,user_id:str):
        with self._connect() as c:c.execute("DELETE FROM article_permissions WHERE article_id=? AND user_id=?",(article_id,user_id))
    def revoke_user(self,user_id:str):
        with self._connect() as c:c.execute("DELETE FROM article_permissions WHERE user_id=?",(user_id,))
    def clear_article(self,article_id:str):
        with self._connect() as c:c.execute("DELETE FROM article_permissions WHERE article_id=?",(article_id,))
    def allowed(self,meta:Dict,user:Dict,action:str)->bool:
        if user['role']=='admin':return True
        if meta.get('owner_user_id')==user['id']:
            if action=='push':return user['role']=='editor'
            if action in {'edit','delete'}:return user['role'] in {'editor','creator'}
            return True
        grant=self.user_grant(meta.get('id',''),user['id'])
        if action=='view':return bool(grant.get('can_view'))
        if action=='edit':return user['role'] in {'editor','creator'} and bool(grant.get('can_edit'))
        if action=='push':return user['role']=='editor' and bool(grant.get('can_push'))
        return False
