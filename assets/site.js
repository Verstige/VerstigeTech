/* ─────────────────────────────────────────────────────────────────────
   VerstigeTech — Shared site logic
   - Loads data/dashboard.json
   - Builds nav, footer, code rain
   - Exposes helpers (fmt, render, etc.)
   ───────────────────────────────────────────────────────────────────── */

let DATA = null;

async function loadData(){
  try{
    const r = await fetch(`data/dashboard.json?t=${Date.now()}`);
    if(!r.ok) throw new Error('HTTP '+r.status);
    DATA = await r.json();
    if(window.onDataReady) window.onDataReady(DATA);
    return DATA;
  }catch(e){
    console.error('Failed to load data:', e);
    if(window.onDataError) window.onDataError(e);
    return null;
  }
}

// ── Number formatters ───────────────────────────────────────────────
function fmtPips(n){return (n>=0?'+':'') + Number(n).toFixed(1)}
function fmtUSD(n){const sign=n>=0?'+':'−';return sign+'$'+Math.abs(n).toFixed(2)}
function fmtPct(n){return Number(n).toFixed(1)+'%'}
function fmtNum(n){return Number(n).toLocaleString()}
function fmtTime(iso){
  if(!iso) return '—';
  const d = new Date(iso);
  if(isNaN(d)) return '—';
  const now = new Date();
  const diff = (now - d) / 1000;
  if(diff < 60) return Math.floor(diff)+'s ago';
  if(diff < 3600) return Math.floor(diff/60)+'m ago';
  if(diff < 86400) return Math.floor(diff/3600)+'h ago';
  return d.toLocaleString('en-US',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
}

// ── Engine registry ────────────────────────────────────────────────
const ENGINES = [
  ['NITRO','XAUUSD','Gold SMC + EMA',           '#fbbf24'],
  ['SURGE','US30','US30 Momentum',              '#60a5fa'],
  ['DRAGON','GBPJPY','GBP/JPY Reversal',         '#ef4444'],
  ['TITAN','EURUSD','EUR/USD Trend',             '#10b981'],
  ['CIPHER','BTCUSD','BTC 4H SMC',               '#f97316'],
  ['PHANTOM','SOLUSD','SOL Weighted Score',      '#c084fc'],
  ['NEXUS','NAS100','NAS100 Prime Zone',         '#00d4ff'],
  ['VOLTALITY','US30','US30 Prime Zone',         '#7c3aed'],
  ['FLUENCE','US30','US30 SMC',                  '#ec4899'],
];
const ENGINE_BY_NAME = Object.fromEntries(ENGINES.map(e=>[e[0],e]));

// ── Page-injectable hooks ───────────────────────────────────────────
function buildNav(activePage){
  return `
  <nav class="navbar">
    <div class="nav-container">
      <a href="index.html" class="nav-logo">
        <span class="nav-logo-mark">V</span>
        <span>VERSTIGETECH</span>
      </a>
      <div class="nav-links">
        <a href="index.html"      class="nav-link ${activePage==='overview'?'active':''}">Overview</a>
        <a href="strategies.html" class="nav-link ${activePage==='strategies'?'active':''}">Strategies</a>
        <a href="dashboard.html"  class="nav-link ${activePage==='dashboard'?'active':''}">Live Dashboard</a>
        <a href="analytics.html"  class="nav-link ${activePage==='analytics'?'active':''}">Analytics</a>
        <a href="backtests.html"  class="nav-link ${activePage==='backtests'?'active':''}">Backtests</a>
        <a href="performance.html" class="nav-link ${activePage==='performance'?'active':''}">Performance</a>
        <a href="risk.html"       class="nav-link ${activePage==='risk'?'active':''}">Risk</a>
        <a href="tradelog.html"   class="nav-link ${activePage==='tradelog'?'active':''}">Trade Log</a>
        <a href="https://verstige.io" target="_blank" rel="noopener" class="nav-cta">VerstigeOS →</a>
      </div>
      <button class="nav-mobile-toggle" aria-label="Menu">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="3" y1="6" x2="21" y2="6"/>
          <line x1="3" y1="12" x2="21" y2="12"/>
          <line x1="3" y1="18" x2="21" y2="18"/>
        </svg>
      </button>
    </div>
  </nav>`;
}

function buildFooter(){
  return `
  <footer class="footer">
    <div class="container footer-content">
      <div class="footer-brand">VERSTIGETECH · v1.0</div>
      <div>9 ENGINES · LIVE SIGNAL INTELLIGENCE</div>
      <div id="last-update" class="muted">—</div>
    </div>
  </footer>`;
}

function buildCodeRain(count=20){
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789$€¥£₿';
  let html = '<div class="code-rain">';
  for(let i=0;i<count;i++){
    const left = (i / count) * 100;
    const dur = 4 + Math.random() * 12;
    const delay = -Math.random() * dur;
    let col = '';
    for(let j=0;j<30;j++){
      const c = chars[Math.floor(Math.random()*chars.length)];
      const op = 0.1 + Math.random()*0.4;
      col += `<span style="opacity:${op}">${c}</span>`;
    }
    html += `<div class="code-col" style="left:${left}%;animation-duration:${dur}s;animation-delay:${delay}s">${col}</div>`;
  }
  html += '</div>';
  return html;
}

// ── Toast ───────────────────────────────────────────────────────────
function showToast(msg, isError){
  let t = document.getElementById('toast');
  if(!t){
    t = document.createElement('div');
    t.id = 'toast';t.className='toast';
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.style.borderColor = isError ? 'var(--red)' : 'var(--border-2)';
  t.style.color = isError ? 'var(--red)' : 'var(--muted)';
  t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'), 2500);
}

// ── Init helpers ────────────────────────────────────────────────────
function initPage(activePage){
  // Inject nav, footer, code rain
  document.body.insertAdjacentHTML('afterbegin', buildCodeRain());
  document.body.insertAdjacentHTML('afterbegin', buildNav(activePage));
  document.body.insertAdjacentHTML('beforeend', buildFooter());

  // Load data
  loadData().then(d=>{
    if(d){
      document.getElementById('last-update').textContent =
        'Updated '+new Date(d.generated_at).toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
    }
  });
}

// Mobile nav toggle (basic)
document.addEventListener('click', e=>{
  if(e.target.closest('.nav-mobile-toggle')){
    const links = document.querySelector('.nav-links');
    if(links){
      links.style.display = links.style.display === 'flex' ? 'none' : 'flex';
      links.style.position = 'absolute';
      links.style.top = '60px';
      links.style.left = '0';
      links.style.right = '0';
      links.style.flexDirection = 'column';
      links.style.background = 'var(--bg-2)';
      links.style.padding = '16px';
      links.style.borderBottom = '1px solid var(--border)';
    }
  }
});

// Auto-refresh every 60s
setInterval(()=>loadData().then(d=>{
  if(d && window.onDataReady){
    window.onDataReady(d);
    const el = document.getElementById('last-update');
    if(el) el.textContent = 'Updated '+new Date(d.generated_at).toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
  }
}), 60000);