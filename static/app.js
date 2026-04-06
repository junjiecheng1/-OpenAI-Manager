/* OpenAI 帳號管理 Dashboard */

const API = '';

// ====================== Custom Confirm ======================

function showConfirm(msg, title = '確認') {
  return new Promise(resolve => {
    const modal = document.getElementById('confirm-modal');
    document.getElementById('confirm-title').textContent = title;
    document.getElementById('confirm-msg').textContent = msg;
    modal.classList.add('show');

    const okBtn = document.getElementById('confirm-ok');
    const cancelBtn = document.getElementById('confirm-cancel');

    function cleanup(result) {
      modal.classList.remove('show');
      okBtn.removeEventListener('click', onOk);
      cancelBtn.removeEventListener('click', onCancel);
      resolve(result);
    }
    function onOk() { cleanup(true); }
    function onCancel() { cleanup(false); }

    okBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', onCancel);
  });
}

// ====================== Init ======================

document.addEventListener('DOMContentLoaded', loadAll);

async function loadAll() {
  await Promise.all([loadStats(), loadAccounts(), loadCdkeys(), loadOutlookPool(), loadPatrolStatus()]);
}

// ====================== Stats ======================

async function loadStats() {
  try {
    const resp = await fetch(`${API}/api/stats`);
    const d = await resp.json();

    // 帳號: Plus數(高亮) / 總數
    document.getElementById('s-accounts').innerHTML =
      `${d.plus_accounts}<span class="stat-sub">/${d.total_accounts} plus</span>`;
    // 卡密: 可用(高亮) / 總數
    document.getElementById('s-cdkeys').innerHTML =
      `${d.available_cdkeys}<span class="stat-sub">/${d.total_cdkeys}</span>`;
    // Outlook: 可用(高亮) / 總數
    document.getElementById('s-outlook').innerHTML =
      `${d.outlook_available}<span class="stat-sub">/${d.total_outlook}</span>`;
    // gzyi: 可用(高亮) / 總數
    document.getElementById('s-gzyi').innerHTML =
      `${d.gzyi_available}<span class="stat-sub">/${d.gzyi_total}</span>`;

    setText('nav-accounts', d.total_accounts);
    setText('nav-outlook', d.total_outlook);
    setText('nav-gzyi', d.gzyi_available);
  } catch (e) {
    console.error('載入統計失敗', e);
  }
}

// ====================== Accounts ======================

let gzyiAccounts = [];

async function loadAccounts() {
  const tbody = document.getElementById('accounts-body');
  try {
    const [accResp, gzyiResp] = await Promise.all([
      fetch(`${API}/api/accounts`),
      fetch(`${API}/api/gzyi/accounts`).catch(() => ({ json: () => [] })),
    ]);
    const accounts = await accResp.json();
    gzyiAccounts = await gzyiResp.json();
    const gzyiSet = new Set(gzyiAccounts.map(a => (a.email || a.name || '').toLowerCase()));

    if (accounts.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty"><div class="msg">暫無帳號，點右上角「批量註冊」</div></td></tr>';
      return;
    }
    tbody.innerHTML = accounts.map(a => {
      const inGzyi = gzyiSet.has(a.email.toLowerCase());
      const hasSession = !!a.chatgpt_session_raw;
      return `<tr>
        <td class="mono">${esc(a.email)}</td>
        <td class="mono">${maskPwd(a.password)}</td>
        <td><span class="badge ${a.plan_type === 'plus' ? 'plus' : 'free'}">${a.plan_type}</span></td>
        <td><span class="badge ${hasSession ? 'session-ok' : 'session-empty'}">${hasSession ? 'OK' : '無'}</span></td>
        <td><span class="badge ${inGzyi ? 'gzyi-yes' : 'gzyi-no'}">${inGzyi ? '已同步' : '—'}</span></td>
        <td style="color:var(--text-tertiary);font-size:12px;">${a.created_at || '—'}</td>
        <td>
          <div class="actions-cell">
            <button class="btn btn-ghost btn-sm" id="btn-refresh-${cssId(a.email)}" onclick="refreshSession('${escAttr(a.email)}')">拉 Session</button>
            ${a.plan_type === 'free' && hasSession ? `<button class="btn btn-success btn-sm" id="btn-plus-${cssId(a.email)}" onclick="quickActivatePlus('${escAttr(a.email)}')">開通 Plus</button>` : ''}
            ${!inGzyi && a.plan_type === 'plus' ? `<button class="btn btn-ghost btn-sm" id="btn-gzyi-${cssId(a.email)}" onclick="importGzyi('${escAttr(a.email)}')">導入 gzyi</button>` : ''}
          </div>
        </td>
      </tr>`;
    }).join('');
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty"><div class="msg">載入失敗</div></td></tr>';
  }
}

function maskPwd(pwd) {
  if (!pwd) return '—';
  return pwd.length > 4 ? pwd.slice(0, 2) + '····' + pwd.slice(-2) : '····';
}

function cssId(email) {
  return email.replace(/[^a-zA-Z0-9]/g, '_').slice(0, 30);
}

// ====================== CDKeys ======================

async function loadCdkeys() {
  const tbody = document.getElementById('cdkeys-body');
  try {
    const resp = await fetch(`${API}/api/cdkeys`);
    const data = await resp.json();
    if (data.length === 0) {
      tbody.innerHTML = '<tr><td colspan="2" class="empty"><div class="msg">暫無卡密</div></td></tr>';
      return;
    }
    tbody.innerHTML = data.map(k => {
      const st = k.status || (k.used ? 'used' : 'available');
      const cls = st === 'used' ? 'used' : st === 'available' ? 'available' : 'used';
      const label = st === 'used' ? '已使用' : st === 'available' ? '可用' : '不可用';
      return `<tr>
        <td class="mono">${esc(k.cdkey)}</td>
        <td>${k.gift_name ? esc(k.gift_name) : '—'}</td>
        <td>${k.account ? `<span class="mono" style="font-size:11px;">${esc(k.account)}</span>` : '—'}</td>
        <td><span class="badge ${cls}">${label}</span></td>
      </tr>`;
    }).join('');
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="2" class="empty"><div class="msg">載入失敗</div></td></tr>';
  }
}

// ====================== CDKey Import ======================

function showImportModal() {
  document.getElementById('import-modal').classList.add('show');
  document.getElementById('import-keys').value = '';
  document.getElementById('import-keys').focus();
}
function closeImportModal() { document.getElementById('import-modal').classList.remove('show'); }

async function importCdkeys() {
  const text = document.getElementById('import-keys').value.trim();
  if (!text) { toast('請輸入卡密', 'error'); return; }
  try {
    const resp = await fetch(`${API}/api/cdkeys/import`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cdkeys: text }),
    });
    const data = await resp.json();
    toast(`導入 ${data.imported || 0} 張卡密`, 'success');
    closeImportModal();
    loadCdkeys();
    loadStats();
  } catch (e) { toast('導入失敗', 'error'); }
}


async function quickActivatePlus(email) {
  const btnId = `btn-plus-${cssId(email)}`;
  const btn = document.getElementById(btnId);
  if (btn) setLoading(btn, '開通中...');

  try {
    const resp = await fetch(`${API}/api/plus/activate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    const data = await resp.json();
    if (data.success) {
      toast(`${email} Plus 開通成功`, 'success');
      loadAll();  // 刷新整個頁面數據
    } else {
      toast(data.msg || '開通失敗', 'error');
      if (btn) resetLoading(btn, '開通 Plus');
    }
  } catch (e) {
    toast('請求失敗', 'error');
    if (btn) resetLoading(btn, '開通 Plus');
  }
}

// ====================== Session Refresh (SSE) ======================

async function refreshSession(email) {
  const btnId = `btn-refresh-${cssId(email)}`;
  const btn = document.getElementById(btnId);
  if (btn) setLoading(btn, '連接中...');

  // 在操作列下方插入進度條
  const row = btn?.closest('tr');
  let progressRow = document.getElementById(`progress-${cssId(email)}`);
  if (!progressRow && row) {
    progressRow = document.createElement('tr');
    progressRow.id = `progress-${cssId(email)}`;
    progressRow.innerHTML = `<td colspan="7" style="padding:0;border:none;">
      <div class="session-progress" id="sp-${cssId(email)}">
        <span class="spinner"></span>
        <span class="sp-text">準備中...</span>
      </div>
    </td>`;
    row.after(progressRow);
  }

  const spText = document.getElementById(`sp-${cssId(email)}`)?.querySelector('.sp-text');
  const spWrap = document.getElementById(`sp-${cssId(email)}`);

  try {
    const resp = await fetch(`${API}/api/accounts/refresh-session`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      toast(err.detail || '請求失敗', 'error');
      if (btn) resetLoading(btn, '拉 Session');
      if (progressRow) progressRow.remove();
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        try {
          const event = JSON.parse(line.slice(6));
          // 更新進度文字
          if (spText) spText.textContent = event.msg;
          if (btn) btn.textContent = '拉取中...';

          if (event.done) {
            if (event.success) {
              if (spWrap) {
                spWrap.classList.add('sp-success');
                spWrap.innerHTML = `<span class="sp-text">✓ ${event.msg}</span>`;
              }
              toast(`${email} session 刷新成功`, 'success');
              // 2秒後清除進度條並刷新
              setTimeout(() => {
                if (progressRow) progressRow.remove();
                loadAll();
              }, 2000);
            } else {
              if (spWrap) {
                spWrap.classList.add('sp-error');
                spWrap.innerHTML = `<span class="sp-text">✗ ${event.msg}</span>`;
              }
              toast(event.msg, 'error');
              setTimeout(() => { if (progressRow) progressRow.remove(); }, 5000);
            }
            if (btn) resetLoading(btn, '拉 Session');
            return;
          }
        } catch (e) { /* ignore parse errors */ }
      }
    }
  } catch (e) {
    toast('連接失敗', 'error');
    if (btn) resetLoading(btn, '拉 Session');
    if (progressRow) progressRow.remove();
  }
}

// ====================== gzyi Import / Reauth ======================

let _reauthPolling = null;

async function importGzyi(email) {
  if (!await showConfirm(`導入 ${email} 到 gzyi？\n將啟動瀏覽器走 OAuth 授權。`, '導入 gzyi')) return;
  await _startReauth(email, `btn-gzyi-${cssId(email)}`, '導入 gzyi');
}

async function reauthGzyi(email) {
  if (!await showConfirm(`確認重新授權 ${email}？\n將啟動瀏覽器走 OAuth 登入。`, '重新授權')) return;
  await _startReauth(email, null, '🔄 重新授權');
}

async function _startReauth(email, btnId, resetText) {
  const btn = btnId ? document.getElementById(btnId) : null;
  if (btn) setLoading(btn, '授權中...');

  // 展開日志面板
  const body = document.getElementById('activity-body');
  if (body && body.classList.contains('collapsed')) toggleLog();

  try {
    const resp = await fetch(`${API}/api/gzyi/reauth`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    const data = await resp.json();
    if (!data.success) {
      toast(data.detail || data.msg || '失敗', 'error');
      if (btn) resetLoading(btn, resetText);
      return;
    }
    toast('已啟動授權，查看下方日誌', 'success');

    // 高頻輪詢日誌，檢測完成
    _startReauthPolling(email, btn, resetText);
  } catch (e) {
    toast('請求失敗', 'error');
    if (btn) resetLoading(btn, resetText);
  }
}

function _startReauthPolling(email, btn, resetText) {
  if (_reauthPolling) clearInterval(_reauthPolling);
  const startTime = Date.now();
  _reauthPolling = setInterval(async () => {
    await pollLogs();
    // 檢查是否完成（最近日誌包含成功/失敗關鍵詞）
    const entries = document.querySelectorAll('#log-entries .log-entry');
    const lastEntries = Array.from(entries).slice(-5);
    const recentText = lastEntries.map(e => e.textContent).join('');
    const done = recentText.includes('授權成功') || recentText.includes('授權失敗')
      || recentText.includes('重新授權成功') || recentText.includes('重新授權失敗')
      || recentText.includes('授權異常');
    // 超時 3 分鐘也停
    if (done || (Date.now() - startTime > 180000)) {
      clearInterval(_reauthPolling);
      _reauthPolling = null;
      if (btn) resetLoading(btn, resetText);
      if (done && recentText.includes('成功')) {
        toast(`${email} 授權完成`, 'success');
        loadAll();
      }
    }
  }, 1500);
}

// ====================== Register ======================

function showRegisterModal() {
  document.getElementById('register-modal').classList.add('show');
  document.getElementById('reg-result').innerHTML = '';
}
function closeRegisterModal() { document.getElementById('register-modal').classList.remove('show'); }

async function startRegister() {
  const count = parseInt(document.getElementById('reg-count').value) || 1;
  const headed = document.getElementById('reg-headed').checked;
  const btn = document.getElementById('btn-register');
  const resultDiv = document.getElementById('reg-result');

  if (count < 1 || count > 10) { toast('數量 1~10', 'error'); return; }

  setLoading(btn, '註冊中...');
  resultDiv.innerHTML = '';
  try {
    const resp = await fetch(`${API}/api/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ count, headless: !headed }),
    });
    const data = await resp.json();
    if (data.success) {
      toast(data.msg, 'success');
      closeRegisterModal();
    } else {
      resultDiv.innerHTML = `<div class="form-result error">${esc(data.detail || data.msg || '失敗')}</div>`;
    }
  } catch (e) {
    resultDiv.innerHTML = '<div class="form-result error">請求失敗</div>';
  } finally {
    resetLoading(btn, '開始註冊');
  }
}

// ====================== Tabs ======================

function switchTab(name) {
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelector(`.nav-item[onclick*="${name}"]`).classList.add('active');
  document.getElementById(`panel-${name}`).classList.add('active');
  if (name === 'patrol') loadPatrolStatus();
  if (name === 'gzyi') loadGzyiDetails();
}

// ====================== Patrol ======================

async function loadPatrolStatus() {
  try {
    const resp = await fetch(`${API}/api/patrol/status`);
    const d = await resp.json();
    setText('patrol-plus', `${d.current_plus} / ${d.target_plus}`);
    setText('patrol-gzyi', `${d.gzyi_available} / ${d.gzyi_total}`);
    setText('patrol-interval', `${d.interval_min} min`);
    setText('patrol-lastrun', d.last_run || '未執行');
    setText('nav-patrol', d.enabled ? 'ON' : 'OFF');

    const toggleBtn = document.getElementById('btn-patrol-toggle');
    if (toggleBtn) {
      toggleBtn.textContent = d.enabled ? '停用' : '啟用';
      toggleBtn.className = `btn btn-sm ${d.enabled ? 'btn-ghost' : 'btn-primary'}`;
    }

    const resultDiv = document.getElementById('patrol-result');
    if (resultDiv && d.last_result) {
      resultDiv.textContent = d.last_result;
    }
    if (d.running) {
      setText('patrol-lastrun', '執行中...');
    }
  } catch (e) { /* ignore */ }
}

async function loadGzyiDetails() {
  try {
    const resp = await fetch(`${API}/api/patrol/gzyi-details`);
    const list = await resp.json();
    const tbody = document.getElementById('gzyi-details-body');
    if (!list.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="empty"><div class="msg">無帳號</div></td></tr>';
      return;
    }
    tbody.innerHTML = list.map(d => {
      const statusBadge = d.available
        ? '<span class="badge badge-green">可用</span>'
        : d.rate_limited
          ? '<span class="badge badge-red">限速中</span>'
          : '<span class="badge badge-yellow">額度耗盡</span>';
      return `<tr>
        <td style="font-size:12px;">${esc(d.name)}</td>
        <td>${statusBadge}</td>
        <td><span class="badge ${d.primary_pct >= 100 ? 'badge-red' : 'badge-ghost'}">${d.primary_pct}%</span></td>
        <td><span class="badge ${d.secondary_pct >= 100 ? 'badge-red' : 'badge-ghost'}">${d.secondary_pct}%</span></td>
        <td><button class="btn btn-sm btn-ghost" onclick="reauthGzyi('${esc(d.name)}')">🔄 重新授權</button></td>
      </tr>`;
    }).join('');
  } catch (e) { /* ignore */ }
}

async function togglePatrol() {
  try {
    const resp = await fetch(`${API}/api/patrol/toggle`, { method: 'POST' });
    const d = await resp.json();
    toast(d.enabled ? '巡檢已啟用' : '巡檢已停用', 'success');
    loadPatrolStatus();
  } catch (e) { toast('操作失敗', 'error'); }
}

async function runPatrolNow() {
  const btn = document.getElementById('btn-patrol-run');
  if (btn) setLoading(btn, '執行中...');
  try {
    const resp = await fetch(`${API}/api/patrol/run`, { method: 'POST' });
    const d = await resp.json();
    toast(d.msg, d.success ? 'success' : 'error');
    // 定時刷新狀態直到完成
    const poll = setInterval(async () => {
      await loadPatrolStatus();
      const s = await (await fetch(`${API}/api/patrol/status`)).json();
      if (!s.running) {
        clearInterval(poll);
        if (btn) resetLoading(btn, '立即執行');
        loadAll();
      }
    }, 3000);
  } catch (e) {
    toast('請求失敗', 'error');
    if (btn) resetLoading(btn, '立即執行');
  }
}

async function registerOneFull() {
  if (!await showConfirm('註冊一個新帳號？\n將執行全流程：註冊→Session→Plus→gzyi', '全流程註冊')) return;
  const btn = document.getElementById('btn-register-one');
  if (btn) setLoading(btn, '註冊中...');

  // 展開日志
  const body = document.getElementById('activity-body');
  if (body && body.classList.contains('collapsed')) toggleLog();

  try {
    const resp = await fetch(`${API}/api/patrol/register-one`, { method: 'POST' });
    const d = await resp.json();
    if (!d.success) {
      toast(d.msg || '失敗', 'error');
      if (btn) resetLoading(btn, '➕ 註冊一個');
      return;
    }
    toast('已啟動全流程註冊，查看下方日誌', 'success');

    // 輪詢直到完成
    const poll = setInterval(async () => {
      await pollLogs();
      const s = await (await fetch(`${API}/api/patrol/status`)).json();
      if (!s.running) {
        clearInterval(poll);
        if (btn) resetLoading(btn, '➕ 註冊一個');
        loadAll();
      }
    }, 2000);
  } catch (e) {
    toast('請求失敗', 'error');
    if (btn) resetLoading(btn, '➕ 註冊一個');
  }
}

// ====================== Outlook Pool ======================

async function loadOutlookPool() {
  const tbody = document.getElementById('outlook-body');
  try {
    const resp = await fetch(`${API}/api/outlook/pool`);
    const pool = await resp.json();
    if (pool.length === 0) {
      tbody.innerHTML = '<tr><td colspan="3" class="empty"><div class="msg">暫無郵箱，點「卡密提取」添加</div></td></tr>';
      return;
    }
    tbody.innerHTML = pool.map(a => `
      <tr>
        <td class="mono">${esc(a.email)}</td>
        <td class="mono">${esc(a.password || '')}</td>
        <td><span class="badge ${a.status === 'available' ? 'available' : 'used'}">${a.status === 'available' ? '可用' : '已用'}</span></td>
      </tr>
    `).join('');
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="3" class="empty"><div class="msg">載入失敗</div></td></tr>';
  }
}

function showExtractModal() {
  document.getElementById('extract-modal').classList.add('show');
  document.getElementById('extract-codes').value = '';
  document.getElementById('extract-codes').focus();
}
function closeExtractModal() { document.getElementById('extract-modal').classList.remove('show'); }

async function extractCards() {
  const text = document.getElementById('extract-codes').value.trim();
  if (!text) { toast('請輸入卡號', 'error'); return; }
  const codes = text.split('\n').map(s => s.trim()).filter(Boolean);
  try {
    const resp = await fetch(`${API}/api/outlook/extract`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ card_codes: codes }),
    });
    const data = await resp.json();
    if (data.success) {
      toast(data.msg, 'success');
      closeExtractModal();
      loadOutlookPool();
      loadStats();
    } else {
      toast(data.msg || '提取失敗', 'error');
    }
  } catch (e) { toast('請求失敗', 'error'); }
}

function showOutlookImportModal() {
  document.getElementById('outlook-import-modal').classList.add('show');
  document.getElementById('outlook-import-text').value = '';
  document.getElementById('outlook-import-text').focus();
}
function closeOutlookImportModal() { document.getElementById('outlook-import-modal').classList.remove('show'); }

async function importOutlookText() {
  const text = document.getElementById('outlook-import-text').value.trim();
  if (!text) { toast('請輸入帳號', 'error'); return; }
  try {
    const resp = await fetch(`${API}/api/outlook/import-text`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    const data = await resp.json();
    if (data.success) {
      toast(`匯入 ${data.imported} 個郵箱`, 'success');
      closeOutlookImportModal();
      loadOutlookPool();
      loadStats();
    } else { toast('匯入失敗', 'error'); }
  } catch (e) { toast('請求失敗', 'error'); }
}

// ====================== Loading Helpers ======================

function setLoading(btn, text) {
  if (!btn) return;
  btn.disabled = true;
  btn.dataset.origText = btn.textContent;
  btn.innerHTML = `<span class="spinner"></span>${text}`;
  btn.classList.add('loading');
}

function resetLoading(btn, text) {
  if (!btn) return;
  btn.disabled = false;
  btn.textContent = text || btn.dataset.origText || '';
  btn.classList.remove('loading');
}

// ====================== Toast ======================

function toast(msg, type = 'success') {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

// ====================== Helpers ======================

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}

function escAttr(s) {
  return (s || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
}

function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

// ====================== Activity Log ======================

let lastLogId = 0;
let logExpanded = true;

function toggleLog() {
  const body = document.getElementById('activity-body');
  logExpanded = !logExpanded;
  body.classList.toggle('collapsed', !logExpanded);
  document.getElementById('log-toggle').textContent = logExpanded ? '▲' : '▼';
}

async function pollLogs() {
  try {
    const resp = await fetch(`${API}/api/logs?since=${lastLogId}`);
    const logs = await resp.json();
    if (logs.length > 0) {
      const container = document.getElementById('log-entries');
      for (const log of logs) {
        const el = document.createElement('div');
        el.className = 'log-entry';
        el.innerHTML = `<span class="log-time">${log.time}</span><span class="log-msg ${log.level}">${esc(log.msg)}</span>`;
        container.appendChild(el);
        lastLogId = log.id;
      }
      container.scrollTop = container.scrollHeight;
      const badge = document.getElementById('log-badge');
      badge.style.display = 'inline';
      badge.textContent = lastLogId;
    }
  } catch (e) { /* ignore */ }
}

setInterval(pollLogs, 2000);
pollLogs();
