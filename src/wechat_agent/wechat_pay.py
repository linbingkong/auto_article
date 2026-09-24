"""微信支付 API v3 Native：请求签名、响应验签与支付通知解密。"""
from __future__ import annotations
import base64,hashlib,io,json,secrets,time
from datetime import datetime,timedelta,timezone
from pathlib import Path
from typing import Any,Dict,Mapping,Tuple
import requests
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .auth import AuthError
from .config import Config,PROJECT_ROOT
API_ORIGIN="https://api.mch.weixin.qq.com"
def _path(value:str)->Path:
 p=Path(value);return p if p.is_absolute() else PROJECT_ROOT/p
def payment_ready(cfg:Config)->Tuple[bool,str]:
 b=cfg.billing;required={"微信公众号 AppID":cfg.wechat.app_id,"微信支付商户号":b.wechat_pay_mch_id,"商户证书序列号":b.wechat_pay_serial_no,"商户私钥路径":b.wechat_pay_private_key_path,"微信支付公钥路径":b.wechat_pay_public_key_path,"微信支付公钥 ID":b.wechat_pay_public_key_id,"API v3 Key":b.wechat_pay_api_v3_key,"支付通知 URL":b.payment_notify_url};missing=[n for n,v in required.items() if not v]
 if missing:return False,"缺少"+"、".join(missing)
 if b.price_per_generation_fen<=0:return False,"单次价格必须大于 0 分"
 if len(b.wechat_pay_api_v3_key.encode())!=32:return False,"API v3 Key 必须为 32 字节"
 for n,v in (("商户私钥",b.wechat_pay_private_key_path),("微信支付公钥",b.wechat_pay_public_key_path)):
  if not _path(v).is_file():return False,f"{n}文件不存在"
 if not b.payment_notify_url.lower().startswith("https://"):return False,"支付通知 URL 必须使用 HTTPS"
 return True,""
class WechatNativePay:
 def __init__(self,cfg:Config):
  ready,reason=payment_ready(cfg)
  if not ready:raise AuthError(f"微信支付尚未就绪：{reason}","WECHAT_PAY_NOT_READY")
  self.cfg=cfg;self.billing=cfg.billing;self.session=requests.Session();self.session.trust_env=False
  try:
   self.private_key=serialization.load_pem_private_key(_path(self.billing.wechat_pay_private_key_path).read_bytes(),password=None);raw=_path(self.billing.wechat_pay_public_key_path).read_bytes()
   try:self.public_key=serialization.load_pem_public_key(raw)
   except ValueError:self.public_key=x509.load_pem_x509_certificate(raw).public_key()
  except Exception as exc:raise AuthError("微信支付商户私钥或验签公钥无效","WECHAT_PAY_KEY_INVALID") from exc
 def _sign(self,message:bytes)->str:return base64.b64encode(self.private_key.sign(message,padding.PKCS1v15(),hashes.SHA256())).decode()
 def _authorization(self,method:str,url:str,body:str)->str:
  ts=str(int(time.time()));nonce=secrets.token_hex(16);sig=self._sign(f"{method}\n{url}\n{ts}\n{nonce}\n{body}\n".encode())
  return f'WECHATPAY2-SHA256-RSA2048 mchid="{self.billing.wechat_pay_mch_id}",nonce_str="{nonce}",signature="{sig}",timestamp="{ts}",serial_no="{self.billing.wechat_pay_serial_no}"'
 def _verify(self,ts:str,nonce:str,body:bytes,signature:str,serial:str=""):
  if serial and serial!=self.billing.wechat_pay_public_key_id:raise AuthError("微信支付签名公钥 ID 不匹配","WECHAT_PAY_SERIAL_MISMATCH")
  try:self.public_key.verify(base64.b64decode(signature),ts.encode()+b"\n"+nonce.encode()+b"\n"+body+b"\n",padding.PKCS1v15(),hashes.SHA256())
  except (InvalidSignature,ValueError) as exc:raise AuthError("微信支付签名验证失败","WECHAT_PAY_SIGNATURE_INVALID") from exc
 def _request(self,method:str,url:str,payload:Dict[str,Any]|None=None)->Dict[str,Any]:
  body=json.dumps(payload,ensure_ascii=False,separators=(",",":")) if payload is not None else "";r=self.session.request(method,API_ORIGIN+url,data=body.encode() if body else None,headers={"Authorization":self._authorization(method,url,body),"Accept":"application/json","Content-Type":"application/json","User-Agent":"guansibianming-agent/1.0"},timeout=(10,30));raw=r.content
  if 200<=r.status_code<300:
   self._verify(r.headers.get("Wechatpay-Timestamp",""),r.headers.get("Wechatpay-Nonce",""),raw,r.headers.get("Wechatpay-Signature",""),r.headers.get("Wechatpay-Serial",""));return r.json() if raw else {}
  try:detail=r.json().get("message",r.text)
  except ValueError:detail=r.text
  raise AuthError(f"微信支付请求失败（{r.status_code}）：{str(detail)[:300]}","WECHAT_PAY_REQUEST_FAILED")
 def create_native(self,order_no:str,amount_fen:int,description:str)->str:
  expire=(datetime.now(timezone.utc)+timedelta(minutes=self.billing.order_expire_minutes)).isoformat(timespec="seconds")
  out=self._request("POST","/v3/pay/transactions/native",{"appid":self.cfg.wechat.app_id,"mchid":self.billing.wechat_pay_mch_id,"description":description[:127],"out_trade_no":order_no,"time_expire":expire,"notify_url":self.billing.payment_notify_url,"amount":{"total":int(amount_fen),"currency":"CNY"}});url=str(out.get("code_url",""))
  if not url:raise AuthError("微信支付未返回付款二维码地址","WECHAT_PAY_CODE_URL_MISSING")
  return url
 def query(self,order_no:str):return self._request("GET",f"/v3/pay/transactions/out-trade-no/{order_no}?mchid={self.billing.wechat_pay_mch_id}")
 def close(self,order_no:str):self._request("POST",f"/v3/pay/transactions/out-trade-no/{order_no}/close",{"mchid":self.billing.wechat_pay_mch_id})
 def verify_notification(self,headers:Mapping[str,str],raw_body:bytes):
  h={str(k).lower():v for k,v in headers.items()};ts=h.get("wechatpay-timestamp","");nonce=h.get("wechatpay-nonce","");sig=h.get("wechatpay-signature","");serial=h.get("wechatpay-serial","")
  if not ts or not nonce or not sig:raise AuthError("微信支付通知缺少签名头","WECHAT_PAY_HEADERS_MISSING")
  try:
   if abs(int(time.time())-int(ts))>600:raise AuthError("微信支付通知时间戳已过期","WECHAT_PAY_TIMESTAMP_EXPIRED")
  except ValueError as exc:raise AuthError("微信支付通知时间戳无效","WECHAT_PAY_TIMESTAMP_INVALID") from exc
  self._verify(ts,nonce,raw_body,sig,serial);env=json.loads(raw_body.decode());res=env.get("resource") or {}
  if res.get("algorithm")!="AEAD_AES_256_GCM":raise AuthError("微信支付通知加密算法不受支持","WECHAT_PAY_ALGORITHM_INVALID")
  try:plain=AESGCM(self.billing.wechat_pay_api_v3_key.encode()).decrypt(str(res["nonce"]).encode(),base64.b64decode(res["ciphertext"]),str(res.get("associated_data","")).encode());transaction=json.loads(plain.decode())
  except Exception as exc:raise AuthError("微信支付通知解密失败","WECHAT_PAY_DECRYPT_FAILED") from exc
  return env,transaction
def qr_data_url(content:str)->str:
 try:import qrcode
 except ImportError as exc:raise AuthError("服务器缺少 qrcode 依赖","QR_LIBRARY_MISSING") from exc
 image=qrcode.make(content);buf=io.BytesIO();image.save(buf,format="PNG");return "data:image/png;base64,"+base64.b64encode(buf.getvalue()).decode()
def payload_hash(raw_body:bytes)->str:return hashlib.sha256(raw_body).hexdigest()
