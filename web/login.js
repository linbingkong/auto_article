import { app, esc, login, toast } from './core.js';

const TOKEN_KEY = 'wechat_registration_token';
let publicConfig = null;

async function publicRequest(path, body = null) {
  const response = await fetch(path, {
    method: body ? 'POST' : 'GET',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  let payload = {};
  try { payload = await response.json(); } catch (_) { }
  if (!response.ok || payload.ok === false) {
    const error = new Error(payload.message || payload.detail || '请求失败');
    error.code = payload.code || '';
    throw error;
  }
  return payload.data;
}

async function getPublicConfig() {
  if (!publicConfig) publicConfig = await publicRequest('/api/registration/public-config');
  return publicConfig;
}

function shell(content) {
  document.body.classList.add('login-mode');
  app.innerHTML = `<div class="login-wrap"><div class="login-card registration-card">
    <div class="login-brand"><img src="/assets/brand-symbol.png" alt="观思辩明 Logo"><div><strong>观思辩明</strong><small>公众号智能体管理台</small></div></div>
    ${content}</div></div>`;
}

export function renderLogin(message = '') {
  shell(`${message ? `<p class="login-msg error">${esc(message)}</p>` : ''}
    <form id="loginForm" autocomplete="off">
      <div class="field"><label>用户名</label><input class="input" id="loginUsername" required minlength="3" maxlength="32" placeholder="请输入用户名" autocomplete="username"></div>
      <div class="field"><label>密码</label><input class="input" id="loginPassword" type="password" required minlength="8" maxlength="128" placeholder="请输入密码" autocomplete="current-password"></div>
      <button class="btn primary login-submit" id="loginSubmit" type="submit">登 录</button>
    </form>
    <div class="login-secondary"><button class="text-button" id="showRegister" type="button">没有账号？申请注册</button><button class="text-button" id="checkApplication" type="button">查询注册进度</button></div>
    <p class="login-hint">注册用户需关注“观思辩明”公众号并私信注册手机号，由管理员人工核对批准。</p>`);
  document.querySelector('#loginForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = document.querySelector('#loginSubmit');
    const username = document.querySelector('#loginUsername').value.trim();
    const password = document.querySelector('#loginPassword').value;
    button.disabled = true; button.textContent = '登录中…';
    try {
      await login(username, password);
      toast('登录成功'); document.body.classList.remove('login-mode');
      window.dispatchEvent(new Event('auth-ready'));
    } catch (error) {
      if (error.code === 'PENDING_APPROVAL' && localStorage.getItem(TOKEN_KEY)) {
        return renderRegistrationStatus();
      }
      button.disabled = false; button.textContent = '登 录';
      renderLogin(error.message || '登录失败');
    }
  });
  document.querySelector('#showRegister').addEventListener('click', renderRegister);
  document.querySelector('#checkApplication').addEventListener('click', () => {
    if (localStorage.getItem(TOKEN_KEY)) renderRegistrationStatus();
    else renderLogin('当前浏览器没有保存注册进度，请使用注册后保存该页面的浏览器查询。');
  });
  document.querySelector('#loginUsername')?.focus();
}

export async function renderRegister() {
  shell('<div class="loading-state"><span class="spinner"></span><p>正在读取注册说明…</p></div>');
  try {
    const config = await getPublicConfig();
    if (!config.enabled) {
      shell('<h2 class="auth-title">公开注册尚未开启</h2><p class="login-msg">请联系管理员创建账号。</p><button class="btn login-submit" id="backLogin">返回登录</button>');
      document.querySelector('#backLogin').addEventListener('click', () => renderLogin());
      return;
    }
    shell(`<h2 class="auth-title">申请注册</h2><p class="auth-subtitle">请先关注“${esc(config.account_name)}”公众号，再填写账号信息并提交申请。</p>
      <div class="follow-panel registration-follow-first">${config.qr_image ? `<img class="account-qr" src="${esc(config.qr_image)}" alt="${esc(config.account_name)}公众号二维码">` : ''}<strong>第一步：关注公众号“${esc(config.account_name)}”</strong><p>${esc(config.follow_instructions)}</p><p>${esc(config.private_message_instructions)}</p></div>
      <form id="registerForm" autocomplete="off">
        <div class="field"><label>用户名 <small>3-32 位，仅字母或数字</small></label><input class="input" id="registerUsername" required minlength="3" maxlength="32" pattern="[A-Za-z0-9]+" autocomplete="username"></div>
        <div class="field"><label>密码 <small>至少 8 位</small></label><input class="input" id="registerPassword" type="password" required minlength="8" maxlength="128" autocomplete="new-password"></div>
        <div class="field"><label>确认密码</label><input class="input" id="registerPassword2" type="password" required minlength="8" maxlength="128" autocomplete="new-password"></div>
        <div class="field"><label>手机号</label><input class="input" id="registerPhone" inputmode="tel" required maxlength="24" placeholder="中国大陆手机号"></div>
        <label class="checkline follow-check"><input id="followConfirmed" type="checkbox" required><span>我已扫码关注“${esc(config.account_name)}”公众号，并知晓需私信注册手机号</span></label>
        <button class="btn primary login-submit" id="registerSubmit" type="submit">提交注册申请</button>
      </form>
      <button class="text-button auth-back" id="backLogin" type="button">返回登录</button>`);
    document.querySelector('#backLogin').addEventListener('click', () => renderLogin());
    document.querySelector('#registerForm').addEventListener('submit', async event => {
      event.preventDefault();
      const password = document.querySelector('#registerPassword').value;
      if (password !== document.querySelector('#registerPassword2').value) return toast('两次输入的密码不一致', 'error');
      const button = document.querySelector('#registerSubmit'); button.disabled = true; button.textContent = '提交中…';
      try {
        const data = await publicRequest('/api/auth/register', {
          username: document.querySelector('#registerUsername').value.trim(), password,
          phone: document.querySelector('#registerPhone').value.trim(),
          follow_confirmed: document.querySelector('#followConfirmed').checked,
        });
        localStorage.setItem(TOKEN_KEY, data.registration_token);
        publicConfig = { ...config, ...data };
        renderRegistrationStatus(data);
      } catch (error) {
        button.disabled = false; button.textContent = '提交注册申请'; toast(error.message, 'error');
      }
    });
  } catch (error) { renderLogin(error.message); }
}

export async function renderRegistrationStatus(initial = null) {
  shell('<div class="loading-state"><span class="spinner"></span><p>正在查询注册进度…</p></div>');
  try {
    const token = localStorage.getItem(TOKEN_KEY);
    if (!token) return renderLogin('当前浏览器没有保存注册进度。');
    const [status, config] = await Promise.all([
      initial ? Promise.resolve(initial) : publicRequest('/api/auth/registration-status', { registration_token: token }),
      getPublicConfig(),
    ]);
    const pending = status.status === 'pending_approval';
    const approved = status.status === 'active';
    const rejected = status.status === 'rejected';
    shell(`<div class="application-state ${approved ? 'approved' : rejected ? 'rejected' : ''}"><span>${approved ? '✓' : rejected ? '!' : '⌛'}</span><h2>${approved ? '申请已批准' : rejected ? '申请未通过' : '等待管理员审核'}</h2></div>
      <p class="auth-subtitle">账号：${esc(status.username)} · 手机号：${esc(status.masked_phone || '')}</p>
      ${pending ? `<div class="follow-panel">${config.qr_image ? `<img class="account-qr" src="${esc(config.qr_image)}" alt="${esc(config.account_name)}公众号二维码">` : ''}<strong>关注公众号：${esc(config.account_name)}</strong><p>${esc(config.follow_instructions)}</p><p>${esc(config.private_message_instructions)}</p><small>系统不调用微信接口，管理员将在公众号后台人工核对私信手机号。</small></div>` : ''}
      ${rejected ? `<p class="login-msg error">${esc(status.review_note || '管理员未批准该申请')}</p><button class="btn primary login-submit" id="resubmitButton">修改后重新提交</button>` : ''}
      ${approved ? '<p class="login-msg success">现在可以使用注册用户名和密码登录。</p>' : ''}
      <div class="login-secondary">${pending ? '<button class="btn" id="refreshStatus">刷新审核状态</button>' : ''}<button class="btn ${approved ? 'primary' : ''}" id="backLogin">返回登录</button></div>`);
    document.querySelector('#backLogin').addEventListener('click', () => renderLogin());
    document.querySelector('#refreshStatus')?.addEventListener('click', () => renderRegistrationStatus());
    document.querySelector('#resubmitButton')?.addEventListener('click', () => renderResubmit(status, config));
  } catch (error) { renderLogin(error.message); }
}

function renderResubmit(status, config) {
  shell(`<h2 class="auth-title">重新提交申请</h2><p class="auth-subtitle">${esc(status.review_note || '')}</p>
    <div class="follow-panel registration-follow-first">${config.qr_image ? `<img class="account-qr" src="${esc(config.qr_image)}" alt="${esc(config.account_name)}公众号二维码">` : ''}<strong>请先关注公众号“${esc(config.account_name)}”</strong><p>${esc(config.follow_instructions)}</p><p>${esc(config.private_message_instructions)}</p></div>
    <form id="resubmitForm"><div class="field"><label>新密码</label><input class="input" id="resubmitPassword" type="password" minlength="8" required></div><div class="field"><label>手机号</label><input class="input" id="resubmitPhone" inputmode="tel" maxlength="24" required></div><label class="checkline follow-check"><input id="resubmitFollow" type="checkbox" required><span>我已关注“${esc(config.account_name)}”公众号</span></label><button class="btn primary login-submit" type="submit">重新提交</button></form><button class="text-button auth-back" id="backStatus">返回进度</button>`);
  document.querySelector('#backStatus').addEventListener('click', () => renderRegistrationStatus());
  document.querySelector('#resubmitForm').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      const data = await publicRequest('/api/auth/registration-resubmit', {
        registration_token: localStorage.getItem(TOKEN_KEY),
        password: document.querySelector('#resubmitPassword').value,
        phone: document.querySelector('#resubmitPhone').value.trim(),
        follow_confirmed: document.querySelector('#resubmitFollow').checked,
      });
      localStorage.setItem(TOKEN_KEY, data.registration_token); renderRegistrationStatus(data);
    } catch (error) { toast(error.message, 'error'); }
  });
}
