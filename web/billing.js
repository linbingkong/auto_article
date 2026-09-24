import {api,app,closeModal,empty,esc,fmtDate,loading,renderError,setMeta,showModal,toast} from './core.js';

const money=fen=>`¥${((Number(fen)||0)/100).toFixed(2)}`;
const orderState={pending:['等待支付','gold'],paid:['支付成功','green'],closed:['已关闭',''],failed:['下单失败','red'],refunded:['已退款','']};
const usageState={reserved:['执行中','gold'],consumed:['已扣除','green'],released:['已退回','']};

export async function renderBilling(){
  setMeta('billing');loading('正在读取生成额度…');
  try{paint(await api('/api/me/billing'));}catch(e){renderError(e);}
}

function paint(data){
  const a=data.account,p=data.pricing,usage=data.usage||[],orders=data.orders||[];
  const total=a.total_available||0,trial=a.trial_remaining||0,paid=a.paid_available||0;
  app.innerHTML=`<div class="quota-page">
    <section class="quota-hero">
      <div class="quota-hero-copy"><span class="quota-eyebrow">PLATFORM CREDITS</span><h2>生成额度</h2><p>一次额度对应一篇成功保存的完整文章。失败或取消的任务不会扣除。</p><div class="quota-tags"><span>${p.enabled?'● 平台生成已开放':'○ 平台生成已暂停'}</span><span>${money(p.price_per_generation_fen)}/次</span><span>最多 ${p.max_target_words} 字</span></div></div>
      <div class="quota-total"><small>当前可用</small><strong>${total}</strong><span>次生成</span></div>
    </section>
    <div class="quota-summary-grid">
      <article class="quota-summary-card trial"><div><span>免费试用</span><strong>${trial}</strong></div><p>已使用 ${a.trial_used||0} 次 · 共获赠 ${a.trial_total||0} 次</p><div class="quota-progress"><i style="width:${a.trial_total?Math.min(100,(trial/a.trial_total)*100):0}%"></i></div></article>
      <article class="quota-summary-card paid"><div><span>付费额度</span><strong>${paid}</strong></div><p>已预占 ${a.paid_reserved||0} 次 · 永久有效</p><a href="#purchaseCredits">购买额度 ↓</a></article>
      <article class="quota-summary-card api"><div><span>个人 API</span><strong>不扣次</strong></div><p>使用自己的模型和搜索服务，费用由个人 API 账号承担。</p><a href="#my-api-config">配置个人 API →</a></article>
    </div>
    <section class="card purchase-section" id="purchaseCredits"><div class="card-header"><div><h3>购买生成额度</h3><p>${p.payment_enabled?'微信扫码支付，支付成功后额度自动到账。':'在线支付尚未开放，可联系管理员人工充值。'}</p></div><span class="secure-pay">${p.payment_enabled?'微信支付 · 安全到账':'人工充值'}</span></div>
      <div class="purchase-grid">${(p.purchase_options||[1,5,10]).map((count,index)=>`<article class="purchase-option ${index===1?'recommended':''}">${index===1?'<em>常用</em>':''}<strong>${count}<small> 次</small></strong><p>${money(count*p.price_per_generation_fen)}</p><span>${money(p.price_per_generation_fen)}/次</span><button class="btn ${index===1?'primary':''}" data-buy="${count}" ${p.payment_enabled?'':'disabled'}>${p.payment_enabled?'立即购买':'暂未开放'}</button></article>`).join('')}</div>
      ${!p.payment_enabled?'<div class="manual-payment-note"><strong>需要充值？</strong><span>请联系管理员并提供用户名，管理员确认收款后可在用户管理中增加额度。</span></div>':''}
    </section>
    <div class="quota-detail-grid">
      <section class="card"><div class="card-header"><div><h3>最近订单</h3><p>微信支付订单与到账状态</p></div></div>${orderTable(orders)}</section>
      <section class="card"><div class="card-header"><div><h3>生成流水</h3><p>预占、扣除和退回状态清晰可查</p></div></div>${usageTable(usage)}</section>
    </div>
  </div>`;
  document.querySelectorAll('[data-buy]').forEach(btn=>btn.addEventListener('click',()=>buyCredits(Number(btn.dataset.buy),btn)));
  document.querySelectorAll('[data-resume-order]').forEach(btn=>btn.addEventListener('click',()=>resumeOrder(btn.dataset.resumeOrder,btn)));
}

function orderTable(rows){
  if(!rows.length)return empty('暂无购买订单','在线购买后将在这里显示支付状态');
  return `<div class="quota-list">${rows.slice(0,12).map(x=>{const [label,color]=orderState[x.status]||[x.status,''];return `<div class="quota-list-row"><div><strong>${x.credit_count} 次额度 · ${money(x.amount_fen)}</strong><small>${fmtDate(x.created_at)} · ${esc(x.order_no)}</small></div><div class="row-actions"><span class="badge ${color}">${label}</span>${x.status==='pending'?`<button class="btn small" data-resume-order="${esc(x.order_no)}">继续支付</button>`:''}</div></div>`}).join('')}</div>`;
}

function usageTable(rows){
  if(!rows.length)return empty('暂无生成流水','生成第一篇文章后会记录额度变化');
  return `<div class="quota-list">${rows.slice(0,12).map(x=>{const [label,color]=usageState[x.status]||[x.status,''];return `<div class="quota-list-row"><div><strong>${x.source==='trial'?'免费试用':'付费额度'}</strong><small>${fmtDate(x.created_at)}${x.article_id?` · <a href="#article/${esc(x.article_id)}">查看文章</a>`:''}</small></div><span class="badge ${color}">${label}</span></div>`}).join('')}</div>`;
}

async function buyCredits(credits,button){
  const old=button.textContent;button.disabled=true;button.textContent='正在创建订单…';
  try{
    const data=await api('/api/me/billing/orders',{method:'POST',body:JSON.stringify({credits})});
    showPayment(data.order,data.qr_image);
  }catch(e){toast(e.message,'error');}finally{button.disabled=false;button.textContent=old;}
}

async function resumeOrder(orderNo,button){
  const old=button.textContent;button.disabled=true;button.textContent='读取中…';
  try{const data=await api(`/api/me/billing/orders/${orderNo}/resume`,{method:'POST'});showPayment(data.order,data.qr_image);}catch(e){toast(e.message,'error');renderBilling();}finally{button.disabled=false;button.textContent=old;}
}

function showPayment(order,qrImage){
  showModal({title:`微信支付 ${money(order.amount_fen)}`,subtitle:`购买 ${order.credit_count} 次文章生成额度`,body:`<div class="payment-panel"><img class="payment-qr" src="${qrImage}" alt="微信支付二维码"><strong>请使用微信扫码支付</strong><p>电脑端请用微信扫码；手机端可长按识别二维码。支付成功后额度自动到账，请勿重复支付。</p><div class="payment-status" id="paymentStatus" data-order="${esc(order.order_no)}"><span class="spinner"></span> 正在等待支付…</div><small>订单号：${esc(order.order_no)}<br>有效期至：${fmtDate(order.expires_at)}</small></div>`,footer:'<button class="btn" id="closePayOrder">取消订单</button><button class="btn primary" data-close>稍后再看</button>'});
  document.querySelector('#closePayOrder').addEventListener('click',async()=>{try{await api(`/api/me/billing/orders/${order.order_no}/close`,{method:'POST'});closeModal();toast('订单已关闭');renderBilling();}catch(e){toast(e.message,'error')}});
  pollOrder(order.order_no);
}

async function pollOrder(orderNo){
  const target=document.querySelector(`#paymentStatus[data-order="${CSS.escape(orderNo)}"]`);if(!target)return;
  try{
    const order=await api(`/api/me/billing/orders/${orderNo}`);
    if(order.status==='paid'){target.className='payment-status paid';target.innerHTML='✓ 支付成功，额度已到账';toast('支付成功，生成额度已到账');setTimeout(()=>{closeModal();renderBilling()},1000);return;}
    if(['closed','failed','refunded'].includes(order.status)){target.className='payment-status failed';target.textContent=order.status==='closed'?'订单已关闭':'订单无法继续支付';return;}
  }catch(e){target.textContent='状态查询暂时失败，正在重试…';}
  setTimeout(()=>pollOrder(orderNo),2000);
}
