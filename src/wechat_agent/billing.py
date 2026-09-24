"""按次生成额度：试用赠送、付费额度、原子预占/结算与流水。"""
from __future__ import annotations
import sqlite3, uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List
from .auth import AuthError
from .database import StorageTarget, connect_database, is_mysql, verify_mysql_tables

class BillingStore:
    TABLES = ["user_generation_accounts", "trial_identities", "generation_usage", "credit_adjustments", "payment_orders", "payment_events"]
    def __init__(self, db_path: StorageTarget):
        self.db_path=db_path
        if is_mysql(db_path): verify_mysql_tables(db_path,self.TABLES)
        else:
            Path(db_path).parent.mkdir(parents=True,exist_ok=True); self._init_db()
    def _connect(self):
        return connect_database(self.db_path)
    @staticmethod
    def _now(): return datetime.now().isoformat(timespec="seconds")
    def _init_db(self):
        with self._connect() as c:c.executescript("""
        CREATE TABLE IF NOT EXISTS user_generation_accounts(user_id TEXT PRIMARY KEY,trial_total INTEGER NOT NULL DEFAULT 0 CHECK(trial_total>=0),trial_used INTEGER NOT NULL DEFAULT 0 CHECK(trial_used>=0),trial_reserved INTEGER NOT NULL DEFAULT 0 CHECK(trial_reserved>=0),paid_credits INTEGER NOT NULL DEFAULT 0 CHECK(paid_credits>=0),paid_reserved INTEGER NOT NULL DEFAULT 0 CHECK(paid_reserved>=0),created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS trial_identities(phone_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL,granted_count INTEGER NOT NULL,granted_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS generation_usage(id TEXT PRIMARY KEY,task_id TEXT NOT NULL UNIQUE,user_id TEXT NOT NULL,article_id TEXT,source TEXT NOT NULL CHECK(source IN ('trial','paid')),status TEXT NOT NULL CHECK(status IN ('reserved','consumed','released')),unit_price_fen INTEGER NOT NULL DEFAULT 0,failure_reason TEXT,created_at TEXT NOT NULL,settled_at TEXT);
        CREATE INDEX IF NOT EXISTS idx_generation_usage_user_created ON generation_usage(user_id,created_at DESC);
        CREATE TABLE IF NOT EXISTS credit_adjustments(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,delta INTEGER NOT NULL,balance_after INTEGER NOT NULL,reason TEXT NOT NULL,operator_user_id TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS payment_orders(id TEXT PRIMARY KEY,order_no TEXT NOT NULL UNIQUE,user_id TEXT NOT NULL,amount_fen INTEGER NOT NULL,credit_count INTEGER NOT NULL,unit_price_fen INTEGER NOT NULL,status TEXT NOT NULL CHECK(status IN ('pending','paid','closed','failed','refunded')),provider TEXT NOT NULL,code_url TEXT,provider_transaction_id TEXT,created_at TEXT NOT NULL,expires_at TEXT NOT NULL,paid_at TEXT,updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_payment_orders_user_created ON payment_orders(user_id,created_at DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_orders_transaction ON payment_orders(provider_transaction_id) WHERE provider_transaction_id IS NOT NULL AND provider_transaction_id<>'';
        CREATE TABLE IF NOT EXISTS payment_events(event_id TEXT PRIMARY KEY,order_no TEXT NOT NULL,transaction_id TEXT,payload_hash TEXT NOT NULL,created_at TEXT NOT NULL);
        """)
    def ensure_account(self,user_id:str,trial_total:int=0):
        now=self._now()
        with self._connect() as c:c.execute("INSERT OR IGNORE INTO user_generation_accounts(user_id,trial_total,created_at,updated_at) VALUES(?,?,?,?)",(user_id,max(0,int(trial_total)),now,now))
        return self.account(user_id)
    def delete_account(self,user_id:str)->bool:
        """删除用户时移除其额度快照；流水/订单/试用身份保留作审计与防刷依据。"""
        with self._connect() as c:
            cur=c.execute("DELETE FROM user_generation_accounts WHERE user_id=?",(user_id,))
        return int(cur.rowcount)>0
    def grant_trial(self,user_id:str,phone_hash:str,count:int):
        count=max(0,int(count));now=self._now()
        with self._connect() as c:
            c.execute("BEGIN IMMEDIATE");old=c.execute("SELECT * FROM trial_identities WHERE phone_hash=?",(phone_hash,)).fetchone()
            granted=count if old is None else (int(old['granted_count']) if old['user_id']==user_id else 0)
            if old is None:c.execute("INSERT INTO trial_identities VALUES(?,?,?,?)",(phone_hash,user_id,granted,now))
            c.execute("INSERT INTO user_generation_accounts(user_id,trial_total,created_at,updated_at) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET trial_total=MAX(trial_total,excluded.trial_total),updated_at=excluded.updated_at",(user_id,granted,now,now))
        return self.account(user_id)
    def account(self,user_id:str):
        with self._connect() as c:r=c.execute("SELECT * FROM user_generation_accounts WHERE user_id=?",(user_id,)).fetchone()
        if not r:return self.ensure_account(user_id,0)
        v=dict(r);v['trial_remaining']=max(0,v['trial_total']-v['trial_used']-v['trial_reserved']);v['paid_available']=max(0,v['paid_credits']-v['paid_reserved']);v['total_available']=v['trial_remaining']+v['paid_available'];return v
    def reserve(self,user_id:str,task_id:str,unit_price_fen:int):
        now=self._now()
        with self._connect() as c:
            c.execute("BEGIN IMMEDIATE");r=c.execute("SELECT * FROM user_generation_accounts WHERE user_id=?",(user_id,)).fetchone()
            if not r:
                c.execute("INSERT INTO user_generation_accounts(user_id,created_at,updated_at) VALUES(?,?,?)",(user_id,now,now));r=c.execute("SELECT * FROM user_generation_accounts WHERE user_id=?",(user_id,)).fetchone()
            if r['trial_total']-r['trial_used']-r['trial_reserved']>0:
                source='trial';c.execute("UPDATE user_generation_accounts SET trial_reserved=trial_reserved+1,updated_at=? WHERE user_id=?",(now,user_id))
            elif r['paid_credits']-r['paid_reserved']>0:
                source='paid';c.execute("UPDATE user_generation_accounts SET paid_reserved=paid_reserved+1,updated_at=? WHERE user_id=?",(now,user_id))
            else:raise AuthError("免费试用次数已用完，请购买生成额度或使用个人 API","GENERATION_CREDIT_REQUIRED")
            uid=uuid.uuid4().hex;c.execute("INSERT INTO generation_usage(id,task_id,user_id,source,status,unit_price_fen,created_at) VALUES(?,?,?,?,?,?,?)",(uid,task_id,user_id,source,'reserved',max(0,int(unit_price_fen)),now))
        return {'id':uid,'task_id':task_id,'source':source,'status':'reserved'}
    def settle(self,task_id:str,success:bool,article_id:str='',reason:str=''):
        now=self._now()
        with self._connect() as c:
            c.execute("BEGIN IMMEDIATE");r=c.execute("SELECT * FROM generation_usage WHERE task_id=?",(task_id,)).fetchone()
            if not r:raise AuthError("生成额度预占记录不存在","GENERATION_RESERVATION_NOT_FOUND")
            if r['status']!='reserved':return dict(r)
            reserved='trial_reserved' if r['source']=='trial' else 'paid_reserved';extra=',trial_used=trial_used+1' if success and r['source']=='trial' else (',paid_credits=MAX(0,paid_credits-1)' if success else '')
            c.execute(f"UPDATE user_generation_accounts SET {reserved}=MAX(0,{reserved}-1){extra},updated_at=? WHERE user_id=?",(now,r['user_id']))
            status='consumed' if success else 'released';c.execute("UPDATE generation_usage SET status=?,article_id=?,failure_reason=?,settled_at=? WHERE task_id=?",(status,article_id or None,(reason or '')[:500] or None,now,task_id));out=c.execute("SELECT * FROM generation_usage WHERE task_id=?",(task_id,)).fetchone()
        return dict(out)
    def release_stale(self, older_than_minutes:int=120)->int:
        cutoff=(datetime.now()-timedelta(minutes=max(10,int(older_than_minutes)))).isoformat(timespec='seconds')
        with self._connect() as c:rows=c.execute("SELECT task_id FROM generation_usage WHERE status='reserved' AND created_at<?",(cutoff,)).fetchall()
        for row in rows:self.settle(row['task_id'],False,reason='服务重启后释放过期预占')
        return len(rows)
    def adjust_paid(self,user_id:str,delta:int,operator_user_id:str,reason:str):
        delta=int(delta);reason=(reason or '').strip();now=self._now()
        if delta==0 or not reason:raise AuthError("调整次数不能为 0，且必须填写原因","INVALID_CREDIT_ADJUSTMENT")
        with self._connect() as c:
            c.execute("BEGIN IMMEDIATE");c.execute("INSERT OR IGNORE INTO user_generation_accounts(user_id,created_at,updated_at) VALUES(?,?,?)",(user_id,now,now));r=c.execute("SELECT paid_credits,paid_reserved FROM user_generation_accounts WHERE user_id=?",(user_id,)).fetchone();balance=int(r['paid_credits'])+delta
            if balance<int(r['paid_reserved']) or balance<0:raise AuthError("扣减后额度不足或低于已预占次数","INSUFFICIENT_PAID_CREDITS")
            c.execute("UPDATE user_generation_accounts SET paid_credits=?,updated_at=? WHERE user_id=?",(balance,now,user_id));c.execute("INSERT INTO credit_adjustments VALUES(?,?,?,?,?,?,?)",(uuid.uuid4().hex,user_id,delta,balance,reason[:500],operator_user_id,now))
        return self.account(user_id)
    def create_order(self,user_id:str,credits:int,unit_price_fen:int,expire_minutes:int=15,provider:str='wechat_native'):
        credits=max(1,int(credits));unit=max(0,int(unit_price_fen));now_dt=datetime.now();now=now_dt.isoformat(timespec='seconds');expires=(now_dt+timedelta(minutes=max(5,min(60,int(expire_minutes))))).isoformat(timespec='seconds');order_no='GSBM'+now_dt.strftime('%Y%m%d%H%M%S')+uuid.uuid4().hex[:12].upper();order_id=uuid.uuid4().hex
        with self._connect() as c:c.execute("INSERT INTO payment_orders(id,order_no,user_id,amount_fen,credit_count,unit_price_fen,status,provider,created_at,expires_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(order_id,order_no,user_id,credits*unit,credits,unit,'pending',provider,now,expires,now))
        return self.get_order(order_no,user_id)
    def set_order_code_url(self,order_no:str,code_url:str):
        now=self._now()
        with self._connect() as c:c.execute("UPDATE payment_orders SET code_url=?,updated_at=? WHERE order_no=? AND status='pending'",(code_url,now,order_no))
        return self.get_order(order_no)
    def get_order(self,order_no:str,user_id:str=''):
        self.expire_orders()
        with self._connect() as c:
            r=c.execute("SELECT * FROM payment_orders WHERE order_no=?"+(" AND user_id=?" if user_id else ""),(order_no,user_id) if user_id else (order_no,)).fetchone()
        return dict(r) if r else None
    def list_orders(self,user_id:str='',status:str='',limit:int=100):
        self.expire_orders();where=[];values=[]
        if user_id:where.append('user_id=?');values.append(user_id)
        if status:where.append('status=?');values.append(status)
        sql='SELECT * FROM payment_orders'+((' WHERE '+' AND '.join(where)) if where else '')+' ORDER BY created_at DESC LIMIT ?';values.append(min(500,max(1,int(limit))))
        with self._connect() as c:rows=c.execute(sql,values).fetchall()
        return [dict(x) for x in rows]
    def expire_orders(self)->int:
        now=self._now()
        with self._connect() as c:
            cur=c.execute("UPDATE payment_orders SET status='closed',updated_at=? WHERE status='pending' AND expires_at<=?",(now,now))
        return cur.rowcount
    def close_order(self,order_no:str,reason:str=''):
        now=self._now()
        with self._connect() as c:c.execute("UPDATE payment_orders SET status='closed',updated_at=? WHERE order_no=? AND status='pending'",(now,order_no))
        return self.get_order(order_no)
    def fail_order(self,order_no:str):
        now=self._now()
        with self._connect() as c:c.execute("UPDATE payment_orders SET status='failed',updated_at=? WHERE order_no=? AND status='pending'",(now,order_no))
        return self.get_order(order_no)
    def mark_order_paid(self,order_no:str,transaction_id:str,amount_fen:int,event_id:str,payload_hash:str):
        now=self._now()
        with self._connect() as c:
            c.execute('BEGIN IMMEDIATE');order=c.execute("SELECT * FROM payment_orders WHERE order_no=?",(order_no,)).fetchone()
            if not order:raise AuthError('支付订单不存在','PAYMENT_ORDER_NOT_FOUND')
            if int(order['amount_fen'])!=int(amount_fen):raise AuthError('支付回调金额与订单不一致','PAYMENT_AMOUNT_MISMATCH')
            old_event=c.execute("SELECT * FROM payment_events WHERE event_id=?",(event_id,)).fetchone()
            if old_event:
                if old_event['order_no']!=order_no or old_event['payload_hash']!=payload_hash:raise AuthError('支付事件 ID 内容冲突','PAYMENT_EVENT_CONFLICT')
                return dict(order)
            if order['status']=='paid':
                if order['provider_transaction_id']!=transaction_id:raise AuthError('已支付订单的交易号不一致','PAYMENT_TRANSACTION_MISMATCH')
                c.execute("INSERT INTO payment_events VALUES(?,?,?,?,?)",(event_id,order_no,transaction_id,payload_hash,now));return dict(order)
            if order['status'] not in {'pending','closed'}:raise AuthError('订单状态不允许入账','PAYMENT_ORDER_STATE_INVALID')
            duplicate=c.execute("SELECT order_no FROM payment_orders WHERE provider_transaction_id=? AND order_no<>?",(transaction_id,order_no)).fetchone()
            if duplicate:raise AuthError('微信支付交易号已用于其他订单','PAYMENT_TRANSACTION_DUPLICATE')
            c.execute("INSERT OR IGNORE INTO user_generation_accounts(user_id,created_at,updated_at) VALUES(?,?,?)",(order['user_id'],now,now));balance=c.execute("SELECT paid_credits FROM user_generation_accounts WHERE user_id=?",(order['user_id'],)).fetchone()['paid_credits']+order['credit_count']
            c.execute("UPDATE user_generation_accounts SET paid_credits=?,updated_at=? WHERE user_id=?",(balance,now,order['user_id']))
            c.execute("INSERT INTO credit_adjustments VALUES(?,?,?,?,?,?,?)",(uuid.uuid4().hex,order['user_id'],order['credit_count'],balance,f"微信支付订单 {order_no}",'wechat-pay-callback',now))
            c.execute("UPDATE payment_orders SET status='paid',provider_transaction_id=?,paid_at=?,updated_at=? WHERE order_no=?",(transaction_id,now,now,order_no));c.execute("INSERT INTO payment_events VALUES(?,?,?,?,?)",(event_id,order_no,transaction_id,payload_hash,now));out=c.execute("SELECT * FROM payment_orders WHERE order_no=?",(order_no,)).fetchone()
        return dict(out)
    def usage(self,user_id:str,limit:int=50)->List[Dict[str,Any]]:
        with self._connect() as c:rows=c.execute("SELECT * FROM generation_usage WHERE user_id=? ORDER BY created_at DESC LIMIT ?",(user_id,min(200,max(1,int(limit))))).fetchall()
        return [dict(x) for x in rows]
    def adjustments(self,user_id:str,limit:int=50)->List[Dict[str,Any]]:
        with self._connect() as c:rows=c.execute("SELECT * FROM credit_adjustments WHERE user_id=? ORDER BY created_at DESC LIMIT ?",(user_id,min(200,max(1,int(limit))))).fetchall()
        return [dict(x) for x in rows]
