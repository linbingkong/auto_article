import {app,api,can,closeModal,empty,esc,fmtDate,loading,renderError,setMeta,showModal,sourceName,statusBadge,taskList,toast} from './core.js';
let taskRefreshTimer=null;
export async function renderTasks(){
  clearTimeout(taskRefreshTimer);setMeta('tasks');loading('正在读取任务队列…');
  try{
    const tasks=await api('/api/tasks?limit=100');
    app.innerHTML=`<div class="page-intro"><div><h2>任务与运行日志</h2><p>暂停和取消会在当前模型/API 调用结束后的安全检查点生效。</p></div><button class="btn" id="reloadTasks">↻ 刷新</button></div><div class="alert task-control-note">运行中的线程不会被强制终止，避免留下损坏的文章或图片；“正在取消/等待暂停”表示正在等待当前外部调用返回。</div><div class="card section-space"><div class="card-body">${tasks.length?tasks.map(taskControlRow).join(''):empty('暂无任务','生成文章后可在这里查看并控制任务')}</div></div>`;
    document.querySelector('#reloadTasks').addEventListener('click',renderTasks);
    document.querySelectorAll('[data-task-action]').forEach(btn=>btn.addEventListener('click',()=>handleTaskAction(btn.dataset.taskId,btn.dataset.taskAction)));
    if(tasks.some(t=>['pending','running','pausing','paused','cancelling'].includes(t.status)))taskRefreshTimer=setTimeout(()=>{if(location.hash.replace('#','').startsWith('tasks'))renderTasks();},1800);
  }catch(e){renderError(e);}
}
function taskKind(kind){return {generate:'文章生成',push_draft:'草稿推送',image:'图片生成'}[kind]||kind;}
function taskControlRow(t){
  const actions=[];
  const canControl = can('task.control');
  if(canControl){
    if(['pending','running'].includes(t.status))actions.push(`<button class="btn small" data-task-action="pause" data-task-id="${t.id}">暂停</button>`);
    if(['paused','pausing'].includes(t.status))actions.push(`<button class="btn small" data-task-action="resume" data-task-id="${t.id}">继续</button>`);
    if(['pending','running','paused','pausing'].includes(t.status))actions.push(`<button class="btn small danger-outline" data-task-action="cancel" data-task-id="${t.id}">取消</button>`);
    if(['success','failed','cancelled'].includes(t.status))actions.push(`<button class="btn small danger-outline" data-task-action="delete" data-task-id="${t.id}">删除日志</button>`);
  }
  if(t.status==='success'&&t.result?.article_id)actions.unshift(`<a class="btn small" href="#article/${t.result.article_id}">查看文章</a>`);
  const logs=(t.logs||[]).map(x=>`<p>${esc(x)}</p>`).join('');
  return `<section class="task-control-row"><div class="task-control-head"><div><strong>${esc(taskKind(t.kind))}</strong> <span class="code">${esc(t.id.slice(0,10))}</span><small>${fmtDate(t.updated_at)}</small></div><div class="actions">${statusBadge(t.status)}${actions.length?actions.join(''):canControl?'<small>—</small>':'<small class="muted">只读账号无控制权限</small>'}</div></div><div class="progress"><span style="width:${Number(t.progress)||0}%"></span></div><div class="task-control-last">${esc(t.logs?.at(-1)||'等待执行')}${t.error?`<span class="task-error">${esc(t.error)}</span>`:''}</div><details><summary>查看完整日志（${(t.logs||[]).length}）</summary><div class="log-console task-full-log">${logs||'<p>暂无日志</p>'}</div></details></section>`;
}
function handleTaskAction(taskId,action){
  if(['cancel','delete'].includes(action)){
    const deleting=action==='delete';
    showModal({title:deleting?'删除任务日志':'取消任务',subtitle:deleting?'删除后无法恢复，但不会删除已经生成的文章。':'运行中的任务会在当前模型/API 调用结束后取消。',body:`<p>确定要${deleting?'删除这条任务日志':'取消该任务'}吗？</p>`,footer:`<button class="btn" data-close>返回</button><button class="btn danger" id="confirmTaskAction">确认${deleting?'删除':'取消'}</button>`});
    document.querySelector('#confirmTaskAction').addEventListener('click',()=>runTaskAction(taskId,action));
  }else runTaskAction(taskId,action);
}
async function runTaskAction(taskId,action){
  const labels={pause:'暂停请求已提交',resume:'任务已继续',cancel:'取消请求已提交',delete:'任务日志已删除'};
  try{const path=action==='delete'?`/api/tasks/${taskId}?confirm=true`:`/api/tasks/${taskId}/${action}`;await api(path,{method:action==='delete'?'DELETE':'POST'});closeModal();toast(labels[action]);renderTasks();}catch(e){toast(e.message,'error');}
}
export async function renderHistory(){setMeta('history');loading('正在读取发布记录…');try{const rows=await api('/api/history?limit=100');app.innerHTML=`<div class="page-intro"><div><h2>发布与数据复盘</h2><p>记录草稿 ID、内容状态和阅读互动数据，形成内容优化闭环。</p></div></div><div class="card"><div class="table-wrap"><table><thead><tr><th>文章</th><th>来源</th><th>状态</th><th>草稿 ID</th><th>阅读 / 点赞 / 转发</th><th>时间</th><th></th></tr></thead><tbody>${rows.length?rows.map(r=>`<tr><td><strong>${esc(r.article_title)}</strong><br><span>${esc(r.topic)}</span></td><td>${esc(sourceName(r.source))}</td><td>${statusBadge(r.status)}</td><td><span class="code">${esc((r.draft_media_id||'—').slice(0,14))}</span></td><td>${r.read_count??'—'} / ${r.like_count??'—'} / ${r.share_count??'—'}</td><td>${fmtDate(r.created_at)}</td><td>${can('task.control')?`<button class="btn small" data-metrics="${r.id}" data-read="${r.read_count??''}" data-like="${r.like_count??''}" data-share="${r.share_count??''}">录入数据</button>`:''}</td></tr>`).join(''):`<tr><td colspan="7">${empty('暂无发布记录','草稿推送成功后将自动记录')}</td></tr>`}</tbody></table></div></div>`;document.querySelectorAll('[data-metrics]').forEach(b=>b.addEventListener('click',()=>showMetrics(b)));}catch(e){renderError(e);}}
function showMetrics(btn){showModal({title:'录入复盘数据',subtitle:'可从公众号后台复制阅读互动数据',body:`<div class="form-grid"><div class="field"><label>阅读量</label><input class="input" id="metricRead" type="number" min="0" value="${btn.dataset.read}"></div><div class="field"><label>点赞量</label><input class="input" id="metricLike" type="number" min="0" value="${btn.dataset.like}"></div><div class="field"><label>转发量</label><input class="input" id="metricShare" type="number" min="0" value="${btn.dataset.share}"></div></div>`,footer:'<button class="btn" data-close>取消</button><button class="btn primary" id="saveMetrics">保存数据</button>'});document.querySelector('#saveMetrics').addEventListener('click',async()=>{const n=id=>{const v=document.querySelector(id).value;return v===''?null:Number(v)};try{await api(`/api/history/${btn.dataset.metrics}/metrics`,{method:'PUT',body:JSON.stringify({read_count:n('#metricRead'),like_count:n('#metricLike'),share_count:n('#metricShare')})});closeModal();toast('复盘数据已保存');renderHistory();}catch(e){toast(e.message,'error');}});}
export async function renderHelp(){
  setMeta('help');loading('正在加载完整使用手册…');
  try{
    const manual=await api('/api/help/manual');
    const updated=manual.updated_at?new Date(manual.updated_at*1000).toLocaleString('zh-CN'):'';
    app.innerHTML=`<div class="page-intro manual-intro"><div><h2>观思辩明使用手册</h2><p>覆盖注册、选题、生成、文章管理、额度和管理员操作${updated?` · 更新于 ${esc(updated)}`:''}</p></div><div class="actions"><button class="btn" id="manualPrint">打印 / 导出 PDF</button><button class="btn" id="manualTop">返回顶部</button></div></div><article class="card manual-content"><div class="card-body">${manual.html}</div></article>`;
    document.querySelector('#manualPrint').addEventListener('click',()=>window.print());
    document.querySelector('#manualTop').addEventListener('click',()=>window.scrollTo({top:0,behavior:'smooth'}));
    document.querySelectorAll('.manual-content a[href^="#"]').forEach(link=>link.addEventListener('click',event=>{const target=document.querySelector(link.getAttribute('href'));if(target){event.preventDefault();target.scrollIntoView({behavior:'smooth',block:'start'});}}));
  }catch(e){renderError(e,'请确认 web/manual/USER_MANUAL.md 与配套截图文件已经部署。');}
}
