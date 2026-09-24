export const app = document.querySelector('#app');
export const modalRoot = document.querySelector('#modalRoot');
const toastRoot = document.querySelector('#toastRoot');
const pageTitle = document.querySelector('#pageTitle');
const breadcrumb = document.querySelector('#breadcrumb');

export const state = {
  accessToken: localStorage.getItem('wechat_access_token') || '',
  refreshToken: localStorage.getItem('wechat_refresh_token') || '',
  user: JSON.parse(localStorage.getItem('wechat_user') || 'null'),
  currentPage: 'dashboard',
  hotspots: [], selectedTopic: null, hotspotUpdatedAt: '', hotspotKeyword: '', hotspotTotal: 0,
  hotspotSearchMode: 'ranking', hotspotTimeRange: '7d', hotspotSearchTopic: 'news', hotspotPreferChinese: true,
  sourceSet: new Set(['weibo', 'zhihu', 'toutiao', 'cls']),
  article: null, billing: null,
  features: { user_api_config: true },
};

const ROLE_PERMISSIONS = {
  admin: ['article.view','article.create','article.edit','article.delete','article.push','task.control','config.edit','user.manage','audit.view'],
  editor: ['article.view','article.create','article.edit','article.push','task.control'],
  creator: ['article.view','article.create','article.edit','article.delete','task.control'],
  viewer: ['article.view'],
};
export function can(permission) {
  if (!state.user) return false;
  return (ROLE_PERMISSIONS[state.user.role] || []).includes(permission);
}
export function currentUser() { return state.user; }
const pageMeta = {
  dashboard: ['工作台', '概览'], hotspots: ['内容生产', '热点选题'],
  articles: ['内容生产', '文章内容'], article: ['文章内容', '预览与编辑'],
  tasks: ['运营管理', '任务日志'], history: ['运营管理', '发布复盘'],
  config: ['系统设置', '配置中心'], 'my-api-config': ['个人设置', '我的 API 配置'], billing: ['个人设置', '生成额度'], users: ['系统设置', '用户管理'],
  registrations: ['系统设置', '注册审批'], audit: ['系统设置', '审计日志'], 'payment-orders': ['系统设置', '支付订单'], 'token-usage': ['系统设置', 'Token 成本看板'], help: ['帮助中心', '使用指南'],
};
export const esc = (value='') => String(value).replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
export const checked = value => value ? 'checked' : '';
export function fmtDate(value) { if(!value)return '—'; const d=new Date(value); return Number.isNaN(d.getTime())?esc(value):d.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}); }
export function sourceName(source) { return ({weibo:'微博',zhihu:'知乎',toutiao:'头条',cls:'财联社',bili:'B站',baidu:'百度',manual:'手动',web_search:'全网搜索'}[source]||source); }
export function statusBadge(status) { const m={generated:['待审核','gold'],draft_created:['已推草稿','green'],published:['已发布','green'],pending:['排队中','blue'],running:['执行中','blue'],pausing:['等待暂停','gold'],paused:['已暂停','gold'],cancelling:['正在取消','gold'],cancelled:['已取消','red'],success:['已完成','green'],failed:['失败','red'],dry_run:['本地生成','gold'],local_only:['仅本地','gold']}; const [l,c]=m[status]||[status||'未知','']; return `<span class="badge ${c}">${esc(l)}</span>`; }
export function loading(message='正在加载…') { app.innerHTML=`<div class="loading-state"><span class="spinner"></span><p>${esc(message)}</p></div>`; }
export function empty(title,desc='') { return `<div class="empty"><strong>${esc(title)}</strong><span>${esc(desc)}</span></div>`; }
export function toast(message,type='success') { const el=document.createElement('div'); el.className=`toast ${type==='error'?'error':''}`; el.textContent=message; toastRoot.appendChild(el); setTimeout(()=>el.remove(),3600); }

export async function api(path, options = {}, _retry = false) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (state.accessToken) headers['Authorization'] = 'Bearer ' + state.accessToken;
  const response = await fetch(path, { ...options, headers });
  let payload = {};
  try { payload = await response.json(); } catch (_) { payload = {}; }
  if (response.status === 401) {
    if (!_retry && state.refreshToken) {
      const refreshed = await tryRefresh();
      if (refreshed) return api(path, options, true);
    }
    clearSession();
    window.dispatchEvent(new Event('auth-expired'));
    throw new Error('登录已过期，请重新登录');
  }
  if (!response.ok || payload.ok === false) { const m = payload.message || payload.detail || `请求失败（${response.status}）`; throw new Error(typeof m === 'string' ? m : JSON.stringify(m)); }
  return payload.data;
}

function saveSession(data) {
  state.accessToken = data.access_token;
  state.refreshToken = data.refresh_token;
  state.user = data.user;
  localStorage.setItem('wechat_access_token', data.access_token);
  localStorage.setItem('wechat_refresh_token', data.refresh_token);
  localStorage.setItem('wechat_user', JSON.stringify(data.user));
}
export function clearSession() {
  state.accessToken = ''; state.refreshToken = ''; state.user = null;
  localStorage.removeItem('wechat_access_token');
  localStorage.removeItem('wechat_refresh_token');
  localStorage.removeItem('wechat_user');
}
async function tryRefresh() {
  if (!state.refreshToken) return false;
  try {
    const r = await fetch('/api/auth/refresh', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: state.refreshToken }),
    });
    const p = await r.json();
    if (p.ok && p.data?.access_token) { saveSession(p.data); return true; }
    return false;
  } catch (_) { return false; }
}
export async function login(username, password) {
  const r = await fetch('/api/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  let p = {};
  try { p = await r.json(); } catch (_) { }
  if (!r.ok || p.ok === false) {
    const error = new Error(p.message || p.detail || '登录失败');
    error.code = p.code || '';
    throw error;
  }
  saveSession(p.data);
  return p.data.user;
}
export async function logout() {
  try {
    if (state.refreshToken) {
      await fetch('/api/auth/logout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + state.accessToken },
        body: JSON.stringify({ refresh_token: state.refreshToken }),
      });
    }
  } catch (_) { }
  clearSession();
  window.dispatchEvent(new Event('auth-expired'));
}
export async function fetchCurrentUser() {
  const probe = async () => {
    try {
      const r = await fetch('/api/auth/me', {
        headers: state.accessToken ? { 'Authorization': 'Bearer ' + state.accessToken } : {},
      });
      if (!r.ok) return { ok: false, status: r.status };
      const p = await r.json();
      if (p.ok && p.data && p.data.id && p.data.id !== 'anonymous') return { ok: true, user: p.data };
      return { ok: false, status: 200 };
    } catch (_) { return { ok: false, status: 0 }; }
  };
  try {
    let result = await probe();
    if (result.ok) return result.user;
    if (result.status === 401 && state.refreshToken) {
      if (await tryRefresh()) {
        result = await probe();
        if (result.ok) return result.user;
      }
    }
    return null;
  } catch (_) { return null; }
}
export function showModal({title,subtitle='',body='',wide=false,footer=''}) {
  modalRoot.innerHTML=`<div class="modal-backdrop"><section class="modal ${wide?'wide':''}" role="dialog" aria-modal="true"><header class="modal-head"><div><h3>${esc(title)}</h3>${subtitle?`<p>${esc(subtitle)}</p>`:''}</div><button class="close" data-close>×</button></header><div class="modal-body">${body}</div>${footer?`<footer class="modal-foot">${footer}</footer>`:''}</section></div>`;
  modalRoot.querySelectorAll('[data-close]').forEach(b=>b.addEventListener('click',closeModal));
  modalRoot.querySelector('.modal-backdrop')?.addEventListener('click',e=>{if(e.target.classList.contains('modal-backdrop'))closeModal();});
}
export function closeModal(){modalRoot.innerHTML='';}
export function setMeta(page){const m=pageMeta[page]||pageMeta.dashboard;breadcrumb.textContent=m[0];pageTitle.textContent=m[1];document.querySelectorAll('.nav a').forEach(a=>a.classList.toggle('active',a.dataset.page===page||(page==='article'&&a.dataset.page==='articles')));}
export function metric(label,value,icon,note){return `<div class="card metric-card"><div class="metric-label"><span>${label}</span><span class="metric-icon">${icon}</span></div><div class="metric-value">${value??0}</div><div class="metric-note">${note}</div></div>`;}
export function statusRow(label,okState,detail){return `<div class="status-row"><span class="status-label"><i class="dot ${okState?'ok':'warn'}"></i>${label}</span><span>${esc(detail)}</span></div>`;}
export function cardList(items,kind='article'){if(!items?.length)return empty('暂无记录','完成一次内容生成后会显示在这里');return `<ul class="list">${items.map(i=>`<li class="list-row"><div class="list-main"><p class="list-title">${esc(i.title||i.article_title||i.kind)}</p><div class="list-meta"><span>${fmtDate(i.updated_at||i.created_at)}</span>${i.word_count?`<span>${i.word_count} 字</span>`:''}</div></div>${statusBadge(i.status)}${kind==='article'&&i.id?`<a class="btn small" href="#article/${i.id}">查看</a>`:''}</li>`).join('')}</ul>`;}
export function taskList(tasks,limit=20){if(!tasks?.length)return empty('暂无任务','生成文章后可在这里查看执行过程');return tasks.slice(0,limit).map(t=>`<div class="task-row"><div class="task-top"><div><strong>${t.kind==='generate'?'文章生成':'草稿推送'}</strong> <span class="code">${esc(t.id.slice(0,8))}</span></div>${statusBadge(t.status)}</div><div class="progress"><span style="width:${Number(t.progress)||0}%"></span></div><div class="task-log">${esc(t.logs?.at(-1)||'')} · ${fmtDate(t.updated_at)}</div></div>`).join('');}
export function renderError(error,hint=''){app.innerHTML=`<div class="card"><div class="empty"><strong>页面加载失败</strong><span>${esc(error.message||error)}</span>${hint?`<p>${esc(hint)}</p>`:''}<button class="btn section-space" id="retryPage">重新加载</button></div></div>`;document.querySelector('#retryPage')?.addEventListener('click',()=>window.dispatchEvent(new Event('route-refresh')));}
export function watchTask(taskId,title,onSuccess){
  showModal({title,subtitle:`任务 ID：${taskId.slice(0,12)}`,body:'<div class="progress"><span id="taskProgress" style="width:2%"></span></div><p id="taskState" class="field-hint">任务已提交…</p><div class="log-console section-space" id="taskLogs"><p>等待后台执行</p></div>',footer:'<button class="btn" data-close>后台运行</button>'});
  let stopped=false;modalRoot.querySelectorAll('[data-close]').forEach(b=>b.addEventListener('click',()=>{stopped=true;}));
  const poll=async()=>{try{const t=await api(`/api/tasks/${taskId}`);if(stopped)return;const bar=document.querySelector('#taskProgress'),label=document.querySelector('#taskState'),logs=document.querySelector('#taskLogs');if(!bar)return;bar.style.width=`${t.progress}%`;label.textContent=`${t.status==='running'?'执行中':t.status} · ${t.progress}%`;logs.innerHTML=(t.logs||[]).map(x=>`<p>${esc(x)}</p>`).join('');logs.scrollTop=logs.scrollHeight;if(t.status==='success'){toast('任务执行成功');setTimeout(()=>{closeModal();onSuccess?.(t);},450);return;}if(t.status==='failed'){toast(t.error||'任务失败','error');label.textContent='执行失败';return;}if(t.status==='cancelled'){toast('任务已取消','error');label.textContent='任务已取消';return;}setTimeout(poll,1100);}catch(e){toast(e.message,'error');}};poll();
}
