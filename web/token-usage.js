import {app,api,can,empty,esc,fmtDate,loading,metric,renderError,setMeta,showModal} from './core.js';
const nt=n=>Number(n||0).toLocaleString('zh-CN');
const money=n=>`¥${Number(n||0).toFixed(4)}`;
const stageName=s=>({outline:'大纲',refine:'全文润色',fact_revision:'事实修订',fact_audit:'事实审计',fact_audit_retry:'审计重试',titles:'候选标题'}[s]||(s?.startsWith('section_')?`章节 ${s.split('_')[1]}`:s||'未知'));

export async function renderTokenUsage(){
  setMeta('token-usage');
  if(!can('audit.view')){app.innerHTML=`<div class="card">${empty('无权限访问 Token 看板','仅管理员可查看模型成本')}</div>`;return;}
  loading('正在汇总 Token 用量…');
  try{
    const days=Number(sessionStorage.getItem('token_usage_days')||30);
    const data=await api(`/api/token-usage?days=${days}&limit=200`);
    paint(data);
  }catch(e){renderError(e,'请确认数据库已创建 llm_token_usage 表。');}
}

function paint(data){
  const s=data.summary||{},daily=data.daily||[],models=data.by_model||[],stages=data.by_stage||[],rows=data.generations||[];
  const maxDaily=Math.max(1,...daily.map(x=>Number(x.total_tokens||0)));
  app.innerHTML=`<div class="page-intro"><div><h2>Token 与成本看板</h2><p>按每次模型响应记录用量；成本基于配置中心的输入/输出单价快照估算。</p></div><select class="select compact-select" id="usageDays"><option value="7" ${data.days===7?'selected':''}>近 7 天</option><option value="30" ${data.days===30?'selected':''}>近 30 天</option><option value="90" ${data.days===90?'selected':''}>近 90 天</option><option value="365" ${data.days===365?'selected':''}>近一年</option></select></div>
  <div class="metrics-grid">${metric('生成次数',nt(s.generations),'▤',`共 ${nt(s.calls)} 次模型调用`)}${metric('总 Token',nt(s.total_tokens),'∑',`输入 ${nt(s.prompt_tokens)} · 输出 ${nt(s.completion_tokens)}`)}${metric('平均每篇',nt(s.avg_tokens_per_generation),'≈',`缓存 ${nt(s.cached_tokens)} · 推理 ${nt(s.reasoning_tokens)}`)}${metric('估算成本',money(s.estimated_cost_yuan),'¥',`平均每篇 ${money(s.avg_cost_yuan_per_generation)}`)}</div>
  ${s.has_estimated_usage?'<div class="compliance-box">部分服务商响应未返回 usage，带“估算”标记的数据由文本长度近似计算，成本仅供评估。</div>':''}
  <div class="dashboard-grid section-space"><section class="card"><header class="card-header"><div><h3>每日趋势</h3><p>总 Token 用量</p></div></header><div class="card-body">${daily.length?daily.map(x=>`<div class="status-row"><span class="status-label">${esc(x.date)}</span><div style="flex:1;margin:0 14px"><div class="progress"><span style="width:${Math.max(2,Math.round(Number(x.total_tokens||0)/maxDaily*100))}%"></span></div></div><strong>${nt(x.total_tokens)}</strong></div>`).join(''):empty('暂无用量')}</div></section>
  <section class="card"><header class="card-header"><div><h3>模型分布</h3><p>用于核对不同模型成本</p></div></header><div class="card-body">${models.length?models.map(x=>`<div class="status-row"><span class="status-label">${esc(x.model)}</span><span>${nt(x.total_tokens)} Token · ${money(x.estimated_cost_yuan)}</span></div>`).join(''):empty('暂无模型数据')}</div></section></div>
  <section class="card section-space"><header class="card-header"><div><h3>阶段消耗</h3><p>识别大纲、正文、润色和核验中的主要成本</p></div></header><div class="card-body"><div class="table-wrap"><table class="table"><thead><tr><th>阶段</th><th>调用</th><th>输入</th><th>输出</th><th>总 Token</th><th>估算成本</th></tr></thead><tbody>${stages.map(x=>`<tr><td>${esc(stageName(x.stage))}</td><td>${nt(x.calls)}</td><td>${nt(x.prompt_tokens)}</td><td>${nt(x.completion_tokens)}</td><td><strong>${nt(x.total_tokens)}</strong></td><td>${money(x.estimated_cost_yuan)}</td></tr>`).join('')}</tbody></table></div></div></section>
  <section class="card section-space"><header class="card-header"><div><h3>每次文章生成</h3><p>失败任务产生的已完成模型调用同样计入成本</p></div></header><div class="card-body">${rows.length?`<div class="table-wrap"><table class="table"><thead><tr><th>时间/用户</th><th>模型与方式</th><th>调用阶段</th><th>输入</th><th>输出</th><th>总量</th><th>成本</th><th></th></tr></thead><tbody>${rows.map(x=>`<tr><td>${fmtDate(x.created_at)}<br><small>${esc(x.username||x.user_id)}</small></td><td>${esc(x.model)}<br><small>${x.generation_mode==='personal'?'个人 API':'平台 API'}</small></td><td>${nt(x.calls)} 次<br><small>${x.stages.map(stageName).join('、')}</small></td><td>${nt(x.prompt_tokens)}</td><td>${nt(x.completion_tokens)}</td><td><strong>${nt(x.total_tokens)}</strong>${x.has_estimated_usage?'<br><small>含估算</small>':''}</td><td>${money(x.estimated_cost_yuan)}</td><td>${x.article_id?`<a class="btn small" href="#article/${esc(x.article_id)}">文章</a>`:''}<button class="btn small ghost" data-usage-task="${esc(x.task_id)}">明细</button></td></tr>`).join('')}</tbody></table></div>`:empty('暂无 Token 数据','完成一次文章生成后会自动记录')}</div></section>`;
  document.querySelector('#usageDays')?.addEventListener('change',e=>{sessionStorage.setItem('token_usage_days',e.target.value);renderTokenUsage();});
  document.querySelectorAll('[data-usage-task]').forEach(b=>b.addEventListener('click',()=>showDetail(b.dataset.usageTask)));
}

async function showDetail(taskId){
  try{const data=await api(`/api/token-usage/tasks/${encodeURIComponent(taskId)}`);showModal({title:'Token 调用明细',subtitle:`任务 ${taskId.slice(0,12)}`,wide:true,body:`<div class="table-wrap"><table class="table"><thead><tr><th>阶段</th><th>模型</th><th>输入</th><th>输出</th><th>缓存</th><th>推理</th><th>总量</th><th>成本</th></tr></thead><tbody>${(data.items||[]).map(x=>`<tr><td>${esc(stageName(x.stage))}</td><td>${esc(x.model)}</td><td>${nt(x.prompt_tokens)}</td><td>${nt(x.completion_tokens)}</td><td>${nt(x.cached_tokens)}</td><td>${nt(x.reasoning_tokens)}</td><td>${nt(x.total_tokens)}${x.estimated?'<br><small>估算</small>':''}</td><td>${money(x.estimated_cost_yuan)}</td></tr>`).join('')}</tbody></table></div>`});}catch(e){renderError(e);}
}
