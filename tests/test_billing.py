import base64
import json
import shutil
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from wechat_agent import web_app
from wechat_agent.audit import AuditStore
from wechat_agent.auth import AuthError
from wechat_agent.billing import BillingStore
from wechat_agent.config import Config
from wechat_agent.user import UserStore
from wechat_agent.wechat_pay import WechatNativePay


@pytest.fixture()
def store():
    root = Path(__file__).resolve().parents[1] / "output" / "_billing_test" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    value = BillingStore(root / "billing.db")
    yield value
    shutil.rmtree(root, ignore_errors=True)


def test_trial_reserve_consume_and_release(store):
    account = store.grant_trial("u1", "phone-hash-1", 3)
    assert account["trial_remaining"] == 3
    assert store.reserve("u1", "task-1", 200)["source"] == "trial"
    assert store.account("u1")["trial_remaining"] == 2
    store.settle("task-1", False, reason="provider failed")
    assert store.account("u1")["trial_remaining"] == 3
    store.reserve("u1", "task-2", 200)
    store.settle("task-2", True, "article-1")
    account = store.account("u1")
    assert account["trial_used"] == 1 and account["trial_remaining"] == 2


def test_paid_credits_and_atomic_limit(store):
    store.ensure_account("u1")
    store.adjust_paid("u1", 1, "admin", "manual payment")
    assert store.reserve("u1", "task-paid", 200)["source"] == "paid"
    with pytest.raises(AuthError) as exc:
        store.reserve("u1", "task-over", 200)
    assert exc.value.code == "GENERATION_CREDIT_REQUIRED"
    store.settle("task-paid", True, "article-2")
    assert store.account("u1")["paid_credits"] == 0


def test_phone_identity_only_gets_trial_once(store):
    assert store.grant_trial("u1", "same-phone", 3)["trial_total"] == 3
    assert store.grant_trial("u2", "same-phone", 3)["trial_total"] == 0


def test_platform_generate_consumes_creator_trial(store, monkeypatch):
    users = UserStore(store.db_path)
    creator = users.create_user("creatorapi", "password123", role="creator")
    store.grant_trial(creator["id"], "creator-phone", 1)
    cfg = Config()
    cfg.llm.api_key = "system-llm-key"
    cfg.llm.base_url = "https://llm.example/v1"
    cfg.llm.model = "test-model"
    cfg.search.api_key = "system-search-key"
    monkeypatch.setattr(web_app, "user_store", users)
    monkeypatch.setattr(web_app, "billing_store", store)
    monkeypatch.setattr(web_app, "audit_store", AuditStore(store.db_path))
    monkeypatch.setattr(web_app.config_service, "load", lambda: cfg)
    monkeypatch.setattr(web_app.article_store, "generate", lambda body, config, progress, owner_user=None, **kwargs: {"article_id": "a" * 32})
    client = TestClient(web_app.app)
    login = client.post("/api/auth/login", json={"username": "creatorapi", "password": "password123"}).json()["data"]
    response = client.post("/api/generate", headers={"Authorization": "Bearer " + login["access_token"]}, json={"topic": {"title": "测试平台额度生成", "source": "manual"}, "options": {"generation_mode": "platform", "with_images": False}})
    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    deadline = time.time() + 5
    task = web_app.task_manager.get(task_id)
    while task and task["status"] not in {"success", "failed"} and time.time() < deadline:
        time.sleep(0.02)
        task = web_app.task_manager.get(task_id)
    assert task["status"] == "success"
    assert store.account(creator["id"])["trial_used"] == 1
    usage = store.usage(creator["id"])
    assert usage[0]["status"] == "consumed" and usage[0]["article_id"] == "a" * 32


def test_payment_order_idempotently_credits_account(store):
    order = store.create_order("buyer", 5, 200, 15)
    paid = store.mark_order_paid(order["order_no"], "wx-transaction-1", 1000, "event-1", "hash-1")
    assert paid["status"] == "paid"
    assert store.account("buyer")["paid_credits"] == 5
    store.mark_order_paid(order["order_no"], "wx-transaction-1", 1000, "event-1", "hash-1")
    store.mark_order_paid(order["order_no"], "wx-transaction-1", 1000, "event-2", "hash-2")
    assert store.account("buyer")["paid_credits"] == 5
    with pytest.raises(AuthError) as exc:
        second = store.create_order("buyer", 1, 200, 15)
        store.mark_order_paid(second["order_no"], "wx-transaction-2", 201, "event-3", "hash-3")
    assert exc.value.code == "PAYMENT_AMOUNT_MISMATCH"


def test_wechat_notification_signature_and_aesgcm(store):
    merchant_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    platform_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    merchant_path = store.db_path.parent / "merchant.pem"
    public_path = store.db_path.parent / "wechatpay_public.pem"
    merchant_path.write_bytes(merchant_private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    public_path.write_bytes(platform_private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    cfg = Config()
    cfg.wechat.app_id = "wx-test-app"
    cfg.billing.wechat_pay_mch_id = "1900000109"
    cfg.billing.wechat_pay_serial_no = "MERCHANT-SERIAL"
    cfg.billing.wechat_pay_private_key_path = str(merchant_path)
    cfg.billing.wechat_pay_public_key_id = "PUB_KEY_ID_011"
    cfg.billing.wechat_pay_public_key_path = str(public_path)
    cfg.billing.wechat_pay_api_v3_key = "0123456789abcdef0123456789abcdef"
    cfg.billing.payment_notify_url = "https://example.com/api/payments/wechat/notify"
    transaction = {"appid": cfg.wechat.app_id, "mchid": cfg.billing.wechat_pay_mch_id, "out_trade_no": "ORDER1", "transaction_id": "WX1", "trade_state": "SUCCESS", "amount": {"total": 200, "payer_total": 200}}
    nonce, associated = "paymentnonce", "transaction"
    cipher = AESGCM(cfg.billing.wechat_pay_api_v3_key.encode()).encrypt(nonce.encode(), json.dumps(transaction).encode(), associated.encode())
    envelope = {"id": "event-crypto", "event_type": "TRANSACTION.SUCCESS", "resource": {"algorithm": "AEAD_AES_256_GCM", "nonce": nonce, "associated_data": associated, "ciphertext": base64.b64encode(cipher).decode()}}
    raw = json.dumps(envelope, separators=(",", ":")).encode()
    timestamp, header_nonce = str(int(time.time())), "header-nonce"
    message = timestamp.encode() + b"\n" + header_nonce.encode() + b"\n" + raw + b"\n"
    signature = base64.b64encode(platform_private.sign(message, padding.PKCS1v15(), hashes.SHA256())).decode()
    headers = {"Wechatpay-Timestamp": timestamp, "Wechatpay-Nonce": header_nonce, "Wechatpay-Signature": signature, "Wechatpay-Serial": cfg.billing.wechat_pay_public_key_id}
    _, decrypted = WechatNativePay(cfg).verify_notification(headers, raw)
    assert decrypted == transaction
    headers["Wechatpay-Signature"] = base64.b64encode(b"invalid").decode()
    with pytest.raises(AuthError) as exc:
        WechatNativePay(cfg).verify_notification(headers, raw)
    assert exc.value.code == "WECHAT_PAY_SIGNATURE_INVALID"


def test_wechat_callback_is_public_and_idempotent(store, monkeypatch):
    cfg = Config()
    cfg.wechat.app_id = "wx-callback-app"
    cfg.billing.wechat_pay_mch_id = "1900000109"
    order = store.create_order("callback-buyer", 2, 200, 15)
    transaction = {"appid": cfg.wechat.app_id, "mchid": cfg.billing.wechat_pay_mch_id, "out_trade_no": order["order_no"], "transaction_id": "WX-CALLBACK-1", "trade_state": "SUCCESS", "amount": {"total": 400, "payer_total": 300}}
    class FakePay:
        def __init__(self, _cfg):
            pass
        def verify_notification(self, headers, raw):
            return {"id": "CALLBACK-EVENT-1", "event_type": "TRANSACTION.SUCCESS"}, transaction
    monkeypatch.setattr(web_app, "WechatNativePay", FakePay)
    monkeypatch.setattr(web_app, "billing_store", store)
    monkeypatch.setattr(web_app, "audit_store", AuditStore(store.db_path))
    monkeypatch.setattr(web_app.config_service, "load", lambda: cfg)
    client = TestClient(web_app.app)
    first = client.post("/api/payments/wechat/notify", content=b"encrypted-notification")
    second = client.post("/api/payments/wechat/notify", content=b"encrypted-notification")
    assert first.status_code == 200 and second.status_code == 200
    assert store.account("callback-buyer")["paid_credits"] == 2
