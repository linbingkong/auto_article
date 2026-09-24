import { api, app, can, empty, esc, fmtDate, loading, renderError, setMeta, showModal, closeModal, toast } from './core.js';

const ROLE_LABELS = { admin: '管理员', editor: '编辑', creator: '创作者', viewer: '只读' };
const ROLE_BADGE = { admin: 'green', editor: 'blue', creator: 'gold', viewer: '' };

export async function renderUsers() {
  setMeta('users');
  if (!can('user.manage')) { app.innerHTML = `<div class="card">${empty('无权限', '仅管理员可以管理用户')}</div>`; return; }
  loading('正在加载用户列表…');
  try {
    const data = await api('/api/users');
    app.innerHTML = `
    <div class="page-intro"><div><h2>用户管理</h2><p>共 ${data.total} 个账号 · 管理员可创建、调整角色、重置密码或禁用账号</p></div>
    <div class="actions"><button class="btn primary" id="createUser">＋ 创建用户</button></div></div>
    <div class="card">${data.users.length ? userTable(data.users) : empty('暂无用户', '点击右上角创建第一个账号')}</div>`;
    document.querySelector('#createUser').addEventListener('click', showCreateUser);
    bindRowActions(data.users);
  } catch (e) { renderError(e); }
}

function apiConfigBadges(config={}) {
  const items=[['LLM',config.llm],['搜索',config.search],['图片',config.image]];
  return `<div class="api-status-list">${items.map(([name,on])=>`<span class="badge ${on?'green':''}">${name}${on?' ✓':' —'}</span>`).join('')}</div>`;
}

function userTable(users) {
  return `<table class="table"><thead><tr><th>用户名</th><th>注册手机号</th><th>角色</th><th>状态</th><th>个人 API</th><th>生成额度</th><th>创建时间</th><th>操作</th></tr></thead><tbody>
  ${users.map(u => `<tr data-id="${u.id}" data-username="${esc(u.username)}">
    <td><strong>${esc(u.username)}</strong></td>
    <td>${u.phone ? `<span class="code">${esc(u.phone)}</span>` : u.masked_phone ? `<span class="code">${esc(u.masked_phone)}</span>` : '<span class="badge">后台创建</span>'}</td>
    <td><span class="badge ${ROLE_BADGE[u.role] || ''}">${ROLE_LABELS[u.role] || u.role}</span></td>
    <td>${u.status === 'active' ? '<span class="badge green">启用</span>' : '<span class="badge red">禁用</span>'}</td>
    <td>${apiConfigBadges(u.api_config)}</td>
    <td><span class="badge gold">试用 ${u.billing?.trial_remaining||0}</span> <span class="badge green">付费 ${u.billing?.paid_available||0}</span></td>
    <td>${fmtDate(u.created_at)}</td>
    <td class="row-actions">
      <button class="btn small" data-act="edit">编辑</button>
      <button class="btn small" data-act="credits">充值/调整</button>
      <button class="btn small danger-outline" data-act="reset">重置密码</button>
      ${(u.api_config?.llm||u.api_config?.search||u.api_config?.image)?'<button class="btn small danger-outline" data-act="clear-api">清除 API</button>':''}
      <button class="btn small danger" data-act="delete">删除</button>
    </td></tr>`).join('')}
  </tbody></table>`;
}

function bindRowActions(users) {
  app.querySelectorAll('tbody tr').forEach(row => {
    const id = row.dataset.id;
    row.querySelector('[data-act="edit"]')?.addEventListener('click', () => showEditUser(users.find(x => x.id === id)));
    row.querySelector('[data-act="credits"]')?.addEventListener('click', () => showCredits(users.find(x => x.id === id)));
    row.querySelector('[data-act="reset"]')?.addEventListener('click', () => showResetPassword(id, row.dataset.username));
    row.querySelector('[data-act="clear-api"]')?.addEventListener('click', () => clearUserApiConfig(id, row.dataset.username));
    row.querySelector('[data-act="delete"]')?.addEventListener('click', () => showDeleteUser(users.find(x => x.id === id)));
  });
}

function showDeleteUser(user) {
  showModal({
    title: `删除用户：${user.username}`,
    subtitle: '此操作不可恢复',
    body: `<div class="manual-review-warning"><strong>删除影响</strong><p>账号将被永久删除；个人 API 配置和文章共享授权同步清除；该用户名下的文章将自动转移给当前管理员。注册手机号只保存过掩码，无法恢复完整号码。</p></div>
    <div class="field section-space"><label>请输入用户名 <strong>${esc(user.username)}</strong> 以确认删除</label><input class="input" id="deleteConfirmName" autocomplete="off"></div>`,
    footer: '<button class="btn" data-close>取消</button><button class="btn danger" id="deleteSubmit" disabled>确认删除</button>',
  });
  const input = document.querySelector('#deleteConfirmName');
  const submit = document.querySelector('#deleteSubmit');
  input.addEventListener('input', () => { submit.disabled = input.value.trim() !== user.username; });
  submit.addEventListener('click', async () => {
    submit.disabled = true; submit.textContent = '删除中…';
    try {
      const result = await api(`/api/users/${user.id}`, { method: 'DELETE' });
      closeModal(); toast(`用户已删除${result.reassigned_articles ? `，${result.reassigned_articles} 篇文章已转移` : ''}`); renderUsers();
    } catch (e) { submit.disabled = false; submit.textContent = '确认删除'; toast(e.message, 'error'); }
  });
}

function showCreateUser() {
  showModal({
    title: '创建用户', subtitle: '新账号初始状态为启用',
    body: `<div class="field"><label>用户名 <small>3-32 位，仅字母数字</small></label><input class="input" id="nuName" minlength="3" maxlength="32" pattern="[A-Za-z0-9]+" placeholder="如 zhangsan"></div>
    <div class="field"><label>初始密码 <small>至少 8 位</small></label><input class="input" id="nuPass" type="password" minlength="8" placeholder="至少 8 位"></div>
    <div class="field"><label>角色</label><select class="select" id="nuRole"><option value="viewer">只读（只能查看）</option><option value="creator" selected>创作者（平台额度或个人 API）</option><option value="editor">编辑（可生成、编辑、推送、控制任务）</option><option value="admin">管理员（全部权限）</option></select></div>`,
    footer: '<button class="btn" data-close>取消</button><button class="btn primary" id="nuSubmit">创建</button>',
  });
  document.querySelector('#nuSubmit').addEventListener('click', async () => {
    try {
      await api('/api/users', { method: 'POST', body: JSON.stringify({
        username: document.querySelector('#nuName').value.trim(),
        password: document.querySelector('#nuPass').value,
        role: document.querySelector('#nuRole').value,
      }) });
      closeModal(); toast('用户已创建'); renderUsers();
    } catch (e) { toast(e.message, 'error'); }
  });
}

function showEditUser(user) {
  showModal({
    title: `编辑用户：${user.username}`,
    body: `<div class="field"><label>角色</label><select class="select" id="euRole">
      <option value="viewer" ${user.role === 'viewer' ? 'selected' : ''}>只读</option>
      <option value="creator" ${user.role === 'creator' ? 'selected' : ''}>创作者</option>
      <option value="editor" ${user.role === 'editor' ? 'selected' : ''}>编辑</option>
      <option value="admin" ${user.role === 'admin' ? 'selected' : ''}>管理员</option></select></div>
    <div class="field"><label>状态</label><select class="select" id="euStatus">
      <option value="active" ${user.status === 'active' ? 'selected' : ''}>启用</option>
      <option value="disabled" ${user.status === 'disabled' ? 'selected' : ''}>禁用</option></select></div>
    <div class="field"><label>注册手机号 <small>保存明文，留空不变</small></label><input class="input" id="euPhone" inputmode="tel" maxlength="24" value="${esc(user.phone || '')}" placeholder="如 13800138000"></div>`,
    footer: '<button class="btn" data-close>取消</button><button class="btn primary" id="euSubmit">保存</button>',
  });
  document.querySelector('#euSubmit').addEventListener('click', async () => {
    try {
      await api(`/api/users/${user.id}`, { method: 'PUT', body: JSON.stringify({
        role: document.querySelector('#euRole').value,
        status: document.querySelector('#euStatus').value,
        phone: document.querySelector('#euPhone').value.trim(),
      }) });
      closeModal(); toast('用户已更新'); renderUsers();
    } catch (e) { toast(e.message, 'error'); }
  });
}

function showCredits(user) {
  showModal({
    title: `调整额度：${user.username}`,
    subtitle: `当前付费额度 ${user.billing?.paid_credits || 0} 次；正数充值，负数扣减`,
    body: `<div class="field"><label>调整次数</label><input class="input" id="creditDelta" type="number" min="-10000" max="10000" value="1"></div><div class="field"><label>原因</label><textarea class="textarea" id="creditReason" maxlength="500" placeholder="例如：收到 2 元人工付款，充值 1 次"></textarea></div>`,
    footer: '<button class="btn" data-close>取消</button><button class="btn primary" id="creditSubmit">确认调整</button>',
  });
  document.querySelector('#creditSubmit').addEventListener('click', async () => {
    try {
      await api(`/api/users/${user.id}/billing/credits`, { method: 'POST', body: JSON.stringify({ delta: Number(document.querySelector('#creditDelta').value), reason: document.querySelector('#creditReason').value.trim() }) });
      closeModal(); toast('生成额度已调整'); renderUsers();
    } catch (e) { toast(e.message, 'error'); }
  });
}

async function clearUserApiConfig(id, username) {
  if (!confirm(`确定清除 ${username} 的全部个人 API 配置吗？管理员无法读取或恢复原密钥。`)) return;
  try { await api(`/api/users/${id}/api-config`, { method: 'DELETE' }); toast('个人 API 配置已清除'); renderUsers(); }
  catch (e) { toast(e.message, 'error'); }
}

function showResetPassword(id, username) {
  showModal({
    title: `重置密码：${username}`,
    body: `<div class="field"><label>新密码 <small>至少 8 位</small></label><input class="input" id="rpPass" type="password" minlength="8" placeholder="至少 8 位"></div>`,
    footer: '<button class="btn" data-close>取消</button><button class="btn primary" id="rpSubmit">重置</button>',
  });
  document.querySelector('#rpSubmit').addEventListener('click', async () => {
    try {
      await api(`/api/users/${id}`, { method: 'PUT', body: JSON.stringify({ password: document.querySelector('#rpPass').value }) });
      closeModal(); toast('密码已重置');
    } catch (e) { toast(e.message, 'error'); }
  });
}