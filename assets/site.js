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

// ── Doughnut/pie helper ────────────────────────────────────────────
// Renders a doughnut chart with a center metric label. Takes absolute
// values + labels + colors. Negative values are split into a "Losses"
// slice so the donut stays readable.
function renderDoughnut(canvasId, labels, values, colors, opts={}){
  const ctx = document.getElementById(canvasId);
  if(!ctx) return null;
  if(window.__charts && window.__charts[canvasId]) window.__charts[canvasId].destroy();

  // Split positives / negatives into separate slices if requested
  let dataLabels = labels.slice();
  let dataValues = values.slice();
  let dataColors = colors.slice();
  if(opts.splitNegatives){
    const posLabels=[], posValues=[], posColors=[];
    const negLabels=[], negValues=[], negColors=[];
    dataLabels.forEach((l,i)=>{
      const v = dataValues[i] || 0;
      if(v >= 0){ posLabels.push(l); posValues.push(v); posColors.push(dataColors[i]); }
      else { negLabels.push(l + ' (loss)'); negValues.push(Math.abs(v)); negColors.push(shadeColor(dataColors[i], -0.3)); }
    });
    if(negLabels.length){
      dataLabels = ['Winners', ...posLabels, 'Losers', ...negLabels];
      dataValues = [posValues.reduce((a,b)=>a+b,0), ...posValues, negValues.reduce((a,b)=>a+b,0), ...negValues];
      dataColors = ['#10b981', ...posColors, '#ef4444', ...negColors];
    }
  }

  // If all values are 0, show placeholder
  const total = dataValues.reduce((a,b)=>a+b,0);
  if(total === 0){
    dataLabels = ['No data'];
    dataValues = [1];
    dataColors = ['#1f2937'];
  }

  const chart = new Chart(ctx,{
    type:'doughnut',
    data:{
      labels: dataLabels,
      datasets:[{
        data: dataValues,
        backgroundColor: dataColors,
        borderColor: 'rgba(13,19,34,0.85)',
        borderWidth: 2,
        hoverOffset: 8,
        hoverBorderWidth: 3,
      }]
    },
    options:{
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      cutout: opts.cutout || '68%',
      plugins:{
        legend:{
          display: opts.legend !== false,
          position: opts.legendPosition || 'right',
          labels:{
            color: '#94a3b8',
            font: {family: 'JetBrains Mono', size: 10},
            boxWidth: 10,
            boxHeight: 10,
            padding: 10,
            generateLabels: opts.legendFormatter || undefined,
          }
        },
        tooltip:{
          backgroundColor: 'rgba(13,19,34,0.95)',
          borderColor: 'rgba(0,212,255,0.3)',
          borderWidth: 1,
          padding: 12,
          titleFont: {family: 'JetBrains Mono', size: 11, weight: '600'},
          bodyFont: {family: 'JetBrains Mono', size: 11},
          callbacks:{
            label: (c)=> opts.tooltipLabel
              ? opts.tooltipLabel(c, dataLabels, dataValues)
              : `${c.label}: ${fmtPips(c.parsed)} pips`
          }
        }
      }
    }
  });

  // Center metric label (HTML overlay)
  if(opts.centerLabel !== false){
    const wrapper = ctx.parentElement;
    if(wrapper && !wrapper.querySelector('.donut-center')){
      const center = document.createElement('div');
      center.className = 'donut-center';
      center.style.cssText = 'position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;pointer-events:none;text-align:center';
      const labelEl = document.createElement('div');
      labelEl.className = 'donut-center-label';
      labelEl.style.cssText = 'font-family:var(--font-mono);font-size:9px;color:var(--dim);letter-spacing:0.15em;text-transform:uppercase;margin-bottom:4px';
      labelEl.textContent = opts.centerLabelText || 'TOTAL';
      const valueEl = document.createElement('div');
      valueEl.className = 'donut-center-value';
      valueEl.style.cssText = 'font-family:var(--font-mono);font-size:22px;font-weight:600;line-height:1;letter-spacing:-0.02em';
      valueEl.textContent = opts.centerValue !== undefined ? opts.centerValue : fmtPips(dataValues.reduce((a,b)=>a+b,0));
      const subEl = document.createElement('div');
      subEl.className = 'donut-center-sub';
      subEl.style.cssText = 'font-family:var(--font-mono);font-size:10px;color:var(--muted);margin-top:4px';
      subEl.textContent = opts.centerSub || '';
      center.appendChild(labelEl);
      center.appendChild(valueEl);
      center.appendChild(subEl);
      if(getComputedStyle(wrapper).position === 'static') wrapper.style.position = 'relative';
      wrapper.appendChild(center);
    } else if(wrapper){
      // Update existing
      const center = wrapper.querySelector('.donut-center');
      if(center){
        center.querySelector('.donut-center-label').textContent = opts.centerLabelText || 'TOTAL';
        center.querySelector('.donut-center-value').textContent = opts.centerValue !== undefined ? opts.centerValue : fmtPips(dataValues.reduce((a,b)=>a+b,0));
        center.querySelector('.donut-center-sub').textContent = opts.centerSub || '';
      }
    }
  }

  if(!window.__charts) window.__charts = {};
  window.__charts[canvasId] = chart;
  return chart;
}

// Darken/lighten a hex color by a factor (-1..1)
function shadeColor(hex, factor){
  const c = hex.replace('#','');
  const r = parseInt(c.substr(0,2),16);
  const g = parseInt(c.substr(2,2),16);
  const b = parseInt(c.substr(4,2),16);
  const f = (v) => Math.max(0, Math.min(255, Math.round(v + (factor<0 ? v*factor : (255-v)*factor))));
  return `rgb(${f(r)},${f(g)},${f(b)})`;
}

// Per-engine color map (matches the engines list)
const ENGINE_COLORS = {
  NITRO: '#fbbf24', SURGE: '#60a5fa', DRAGON: '#ef4444', TITAN: '#10b981',
  CIPHER: '#f97316', PHANTOM: '#c084fc', NEXUS: '#00d4ff',
  VOLTALITY: '#7c3aed', FLUENCE: '#ec4899',
};
function engineColor(name){ return ENGINE_COLORS[name] || '#94a3b8'; }

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