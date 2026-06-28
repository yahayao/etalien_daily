/* 外星仔加速器 — 前端逻辑 */

// ── Splash ────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  setTimeout(() => {
    const splash = document.getElementById('splash');
    splash.classList.add('fade-out');
    setTimeout(() => splash.remove(), 350);
  }, 600);
});

// ── HTML escaping ─────────────────────────────────────────
function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

// ── Titlebar Drag ─────────────────────────────────────────
(function initDrag() {
  const titlebar = document.getElementById('titlebar');
  let drag = false, startX, startY, winX, winY, rafId = null;
  const THRESHOLD = 3;

  titlebar.addEventListener('mousedown', async (e) => {
    if (e.target.closest('.titlebar-controls')) return;
    startX = e.screenX; startY = e.screenY;
    try {
      if (await pywebview.api.is_maximized()) {
        await pywebview.api.restore();
        await new Promise(r => requestAnimationFrame(r));
      }
      const pos = await pywebview.api.get_position();
      winX = pos.x; winY = pos.y;
      drag = true;
    } catch (_) { winX = 0; winY = 0; drag = true; }
  });

  window.addEventListener('mousemove', (e) => {
    if (!drag) return;
    const dx = e.screenX - startX, dy = e.screenY - startY;
    if (Math.abs(dx) < THRESHOLD && Math.abs(dy) < THRESHOLD) return;
    if (rafId) return;
    rafId = requestAnimationFrame(() => {
      rafId = null;
      try { pywebview.api.move_window(winX + dx, winY + dy); } catch (_) {}
    });
  });
  window.addEventListener('mouseup', () => { drag = false; });
  titlebar.addEventListener('dblclick', (e) => {
    if (e.target.closest('.titlebar-controls')) return;
    windowMaximize();
  });
})();

// ── Window Controls ────────────────────────────────────────
async function windowMinimize() {
  document.body.classList.add('win-close-out');
  await new Promise(r => setTimeout(r, 200));
  try { await pywebview.api.minimize(); } catch (_) {}
  document.body.classList.remove('win-close-out');
}
async function windowMaximize() {
  try { await pywebview.api.maximize(); } catch (_) {}
}
async function windowClose() {
  document.body.classList.add('win-close-out');
  await new Promise(r => setTimeout(r, 200));
  try { await pywebview.api.close(); } catch (_) {}
}

// ── Toast ─────────────────────────────────────────────────
function toast(msg, type) {
  const c = document.getElementById('toast-container');
  const el = document.createElement('div');
  el.className = 'toast ' + (type || '');
  el.textContent = msg;
  c.appendChild(el);
  setTimeout(() => el.remove(), 2800);
}

// ── API ───────────────────────────────────────────────────
async function api(path, options) {
  options = options || {};
  const headers = options.headers || {};
  if (options.body && typeof options.body === 'object') {
    headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(options.body);
  }
  const res = await fetch(path, { ...options, headers });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'HTTP ' + res.status);
  return data;
}

// ── Modals ────────────────────────────────────────────────
function showModal(id) { document.getElementById(id).classList.remove('hidden'); }
function closeModal(id) {
  const el = document.getElementById(id);
  el.classList.add('fade-out');
  setTimeout(() => { el.classList.add('hidden'); el.classList.remove('fade-out'); }, 150);
}
// Click overlay to close
document.addEventListener('click', (e) => {
  if (e.target.classList.contains('modal-overlay') && !e.target.classList.contains('hidden')) {
    closeModal(e.target.id);
  }
});

// ── Confirm dialog ────────────────────────────────────────
function showConfirm(msg) {
  return new Promise((resolve) => {
    document.getElementById('confirm-msg').textContent = msg;
    const ok = document.getElementById('btn-confirm-ok');
    const cancel = document.getElementById('btn-confirm-cancel');
    const cleanup = () => { ok.removeEventListener('click', onOk); cancel.removeEventListener('click', onCancel); closeModal('dlg-confirm'); };
    const onOk = () => { cleanup(); resolve(true); };
    const onCancel = () => { cleanup(); resolve(false); };
    ok.addEventListener('click', onOk);
    cancel.addEventListener('click', onCancel);
    showModal('dlg-confirm');
  });
}

// ── Button ripple ─────────────────────────────────────────
document.addEventListener('click', (e) => {
  const btn = e.target.closest('button');
  if (!btn) return;
  const ripple = document.createElement('span');
  ripple.className = 'ripple';
  const size = Math.max(btn.offsetWidth, btn.offsetHeight);
  ripple.style.width = ripple.style.height = size + 'px';
  ripple.style.left = (e.clientX - btn.getBoundingClientRect().left - size / 2) + 'px';
  ripple.style.top = (e.clientY - btn.getBoundingClientRect().top - size / 2) + 'px';
  btn.appendChild(ripple);
  ripple.addEventListener('animationend', () => ripple.remove());
});

// ── Accounts ──────────────────────────────────────────────
async function loadAccounts() {
  try {
    const accounts = await api('/api/accounts');
    const enabled = accounts.filter(a => a.enabled);
    animateValue('stat-total', accounts.length);
    animateValue('stat-enabled', enabled.length);
    renderAccountCards(accounts);
  } catch (e) { toast(e.message, 'error'); }
}

function renderAccountCards(accounts) {
  const strip = document.getElementById('account-strip');
  if (!accounts.length) {
    strip.innerHTML = '<div class="hint" style="padding:24px 0">暂无账号，点击"+ 添加账号"开始</div>';
    return;
  }
  strip.innerHTML = accounts.map(acc => `
    <div class="account-card" data-phone="${escapeHtml(acc.phone)}">
      <button class="acct-del-btn" onclick="removeAccount('${escapeHtml(acc.phone)}')" title="删除">×</button>
      <div class="acct-menu-wrap">
        <button class="acct-menu-btn" onclick="toggleMenu(event, '${escapeHtml(acc.phone)}')">⋯</button>
        <div class="acct-menu" id="menu-${escapeHtml(acc.phone)}">
          ${!acc.user_id ? `<button class="acct-menu-item" onclick="startLogin('${escapeHtml(acc.phone)}')">登录</button>` : ''}
          <button class="acct-menu-item" onclick="editAccount('${escapeHtml(acc.phone)}')">编辑</button>
          <button class="acct-menu-item" onclick="toggleAccount('${escapeHtml(acc.phone)}', ${!acc.enabled})">${acc.enabled ? '禁用' : '启用'}</button>
        </div>
      </div>
      <div class="acct-phone">${escapeHtml(maskPhone(acc.phone))}</div>
      <div class="acct-name">${escapeHtml(acc.name || '-')}</div>
      <span class="acct-status ${acc.user_id ? 'ok' : acc.enabled ? 'waiting' : 'error'}">
        ${!acc.enabled ? '已禁用' : acc.user_id ? '已登录' : '未登录'}
      </span>
    </div>
  `).join('');
}

// -- Action menu --
function toggleMenu(e, phone) {
  e.stopPropagation();
  closeAllMenus();
  const menu = document.getElementById('menu-' + phone);
  const card = menu.closest('.account-card');
  const rect = card.getBoundingClientRect();
  // open upward if card is in bottom half of viewport
  if (rect.bottom > window.innerHeight * 0.65) {
    menu.classList.add('up');
  } else {
    menu.classList.remove('up');
  }
  menu.classList.add('open');
}
function closeAllMenus() {
  document.querySelectorAll('.acct-menu.open').forEach(m => m.classList.remove('open'));
}
document.addEventListener('click', closeAllMenus);

// -- Account CRUD --
function showAddDialog() {
  document.getElementById('dlg-title').textContent = '添加账号';
  document.getElementById('dlg-old-phone').value = '';
  document.getElementById('dlg-phone').value = '';
  document.getElementById('dlg-name').value = '';
  document.getElementById('dlg-remark').value = '';
  showModal('dlg-account');
}
document.getElementById('btn-dlg-save').addEventListener('click', async () => {
  const old = document.getElementById('dlg-old-phone').value;
  const phone = document.getElementById('dlg-phone').value.trim();
  const name = document.getElementById('dlg-name').value.trim();
  const remark = document.getElementById('dlg-remark').value.trim();
  if (!phone) return toast('请输入手机号', 'error');
  try {
    if (old) {
      await api('/api/accounts/' + encodeURIComponent(old), { method: 'PUT', body: { phone, name, remark } });
      toast('已更新', 'success');
    } else {
      await api('/api/accounts', { method: 'POST', body: { phone, name, remark } });
      toast('已添加', 'success');
    }
    closeModal('dlg-account');
    loadAccounts();
  } catch (e) { toast(e.message, 'error'); }
});

async function editAccount(phone) {
  try {
    const acc = await api('/api/accounts/' + encodeURIComponent(phone));
    document.getElementById('dlg-title').textContent = '编辑账号';
    document.getElementById('dlg-old-phone').value = phone;
    document.getElementById('dlg-phone').value = acc.phone;
    document.getElementById('dlg-name').value = acc.name || '';
    document.getElementById('dlg-remark').value = acc.remark || '';
    showModal('dlg-account');
  } catch (e) { toast(e.message, 'error'); }
}

async function removeAccount(phone) {
  if (!(await showConfirm('确认删除账号 ' + phone + '？'))) return;
  try {
    // FLIP animation
    const card = document.querySelector('[data-phone="' + phone + '"]');
    if (card) { card.classList.add('card-remove'); await new Promise(r => setTimeout(r, 250)); }
    await api('/api/accounts/' + encodeURIComponent(phone), { method: 'DELETE' });
    toast('已删除', 'success');
    loadAccounts();
  } catch (e) { toast(e.message, 'error'); }
}

async function toggleAccount(phone, enabled) {
  try {
    await api('/api/accounts/' + encodeURIComponent(phone), { method: 'PUT', body: { enabled } });
    loadAccounts();
  } catch (e) { toast(e.message, 'error'); }
}

async function refreshStatus() {
  try {
    await api('/api/status');
    loadAccounts();
    toast('已刷新', 'success');
  } catch (e) { toast(e.message, 'error'); }
}

// ── Login ──────────────────────────────────────────────────
let _loginPhone = '';
function startLogin(phone) {
  _loginPhone = phone;
  document.getElementById('dlg-login-phone').textContent = phone;
  document.getElementById('dlg-login-send').classList.remove('hidden');
  document.getElementById('dlg-login-verify').classList.add('hidden');
  document.getElementById('dlg-login-msg').textContent = '';
  showModal('dlg-login');
}
async function sendLoginCode() {
  try {
    const data = await api('/api/login/' + _loginPhone, { method: 'POST' });
    document.getElementById('dlg-login-send').classList.add('hidden');
    document.getElementById('dlg-login-verify').classList.remove('hidden');
    document.getElementById('dlg-login-msg').textContent = data.msg || '验证码已发送';
    document.getElementById('dlg-login-code').focus();
  } catch (e) { document.getElementById('dlg-login-msg').textContent = '发送失败: ' + e.message; }
}
async function verifyLoginCode() {
  const code = document.getElementById('dlg-login-code').value.trim();
  if (!code) return;
  try {
    await api('/api/login/' + _loginPhone + '/verify', { method: 'POST', body: { code } });
    toast('登录成功', 'success');
    closeModal('dlg-login');
    document.getElementById('dlg-login-code').value = '';
    loadAccounts();
  } catch (e) { document.getElementById('dlg-login-msg').textContent = '登录失败: ' + e.message; }
}

// ── Claim ───────────────────────────────────────────────────
let _claimTimer = null;

function addLog(phone, msg, cls) {
  const entries = document.getElementById('log-entries');
  const t = new Date();
  const time = String(t.getHours()).padStart(2,'0') + ':' + String(t.getMinutes()).padStart(2,'0') + ':' + String(t.getSeconds()).padStart(2,'0');
  const el = document.createElement('div');
  el.className = 'log-entry';
  el.innerHTML = '<span class="log-time">' + time + '</span><span class="log-phone">' + escapeHtml(phone) + '</span><span class="' + (cls || '') + '">' + escapeHtml(msg) + '</span>';
  entries.appendChild(el);
  entries.scrollTop = entries.scrollHeight;
}

async function startClaim() {
  const btn = document.getElementById('btn-claim-start');
  btn.disabled = true; btn.textContent = '启动中...';
  document.getElementById('claim-results').classList.add('hidden');
  document.getElementById('log-entries').innerHTML = '';
  showModal('dlg-log');

  try {
    const data = await api('/api/claim', { method: 'POST' });
    btn.textContent = '领取中...';
    _claimTimer = setInterval(pollClaimProgress, 1000);
  } catch (e) {
    btn.disabled = false; btn.textContent = '开始领取';
    toast(e.message, 'error');
  }
}

async function pollClaimProgress() {
  try {
    const data = await api('/api/claim/progress');
    const entries = data.progress || [];
    renderClaimProgress(entries);

    // Add log entries for state changes
    for (const e of entries) {
      if (e._logged !== e.status) {
        e._logged = e.status;
        if (e.status === 'done' || e.status === 'already_done') {
          const gained = (e.vip_after || 0) - (e.vip_before || 0);
          addLog(e.phone, '完成 +' + fmtDuration(Math.max(0, gained)), 'log-ok');
        } else if (e.status === 'error') {
          addLog(e.phone, '错误: ' + (e.error || ''), 'log-warn');
        } else if (e.status === 'need_login') {
          addLog(e.phone, '需要登录', 'log-warn');
        }
      }
    }

    if (!data.running) {
      clearInterval(_claimTimer); _claimTimer = null;
      document.getElementById('btn-claim-start').disabled = false;
      document.getElementById('btn-claim-start').textContent = '开始领取';
      if (entries.length) renderClaimResults(entries);
    }
  } catch (_) {}
}

function renderClaimProgress(entries) {
  const c = document.getElementById('claim-progress');
  if (!entries.length) { c.innerHTML = ''; return; }
  c.innerHTML = entries.map(e => {
    const pct = e.status === 'done' || e.status === 'already_done' ? 100 : e.status === 'error' || e.status === 'need_login' ? 0 : 60;
    const cls = e.status === 'error' ? 'error' : e.status === 'done' || e.status === 'already_done' ? 'done' : '';
    return '<div class="card progress-card"><div class="progress-header"><span>' + escapeHtml(e.phone) + '</span><span class="hint">' + statusLabel(e.status) + '</span></div><div class="progress-bar-wrap"><div class="progress-bar ' + cls + '" style="width:' + pct + '%"></div></div><div class="progress-detail">' + escapeHtml(e.detail || e.error || '') + '</div></div>';
  }).join('');
}

function renderClaimResults(entries) {
  const c = document.getElementById('claim-results');
  c.classList.remove('hidden');
  c.innerHTML = '<div class="card result-header"><span>手机号</span><span>状态</span><span>领取前</span><span>领取后</span><span>成功</span><span>失败</span></div>' + entries.map(e => '<div class="card result-row"><span>' + escapeHtml(e.phone) + '</span><span>' + statusLabel(e.status) + '</span><span>' + fmtDuration(e.vip_before || 0) + '</span><span>' + fmtDuration(e.vip_after || 0) + '</span><span>' + (e.current || 0) + '</span><span>' + ((e.total || 0) - (e.current || 0)) + '</span></div>').join('');
}

// ── Settings ────────────────────────────────────────────────
async function showSettings() {
  try {
    const s = await api('/api/settings');
    document.getElementById('settings-form-inner').innerHTML =
      '<div class="form-group"><label>最大并发数 (1-50)</label><input id="set-concurrent" type="number" min="1" max="50" value="' + s.max_concurrent + '"><div class="form-hint">同时领取的账号数量</div></div>' +
      '<div class="form-group"><label>请求间隔 秒 (0.1-30)</label><input id="set-interval" type="number" min="0.1" max="30" step="0.1" value="' + s.request_interval + '"></div>' +
      '<div class="form-group"><label>最大轮数 (1-200)</label><input id="set-rounds" type="number" min="1" max="200" value="' + s.max_rounds + '"></div>' +
      '<div class="form-group"><label>定时时间</label><input id="set-schedule" type="text" value="' + escapeHtml(s.schedule_time) + '" placeholder="HH:MM"></div>' +
      '<button class="btn-primary" onclick="saveSettings()">保存设置</button>';
    showModal('dlg-settings');
  } catch (e) { toast(e.message, 'error'); }
}
async function saveSettings() {
  const data = {
    max_concurrent: parseInt(document.getElementById('set-concurrent').value) || 10,
    request_interval: parseFloat(document.getElementById('set-interval').value) || 1.0,
    max_rounds: parseInt(document.getElementById('set-rounds').value) || 21,
    schedule_time: document.getElementById('set-schedule').value || '08:00',
  };
  if (data.max_concurrent / data.request_interval > 50) {
    if (!(await showConfirm('请求频率较高，可能触发风控。确认继续？'))) return;
  }
  try {
    await api('/api/settings', { method: 'PUT', body: data });
    toast('设置已保存', 'success');
    closeModal('dlg-settings');
  } catch (e) { toast(e.message, 'error'); }
}

// ── History ─────────────────────────────────────────────────
async function showHistory() {
  try {
    const history = await api('/api/history?limit=50');
    const c = document.getElementById('history-list-inner');
    if (!history.length) { c.innerHTML = '<div class="hint" style="padding:20px 0">暂无领取记录</div>'; }
    else {
      c.innerHTML = history.map(h => {
        const g = (h.vip_after || 0) - (h.vip_before || 0);
        return '<div class="history-item"><span class="history-time">' + fmtTime(h.claimed_at) + '</span><span>' + escapeHtml(h.phone || '-') + '</span><span class="history-status" style="color:' + (h.status === 'ok' ? 'var(--sage)' : 'var(--ember)') + '">' + statusLabel(h.status) + '</span><span class="spacer"></span><span style="color:var(--amber)">+' + fmtDuration(Math.max(0, g)) + '</span></div>';
      }).join('');
    }
    showModal('dlg-history');
  } catch (e) { toast(e.message, 'error'); }
}

// ── Helpers ────────────────────────────────────────────────
function maskPhone(p) { return p && p.length > 6 ? p.substring(0,3) + '****' + p.slice(-4) : p; }
function statusLabel(s) {
  const m = { ok:'成功',done:'完成',already_done:'已完成',running:'进行中',error:'错误',auth_error:'认证失败',need_login:'需登录',partial:'部分完成' };
  return m[s] || s;
}
function fmtDuration(s) {
  if (!s || s <= 0) return '-';
  const h = Math.floor(s/3600), m = Math.floor((s%3600)/60);
  return h > 0 ? h + 'h' + m + 'm' : m > 0 ? m + 'm' : s + 's';
}
function fmtTime(ts) {
  if (!ts) return '-';
  const d = new Date(ts * 1000);
  return (d.getMonth()+1) + '-' + d.getDate() + ' ' + String(d.getHours()).padStart(2,'0') + ':' + String(d.getMinutes()).padStart(2,'0');
}
function animateValue(id, target) {
  const el = document.getElementById(id);
  if (!el) return;
  const start = parseInt(el.textContent) || 0;
  if (start === target) return;
  const dur = 300, t0 = performance.now();
  function step(now) {
    const p = Math.min((now - t0) / dur, 1);
    const v = Math.round(start + (target - start) * easeOutCubic(p));
    el.textContent = v;
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}
function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }

// ── Init ───────────────────────────────────────────────────
loadAccounts();
