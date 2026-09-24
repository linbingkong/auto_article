import { api, app, can, empty, esc, fmtDate, loading, renderError, setMeta, toast } from './core.js';

const ACTION_LABELS = {
  'login': '登录', 'login.failed': '登录失败', 'logout': '退出登录',
  'user.create': '创建用户', 'user.update': '更新用户', 'user.list': '查看用户列表',
  'config.update': '修改配置', 'audit.list': '查看审计日志',
  'article.generate': '生成文章', 'article.delete': '删除文章', 'article.push': '推送草稿',
  'task.pause': '暂停任务', 'task.resume': '恢复任务', 'task.cancel': '取消任务', 'task.delete': '删除任务',
};

export async function renderAudit() {
  setMeta('audit');
  if (!can('audit.view')) { app.innerHTML = `<div class="card">${empty('无权限', '仅管理员可以查看审计日志')}</div>`; return; }
  loading('正在加载审计日志…');
  try {
    const data = await api('/api/audit?limit=200');
    app.innerHTML = `
    <div class="page-intro"><div><h2>审计日志</h2><p>共 ${data.total} 条操作记录 · 按时间倒序</p></div></div>
    <div class="card">${data.logs.length ? auditTable(data.logs) : empty('暂无审计记录', '系统关键操作会记录在这里')}</div>`;
  } catch (e) { renderError(e); }
}

function auditTable(logs) {
  return `<table class="table"><thead><tr><th>时间</th><th>用户</th><th>操作</th><th>对象</th><th>详情</th><th>来源</th></tr></thead><tbody>
  ${logs.map(l => `<tr>
    <td class="nowrap">${fmtDate(l.created_at)}</td>
    <td>${esc(l.username || '匿名')}</td>
    <td><span class="badge blue">${esc(ACTION_LABELS[l.action] || l.action)}</span></td>
    <td>${esc(l.resource || '')}${l.resource_id ? ` <span class="code">${esc(l.resource_id.slice(0, 8))}</span>` : ''}</td>
    <td class="detail-cell">${esc(formatDetail(l.detail))}</td>
    <td class="nowrap">${esc(l.ip || '')}</td></tr>`).join('')}
  </tbody></table>`;
}

function formatDetail(detail) {
  if (!detail) return '';
  if (typeof detail === 'string') return detail.slice(0, 200);
  try { return JSON.stringify(detail).slice(0, 200); } catch (_) { return ''; }
}