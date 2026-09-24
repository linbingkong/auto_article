import { state, can, logout, fetchCurrentUser } from './core.js';
import { renderDashboard } from './dashboard.js';
import { renderHotspots } from './hotspots.js';
import { renderArticles, renderArticle } from './articles.js';
import { renderConfig } from './config-page.js';
import { renderMyApiConfig } from './my-api-config.js';
import { renderBilling } from './billing.js';
import { renderHelp, renderHistory, renderTasks } from './operations.js';
import { renderLogin } from './login.js';
import { renderUsers } from './users.js';
import { renderRegistrations } from './registrations.js';
import { renderAudit } from './audit.js';
import { renderPaymentOrders } from './payment-orders.js';
import { renderTokenUsage } from './token-usage.js';

function applyRoleNav() {
  document.querySelectorAll('.nav a[data-role]').forEach(a => {
    const need = a.dataset.role;
    a.style.display = (need === 'admin' && can('user.manage')) ? '' : (need !== 'admin' ? '' : 'none');
  });
  const configLink = document.querySelector('.nav a[data-page="config"]');
  if (configLink) configLink.style.display = can('config.edit') ? '' : 'none';
  document.querySelectorAll('.nav a[data-permission]').forEach(a => {
    a.style.display = can(a.dataset.permission) ? '' : 'none';
  });
  const myApiLink = document.querySelector('.nav a[data-page="my-api-config"]');
  if (myApiLink && can(myApiLink.dataset.permission)) {
    const open = can('config.edit') || state.features?.user_api_config !== false;
    myApiLink.style.display = open ? '' : 'none';
  }
  const box = document.querySelector('#userBox');
  if (box) {
    if (state.user) {
      box.style.display = '';
      document.querySelector('#userName').textContent = state.user.username;
      document.querySelector('#userRole').textContent = { admin: '管理员', editor: '编辑', creator: '创作者', viewer: '只读' }[state.user.role] || state.user.role;
    } else { box.style.display = 'none'; }
  }
}

async function route() {
  const raw = location.hash.replace(/^#/, '') || 'dashboard'; const [page, id] = raw.split('/'); state.currentPage = page;
  document.querySelector('#sidebar').classList.remove('open'); document.body.classList.remove('menu-open');
  applyRoleNav();
  if (page === 'dashboard') return renderDashboard();
  if (page === 'hotspots') return renderHotspots();
  if (page === 'articles') return renderArticles();
  if (page === 'article' && id) return renderArticle(id);
  if (page === 'tasks') return renderTasks();
  if (page === 'history') return renderHistory();
  if (page === 'config') return renderConfig();
  if (page === 'my-api-config') return renderMyApiConfig();
  if (page === 'billing') return renderBilling();
  if (page === 'users') return renderUsers();
  if (page === 'registrations') return renderRegistrations();
  if (page === 'audit') return renderAudit();
  if (page === 'payment-orders') return renderPaymentOrders();
  if (page === 'token-usage') return renderTokenUsage();
  if (page === 'help') return renderHelp();
  location.hash = 'dashboard';
}

window.addEventListener('hashchange', route);
window.addEventListener('route-refresh', route);
window.addEventListener('auth-expired', () => { document.body.classList.add('login-mode'); renderLogin('登录已过期，请重新登录'); });
window.addEventListener('auth-ready', () => { applyRoleNav(); route(); });
document.querySelector('#refreshButton').addEventListener('click', route);
const toggleMenu = () => { const open = document.querySelector('#sidebar').classList.toggle('open'); document.body.classList.toggle('menu-open', open); };
document.querySelector('#menuButton').addEventListener('click', toggleMenu);
document.querySelector('#sidebarBackdrop').addEventListener('click', () => { document.querySelector('#sidebar').classList.remove('open'); document.body.classList.remove('menu-open'); });

(async function boot() {
  try {
    document.querySelector('#logoutButton')?.addEventListener('click', () => { logout(); });
    const user = await fetchCurrentUser();
    if (user) { state.user = user; if (user.features) state.features = user.features; localStorage.setItem('wechat_user', JSON.stringify(user)); applyRoleNav(); route(); }
    else { document.body.classList.add('login-mode'); renderLogin(); }
  } catch (e) {
    console.error('Boot failed:', e);
    document.body.classList.add('login-mode');
    renderLogin('页面初始化失败：' + (e.message || '未知错误'));
  }
})();