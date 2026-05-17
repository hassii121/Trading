/* ── Socket.IO connection ──────────────────────────────────────────── */
const socket = io();

let currentPair = null;
let currentTF   = '30m';
let latestData  = {};   // keyed by pair symbol

/* ── Clock ─────────────────────────────────────────────────────────── */
function updateClock() {
  const now  = new Date();
  const hh   = String(now.getUTCHours()).padStart(2, '0');
  const mm   = String(now.getUTCMinutes()).padStart(2, '0');
  const ss   = String(now.getUTCSeconds()).padStart(2, '0');
  document.getElementById('clock').textContent = `${hh}:${mm}:${ss} UTC`;
}
setInterval(updateClock, 1000);
updateClock();

/* ── Pair selection ─────────────────────────────────────────────────── */
function selectPair(pair) {
  currentPair = pair;

  document.querySelectorAll('.pair-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.pair === pair);
  });

  if (latestData[pair]) renderAll(latestData[pair]);
  else clearSignalCard();
}

/* ── Timeframe selection ────────────────────────────────────────────── */
function selectTF(tf) {
  currentTF = tf;
  document.querySelectorAll('.tf-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tf === tf);
  });
  // Tell server → triggers immediate re-analysis
  socket.emit('set_timeframe', { tf });
}

// Auto-select first tab on load
document.addEventListener('DOMContentLoaded', () => {
  const first = document.querySelector('.pair-tab');
  if (first) selectPair(first.dataset.pair);
});

/* ── SocketIO events ────────────────────────────────────────────────── */
socket.on('connect', () => {
  document.getElementById('status-badge').textContent = '● LIVE';
  document.getElementById('status-badge').style.color  = 'var(--live)';
});

socket.on('disconnect', () => {
  document.getElementById('status-badge').textContent = '● OFFLINE';
  document.getElementById('status-badge').style.color  = 'var(--red)';
});

socket.on('pairs_update', (data) => {
  const pairs  = data.pairs || [];
  const nav    = document.querySelector('.pair-nav');
  if (!nav || !pairs.length) return;

  nav.innerHTML = '';
  pairs.forEach((pair, i) => {
    const btn = document.createElement('button');
    btn.className  = 'pair-tab' + (i === 0 && !currentPair ? ' active' : (pair === currentPair ? ' active' : ''));
    btn.dataset.pair = pair;
    btn.textContent  = pair.replace('USDT', '/USDT');
    btn.onclick      = () => selectPair(pair);
    nav.appendChild(btn);
  });

  // If current pair no longer in list, select first
  if (currentPair && !pairs.includes(currentPair)) {
    selectPair(pairs[0]);
  }
});

socket.on('signal', (data) => {
  // data = { pair, signal, engines }
  latestData[data.pair] = data;

  if (data.pair === currentPair) renderAll(data);
  appendHistory(data);
});

/* ── Render signal card ─────────────────────────────────────────────── */
function renderAll(data) {
  const sig = data.signal || {};
  const eng = data.engines || {};

  // Signal card
  setText('sig-pair',    data.pair ? data.pair.replace('USDT', '/USDT') : '—');
  setText('sig-tf',      data.timeframe ? data.timeframe.toUpperCase() : '—');
  setDecision('sig-bias', sig.bias);
  setConfidenceBadge('sig-conf', sig.confidence);
  setText('sig-entry',   fmt(sig.entry_low)  + (sig.entry_high ? ' – ' + fmt(sig.entry_high) : ''));
  setText('sig-sl',      fmt(sig.stop_loss));
  setText('sig-tp1',     fmt(sig.tp1));
  setText('sig-tp2',     fmt(sig.tp2));
  setText('sig-tp3',     fmt(sig.tp3));
  setConfidencePct('sig-conf-pct', sig.confidence);
  setText('sig-reason',  sig.reason || '—');

  // Engine 1 — Market Data
  const e1 = eng.engine1 || {};
  setText('e1-price',      fmt(e1.price));
  setTrend('e1-trend',     e1.trend);
  setText('e1-volatility', e1.volatility || '—');
  setText('e1-session',    e1.session    || '—');
  setPhase('e1-phase',     e1.classification);
  setSentiment('e1-sentiment', e1.sentiment_label, e1.sentiment_score);
  setFunding('e1-funding', e1.funding_bias, e1.funding_rate);
  setOI('e1-oi', e1.oi_trend);

  // Engine 2 — Liquidity
  const e2 = eng.engine2 || {};
  setText('e2-bsl',      e2.nearest_bsl != null ? fmt(e2.nearest_bsl) : '—');
  setText('e2-ssl',      e2.nearest_ssl != null ? fmt(e2.nearest_ssl) : '—');
  setSweep('e2-sweep',   e2.sweep, e2.sweep_dir, e2.swept_level);
  setReaction('e2-reaction', e2.reaction);
  setBias('e2-bias',     e2.bias);
  setSignal('e2-sig',    e2.signal);

  // Engine 3 — Structure
  const e3 = eng.engine3 || {};
  setTrend('e3-trend',    e3.trend);
  setText('e3-labels',    formatStructureLabels(e3.labeled_highs, e3.labeled_lows));
  setBosRow('e3-bos',     e3.bos_confirmed, e3.bos_direction, e3.bos_level);
  setChochRow('e3-choch', e3.choch_detected, e3.choch_direction, e3.choch_level);
  setStrength('e3-strength', e3.strength);
  setManip('e3-manip',    e3.manipulation);
  setText('e3-bias',      e3.bias || '—');
  setSignal('e3-sig',     e3.signal);

  // Engine 4 — Strategy Brain
  const e4 = eng.engine4 || {};
  setDecision('e4-dec',  e4.decision);
  setText('e4-setup',    e4.setup || '—');
  setText('e4-entry',    (e4.entry_low != null && e4.entry_high != null)
                          ? `${fmt(e4.entry_low)} – ${fmt(e4.entry_high)}` : '—');
  setText('e4-sl',       e4.stop_loss  != null ? fmt(e4.stop_loss)  : '—');
  setText('e4-tp',       (e4.tp1 != null ? fmt(e4.tp1) : '—') +
                         (e4.tp2 != null ? ' / ' + fmt(e4.tp2) : ''));
  setText('e4-rr',       e4.risk_reward != null ? e4.risk_reward + ':1' : '—');
  setText('e4-prob',     e4.prob_score  != null ? e4.prob_score + ' pts' : '—');

  // Engine 5 — Confidence
  const e5 = eng.engine5 || {};
  setText('e5-session', e5.score_session  != null ? e5.score_session  + ' pts' : '—');
  setText('e5-liq',     e5.score_liquidity != null ? e5.score_liquidity + ' pts' : '—');
  setText('e5-str',     e5.score_structure != null ? e5.score_structure + ' pts' : '—');
  setText('e5-fund',    e5.score_funding   != null ? e5.score_funding   + ' pts' : '—');
  setText('e5-vol',     e5.score_volatility!= null ? e5.score_volatility+ ' pts' : '—');
  setRiskPct('e5-risk', e5.risk_pct);
  setSlValid('e5-sl',   e5.sl_valid, e5.sl_note);
  setConfidenceTotal('e5-total', e5.confidence, e5.label);
}

/* ── Signal history ─────────────────────────────────────────────────── */
const MAX_HISTORY = 50;

function appendHistory(data) {
  const sig  = data.signal || {};
  if (!sig.bias || sig.bias === 'NO_TRADE') return;

  const tbody = document.getElementById('history-body');

  // Remove placeholder row
  const ph = tbody.querySelector('.no-data');
  if (ph) ph.parentElement.remove();

  const now   = new Date(Date.now() + 5 * 60 * 60 * 1000);
  const date  = now.toISOString().substring(0, 10);
  const time  = date + ' ' + now.toISOString().substring(11, 19) + ' UTC+5';
  const bias  = sig.bias || '—';
  const bCls  = bias === 'LONG' ? 'green' : bias === 'SHORT' ? 'red' : '';

  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td>${time}</td>
    <td>${(data.pair || '').replace('USDT', '/USDT')}</td>
    <td style="color:var(--gold)">${(data.timeframe || '—').toUpperCase()}</td>
    <td class="${bCls}">${bias}</td>
    <td>${fmt(sig.entry_low)}</td>
    <td class="red">${fmt(sig.stop_loss)}</td>
    <td class="green">${fmt(sig.tp1)}</td>
    <td class="gold">${sig.confidence != null ? sig.confidence + '%' : '—'}</td>
    <td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
        title="${esc(sig.reason || '')}">${esc(sig.reason || '—')}</td>
  `;

  tbody.insertBefore(tr, tbody.firstChild);

  // Keep table bounded
  while (tbody.rows.length > MAX_HISTORY) {
    tbody.deleteRow(tbody.rows.length - 1);
  }
}

/* ── Clear signal card ──────────────────────────────────────────────── */
function clearSignalCard() {
  ['sig-pair','sig-conf','sig-entry','sig-sl','sig-tp1','sig-tp2','sig-tp3','sig-conf-pct'].forEach(id => setText(id, '—'));
  setText('sig-reason', 'Waiting for analysis…');
  const biasEl = document.getElementById('sig-bias');
  if (biasEl) { biasEl.textContent = 'NO TRADE'; biasEl.className = 'bias-badge neutral'; }
}

/* ── Helpers ────────────────────────────────────────────────────────── */
function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val != null && val !== '' ? val : '—';
}

function fmt(val) {
  if (val == null) return '—';
  const n = parseFloat(val);
  if (isNaN(n)) return String(val);
  return n >= 1000 ? n.toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2})
                   : n >= 1    ? n.toFixed(4)
                   : n.toFixed(6);
}

function fmtVol(val) {
  if (val == null) return '—';
  const n = parseFloat(val);
  if (isNaN(n)) return '—';
  if (n >= 1e9) return (n/1e9).toFixed(2) + 'B';
  if (n >= 1e6) return (n/1e6).toFixed(2) + 'M';
  if (n >= 1e3) return (n/1e3).toFixed(2) + 'K';
  return n.toFixed(2);
}

function setColored(id, text, numericVal) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text || '—';
  el.className = '';
  if (numericVal > 0) el.classList.add('green');
  else if (numericVal < 0) el.classList.add('red');
}

function setBias(id, bias) {
  const el = document.getElementById(id);
  if (!el) return;
  const b = (bias || 'NO_TRADE').toUpperCase();
  el.textContent = b;
  el.className   = 'bias-badge ' + (b === 'LONG' ? 'long' : b === 'SHORT' ? 'short' : 'neutral');
}

function setTrend(id, trend) {
  const el = document.getElementById(id);
  if (!el) return;
  const t = (trend || '').toUpperCase();
  el.textContent = t || '—';
  el.style.color = t === 'BULLISH' ? 'var(--green)' : t === 'BEARISH' ? 'var(--red)' : 'var(--text2)';
}

function setSignal(id, sig) {
  const el = document.getElementById(id);
  if (!el) return;
  const s = (sig || '').toUpperCase();
  el.textContent = s || '—';
  el.style.color = s === 'BUY' || s === 'LONG'  ? 'var(--green)'
                 : s === 'SELL'|| s === 'SHORT' ? 'var(--red)'
                 : 'var(--text2)';
}

function scoreBar(val) {
  if (val == null) return '—';
  return val + '/25';
}

function setRiskPct(id, pct) {
  const el = document.getElementById(id);
  if (!el) return;
  if (pct == null || pct === 0) { el.textContent = '—'; el.style.color = 'var(--text2)'; return; }
  el.textContent = pct + '%';
  el.style.color = pct >= 1 ? 'var(--green)' : pct >= 0.5 ? 'var(--gold)' : 'var(--text2)';
}

function setSlValid(id, valid, note) {
  const el = document.getElementById(id);
  if (!el) return;
  if (valid == null) { el.textContent = '—'; el.style.color = 'var(--text2)'; return; }
  el.textContent = valid ? '✓ Valid' : '⚠ ' + (note || 'Check SL');
  el.style.color = valid ? 'var(--green)' : 'var(--gold)';
}

function setConfidenceTotal(id, score, label) {
  const el = document.getElementById(id);
  if (!el) return;
  if (score == null) { el.textContent = '—'; el.style.color = 'var(--text2)'; return; }
  el.textContent = `${score}/100 — ${label || ''}`;
  el.style.color = score >= 80 ? 'var(--green)'
                 : score >= 60 ? 'var(--gold)'
                 : score >= 40 ? 'var(--text2)'
                 : 'var(--red)';
}

function setConfidenceBadge(id, score) {
  const el = document.getElementById(id);
  if (!el) return;
  if (score == null) { el.textContent = '—'; el.style.color = 'var(--text2)'; return; }
  el.textContent = score + '%';
  el.style.color = score >= 80 ? 'var(--green)'
                 : score >= 60 ? 'var(--gold)'
                 : 'var(--red)';
}

function setConfidencePct(id, score) {
  const el = document.getElementById(id);
  if (!el) return;
  if (score == null) { el.textContent = '—'; el.style.color = 'var(--gold)'; return; }
  el.textContent = score + '%';
  el.style.color = score >= 80 ? 'var(--green)'
                 : score >= 60 ? 'var(--gold)'
                 : 'var(--red)';
}

function setPhase(id, phase) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = phase || '—';
  const p = (phase || '').toLowerCase();
  el.style.color = p.includes('expansion')    ? 'var(--green)'
                 : p.includes('distribution') ? 'var(--red)'
                 : p.includes('manipulation') ? 'var(--gold)'
                 : 'var(--text2)';
}

function setSentiment(id, label, score) {
  const el = document.getElementById(id);
  if (!el) return;
  const l = (label || '').toLowerCase();
  const display = score != null ? `${label} (${score})` : label || '—';
  el.textContent = display;
  el.style.color = l === 'fear'  ? 'var(--red)'
                 : l === 'greed' ? 'var(--green)'
                 : 'var(--text2)';
}

function setFunding(id, bias, rate) {
  const el = document.getElementById(id);
  if (!el) return;
  const b = (bias || 'N/A');
  const display = rate != null ? `${b} (${rate}%)` : b;
  el.textContent = display;
  el.style.color = b === 'Long-heavy'  ? 'var(--green)'
                 : b === 'Short-heavy' ? 'var(--red)'
                 : 'var(--text2)';
}

function setSweep(id, detected, direction, level) {
  const el = document.getElementById(id);
  if (!el) return;
  if (!detected) { el.textContent = 'None'; el.style.color = 'var(--text2)'; return; }
  const dir = direction === 'up' ? '↑ BSL' : '↓ SSL';
  el.textContent = level != null ? `${dir} @ ${fmt(level)}` : dir;
  el.style.color = direction === 'up' ? 'var(--red)' : 'var(--green)';
}

function setReaction(id, reaction) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = reaction || '—';
  el.style.color = reaction === 'Reversal'     ? 'var(--gold)'
                 : reaction === 'Continuation' ? 'var(--accent)'
                 : 'var(--text2)';
}

function setOI(id, trend) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = trend || '—';
  el.style.color = trend === 'Rising'  ? 'var(--green)'
                 : trend === 'Falling' ? 'var(--red)'
                 : 'var(--text2)';
}

function formatStructureLabels(highs, lows) {
  if (!highs || !lows || (!highs.length && !lows.length)) return '—';
  const h = (highs || []).slice(-2).join('/') || '?';
  const l = (lows  || []).slice(-2).join('/') || '?';
  return `H:${h}  L:${l}`;
}

function setBosRow(id, confirmed, direction, level) {
  const el = document.getElementById(id);
  if (!el) return;
  if (!confirmed) { el.textContent = 'Not Confirmed'; el.style.color = 'var(--text2)'; return; }
  el.textContent = `${direction} @ ${fmt(level)}`;
  el.style.color = direction === 'Bullish' ? 'var(--green)' : 'var(--red)';
}

function setChochRow(id, detected, direction, level) {
  const el = document.getElementById(id);
  if (!el) return;
  if (!detected) { el.textContent = 'None'; el.style.color = 'var(--text2)'; return; }
  el.textContent = `${direction} @ ${fmt(level)}`;
  el.style.color = direction === 'Bullish' ? 'var(--green)' : 'var(--red)';
}

function setStrength(id, strength) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = strength || '—';
  el.style.color = strength === 'Strong' ? 'var(--green)'
                 : strength === 'Weak'   ? 'var(--gold)'
                 : 'var(--red)';
}

function setDecision(id, decision) {
  const el = document.getElementById(id);
  if (!el) return;
  const d = (decision || 'NO_TRADE').toUpperCase();
  el.textContent = d === 'NO_TRADE' ? 'NO TRADE' : d;
  el.className   = 'bias-badge ' + (d === 'BUY' ? 'long' : d === 'SELL' ? 'short' : 'neutral');
}

function setManip(id, detected) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = detected ? 'YES — Sweep+CHoCH' : 'No';
  el.style.color = detected ? 'var(--gold)' : 'var(--text2)';
}

function esc(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

/* ── Page navigation ────────────────────────────────────────────────── */
function switchPage(page) {
  document.querySelectorAll('.page-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.page === page);
  });
  document.querySelectorAll('.page').forEach(el => {
    el.classList.toggle('hidden', el.id !== 'page-' + page);
  });

  if (page === 'account')  loadAccount();
  if (page === 'settings') loadTradingSettings();
}

/* ── Account page ───────────────────────────────────────────────────── */
function loadAccount() {
  fetch('/api/trading/account')
    .then(r => r.json())
    .then(d => {
      if (d.ok) {
        const pnl = d.unrealized_pnl || 0;
        const el  = document.getElementById('acc-upnl');
        document.getElementById('acc-balance').textContent = fmt(d.balance);
        document.getElementById('acc-equity').textContent  = fmt(d.equity);
        document.getElementById('acc-avail').textContent   = fmt(d.available);
        if (el) {
          el.textContent = (pnl >= 0 ? '+' : '') + fmt(pnl);
          el.style.color = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--text)';
        }
      }
    })
    .catch(() => {});

  fetch('/api/trading/positions')
    .then(r => r.json())
    .then(rows => renderOpenTrades(Array.isArray(rows) ? rows : []))
    .catch(() => {});

  fetch('/api/trading/history')
    .then(r => r.json())
    .then(rows => renderClosedTrades(rows))
    .catch(() => {});
}

function renderOpenTrades(rows) {
  const tbody = document.getElementById('open-trades-body');
  if (!tbody) return;
  if (!rows || !rows.length) {
    tbody.innerHTML = '<tr><td colspan="11" class="no-data">No open trades</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map(t => {
    const dirCls = t.direction === 'BUY' ? 'green' : 'red';
    const pnl    = t.unrealized_pnl != null ? t.unrealized_pnl : 0;
    const pnlCls = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--text)';
    const pnlStr = `<span id="upnl-${t.pair}" style="color:${pnlCls}">${pnl>=0?'+':''}${fmt(pnl)}</span>`;
    return `<tr>
      <td>${(t.pair||'').replace('USDT','/USDT')}</td>
      <td class="${dirCls}">${t.direction||'—'}</td>
      <td style="color:var(--gold)">${t.leverage!=null?t.leverage+'x':'—'}</td>
      <td>${fmt(t.entry_price)}</td>
      <td>${fmt(t.mark_price)}</td>
      <td>—</td>
      <td>${t.qty!=null?t.qty:'—'}</td>
      <td>${fmt(t.notional)}</td>
      <td>—</td>
      <td>${pnlStr}</td>
      <td>—</td>
    </tr>`;
  }).join('');
}

function renderClosedTrades(rows) {
  const tbody = document.getElementById('closed-trades-body');
  if (!tbody) return;
  if (!rows || !rows.length) {
    tbody.innerHTML = '<tr><td colspan="11" class="no-data">No closed trades</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map(t => {
    const dirCls = t.direction === 'BUY' ? 'green' : 'red';
    const pnl    = t.pnl || 0;
    const pnlCls = pnl > 0 ? 'green' : pnl < 0 ? 'red' : '';
    return `<tr>
      <td>${(t.pair||'').replace('USDT','/USDT')}</td>
      <td class="${dirCls}">${t.direction||'—'}</td>
      <td style="color:var(--gold)">${(t.timeframe||'—').toUpperCase()}</td>
      <td>${fmt(t.entry_price)}</td>
      <td>${fmt(t.close_price)}</td>
      <td class="red">${fmt(t.sl)}</td>
      <td class="green">${fmt(t.tp1)}</td>
      <td>${t.qty!=null?t.qty:'—'}</td>
      <td class="${pnlCls}">${pnl>=0?'+':''}${fmt(pnl)}</td>
      <td style="color:${t.close_reason==='TP'?'var(--green)':'var(--red)'}">${t.close_reason||'—'}</td>
      <td>${t.closed_at||'—'}</td>
    </tr>`;
  }).join('');
}

/* ── Trading settings page ──────────────────────────────────────────── */
function loadTradingSettings() {
  fetch('/api/trading/settings')
    .then(r => r.json())
    .then(d => {
      document.getElementById('set-apikey').value        = d.api_key        || '';
      document.getElementById('set-apisecret').value     = d.api_secret     || '';
      document.getElementById('set-tn-apikey').value     = d.tn_api_key     || '';
      document.getElementById('set-tn-apisecret').value  = d.tn_api_secret  || '';
      document.getElementById('set-confidence').value  = d.min_confidence != null ? d.min_confidence : 75;
      document.getElementById('set-maxtrades').value   = d.max_trades   != null ? d.max_trades   : 6;
      document.getElementById('set-leverage').value    = d.leverage     != null ? d.leverage     : 10;
      document.getElementById('set-riskpct').value     = d.risk_pct     != null ? d.risk_pct     : 0.5;
      document.getElementById('set-trade-tp').value    = d.trade_tp_usd  != null ? d.trade_tp_usd  : 0;
      document.getElementById('set-basket-tp').value   = d.basket_tp_usd != null ? d.basket_tp_usd : 0;
      const cb = document.getElementById('set-enabled');
      if (cb) { cb.checked = !!d.enabled; _updateToggleText(cb.checked); }
      const tn = document.getElementById('set-testnet');
      if (tn) { tn.checked = !!d.testnet; _updateTestnetText(tn.checked); }
    })
    .catch(() => {});
}

function saveTradingSettings() {
  const cb = document.getElementById('set-enabled');
  const tn = document.getElementById('set-testnet');
  const payload = {
    api_key:        document.getElementById('set-apikey').value.trim(),
    api_secret:     document.getElementById('set-apisecret').value.trim(),
    tn_api_key:     document.getElementById('set-tn-apikey').value.trim(),
    tn_api_secret:  document.getElementById('set-tn-apisecret').value.trim(),
    enabled:        cb ? cb.checked : false,
    testnet:        tn ? tn.checked : false,
    min_confidence: parseInt(document.getElementById('set-confidence').value)  || 75,
    max_trades:     parseInt(document.getElementById('set-maxtrades').value)   || 6,
    leverage:       parseInt(document.getElementById('set-leverage').value)    || 10,
    risk_pct:       parseFloat(document.getElementById('set-riskpct').value)   || 0.5,
    trade_tp_usd:   parseFloat(document.getElementById('set-trade-tp').value)  || 0,
    basket_tp_usd:  parseFloat(document.getElementById('set-basket-tp').value) || 0,
  };

  const statusEl = document.getElementById('settings-status');
  if (statusEl) statusEl.textContent = 'Saving…';

  fetch('/api/trading/settings', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(payload),
  })
    .then(r => r.json())
    .then(d => {
      if (statusEl) {
        statusEl.textContent = d.ok ? '✓ Saved' : '✗ Error';
        statusEl.style.color = d.ok ? 'var(--green)' : 'var(--red)';
        setTimeout(() => { if (statusEl) statusEl.textContent = ''; }, 3000);
      }
    })
    .catch(() => {
      if (statusEl) { statusEl.textContent = '✗ Network error'; statusEl.style.color = 'var(--red)'; }
    });
}

document.addEventListener('DOMContentLoaded', () => {
  const cb = document.getElementById('set-enabled');
  if (cb) cb.addEventListener('change', () => _updateToggleText(cb.checked));
  const tn = document.getElementById('set-testnet');
  if (tn) tn.addEventListener('change', () => _updateTestnetText(tn.checked));
});

function _updateToggleText(checked) {
  const el = document.getElementById('toggle-text');
  if (el) { el.textContent = checked ? 'ENABLED' : 'DISABLED'; }
}

function _updateTestnetText(checked) {
  const el = document.getElementById('toggle-testnet-text');
  if (el) {
    el.textContent  = checked ? 'TESTNET' : 'REAL';
    el.style.color  = checked ? 'var(--gold)' : 'var(--text2)';
  }
}

/* ── Live unrealized PnL cache (updated via socket) ─────────────────── */
const _openPnl = {};

socket.on('trade_pnl', (data) => {
  _openPnl[data.pair] = data.unrealized_pnl;
  const el = document.getElementById('upnl-' + data.pair);
  if (el) {
    const v = data.unrealized_pnl || 0;
    el.textContent = (v >= 0 ? '+' : '') + fmt(v);
    el.style.color = v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : 'var(--text)';
  }
});

socket.on('trade_opened', (data) => {
  if (document.getElementById('page-account') &&
      !document.getElementById('page-account').classList.contains('hidden')) {
    loadAccount();
  }
});

socket.on('trade_closed', (data) => {
  delete _openPnl[data.pair];
  if (document.getElementById('page-account') &&
      !document.getElementById('page-account').classList.contains('hidden')) {
    loadAccount();
  }
});
