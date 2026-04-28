// ═══════════════════════════════════════════
// SAFE QUERY: protegge dai residui del vecchio JS che cercano elementi
// dello screen-login (non più esistente nella nuova architettura NETGOLF).
// Wrappa document.getElementById in modo che, su id mancanti, ritorni un
// oggetto-fantasma che assorbe addEventListener / classList / style / .value
// senza esplodere.
// ═══════════════════════════════════════════
(function() {
  const _orig = document.getElementById.bind(document);
  const noop = function() {};
  const ghostClassList = { add: noop, remove: noop, toggle: noop, contains: () => false };
  const ghostStyle = new Proxy({}, { get: () => '', set: () => true });
  document.getElementById = function(id) {
    const el = _orig(id);
    if (el) return el;
    return new Proxy({ _ghost: true, _id: id }, {
      get(target, prop) {
        if (prop === '_ghost' || prop === '_id') return target[prop];
        if (prop === 'addEventListener' || prop === 'removeEventListener') return noop;
        if (prop === 'classList') return ghostClassList;
        if (prop === 'style') return ghostStyle;
        if (prop === 'dataset') return {};
        if (prop === 'textContent' || prop === 'innerHTML' || prop === 'innerText' || prop === 'value') return '';
        if (prop === 'children' || prop === 'childNodes') return [];
        if (prop === 'parentNode' || prop === 'parentElement') return null;
        if (prop === 'click' || prop === 'focus' || prop === 'blur') return noop;
        if (prop === 'getAttribute' || prop === 'setAttribute' || prop === 'removeAttribute') return noop;
        if (prop === 'appendChild' || prop === 'removeChild' || prop === 'insertBefore') return function(n) { return n; };
        return undefined;
      },
      set(target, prop, value) {
        // assegnazioni tipo el.textContent = '...' silenziosamente assorbite
        return true;
      }
    });
  };
})();

// Gara valida se valida === 'S' oppure 'V'
function isValida(v, sd) {
  if (v !== 'S' && v !== 'V') return false;
  // Esclude gare senza SD valido
  if (sd !== undefined) {
    const sdVal = parseFloat(String(sd || '').replace(',', '.'));
    if (!sd || isNaN(sdVal) || sdVal === 0) return false;
  }
  return true;
}

// Normalizza una data dal formato italiano "11/04/2026" al formato ISO
// "2026-04-11" per il match col backend scorecard /lookup, che usa
// sempre date in ISO come restituite da Claude vision.
function _normDateForLookup(s) {
  if (!s) return '';
  s = String(s).trim();
  // già in ISO?
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 10);
  // formato italiano DD/MM/YYYY o DD-MM-YYYY?
  const m = s.match(/^(\d{2})[\/\-](\d{2})[\/\-](\d{4})/);
  if (m) return m[3] + '-' + m[2] + '-' + m[1];
  return s;
}

// ═══════════════════════════════════════════
// STATE
// ═══════════════════════════════════════════
let state = {
  user: null,
  sessionId: null,
  results: [],
  hcpHistory: [],
  activeFilter: 'all',
  _dataLoaded: false
};

// ═══════════════════════════════════════════
// SESSION CACHE (sessionStorage)
// Persiste i dati FIG per tutta la sessione del tab.
// Viene cancellato automaticamente alla chiusura del browser/tab.
// ═══════════════════════════════════════════
const CACHE_KEY = 'netgolf_fig_cache';

function saveToSessionCache(data) {
  try {
    sessionStorage.setItem(CACHE_KEY, JSON.stringify(data));
  } catch(e) {
    console.warn('[CACHE] salvataggio fallito:', e);
  }
}

function loadFromSessionCache() {
  try {
    const raw = sessionStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch(e) {
    console.warn('[CACHE] lettura fallita:', e);
    return null;
  }
}

function clearSessionCache() {
  try {
    sessionStorage.removeItem(CACHE_KEY);
  } catch(e) {}
}

// ── BOOT: il login NETGOLF (email+pwd) è già avvenuto lato Flask.
//     Quando arriviamo qui il cookie di sessione è valido e basta caricare i dati.
async function tryAutoLogin() {
  try {
    await loadConfig();   // features disponibili prima di loadAllData
    await loadAllData();
  } catch (e) {
    console.error('[BOOT] errore caricamento:', e);
    if (String(e.message || '').includes('401')) {
      window.location.href = '/auth/login';
    }
  }
}

function showLoginScreen() { window.location.href = '/auth/login'; }

// ═══════════════════════════════════════════
// PROXY URL — dopo il deploy su Render, sostituisci questa riga con
// il tuo URL, es: https://federgolf-proxy.onrender.com
// ═══════════════════════════════════════════
const PROXY_URL = window.location.origin; // funziona su PRD, DEV e localhost

// ── Config app (public/api/config) ──────────────────────────
let APP_CONFIG = null;
async function loadConfig() {
  try {
    const r = await fetch(PROXY_URL + '/api/config');
    if (r.ok) {
      APP_CONFIG = await r.json();
      console.log('[CONFIG] Loaded v' + APP_CONFIG.app.version);
    } else {
      console.log('[CONFIG] config.json non trovato (' + r.status + ')');
    }
  } catch(e) {
    console.log('[CONFIG] Errore fetch:', e.message);
  }
  applyHcpColor(parseFloat(state.user && state.user.hcp));
}

// ── FRASI OBIETTIVO ──────────────────────────────────────────
// Nella nuova architettura NETGOLF, l'endpoint /api/frase sceglie
// direttamente server-side la frase casuale (in base alla fascia HCP
// passata come query param), la salva nel DB e la ritorna. Il client
// non deve più scaricare l'intero catalogo frasi_obiettivo.json.
async function loadFraseObiettivo(bandLabel) {
  if (!bandLabel) return;
  const accent = APP_CONFIG && APP_CONFIG.hcpColors
    ? (APP_CONFIG.hcpColors.find(b => b.label === bandLabel) || {}).accent || '#00ff66'
    : '#00ff66';
  try {
    const hcp = state.user && state.user.hcp ? String(state.user.hcp).replace(',', '.') : '';
    const url = PROXY_URL + '/api/frase' + (hcp ? '?hcp=' + encodeURIComponent(hcp) : '');
    const r = await fetch(url, { headers: apiHeaders() });
    if (!r.ok) { console.log('[FRASI] endpoint non disponibile:', r.status); return; }
    const data = await r.json();
    if (data.frase) {
      mostraFrase(data.frase, data.fraseId || '', accent);
    }
  } catch(e) {
    console.error('[FRASI] errore:', e);
  }
}

function mostraFrase(frase, fraseId, accent) {
  const el = document.getElementById('frase-obiettivo');

  el.style.borderLeftColor = accent || '#00ff66';
  const wrap = document.getElementById('frase-obiettivo-wrap');
  if (wrap) wrap.style.display = 'block';
  el.innerHTML = '<span>' + frase + '</span>';
}

function testHcpBand(val) {
  document.getElementById('hcp-test-val').textContent = parseFloat(val).toFixed(1);
  document.getElementById('hcp-value').textContent = parseFloat(val).toFixed(1);
  applyHcpColor(parseFloat(val));
}

function applyHcpColor(hcp) {
  // Mostra versione
  const vb = document.getElementById('version-bar');
  if (vb && APP_CONFIG) vb.textContent = 'v' + APP_CONFIG.app.version;
  if (!APP_CONFIG || isNaN(hcp)) return;
  const band = APP_CONFIG.hcpColors.find(b => hcp >= b.min && hcp <= b.max);
  if (!band) return;
  // Lascia la card sempre con sfondo blu fisso — colora solo HCP e badge
  // Colora il valore HCP
  const val = document.getElementById('hcp-value');
  if (val) {
    val.style.color = band.accent;
    val.style.textShadow = '0 0 20px ' + band.accent + '99';
  }
  // Badge fascia
  const badge = document.getElementById('hcp-band-badge');
  if (badge) {
    badge.textContent = band.label;
    badge.style.color = band.accent;
    badge.style.opacity = '1';
  }
  // Carica frase obiettivo del mese
  loadFraseObiettivo(band.label);
}

function showWhatsNew() {
  if (!APP_CONFIG) return;
  const { version, releaseDate, whatsNew } = APP_CONFIG.app;
  const msg = 'NETGOLF v' + version + ' · ' + releaseDate + '\n\nNovità:\n' +
    whatsNew.map((w, i) => '• ' + w).join('\n');
  alert(msg);
}

function apiHeaders() {
  // Con la nuova architettura NETGOLF, l'auth passa per il cookie Flask
  // (httpOnly). Niente più x-session-id proprietario.
  return { 'Content-Type': 'application/json' };
}

// ═══════════════════════════════════════════
// LOGIN
// ═══════════════════════════════════════════
const btnLogin = document.getElementById('btn-login');
if (btnLogin) btnLogin.addEventListener('click', doLogin);
['inp-user','inp-pass'].forEach(id => { const el = document.getElementById(id); if (el) el.addEventListener('keydown', e => { if(e.key==='Enter') doLogin(); }); });

async function doLogin() { window.location.href = '/auth/login'; }

// Stato caricamento dati extra
state._dataLoaded = false;
state._dataLoading = false;

async function loadAllData(forceRefresh = false) {
  console.log('[LOAD] loadAllData START | forceRefresh:', forceRefresh);
  if (!state.user) state.user = {};
  const btn = document.getElementById('btn-login');

  // Se non è un refresh forzato, prova a usare la cache
  if (!forceRefresh) {
    const cached = loadFromSessionCache();
    if (cached) {
      console.log('[CACHE] dati trovati in sessionStorage, skip fetch FIG');
      state.results     = cached.results     || [];
      state.hcpHistory  = cached.hcpHistory  || [];
      state.scorecards  = cached.scorecards  || [];
      if (cached.profile) {
        state.user.profile     = cached.profile;
        state.user.hcp         = cached.profile.handicapIndex || cached.profile.handicap_index || cached.hcp || '—';
        state.user.displayName = ((cached.profile.nome||'') + ' ' + (cached.profile.cognome||'')).trim() || state.user.displayName;
      }
      state._dataLoaded = true;
      switchToMain();
      if (btn) btn.classList.remove('loading');
      return;
    }
  }

  try {
    const feat = APP_CONFIG?.features || {};
    const fetchList = [
      fetch(PROXY_URL + '/api/fig/profilo',    { headers: apiHeaders() }),
      fetch(PROXY_URL + '/api/fig/storico',    { headers: apiHeaders() }),
      feat.scorecard !== false
        ? fetch('/scorecard/api/scorecards-index', { headers: apiHeaders() })
        : Promise.resolve(null),
    ];
    const [profiloRes, storicoRes, scorecardsRes] = await Promise.all(fetchList);

    if (profiloRes.ok) {
      const profiloData = await profiloRes.json();
      const profile = profiloData.profile;
      if (profile) {
        state.user.profile     = profile;
        state.user.tessHistory = profiloData.tessHistory || [];
        state.user.hcp         = profile.handicapIndex || profile.handicap_index || profile.handicap || '—';
        const nome = ((profile.nome||'') + ' ' + (profile.cognome||'')).trim();
        state.user.displayName = nome || state.user.displayName;
      }
    }

    if (storicoRes.ok) {
      const storicoData = await storicoRes.json();
      console.log('[APP] storico results:', storicoData.results?.length, '| hcpHistory:', storicoData.hcp_history?.length);
      state.results    = storicoData.results     || [];
      state.hcpHistory = storicoData.hcp_history || storicoData.hcpHistory || [];
      if (!state.user.hcp && state.hcpHistory.length)
        state.user.hcp = state.hcpHistory[state.hcpHistory.length-1].value.toFixed(1);
    } else {
      console.log('[APP] storico fallito:', storicoRes.status);
    }

    if (scorecardsRes && scorecardsRes.ok) {
      const scData = await scorecardsRes.json();
      state.scorecards = scData.scorecards || [];
    } else {
      state.scorecards = [];
    }

    // Salva tutto in sessionStorage
    saveToSessionCache({
      results:    state.results,
      hcpHistory: state.hcpHistory,
      scorecards: state.scorecards,
      profile:    state.user.profile,
      hcp:        state.user.hcp,
    });

    state._dataLoaded = true;
    switchToMain();
  } catch(e) {
    console.error('[LOAD] loadAllData ERROR:', e.message, e);
    showError('Errore di connessione. Riprova più tardi.');
  } finally {
    if (btn) btn.classList.remove('loading');
  }
}

function showError(msg) {
  const el = document.getElementById('login-error');
  if (el) {
    el.textContent = msg;
    el.style.display = 'block';
  } else {
    console.error('[showError]', msg);
  }
}

// ═══════════════════════════════════════════
// SWITCH TO MAIN
// ═══════════════════════════════════════════
function switchToMain() {
  document.getElementById('screen-login').classList.remove('active');
  document.getElementById('screen-main').classList.add('active');

  const nome = (state.user.profile && state.user.profile.nome) || state.user.displayName || '—';
  document.getElementById('main-name').textContent = nome;
  document.getElementById('main-tessera').textContent = '';
  document.getElementById('hcp-value').textContent = state.user.hcp || '—';
  // Config caricato separatamente dopo il rendering
  setTimeout(() => {
    if (APP_CONFIG) {
      applyHcpColor(parseFloat(state.user.hcp));
    } else {
      loadConfig();
    }
  }, 100);

  if (state.hcpHistory.length > 1) {
    const hist = [...state.hcpHistory].sort((a,b) => {
      const pa = (a.date||'').split('/'), pb = (b.date||'').split('/');
      return new Date(pa[2],pa[1]-1,pa[0]) - new Date(pb[2],pb[1]-1,pb[0]);
    });
    const last = hist[hist.length-1].value;
    const prev = hist[hist.length-2].value;
    const diff = (last - prev).toFixed(1);
    const el = document.getElementById('hcp-trend');
    el.textContent = (diff > 0 ? '▲' : '▼') + ' ' + Math.abs(diff) + ' rispetto alla gara precedente';
    el.className = 'trend' + (diff > 0 ? ' negative' : '');
  }

  // Nascondi banner carica dati se presente
  const banner = document.getElementById('load-data-banner');
  if (banner) banner.style.display = 'none';

  // Mostra bottom nav e abilita tutti i tab
  const nav = document.querySelector('.bottom-nav');
  if (nav) nav.style.display = '';
  document.querySelectorAll('.tab-btn, .nav-item').forEach(b => b.disabled = false);

  // Aggiorna indice scorecard solo se la feature è attiva
  const _feat = APP_CONFIG?.features || {};
  if (_feat.scorecard !== false) {
    refreshScorecards().then(() => renderResults());
  } else {
    renderResults();
  }
  
  // Render tutto
  renderSparkline();
  renderCharts();
  renderProfile();
  renderHcpCalc();
  updateHcpInsight();
}

// ═══════════════════════════════════════════
// SPARKLINE
// ═══════════════════════════════════════════
function renderSparkline() {
  const wrap = document.getElementById('hcp-sparkline');
  if (!wrap) return;
  wrap.innerHTML = '';
  const vals = state.hcpHistory.slice(-12).map(h => h.value);
  if (!vals.length) return;
  const min = Math.min(...vals), max = Math.max(...vals);
  const range = max - min || 1;
  vals.forEach((v, i) => {
    const b = document.createElement('div');
    b.className = 'spark-bar' + (i === vals.length-1 ? ' highlight' : '');
    b.style.height = (10 + ((v - min) / range) * 22) + 'px';
    wrap.appendChild(b);
  });
}

// ═══════════════════════════════════════════
// RESULTS
// ═══════════════════════════════════════════
function renderResults(filter = 'all') {
  const list = document.getElementById('results-list');
  let data = state.results;

  if (filter !== 'all') {
    data = data.filter(r => r.formula && r.formula.toLowerCase().includes(filter.toLowerCase()));
  }

  document.getElementById('results-count').textContent = data.length + ' gare';

  if (!data.length) {
    if (!state._dataLoaded) {
      list.innerHTML = '<div class="empty-state"><span class="empty-icon">⬇️</span><p>Premi "Carica gare e statistiche" per visualizzare i risultati.</p></div>';
    } else {
      list.innerHTML = '<div class="empty-state"><span class="empty-icon">🏌️</span><p>Nessun risultato trovato.</p></div>';
    }
    return;
  }

  list.innerHTML = data.map((r, i) => {
    const varNum = parseFloat((r.variazione || '0').replace(',','.'));
    const varColor = varNum > 0 ? 'var(--red-score)' : varNum < 0 ? 'var(--green-light)' : 'var(--gray-soft)';
    const varSign = varNum > 0 ? '+' : '';
    const _featR = APP_CONFIG?.features || {};
    const sc = (_featR.scorecard !== false) ? findScorecardForResult(r) : null;
    const scorecardBadge = sc
       ? '<a href="/scorecard/' + sc.id + '" onclick="event.stopPropagation()" ' +
       'style="display:inline-flex;align-items:center;gap:4px;text-decoration:none;' +
       'background:rgba(0,255,102,0.15);border:1px solid rgba(0,255,102,0.35);' +
       'color:var(--green-accent);border-radius:6px;padding:3px 8px;font-size:11px;font-weight:600">' +
       '📋 Scorecard</a>'
       : '';
    return '<div class="result-card" style="animation-delay:' + (i*20) + 'ms" onclick="openDetail(' + r.id + ')">' +
      '<div class="rc-top">' +
        '<div class="rc-name">' + (r.gara || '—') + '</div>' +
        '<div style="display:flex;flex-direction:column;align-items:flex-end;gap:4px">' +
          (r.stbl ? '<div style="font-size:20px;font-weight:700;color:var(--green-accent);font-family:DM Mono,monospace;line-height:1">' + r.stbl + '<span style="font-size:10px;font-weight:400;opacity:0.6;margin-left:2px">stbl</span></div>' : '') +
          (r.variazione !== undefined && r.variazione !== '' ?
            '<div style="font-size:12px;font-weight:600;color:' + varColor + ';font-family:DM Mono,monospace;line-height:1">' + varSign + r.variazione + ' hcp</div>' : '') +
        '</div>' +
      '</div>' +
      '<div style="font-size:12px;color:var(--gray-soft);margin-bottom:8px">' + (r.esecutore || '') + ' · ' + (r.data || '') + '</div>' +
      '<div class="rc-meta">' +
        '<span class="badge badge-format">' + (r.formula || '—') + '</span>' +
        // ... altri badge ...
        (scorecardBadge ? '<span>' + scorecardBadge + '</span>' : '') +
        '<span class="badge badge-club">' + (r.buche || '—') + ' buche</span>' +
        (isValida(r.valida) ? '<span class="badge" style="background:rgba(76,175,80,0.2);color:var(--green-light)">✓ Valida</span>' : '<span class="badge" style="background:rgba(229,115,115,0.15);color:var(--red-score)">✗ Non valida</span>') +
        '<span class="badge badge-hcp">PHCP ' + (r.playingHcp || '—') + '</span>' +
        '<span class="badge" style="background:rgba(0,255,102,0.12);color:#66ffaa">Stbl ' + (r.stbl || '—') + '</span>' +
      '</div>' +


    '</div>';
  }).join('');
}

function scoreClass(r) {
  if (r.format === 'Stableford') {
    const s = parseInt(r.score);
    return s >= 36 ? 'eagle' : s < 28 ? 'over' : '';
  }
  return '';
}


// ═══════════════════════════════════════════
// CHARTS (pure SVG, no deps)
// ═══════════════════════════════════════════
function renderCharts() {
  // hcpHistory è già cronologico (vecchio→nuovo) dal server → data vecchia a sinistra
  renderLineChart('chart-hcp',
    state.hcpHistory.map(h=>h.value),
    state.hcpHistory.map(h=>h.date),
    '#c9a84c', '#e8c96d');

  // Score medi mensili — usa campo 'data' e solo gare valide con stbl
  const monthly = {};
  const validRes = [...state.results].filter(r => isValida(r.valida, r.sd) && r.stbl && !isNaN(parseInt(r.stbl)));
  // Ordina cronologico per il grafico (vecchio→nuovo)
  validRes.sort((a,b) => {
    const pa = (a.data||'').split('/'); const pb = (b.data||'').split('/');
    return new Date(pa[2],pa[1]-1,pa[0]) - new Date(pb[2],pb[1]-1,pb[0]);
  });
  validRes.forEach(r => {
    const parts = (r.data||'').split('/');
    if (parts.length !== 3) return;
    const key = parts[1] + '/' + parts[2].slice(-2);
    if (!monthly[key]) monthly[key] = [];
    monthly[key].push(parseInt(r.stbl));
  });

  const months = Object.keys(monthly).slice(-12);
  const avgs = months.map(m => {
    const arr = monthly[m];
    return arr.reduce((a,b)=>a+b,0)/arr.length;
  });

  renderLineChart('chart-scores', avgs, months, '#4caf50', '#81c784');

  // SD chart — tutti gli SD validi in ordine cronologico, zoomabile
  renderSdChart();
}

function renderSdChart() {
  const wrap = document.getElementById('chart-sd');
  if (!wrap) return;

  // Prendi tutte le gare con SD valido, ordina cronologico
  const sdData = [...state.results]
    .filter(r => {
      const sd = parseFloat((r.sd || '').replace(',', '.'));
      return !isNaN(sd) && sd !== 0;
    })
    .map(r => ({
      date: r.data,
      sd:   parseFloat((r.sd     || '').replace(',', '.')),
      corr: parseFloat((r.corrSd || '').replace(',', '.')) || 0,
      gara: r.gara || '',
    }))
    .sort((a, b) => {
      const pa = (a.date||'').split('/'), pb = (b.date||'').split('/');
      return new Date(pa[2],pa[1]-1,pa[0]) - new Date(pb[2],pb[1]-1,pb[0]);
    });

  if (!sdData.length) { wrap.innerHTML = '<p style="color:var(--gray-soft);padding:20px;font-size:12px">Nessun SD disponibile</p>'; return; }

  const vals = sdData.map(d => d.sd + d.corr);
  const minV = Math.min(...vals), maxV = Math.max(...vals);
  const range = maxV - minV || 1;

  const BAR_W = 14, BAR_GAP = 6, PAD_L = 36, PAD_R = 12, PAD_T = 20, PAD_B = 40;
  const chartW = Math.max(sdData.length * (BAR_W + BAR_GAP) + PAD_L + PAD_R, 300);
  const chartH = 200;
  const plotH = chartH - PAD_T - PAD_B;

  // HCP medio come linea di riferimento
  const avgSD = vals.reduce((a,b)=>a+b,0)/vals.length;

  let bars = '', labels = '', axes = '';

  // Y axis ticks
  const ticks = 4;
  for (let i = 0; i <= ticks; i++) {
    const v = minV + (range / ticks) * i;
    const y = PAD_T + plotH - (plotH * (v - minV) / range);
    axes += '<line x1="' + PAD_L + '" y1="' + y.toFixed(1) + '" x2="' + (chartW - PAD_R) + '" y2="' + y.toFixed(1) + '" stroke="rgba(255,255,255,0.06)" stroke-width="1"/>';
    axes += '<text x="' + (PAD_L - 4) + '" y="' + (y + 4).toFixed(1) + '" fill="rgba(255,255,255,0.3)" font-size="9" text-anchor="end">' + v.toFixed(1) + '</text>';
  }

  // Avg line
  const avgY = PAD_T + plotH - (plotH * (avgSD - minV) / range);
  axes += '<line x1="' + PAD_L + '" y1="' + avgY.toFixed(1) + '" x2="' + (chartW - PAD_R) + '" y2="' + avgY.toFixed(1) + '" stroke="rgba(0,255,102,0.25)" stroke-width="1" stroke-dasharray="4,3"/>';
  axes += '<text x="' + (chartW - PAD_R - 2) + '" y="' + (avgY - 3).toFixed(1) + '" fill="rgba(0,255,102,0.5)" font-size="9" text-anchor="end">avg ' + avgSD.toFixed(1) + '</text>';

  sdData.forEach((d, i) => {
    const x = PAD_L + i * (BAR_W + BAR_GAP);
    const v = d.sd + d.corr;
    const barH = Math.max(2, plotH * (v - minV) / range);
    const y = PAD_T + plotH - barH;
    const isTop = v <= avgSD;
    const fill = isTop ? '#00ff66' : '#f87171';
    const opacity = isTop ? '0.7' : '0.5';

    bars += '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + BAR_W + '" height="' + barH.toFixed(1) + '" fill="' + fill + '" opacity="' + opacity + '" rx="2">' +
      '<title>' + d.gara + ' ' + d.date + '\nSD: ' + v.toFixed(1) + (d.corr ? ' (corr ' + d.corr.toFixed(1) + ')' : '') + '</title></rect>';

    // Label data ogni 5 barre
    if (i % 5 === 0) {
      const parts = (d.date||'').split('/');
      const lbl = parts.length === 3 ? parts[0] + '/' + parts[1] : d.date;
      labels += '<text x="' + (x + BAR_W/2).toFixed(1) + '" y="' + (chartH - 4) + '" fill="rgba(255,255,255,0.3)" font-size="9" text-anchor="middle" transform="rotate(-45,' + (x + BAR_W/2).toFixed(1) + ',' + (chartH - 4) + ')">' + lbl + '</text>';
    }
  });

  wrap.style.width = chartW + 'px';
  wrap.innerHTML = '<svg width="' + chartW + '" height="' + chartH + '" style="display:block">' + axes + bars + labels + '</svg>';

  // Touch/mouse drag to scroll
  const scrollWrap = document.getElementById('chart-sd-wrap');
  if (scrollWrap) {
    let isDown = false, startX = 0, scrollLeft = 0;
    scrollWrap.addEventListener('mousedown', e => { isDown = true; startX = e.pageX - scrollWrap.offsetLeft; scrollLeft = scrollWrap.scrollLeft; scrollWrap.style.cursor='grabbing'; });
    scrollWrap.addEventListener('mouseleave', () => { isDown = false; scrollWrap.style.cursor='grab'; });
    scrollWrap.addEventListener('mouseup', () => { isDown = false; scrollWrap.style.cursor='grab'; });
    scrollWrap.addEventListener('mousemove', e => { if (!isDown) return; e.preventDefault(); scrollWrap.scrollLeft = scrollLeft - (e.pageX - scrollWrap.offsetLeft - startX); });
    // Scroll to right (most recent) on load
    setTimeout(() => { scrollWrap.scrollLeft = scrollWrap.scrollWidth; }, 100);
  }
}


function renderLineChart(containerId, values, labels, color, fill) {
  const wrap = document.getElementById(containerId);
  if (!values.length) { wrap.innerHTML = '<p style="color:var(--gray-soft);font-size:12px;padding:10px 0">Dati insufficienti</p>'; return; }

  const W = wrap.clientWidth || 300, H = 120;
  const pad = { t:10, r:10, b:30, l:36 };
  const cW = W - pad.l - pad.r, cH = H - pad.t - pad.b;
  const min = Math.min(...values) * 0.98, max = Math.max(...values) * 1.02;
  const range = max - min || 1;

  const pts = values.map((v, i) => ({
    x: pad.l + (i / (values.length-1 || 1)) * cW,
    y: pad.t + (1 - (v - min)/range) * cH
  }));

  const linePath = 'M ' + pts.map(p=>`${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' L ');
  const areaPath = linePath +
    ` L ${pts[pts.length-1].x.toFixed(1)},${(pad.t+cH).toFixed(1)}` +
    ` L ${pts[0].x.toFixed(1)},${(pad.t+cH).toFixed(1)} Z`;

  // Y axis labels
  const yLabels = [min, (min+max)/2, max].map((v, i) => {
    const y = pad.t + (1 - (v - min)/range) * cH;
    return `<text x="${pad.l - 6}" y="${y+4}" text-anchor="end" fill="rgba(255,255,255,0.3)" font-size="9" font-family="DM Mono">${v.toFixed(1)}</text>`;
  }).join('');

  // X labels (every n)
  const step = Math.max(1, Math.floor(values.length / 5));
  const xLabels = pts.filter((_,i) => i % step === 0 || i === pts.length-1).map((p, i, arr) => {
    const idx = values.indexOf(values.filter((_,j) => j % step === 0 || j === values.length-1)[i]);
    const lbl = (labels[idx] || '').toString().slice(0,5);
    return `<text x="${p.x}" y="${H-4}" text-anchor="middle" fill="rgba(255,255,255,0.3)" font-size="9" font-family="DM Mono">${lbl}</text>`;
  }).join('');

  wrap.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;overflow:visible">
      <defs>
        <linearGradient id="grad-${containerId}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${fill}" stop-opacity="0.25"/>
          <stop offset="100%" stop-color="${fill}" stop-opacity="0"/>
        </linearGradient>
      </defs>
      <path d="${areaPath}" fill="url(#grad-${containerId})"/>
      <path d="${linePath}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
      ${pts.map((p,i) => i===pts.length-1 ? `<circle cx="${p.x}" cy="${p.y}" r="4" fill="${color}"/>` : '').join('')}
      ${yLabels}${xLabels}
    </svg>`;
}

// ═══════════════════════════════════════════
// ═══════════════════════════════════════════
// PROFILE RENDERING
// ═══════════════════════════════════════════
function renderProfile() {
  console.log('[APP] renderProfile called, profile:', state.user && state.user.profile ? 'present' : 'NULL', '| hcp:', state.user && state.user.hcp);
  const p = state.user && state.user.profile;

  const set = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.textContent = (val && val !== '---') ? val : '—';
  };

  const nome = p ? ((p.nome||'') + ' ' + (p.cognome||'')).trim() : (state.user && state.user.displayName || '—');
  set('prof-fullname', nome);
  set('prof-tessera-num', 'N° ' + ((p && p.tessera) || (state.user && state.user.username) || '—'));
  set('prof-category', (p && p.qualifica) || '—');

  const parts = nome.split(' ');
  const initials = parts.length >= 2 ? parts[0][0] + parts[parts.length-1][0] : nome.slice(0,2);
  const av = document.getElementById('prof-avatar');
  if (av) av.textContent = initials.toUpperCase();

  set('pstat-hcp', (state.user && state.user.hcp) || '—');
  set('pstat-gare', state.results.length);
  const yr = new Date().getFullYear().toString().slice(-2);
  const gareAnno = state.results.filter(function(r) { return r.data && r.data.slice(-2) === yr; }).length;
  set('pstat-anno', gareAnno + ' nel ' + new Date().getFullYear());
  const validResults = state.results.filter(function(r) { return isValida(r.valida, r.sd); });
  const stbls = validResults.map(function(r) { return parseInt(r.stbl); }).filter(function(s) { return !isNaN(s); });
  if (stbls.length) {
    set('pstat-best', Math.max.apply(null, stbls));
    set('pstat-avg', (stbls.slice(0,10).reduce(function(a,b){return a+b;},0) / Math.min(stbls.length,10)).toFixed(1));
  }

  if (!p) return;

  // Dati personali
  set('pinfo-nome', (p.nome||'') + ' ' + (p.cognome||''));
  set('pinfo-nascita', p.dataNascita);
  set('pinfo-cf', p.codiceFiscale);
  set('pinfo-sesso', p.sesso);
  set('pinfo-naz', p.cittadinanza);
  set('pinfo-luogo-nascita', p.luogoNascita);

  // Tesseramento
  set('pinfo-tessera', p.tessera);
  set('pinfo-circolo', p.circolo);
  set('pinfo-zona', p.zona);
  set('pinfo-qualifica', p.qualifica);
  set('pinfo-stato', p.statoTessera);
  set('pinfo-tipologia', p.tipologia);
  set('pinfo-rinnovo', p.dataRinnovo);
  set('pinfo-sottotipo', p.sottotipoTess);
  set('pinfo-tipo-tess', p.tipoTesserato);

  // Certificato medico
  set('pinfo-cm-tipo', p.certMedico);
  set('pinfo-cm-rilascio', p.dataRilascioCM);
  var scad = document.getElementById('pinfo-cm-scadenza');
  if (scad) {
    scad.textContent = p.scadenzaCM || '—';
    if (p.scadenzaCM) {
      var pts = p.scadenzaCM.split('/');
      var exp = new Date(pts[2], pts[1]-1, pts[0]);
      scad.style.color = exp < new Date() ? 'var(--red-score)' : 'var(--green-light)';
    }
  }

  // HCP
  set('pinfo-hcp-index', p.handicapIndex);
  set('pinfo-low-hcp', p.lowHcpIndex);

  // Recapiti
  set('pinfo-email', p.email);
  set('pinfo-cell', p.cellulare);
  set('pinfo-tel-uff', p.telefonoUfficio);

  // Indirizzo
  set('pinfo-indirizzo', p.indirizzo);
  set('pinfo-cap', p.cap);
  set('pinfo-citta', p.citta);
  set('pinfo-provincia', p.provincia);
  set('pinfo-regione', p.regione);

  // Storico tesseramenti
  var tessEl = document.getElementById('tess-history-list');
  if (tessEl && state.user.tessHistory && state.user.tessHistory.length) {
    tessEl.innerHTML = state.user.tessHistory.map(function(t) {
      return '<div class="hcp-history-item">' +
        '<div class="hhi-date">' + t.anno + '</div>' +
        '<div class="hhi-bar-wrap">' +
          '<div style="font-size:12px;color:var(--cream)">' + t.processo + ' — ' + t.circolo + '</div>' +
          '<div class="hhi-delta">' + t.stato + '</div>' +
        '</div>' +
        '<div style="font-size:11px;color:var(--gray-soft);text-align:right">' + t.data + '</div>' +
      '</div>';
    }).join('');
  }
}

// DETAIL OVERLAY
// ═══════════════════════════════════════════
function openDetail(id) {
  const r = state.results.find(x => x.id === id);
  if (!r) return;

  document.getElementById('ov-title').textContent = r.gara || '—';
  document.getElementById('ov-meta').textContent = (r.data || '') + ' · ' + (r.esecutore || '') + ' · ' + (r.formula || '');

  const varNum = parseFloat((r.variazione||'0').replace(',','.'));
  const varColor = varNum > 0 ? '#e57373' : varNum < 0 ? '#81c784' : '#a0a8a0';

  const row = (icon, label, value, mono=false, color='') =>
    value ? `<div class="info-row">
      <span class="info-icon">${icon}</span>
      <span class="info-label">${label}</span>
      <span class="info-value ${mono?'mono':''}" style="${color?'color:'+color:''}">${value}</span>
    </div>` : '';

  document.getElementById('ov-body').innerHTML = `
    <div style="padding:0 16px 24px">

      <div class="info-section" style="margin-bottom:12px">
        <div class="info-section-title">Gara</div>
        ${row('📅','Data',r.data,true)}
        ${row('⛳','Gara',r.gara)}
        ${row('🏌️','Esecutore',r.esecutore)}
        ${row('📋','Formula',r.formula)}
        ${row('🕳️','Buche',r.buche)}
        ${row('✅','Valida',isValida(r.valida)?'Sì':'No',false,isValida(r.valida)?'#81c784':'#e57373')}
        ${row('🎯','Tipo',r.tipoRisultato)}
        ${row('💬','Motivazione',r.motivazione)}
      </div>

      <div class="info-section" style="margin-bottom:12px">
        <div class="info-section-title">Score</div>
        ${row('🏷️','Playing HCP',r.playingHcp,true)}
        ${row('📐','Par',r.par,true)}
        ${row('📏','CR',r.cr,true)}
        ${row('⚙️','SR',r.sr,true)}
        ${row('🎯','Stableford',r.stbl,true,'#e8c96d')}
        ${row('📊','AGS',r.ags,true)}
        ${row('🌦️','PCC',r.pcc,true)}
        ${row('📉','SD',r.sd,true)}
        ${row('🔧','Corr SD',r.corrSd,true)}
        ${row('➕','Correzione',r.corr,true)}
      </div>

      <div class="info-section">
        <div class="info-section-title">Handicap</div>
        ${row('📉','Index Vecchio',r.indexVecchio,true)}
        ${row('📈','Index Nuovo',r.indexNuovo,true,'#e8c96d')}
        ${r.variazione ? `<div class="info-row">
          <span class="info-icon">↕️</span>
          <span class="info-label">Variazione HCP</span>
          <span class="info-value mono" style="color:${varColor};font-size:18px;font-weight:700">${varNum>0?'+':''}${r.variazione}</span>
        </div>` : ''}
      </div>
    </div>`;

  document.getElementById('detail-overlay').classList.add('open');
}

function closeOverlay(e) {
  if (e.target === document.getElementById('detail-overlay')) {
    document.getElementById('detail-overlay').classList.remove('open');
  }
}

// ═══════════════════════════════════════════
// HCP CALCULATOR
// ═══════════════════════════════════════════
// Calcola exceptional score adjustment su un set di SD
// hcpAtTime = HCP Index al momento di ogni gara
// Restituisce array di SD corretti
function applyExceptionalScores(sdArray, hcpIndex) {
  // Per semplicità usiamo l'HCP attuale come riferimento
  // (il sistema reale userebbe l'HCP al momento di ogni gara)
  let adjusted = [...sdArray];
  let totalAdj = 0;

  sdArray.forEach((sd, i) => {
    const diff = hcpIndex - sd; // diff positiva = SD più basso dell'HCP = migliore
    if (diff >= 10.0) {
      totalAdj += -2.0;
    } else if (diff >= 7.0) {
      totalAdj += -1.0;
    }
  });

  // L'adjustment viene applicato sommandolo alla media (non ai singoli SD)
  // In realtà WHS applica -1 o -2 a ciascuno dei 20 SD recenti per ogni exceptional score
  // ma qui calcoliamo il delta finale sulla media
  return { adjusted, totalAdj };
}

// Arrotondamento WHS: al decimo più vicino, con .5 arrotondato al superiore.
// toFixed(2) elimina il rumore floating-point (es. 24.84999... → "24.85" → 24.9)
// prima del round finale al primo decimale.
function whsRound(x) {
  return Math.round(parseFloat(x.toFixed(2)) * 10) / 10;
}

// Calcola HCP Index da un array di SD con exceptional score e cap
function calcHcpIndex(sdArray, lowHcp, hcpCurrent, applyExceptional) {
  if (sdArray.length === 0) return null;

  // Ordina asc (migliori = più bassi primi)
  const sorted = [...sdArray].sort((a, b) => a - b);

  // Quanti SD usare in base al numero disponibile (Rule 5.2a)
  const n = sdArray.length;
  let useCount, adjustment;
  if (n >= 20)      { useCount = 8;  adjustment = 0; }
  else if (n === 19){ useCount = 7;  adjustment = 0; }
  else if (n === 17 || n === 18){ useCount = 6; adjustment = 0; }
  else if (n === 15 || n === 16){ useCount = 5; adjustment = 0; }
  else if (n === 12 || n === 13 || n === 14){ useCount = 4; adjustment = 0; }
  else if (n >= 9)  { useCount = 3;  adjustment = 0; }
  else if (n === 7 || n === 8){ useCount = 2; adjustment = 0; }
  else if (n === 6) { useCount = 2;  adjustment = -1.0; }
  else if (n === 5) { useCount = 1;  adjustment = 0; }
  else if (n === 4) { useCount = 1;  adjustment = -1.0; }
  else if (n === 3) { useCount = 1;  adjustment = -2.0; }
  else return null;

  const top = sorted.slice(0, useCount);
  const avg = top.reduce((a, b) => a + b, 0) / useCount;
  // WHS: arrotonda la media al decimo più vicino (nearest tenth)
  // Usa parseFloat+toFixed per evitare errori floating point
  let hcp = whsRound(avg + adjustment);

  // Exceptional Score adjustment (Rule 5.9) — solo se richiesto esplicitamente
  let exceptAdj = 0;
  if (applyExceptional) {
    sdArray.forEach(sd => {
      const diff = (hcpCurrent || hcp) - sd;
      if (diff >= 10.0)     exceptAdj += -2.0;
      else if (diff >= 7.0) exceptAdj += -1.0;
    });
    if (exceptAdj < 0) {
      hcp = whsRound(hcp + exceptAdj);
    }
  }

  // Soft Cap / Hard Cap (Rule 5.8)
  // lowHcp arrotondato al 1° decimale prima del confronto, così il diff
  // è calcolato su valori omogenei (entrambi già al 1° decimale).
  let capNote = '';
  if (lowHcp !== null && !isNaN(lowHcp)) {
    const lowHcpR = whsRound(lowHcp);
    const diff = hcp - lowHcpR;
    if (diff > 5.0) {
      hcp = whsRound(lowHcpR + 5.0);
      capNote = 'Hard Cap (Low HCP: ' + lowHcpR.toFixed(1) + ')';
    } else if (diff > 3.0) {
      const excess = diff - 3.0;
      hcp = whsRound(lowHcpR + 3.0 + excess * 0.5);
      capNote = 'Soft Cap (Low HCP: ' + lowHcpR.toFixed(1) + ')';
    }
  }

  return {
    hcp,
    avg8: avg,
    top8: top,
    useCount,
    adjustment,
    exceptAdj,
    capNote
  };
}

function renderHcpCalc() {
  const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };

  const valid = state.results.filter(r => isValida(r.valida, r.sd));
  const last20 = valid.slice(0, 20); // già ordinati desc per data (più recente = indice 0)

  set('hcpc-count', last20.length + ' / 20');

  if (!last20.length) {
    document.getElementById('hcpc-list').innerHTML =
      '<div class="empty-state"><span class="empty-icon">📊</span><p>Nessun risultato valido disponibile.</p></div>';
    return;
  }

  const withSD = last20.map((r, i) => {
    const sdRaw   = parseFloat((r.sd     || '').replace(',', '.'));
    const corrRaw = parseFloat((r.corrSd || '').replace(',', '.')) || 0;
    // SD valido = sd presente e non zero; corrSd si somma solo se sd è presente
    const sdVal = isNaN(sdRaw) ? NaN : sdRaw + corrRaw;
    return { ...r, sdVal, chronoIdx: i };  // 0 = più recente
  }).filter(r => !isNaN(r.sdVal) && r.sdVal !== 0);

  const currentHcp = parseFloat(state.user.hcp) || 0;
  const lowHcpRaw = state.user.profile && state.user.profile.lowHcpIndex;
  const lowHcp = lowHcpRaw ? parseFloat(String(lowHcpRaw).replace(',','.')) : null;
  console.log('[HCP] lowHcp:', lowHcp, '| raw:', lowHcpRaw);

  const sdValues = withSD.map(r => r.sdVal);
  const calc = calcHcpIndex(sdValues, lowHcp, currentHcp, false); // no exceptional nel calcolo reale
  if (!calc) return;
  console.log('[HCPcalc] avg8:', calc.avg8.toFixed(3), '| pre-cap hcp:', (calc.avg8).toFixed(3), '| hcp finale:', calc.hcp.toFixed(1), '| cap:', calc.capNote || 'nessuno', '| lowHcp raw:', lowHcp, '| lowHcpR:', lowHcp !== null ? Math.round(lowHcp*10)/10 : null);

  // Identifica quali sono i migliori 8 (i più bassi)
  // IMPORTANTE: l'ultimo in ordine cronologico (chronoIdx=0, il più recente)
  // viene considerato anche se non è tra i più bassi in assoluto —
  // è lui quello rilevante per il messaggio
  const sortedBySD = [...withSD].sort((a, b) => a.sdVal - b.sdVal);
  const top8Indices = new Set(sortedBySD.slice(0, calc.useCount).map(r => r.chronoIdx));

  // L'ultima gara in ordine cronologico = chronoIdx 0
  // 'Ultima' = la più vecchia tra le 20 (ultimo posto in ordine cronologico)
  const maxIdx = Math.max(...withSD.map(r => r.chronoIdx));
  const lastGame = withSD.find(r => r.chronoIdx === maxIdx);

  // Salva per insight e simulazione
  state._hcpCalc = { withSD, top8Indices, sortedBySD, lastGame, calc, currentHcp, lowHcp, maxIdx };

  // Aggiorna UI summary
  set('hcpc-index', calc.hcp.toFixed(1));
  set('hcpc-avg8', calc.avg8.toFixed(2));
  set('hcpc-avg20', (sdValues.reduce((a,b)=>a+b,0)/sdValues.length).toFixed(2));
  set('hcpc-lowHcp', lowHcp !== null ? lowHcp.toFixed(1) : '—');

  const sub = document.getElementById('hcpc-index-sub');
  if (sub) sub.textContent = 'su ' + withSD.length + ' risultati validi · FIG: ' + (state.user.hcp || '—');

  // Cap/exceptional info
  const capEl = document.getElementById('hcpc-cap-info');
  if (capEl) {
    const notes = [];
    if (calc.capNote) notes.push(calc.capNote);
    if (calc.exceptAdj < 0) notes.push('Exceptional Score: ' + calc.exceptAdj.toFixed(1) + ' applicato');
    capEl.style.display = notes.length ? 'block' : 'none';
    capEl.textContent = notes.join(' · ');
  }

  // Render lista SD
  const avg20 = sdValues.reduce((a,b)=>a+b,0)/sdValues.length;
  const list = document.getElementById('hcpc-list');

  list.innerHTML = withSD.map((r, i) => {
    const isTop = top8Indices.has(r.chronoIdx);
    const isLast = r.chronoIdx === state._hcpCalc.maxIdx; // più vecchia = ultima gara
    // Una gara è "best" se è la più bassa in assoluto E nei top8
    const isBest = isTop && r.sdVal === sortedBySD[0].sdVal;
    const rowClass = isBest ? 'sd-row top8-best' : isTop ? 'sd-row top8' : 'sd-row';
    const valClass = isBest ? 'sd-value best' : isTop ? 'sd-value good' : 'sd-value normal';

    let badge = '';
    if (isLast && isTop) {
      badge = '<span class="sd-badge gold">★ Ultima + Top</span>';
    } else if (isLast) {
      badge = '<span class="sd-badge" style="background:rgba(100,181,246,0.2);color:var(--blue-score)">← Ultima</span>';
    } else if (isBest) {
      badge = '<span class="sd-badge gold">★ Best</span>';
    } else if (isTop) {
      badge = '<span class="sd-badge">Top ' + calc.useCount + '</span>';
    }
    // Tag CORREZIONE SD se corrSd è diverso da zero
    const corrSdVal = parseFloat((r.corrSd || '0').replace(',','.'));
    const corrBadge = (!isNaN(corrSdVal) && corrSdVal !== 0)
      ? '<span class="sd-badge" style="background:rgba(255,200,0,0.15);color:#ffd040;margin-left:4px">Corr ' + (corrSdVal > 0 ? '+' : '') + corrSdVal.toFixed(1) + '</span>'
      : '';

    // Exceptional score badge rimosso (non mostrato all'utente)
    const exceptBadge = '';

    return '<div class="' + rowClass + '" style="animation-delay:' + (i*20) + 'ms"' +
      ' data-date="' + _normDateForLookup(r.data) + '"' +
      ' data-circolo="' + (r.esecutore || '').replace(/"/g,'&quot;') + '">' +
      '<div class="sd-rank' + (isTop ? ' highlight' : '') + '">' + (i+1) + '</div>' +
      '<div class="sd-info">' +
        '<div class="sd-gara">' + (r.gara || '—') + (isLast ? ' 🔴' : '') + '</div>' +
        '<div class="sd-meta">' + r.data + ' · ' + (r.esecutore||'') + ' · PHCP ' + (r.playingHcp||'—') + '</div>' +
      '</div>' +
      badge + corrBadge +
      '<div class="' + valClass + '">' + r.sdVal.toFixed(1) + '</div>' +
    '</div>';
  }).join('');
}


function updateHcpInsight() {
  if (!state._hcpCalc) return;
  const { withSD, top8Indices, sortedBySD, lastGame, calc } = state._hcpCalc;
  if (!lastGame) return;

  const isInTop8 = top8Indices.has(state._hcpCalc.maxIdx); // maxIdx = ultima gara (più vecchia)
  let insightText = '';
  let thresholdSD;

  if (isInTop8) {
    // Ultima gara nei migliori — soglia = il suo SD
    thresholdSD = lastGame.sdVal;
    insightText = 'Ultimo risultato valido (SD ' + thresholdSD.toFixed(1) +
      ') è nei tuoi migliori ' + calc.useCount + '. ' +
      'PROSSIMA GARA: abbassa HCP con SD < ' + thresholdSD.toFixed(1) +
      ', altrimenti lo alzerai.';
  } else {
    // Ultima gara fuori dai migliori — soglia = il PEGGIORE (più alto) tra i top 8
    // cioè l'ultimo dei migliori 8 ordinati in modo crescente
    thresholdSD = sortedBySD[calc.useCount - 1].sdVal;
    insightText = 'Ultima gara (SD ' + lastGame.sdVal.toFixed(1) +
      ') fuori dai migliori ' + calc.useCount + '. ' +
      'PROSSIMA GARA: abbassa HCP con SD < ' + thresholdSD.toFixed(1) +
      ' (il peggiore dei top ' + calc.useCount + '), altrimenti HCP invariato.';
  }

  state._hcpCalc.thresholdSD = thresholdSD;
  state._hcpCalc.isInTop8Last = isInTop8;

  const insight = document.getElementById('hcp-next-insight');
  const text = document.getElementById('hcp-next-text');
  if (insight && text) {
    text.textContent = insightText;
    insight.style.display = 'block';
  }
}



// ── HCP DI GIOCO ─────────────────────────────────────────────
function openHcpGiocoOverlay() {
  document.getElementById('hg-result').style.display = 'none';
  document.getElementById('hcpgioco-overlay').style.display = 'flex';
  // Pre-popola circoli se non già fatto
  hgPopulateCircoli();
  // Pre-compila HCP Index attuale (modificabile)
  const hcp = parseFloat(state.user && state.user.hcp);
  const hgHcpEl = document.getElementById('hg-hcp');
  if (!isNaN(hcp)) hgHcpEl.value = hcp.toFixed(1);
  document.getElementById('hg-result').style.display = 'none';
}

function closeHcpGiocoOverlay(e) {
  if (e.target === document.getElementById('hcpgioco-overlay'))
    document.getElementById('hcpgioco-overlay').style.display = 'none';
}

async function hgPopulateCircoli() {
  const sel = document.getElementById('hg-circolo');
  if (sel.options.length > 1) return;
  try {
    const r = await fetch(PROXY_URL + '/api/campi', { headers: apiHeaders() });
    if (!r.ok) return;
    const data = await r.json();
    const circoli = (data.circoli || []).filter(c => c.nome);  // solo filtro nome
    circoli.sort((a, b) => a.nome.localeCompare(b.nome));
    circoli.forEach(c => {
      const o = document.createElement('option');
      o.value = c.nome; o.textContent = c.nome;
      sel.appendChild(o);
    });
    const lastGame = state.results && state.results[0];
    if (lastGame) {
      const match = circoli.find(c =>
        c.nome.includes((lastGame.esecutore || '').split(' ')[0].toUpperCase())
      );
      if (match) { sel.value = match.nome; hgLoadPercorsi(); }
    }
  } catch(e) { console.warn('hgPopulateCircoli error:', e); }
}

async function hgLoadPercorsi() {
  const nome = document.getElementById('hg-circolo').value;
  const percSel = document.getElementById('hg-percorso');
  const teeSel  = document.getElementById('hg-tee');
  percSel.innerHTML = '<option value="">— Percorso —</option>';
  teeSel.innerHTML  = '<option value="">— Tee —</option>';
  teeSel.disabled = percSel.disabled = true;
  ['hg-cr','hg-sr','hg-par'].forEach(id => document.getElementById(id).value = '');
  if (!nome) return;
  try {
    const r = await fetch(PROXY_URL + '/api/campi/' + encodeURIComponent(nome) + '/percorsi', { headers: apiHeaders() });
    if (!r.ok) return;
    const data = await r.json();
    (data.percorsi || []).forEach(p => {
      const o = document.createElement('option');
      o.value = p.id; o.textContent = p.nome;
      o.dataset.tees = JSON.stringify(p.tees || []);
      o.dataset.par  = p.par || '';          // ← aggiungi questa riga
      percSel.appendChild(o);
    });
    percSel.disabled = false;
    if (percSel.options.length === 2) { percSel.selectedIndex = 1; hgLoadTees(); }
  } catch(e) {}
}

function hgLoadTees() {
  const percSel = document.getElementById('hg-percorso');
  const teeSel  = document.getElementById('hg-tee');
  teeSel.innerHTML = '<option value="">— Tee —</option>';
  teeSel.disabled = true;
  ['hg-cr','hg-sr','hg-par'].forEach(id => document.getElementById(id).value = '');
  const sel = percSel.options[percSel.selectedIndex];
  if (!sel || !sel.dataset.tees) return;
  console.log('[HG] dataset.tees raw:', sel.dataset.tees);   // ← aggiungi
  console.log('[HG] dataset.par:', sel.dataset.par);          // ← aggiungi
  // Precompila par dal percorso (non più stima da CR)
  if (sel.dataset.par) document.getElementById('hg-par').value = sel.dataset.par;

  const tees = JSON.parse(sel.dataset.tees);
  tees.filter(t => t.cr && t.slope).forEach(t => {
    console.log('[HG] aggiungendo tee:', t);   // ← aggiungi
    const o = document.createElement('option');
    o.value = JSON.stringify({ cr: t.cr, sr: t.slope });
    o.textContent = (t.tee_nome || t.tee_id) + '  CR ' + t.cr + ' / SR ' + t.slope;
    teeSel.appendChild(o);
  });
  teeSel.disabled = false;
  if (teeSel.options.length === 2) { teeSel.selectedIndex = 1; hgSelectTee(); }
}

function hgSelectTee() {
  const val = document.getElementById('hg-tee').value;
  if (!val) return;
  try {
    const { cr, sr } = JSON.parse(val);
    document.getElementById('hg-cr').value = cr;
    document.getElementById('hg-sr').value = sr;
    // Par non viene sovrascritto: è già compilato da hgLoadTees
  } catch(e) {}
}

function calcHcpGioco() {
  const hcpIndex = parseFloat(document.getElementById('hg-hcp').value)
              || parseFloat(state.user && state.user.hcp);
  const cr       = parseFloat(document.getElementById('hg-cr').value);
  const sr       = parseFloat(document.getElementById('hg-sr').value);
  const par      = parseFloat(document.getElementById('hg-par').value);
  const buche    = parseInt(document.getElementById('hg-buche').value);
  const corr = 1.0; // Stableford 100%

  if (isNaN(hcpIndex)) { alert('HCP Index non disponibile'); return; }
  if (isNaN(cr) || isNaN(sr) || isNaN(par)) { alert('Inserisci CR, Slope e Par'); return; }

  let courseHcp;
  if (buche === 18) {
    // Course HCP = HCP_Index × (SR/113) + (CR - Par)
    courseHcp = hcpIndex * (sr / 113) + (cr - par);
  } else {
    // 9 buche: HCP_Index/2 (primo decimale) × (SR/113) + (CR - Par)
    const hcpHalf = parseFloat((hcpIndex / 2).toFixed(1));
    courseHcp = hcpHalf * (sr / 113) + (cr - par);
  }
  // Arrotonda al primo intero (0.5 per eccesso)
  const courseHcpInt = Math.floor(courseHcp + 0.5);

  // Playing HCP = Course HCP × correttivo, arrotondato (0.5 per eccesso)
  const playingHcp = Math.floor(courseHcpInt * corr + 0.5);
  const corrPct = Math.round(corr * 100);

  // Mostra risultato
  document.getElementById('hg-course-hcp').textContent = courseHcpInt;
  document.getElementById('hg-result').style.display = 'block';

  document.getElementById('hg-playing-wrap').style.display = 'none';

  const detail = buche === 18
    ? hcpIndex.toFixed(1) + ' × (' + sr + '/113) + (' + cr + ' − ' + par + ') = ' + courseHcp.toFixed(2) + ' → ' + courseHcpInt
    : (hcpIndex/2).toFixed(1) + ' × (' + sr + '/113) + (' + cr + ' − ' + par + ') = ' + courseHcp.toFixed(2) + ' → ' + courseHcpInt;
  document.getElementById('hg-detail').innerHTML =
    detail + (corr < 1.0 ? '<br>Playing = ' + courseHcpInt + ' × ' + corrPct + '% = ' + playingHcp : '');
}

// ── Helpers dropdown simulazione ────────────────────────────
async function simPopulateCircoli() {
  const sel = document.getElementById('sim-circolo-sel');
  if (!sel || sel.options.length > 1) return;
  try {
    const r = await fetch(PROXY_URL + '/api/campi', { headers: apiHeaders() });
    if (!r.ok) return;
    const data = await r.json();
    const circoli = (data.circoli || []).filter(c => c.nome);  // solo filtro nome
    circoli.sort((a, b) => a.nome.localeCompare(b.nome));
    circoli.forEach(c => {
      const opt = document.createElement('option');
      opt.value = c.nome; opt.textContent = c.nome;
      sel.appendChild(opt);
    });
    const lastGame = state.results && state.results[0];
    if (lastGame && lastGame.esecutore) {
      const match = circoli.find(c =>
        c.nome.includes(lastGame.esecutore.split(' ')[0].toUpperCase())
      );
      if (match) { sel.value = match.nome; simLoadPercorsi(); }
    }
  } catch(e) { console.warn('simPopulateCircoli error:', e); }
}

async function simLoadPercorsi() {
  const circoloNome = document.getElementById('sim-circolo-sel').value;
  const percSel = document.getElementById('sim-percorso-sel');
  const teeSel  = document.getElementById('sim-tee-sel');
  percSel.innerHTML = '<option value="">— Percorso —</option>';
  teeSel.innerHTML  = '<option value="">— Tee —</option>';
  teeSel.disabled   = true;
  percSel.disabled  = true;
  if (!circoloNome) return;
  try {
    const r = await fetch(PROXY_URL + '/api/campi/' + encodeURIComponent(circoloNome) + '/percorsi', { headers: apiHeaders() });
    if (!r.ok) return;
    const data = await r.json();
    (data.percorsi || []).forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = p.nome;
      opt.dataset.tees = JSON.stringify(p.tees || []);
      opt.dataset.par  = p.par || '';          // ← aggiungi
      percSel.appendChild(opt);
    });
    percSel.disabled = false;
    if (percSel.options.length === 2) { percSel.selectedIndex = 1; simLoadTees(); }
  } catch(e) {}
}

function simLoadTees() {
  const percSel = document.getElementById('sim-percorso-sel');
  const teeSel  = document.getElementById('sim-tee-sel');
  teeSel.innerHTML = '<option value="">— Tee —</option>';
  teeSel.disabled  = true;
  const selected = percSel.options[percSel.selectedIndex];
  if (!selected || !selected.dataset.tees) return;

  // Precompila par dal percorso
  const parEl = document.getElementById('sim-par');
  if (parEl && selected.dataset.par) parEl.value = selected.dataset.par;

  const tees = JSON.parse(selected.dataset.tees);
  tees.forEach(t => {
    if (!t.cr || !t.slope) return;
    const opt = document.createElement('option');
    opt.value = JSON.stringify({ cr: t.cr, sr: t.slope });
    opt.textContent = (t.tee_nome || 'Tee') + ' — CR ' + t.cr + ' / SR ' + t.slope;
    teeSel.appendChild(opt);
  });
  teeSel.disabled = false;
  if (teeSel.options.length === 2) { teeSel.selectedIndex = 1; simSelectTee(); }
}

function simSelectTee() {
  const teeSel = document.getElementById('sim-tee-sel');
  const val = teeSel.value;
  if (!val) return;
  try {
    const { cr, sr } = JSON.parse(val);
    document.getElementById('sim-cr').value = cr;
    document.getElementById('sim-sr').value = sr;
  } catch(e) {}
}

function openSimOverlay() {
  document.getElementById('sim-result').style.display = 'none';
  document.getElementById('sim-stbl').value = '';
  document.getElementById('sim-cr').value = '';
  document.getElementById('sim-sr').value = '';

  const hcp = parseFloat(state.user.hcp);
  document.getElementById('sim-curr-hcp').textContent = isNaN(hcp) ? '—' : hcp.toFixed(1);
  document.getElementById('sim-subtitle').textContent =
    'HCP attuale: ' + (isNaN(hcp) ? '—' : hcp.toFixed(1)) + ' · Inserisci i dati della prossima gara';

  document.getElementById('sim-overlay').classList.add('open');
  simPopulateCircoli();
}

function closeSimOverlay(e) {
  if (e.target === document.getElementById('sim-overlay'))
    document.getElementById('sim-overlay').classList.remove('open');
}

function runSimulation() {
  const stbl = parseInt(document.getElementById('sim-stbl').value);
  const cr   = parseFloat(document.getElementById('sim-cr').value);
  const sr   = parseFloat(document.getElementById('sim-sr').value);

  if (isNaN(stbl) || isNaN(cr) || isNaN(sr)) {
    alert('Inserisci tutti i valori richiesti');
    return;
  }

  const currentHcp = parseFloat(state.user.hcp);
  if (isNaN(currentHcp)) { alert('HCP attuale non disponibile'); return; }

  // Course Handicap = HCP Index × (SR/113) + (CR - par)
  const par = Math.round(cr);
  const courseHcp = Math.round(currentHcp * (sr / 113) + (cr - par));
  // AGS da Stableford: AGS = par + courseHcp + 36 - stbl
  const ags = par + courseHcp + 36 - stbl;
  // Score Differential = (113/SR) × (AGS - CR)  [PCC=0 per simulazione]
  const newSD = Math.round((113 / sr) * (ags - cr) * 10) / 10;

  // Prendi i 19 risultati validi più recenti (il 20° viene sostituito dalla simulazione)
  const valid = state.results.filter(r => isValida(r.valida, r.sd));
  const last19SDs = valid.slice(0, 19)
    .map(r => parseFloat((r.sd || '').replace(',', '.')))
    .filter(v => !isNaN(v) && v !== 0);

  // Il nuovo SD entra come più recente, il 20° (oldest) viene escluso
  const all20 = [newSD, ...last19SDs].slice(0, 20);

  const lowHcpRaw2 = state.user.profile && state.user.profile.lowHcpIndex;
  const lowHcp = lowHcpRaw2 ? parseFloat(String(lowHcpRaw2).replace(',','.')) : null;

  const calc = calcHcpIndex(all20, lowHcp, currentHcp, true); // exceptional score nella simulazione
  if (!calc) { alert('Dati insufficienti per il calcolo'); return; }

  const delta = Math.round((calc.hcp - currentHcp) * 10) / 10;
  const sign = delta > 0 ? '+' : '';

  document.getElementById('sim-new-hcp').textContent = calc.hcp.toFixed(1);
  document.getElementById('sim-curr-hcp').textContent = currentHcp.toFixed(1);

  const deltaEl = document.getElementById('sim-delta');
  deltaEl.textContent = sign + delta.toFixed(1) + ' rispetto all HCP attuale';
  deltaEl.style.background = delta < 0 ? 'rgba(76,175,80,0.15)' : delta > 0 ? 'rgba(229,115,115,0.15)' : 'rgba(255,255,255,0.06)';
  deltaEl.style.color = delta < 0 ? 'var(--green-light)' : delta > 0 ? 'var(--red-score)' : 'var(--gray-soft)';

  const detail = [
    'SD simulato: ' + newSD.toFixed(1),
    'AGS: ' + ags + ' · Course HCP: ' + courseHcp,
    'Top ' + calc.useCount + ' SD: ' + calc.top8.map(v => v.toFixed(1)).join(', '),
    'Media: ' + calc.avg8.toFixed(3),
  ];
  if (calc.exceptAdj < 0) detail.push('Exceptional Score adj: ' + calc.exceptAdj.toFixed(1));
  if (calc.capNote) detail.push(calc.capNote);

  document.getElementById('sim-detail').textContent = detail.join('  ·  ');
  document.getElementById('sim-result').style.display = 'block';
}

document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('btn-simula');
  if (btn) btn.addEventListener('click', openSimOverlay);

  // Su NETGOLF il login è gestito da Flask: se siamo qui siamo già loggati,
  // quindi facciamo partire direttamente il caricamento dei dati.
  tryAutoLogin();

  // Mostra pannello test se URL contiene ?test=1
  if (new URLSearchParams(window.location.search).get('test') === '1') {
    const panel = document.getElementById('hcp-test-panel');
    if (panel) panel.style.display = 'block';
  }
});

// ═══════════════════════════════════════════
// TABS
// ═══════════════════════════════════════════
document.querySelectorAll('.tab-btn, .nav-item').forEach(btn => {
  btn.addEventListener('click', () => {
    const tab = btn.dataset.tab || btn.dataset.nav;
    document.querySelectorAll('.tab-btn, .nav-item').forEach(b => {
      if (b.dataset.tab === tab || b.dataset.nav === tab) b.classList.add('active');
      else b.classList.remove('active');
    });
    document.querySelectorAll('.tab-panel').forEach(p => {
      p.classList.toggle('active', p.id === 'tab-' + tab);
    });
    // Re-render specifici tab quando vengono aperti
    if (tab === 'profilo') renderProfile();
    if (tab === 'hcpcalc' && state._dataLoaded) renderHcpCalc();

  });
});



// Logout
document.getElementById('btn-logout').addEventListener('click', async () => {
  // Logout via Flask: redirect server-side
  window.location.href = '/auth/logout'; return;
  // Cancella sessione salvata sul device
  try { localStorage.removeItem('scratch_session'); } catch(e) {}
  state = { user: null, sessionId: null, results: [], hcpHistory: [], activeFilter: 'all' };
  document.getElementById('screen-main').classList.remove('active');
  document.getElementById('screen-login').classList.add('active');
  document.getElementById('inp-pass').value = '';
  document.getElementById('login-error').style.display = 'none';
});

// ═══════════════════════════════════════════
// EXPORT
// ═══════════════════════════════════════════
document.getElementById('export-csv').addEventListener('click', () => exportCSV());

// Debug
document.getElementById('btn-debug').addEventListener('click', async () => {
  const out = document.getElementById('debug-output');
  out.style.display = 'block';
  out.textContent = 'Caricamento menu FIG...';
  try {
    const r = await fetch(PROXY_URL + '/api/debug-menu', { headers: apiHeaders() });
    const text = await r.text();
    out.textContent = text;
  } catch(e) {
    out.textContent = 'Errore: ' + e.message;
  }
});
document.getElementById('export-hcp-csv').addEventListener('click', () => exportHcpCSV());
document.getElementById('export-pdf').addEventListener('click', () => exportPDF());

// Tessera HCP NETGOLF: apre la pagina /tessera in una nuova tab.
// Il server fa fetch fresco del profilo FIG e renderizza la tessera; da lì
// l'utente fa Cmd+P / Ctrl+P per salvarla in PDF.
const _btnTessera = document.getElementById('export-tessera');
if (_btnTessera) _btnTessera.addEventListener('click', () => {
  // Prima di aprire, verifichiamo che ci sia uno user caricato. Se non c'è,
  // probabilmente il caricamento dati FIG non è ancora finito o è fallito:
  // in entrambi i casi conviene non andare avanti, perché il backend
  // chiederebbe comunque le credenziali FIG e se mancano restituirebbe un 400.
  if (!state.user || !state.user.profile) {
    alert('Profilo FIG non ancora caricato. Riprova tra qualche secondo.');
    return;
  }
  window.open('/tessera', '_blank', 'noopener');
});
 

function exportCSV() {
  if (!state.results.length) return alert('Nessun dato da esportare.');
  const rows = [['Data','Gara','Esecutore','Formula','Buche','Valida','Playing HCP','Stbl','AGS','PCC','SD','Corr','Index Vecchio','Index Nuovo','Variazione']];
  state.results.forEach(r => rows.push([
    r.data, r.gara, r.esecutore, r.formula, r.buche, r.valida,
    r.playingHcp, r.stbl, r.ags, r.pcc, r.sd, r.corr,
    r.indexVecchio, r.indexNuovo, r.variazione
  ]));
  downloadCSV(rows, 'risultati-golf.csv');
}

function exportHcpCSV() {
  if (!state.hcpHistory.length) return alert('Nessun dato handicap da esportare.');
  const rows = [['Data','Index']];
  state.hcpHistory.forEach(h => rows.push([h.date, h.value.toFixed(1)]));
  downloadCSV(rows, 'storico-index.csv');
}

function downloadCSV(rows, filename) {
  const csv = rows.map(r => r.map(c => `"${c}"`).join(',')).join('\n');
  const blob = new Blob(['\uFEFF'+csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

function exportPDF() {
  if (!state.results.length) return alert('Nessun dato da esportare.');

  const win = window.open('', '_blank');
  const rows = state.results.map(r =>
    '<tr><td>'+r.data+'</td><td>'+r.gara+'</td><td>'+r.esecutore+'</td><td>'+r.formula+'</td><td>'+r.buche+'</td><td>'+r.valida+'</td><td>'+r.playingHcp+'</td><td><strong>'+r.stbl+'</strong></td><td>'+r.indexVecchio+'</td><td><strong>'+r.indexNuovo+'</strong></td><td style="color:'+(parseFloat((r.variazione||'0').replace(',','.'))<0?'green':'red')+'"><strong>'+(parseFloat((r.variazione||'0').replace(',','.'))<0?'':'+') +r.variazione+'</strong></td></tr>'
  ).join('');

  win.document.write(`<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Risultati Golf</title>
  <style>
    body{font-family:Georgia,serif;padding:30px;color:#1a1a1a}
    h1{color:#2d6a2d;border-bottom:2px solid #2d6a2d;padding-bottom:10px}
    table{width:100%;border-collapse:collapse;margin-top:20px;font-size:13px}
    th{background:#2d6a2d;color:white;padding:10px;text-align:left}
    td{padding:8px 10px;border-bottom:1px solid #eee}
    tr:nth-child(even) td{background:#f9f9f9}
    @media print{body{padding:0}}
  </style></head><body>
  <h1>⛳ Storico Risultati Golf</h1>
  <p>Tesserato: <strong>${(state.user && state.user.displayName)}</strong> &nbsp;|&nbsp; Handicap attuale: <strong>${(state.user && state.user.hcp)}</strong></p>
  <table><thead><tr><th>Data</th><th>Gara</th><th>Circolo</th><th>Formato</th><th>Score</th><th>HCP</th></tr></thead>
  <tbody>${rows}</tbody></table>
  <script>setTimeout(()=>window.print(),400)<\/script>
  </body></html>`);
  win.document.close();
}

function findScorecardForResult(r) {
  console.log('[SC] state.scorecards:', state.scorecards?.length, JSON.stringify(state.scorecards));
  if (!state.scorecards || !state.scorecards.length) return null;
  const dataFig = r.data || ''; // "DD/MM/YYYY"
  // converti in YYYY-MM-DD
  const parts = dataFig.split('/');
  if (parts.length !== 3) return null;
  const dataIso = parts[2] + '-' + parts[1].padStart(2,'0') + '-' + parts[0].padStart(2,'0');
  const circoloFig = (r.esecutore || '').trim().toUpperCase();
  return state.scorecards.find(sc => {
    if (sc.data_gara !== dataIso) return false;
    const a = sc.circolo, b = circoloFig;
    return a && b && (a.includes(b) || b.includes(a));
  }) || null;
}

async function refreshScorecards() {
  try {
    const res = await fetch('/scorecard/api/scorecards-index', { headers: apiHeaders() });
    if (res.ok) {
      const data = await res.json();
      state.scorecards = data.scorecards || [];
      // Aggiorna la cache con i nuovi dati scorecard
      const cached = loadFromSessionCache();
      if (cached) {
        cached.scorecards = state.scorecards;
        saveToSessionCache(cached);
      }
    }
  } catch(e) {
    console.warn('refreshScorecards fallito:', e);
  }
}

// Esposto globalmente per poterlo chiamare da un bottone HTML
function forceRefreshData() {
  clearSessionCache();
  loadAllData(true);
}
 
/* ─────────────────────────────────────────────────────────────────────────
 * IMPORTANTE — prerequisito DOM:
 *
 * Le righe .sd-row dello storico DEVONO avere gli attributi:
 *     data-date="YYYY-MM-DD"
 *     data-circolo="NOME CIRCOLO"
 *
 * Se nel rendering corrente NON sono presenti, devi aggiungerli nella
 * funzione che genera l'HTML delle righe storico (cerca dove vengono
 * creati gli .sd-row, intorno alla riga 800-900 di dashboard.js).
 *
 * Esempio: se oggi hai
 *     '<div class="sd-row">...'
 * cambialo in
 *     '<div class="sd-row" data-date="' + r.data + '" data-circolo="' + (r.circolo || '') + '">...'
 *
 * (sostituendo r.data e r.circolo con i nomi dei campi reali del tuo
 * oggetto risultato).
 * ───────────────────────────────────────────────────────────────────────── */
