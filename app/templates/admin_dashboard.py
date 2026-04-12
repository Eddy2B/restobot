# ADMIN_DASHBOARD_HTML — super-admin dashboard
# Extracted from main.py during refactoring

ADMIN_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GuestScale — Super Admin</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32' fill='none'%3E%3Ccircle cx='10' cy='10' r='4' fill='%232D7DD2'/%3E%3Ccircle cx='22' cy='10' r='4' fill='%234ECDC4'/%3E%3Ccircle cx='16' cy='22' r='4' fill='%234ECDC4'/%3E%3Cline x1='13' y1='11' x2='19' y2='11' stroke='%232D7DD2' stroke-width='2'/%3E%3Cline x1='11' y1='13' x2='15' y2='19' stroke='%232D7DD2' stroke-width='2'/%3E%3Cline x1='21' y1='13' x2='17' y2='19' stroke='%234ECDC4' stroke-width='2'/%3E%3C/svg%3E">
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#F4F5F9;--card:#FFF;--t:#111827;--ts:#6B7280;--tm:#9CA3AF;--b:#E5E7EB;--bl:#F3F4F6;
  --ac:#2D7DD2;--ac2:#4ECDC4;--acg:linear-gradient(135deg,#2D7DD2,#4ECDC4);
  --ok:#4ECDC4;--okb:#E6FAF8;--wa:#F59E0B;--wab:#FFFBEB;--da:#EF4444;--dab:#FEF2F2;
  --f:'Plus Jakarta Sans',-apple-system,BlinkMacSystemFont,sans-serif;
  --shadow:0 1px 3px rgba(0,0,0,.06);--shadow-md:0 4px 6px rgba(0,0,0,.05);
  --radius:12px;
}
body{font-family:var(--f);background:var(--bg);color:var(--t);min-height:100vh;-webkit-font-smoothing:antialiased}

/* Login */
.lo{position:fixed;inset:0;background:#0F1117;display:flex;align-items:center;justify-content:center;z-index:100}
.lbox{text-align:center;width:380px}
.l-logo{display:flex;align-items:center;justify-content:center;gap:10px;margin-bottom:8px}
.l-icon{width:40px;height:40px;background:#1A1D27;border-radius:10px;display:flex;align-items:center;justify-content:center}
.l-icon svg{width:28px;height:28px}
.lwm{font-size:28px;font-weight:800;color:#fff;letter-spacing:-.03em}
.lsub{font-size:11px;color:#6B7280;letter-spacing:.12em;margin-bottom:36px;text-transform:uppercase}
.lcd{background:#1A1D27;border-radius:16px;padding:28px 24px;border:1px solid #252836}
.linp{width:100%;padding:13px 16px;border-radius:10px;background:#0F1117;border:1.5px solid #374151;font-size:14px;color:#F9FAFB;outline:none;font-family:var(--f);transition:border .2s;margin-bottom:8px}
.linp::placeholder{color:#6B7280}
.linp:focus{border-color:var(--ac)}
.lbtn{width:100%;padding:13px;border-radius:10px;border:none;background:var(--acg);color:#fff;font-size:14px;font-weight:700;cursor:pointer;font-family:var(--f);margin-top:8px;transition:opacity .2s}
.lbtn:hover{opacity:.9}
.lerr{color:var(--da);font-size:13px;margin-bottom:14px;display:none;background:#FEF2F220;padding:10px 14px;border-radius:10px;border:1px solid #EF444430}

/* Layout */
.app{display:none}
.app.v{display:flex;min-height:100vh}
.sidebar{width:240px;background:#0F1117;color:#fff;padding:20px 16px;display:flex;flex-direction:column;position:fixed;top:0;left:0;bottom:0;z-index:50}
.sb-logo{display:flex;align-items:center;gap:8px;margin-bottom:24px;padding:0 4px}
.sb-logo svg{width:28px;height:28px}
.sb-logo span{font-size:16px;font-weight:800;letter-spacing:-.02em}
.sb-nav{flex:1}
.sb-item{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:8px;color:#9CA3AF;font-size:13px;font-weight:600;cursor:pointer;transition:all .15s;margin-bottom:2px}
.sb-item:hover{background:#1A1D27;color:#fff}
.sb-item.active{background:#1A1D27;color:#fff}
.sb-item svg{width:18px;height:18px;opacity:.7}
.sb-item.active svg{opacity:1}
.sb-footer{font-size:11px;color:#4B5563;padding:8px 4px}
.main{margin-left:240px;flex:1;padding:24px 32px;max-width:1200px}

/* Topbar */
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px}
.topbar h1{font-size:20px;font-weight:800;letter-spacing:-.02em}
.topbar-actions{display:flex;gap:8px;align-items:center}

/* Components */
.btn{padding:8px 16px;border-radius:8px;border:none;font-size:13px;font-weight:600;cursor:pointer;font-family:var(--f);transition:all .15s}
.btn-sm{padding:5px 10px;font-size:11px;border-radius:6px}
.btn-xs{padding:3px 8px;font-size:10px;border-radius:5px}
.btn-primary{background:var(--acg);color:#fff}
.btn-primary:hover{opacity:.9}
.btn-ghost{background:transparent;color:var(--ts);border:1px solid var(--b)}
.btn-ghost:hover{background:var(--bl);color:var(--t)}
.btn-danger{background:var(--dab);color:var(--da);border:1px solid #FECACA}
.btn-danger:hover{background:#FEE2E2}
.btn-ok{background:var(--okb);color:#0D9488;border:1px solid #99F6E4}
.badge{display:inline-flex;padding:3px 10px;border-radius:20px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em}
.badge-ok{background:var(--okb);color:#0D9488}
.badge-wa{background:var(--wab);color:#D97706}
.badge-da{background:var(--dab);color:#DC2626}
.badge-ac{background:#EBF4FF;color:#2563EB}

/* KPIs */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin-bottom:24px}
.kpi{background:var(--card);border-radius:var(--radius);padding:16px;box-shadow:var(--shadow);border:1px solid var(--b)}
.kpi-val{font-size:26px;font-weight:800;letter-spacing:-.03em}
.kpi-label{font-size:10px;font-weight:700;color:var(--tm);text-transform:uppercase;letter-spacing:.06em;margin-top:2px}

/* Cards & Tables */
.card{background:var(--card);border-radius:var(--radius);box-shadow:var(--shadow);border:1px solid var(--b);overflow:hidden;margin-bottom:16px}
.card-h{padding:16px 20px;border-bottom:1px solid var(--b);display:flex;justify-content:space-between;align-items:center}
.card-h h2{font-size:14px;font-weight:700}
table{width:100%;border-collapse:collapse}
th{text-align:left;padding:8px 14px;font-size:10px;font-weight:700;color:var(--tm);text-transform:uppercase;letter-spacing:.06em;border-bottom:1px solid var(--b);background:#FAFBFC}
td{padding:10px 14px;font-size:12px;border-bottom:1px solid #F3F4F6;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:#F9FAFB}

/* Modal */
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:200;align-items:center;justify-content:center}
.modal.v{display:flex}
.modal-box{background:var(--card);border-radius:16px;max-width:700px;width:95%;max-height:90vh;overflow-y:auto;box-shadow:var(--shadow-md)}
.modal-h{padding:18px 24px;border-bottom:1px solid var(--b);display:flex;justify-content:space-between;align-items:center}
.modal-h h2{font-size:16px;font-weight:700}
.modal-close{background:none;border:none;font-size:22px;cursor:pointer;color:var(--tm);padding:4px 8px;border-radius:6px}
.modal-close:hover{background:var(--bl);color:var(--t)}
.modal-body{padding:20px 24px}
.modal-footer{padding:16px 24px;border-top:1px solid var(--b);display:flex;justify-content:flex-end;gap:8px}

/* Form */
.form-group{margin-bottom:14px}
.form-group label{display:block;font-size:11px;font-weight:700;color:var(--ts);text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px}
.form-group input,.form-group textarea,.form-group select{width:100%;padding:9px 12px;border:1.5px solid var(--b);border-radius:8px;font-size:13px;font-family:var(--f);color:var(--t);outline:none;transition:border .15s}
.form-group input:focus,.form-group textarea:focus{border-color:var(--ac)}
.form-group textarea{resize:vertical;min-height:60px}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:12px}

/* Tabs */
.tabs{display:flex;gap:0;border-bottom:2px solid var(--b);margin-bottom:16px}
.tab{padding:10px 18px;font-size:12px;font-weight:700;color:var(--tm);cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-2px;transition:all .15s}
.tab:hover{color:var(--t)}
.tab.active{color:var(--ac);border-color:var(--ac)}

/* Views */
.view{display:none}
.view.v{display:block}

/* Toast */
.toast{position:fixed;bottom:20px;right:20px;background:#1F2937;color:#fff;padding:12px 20px;border-radius:10px;font-size:13px;font-weight:600;z-index:300;opacity:0;transform:translateY(10px);transition:all .3s}
.toast.v{opacity:1;transform:translateY(0)}

/* Responsive */
@media(max-width:768px){
  .sidebar{display:none}
  .main{margin-left:0}
  .form-row{grid-template-columns:1fr}
}
</style>
</head>
<body>

<!-- LOGIN -->
<div class="lo" id="loginOverlay">
<div class="lbox">
  <div class="l-logo"><div class="l-icon"><svg viewBox="0 0 32 32" fill="none"><circle cx="10" cy="10" r="4" fill="#2D7DD2"/><circle cx="22" cy="10" r="4" fill="#4ECDC4"/><circle cx="16" cy="22" r="4" fill="#4ECDC4"/><line x1="13" y1="11" x2="19" y2="11" stroke="#2D7DD2" stroke-width="2"/><line x1="11" y1="13" x2="15" y2="19" stroke="#2D7DD2" stroke-width="2"/><line x1="21" y1="13" x2="17" y2="19" stroke="#4ECDC4" stroke-width="2"/></svg></div><span class="lwm">GuestScale</span></div>
  <div class="lsub">Super Admin</div>
  <div class="lcd">
    <div class="lerr" id="loginError">Clé invalide</div>
    <input class="linp" id="secretInput" type="password" placeholder="Clé admin" autofocus>
    <button class="lbtn" id="loginBtn">Accéder</button>
  </div>
</div>
</div>

<!-- APP -->
<div class="app" id="app">

<!-- Sidebar -->
<div class="sidebar">
  <div class="sb-logo">
    <svg viewBox="0 0 32 32" fill="none"><circle cx="10" cy="10" r="4" fill="#2D7DD2"/><circle cx="22" cy="10" r="4" fill="#4ECDC4"/><circle cx="16" cy="22" r="4" fill="#4ECDC4"/><line x1="13" y1="11" x2="19" y2="11" stroke="#2D7DD2" stroke-width="2"/><line x1="11" y1="13" x2="15" y2="19" stroke="#2D7DD2" stroke-width="2"/><line x1="21" y1="13" x2="17" y2="19" stroke="#4ECDC4" stroke-width="2"/></svg>
    <span>GuestScale</span>
  </div>
  <div class="sb-nav">
    <div class="sb-item active" data-nav="dashboard"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>Dashboard</div>
    <div class="sb-item" data-nav="restaurants"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3h18v18H3z"/><path d="M9 3v18M3 9h18"/></svg>Restaurants</div>
    <div class="sb-item" data-nav="bookings"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>Réservations</div>
    <div class="sb-item" data-nav="conversations"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>Conversations</div>
  </div>
  <div class="sb-footer">GuestScale Admin v5.0</div>
</div>

<!-- Main content -->
<div class="main">

<!-- Dashboard View -->
<div class="view v" id="view-dashboard">
  <div class="topbar"><h1>Dashboard</h1><div class="topbar-actions"><button class="btn btn-ghost btn-sm" onclick="loadAll()">Actualiser</button></div></div>
  <div class="kpis" id="kpis"></div>
  <div id="metricsArea" style="margin-bottom:20px"></div>
  <div id="chartsArea"></div>
  <div class="card">
    <div class="card-h"><h2>Restaurants</h2></div>
    <table><thead><tr><th>Restaurant</th><th>Statut</th><th>Réservations</th><th>Contacts</th><th>Messages</th><th>WhatsApp</th><th>Actions</th></tr></thead><tbody id="dashTbody"></tbody></table>
  </div>
</div>

<!-- Restaurants View -->
<div class="view" id="view-restaurants">
  <div class="topbar"><h1>Restaurants</h1></div>
  <div class="card">
    <table><thead><tr><th>Restaurant</th><th>Slug</th><th>Statut</th><th>Téléphone</th><th>WhatsApp</th><th>Tables</th><th>Actions</th></tr></thead><tbody id="restoTbody"></tbody></table>
  </div>
</div>

<!-- Bookings View -->
<div class="view" id="view-bookings">
  <div class="topbar"><h1>Réservations</h1><div class="topbar-actions"><select id="bookingRestoFilter" class="linp" style="width:200px;background:#fff;color:var(--t);border-color:var(--b);padding:6px 10px;font-size:12px"></select></div></div>
  <div class="card"><table><thead><tr><th>ID</th><th>Client</th><th>Date</th><th>Heure</th><th>Couverts</th><th>Table</th><th>Statut</th><th>Source</th><th>Actions</th></tr></thead><tbody id="bookingsTbody"></tbody></table></div>
</div>

<!-- Conversations View -->
<div class="view" id="view-conversations">
  <div class="topbar"><h1>Conversations</h1><div class="topbar-actions"><select id="convRestoFilter" class="linp" style="width:200px;background:#fff;color:var(--t);border-color:var(--b);padding:6px 10px;font-size:12px"></select></div></div>
  <div id="convList"></div>
</div>

</div><!-- /main -->
</div><!-- /app -->

<!-- Edit Restaurant Modal -->
<div class="modal" id="editModal">
<div class="modal-box">
  <div class="modal-h"><h2 id="editTitle">Modifier le restaurant</h2><button class="modal-close" onclick="closeEdit()">&times;</button></div>
  <div class="modal-body">
    <div class="tabs" id="editTabs">
      <div class="tab active" data-tab="general">Général</div>
      <div class="tab" data-tab="settings">Infos & Menu</div>
      <div class="tab" data-tab="whatsapp">WhatsApp & Twilio</div>
    </div>
    <div id="editTabContent"></div>
  </div>
  <div class="modal-footer">
    <button class="btn btn-ghost" onclick="closeEdit()">Annuler</button>
    <button class="btn btn-primary" onclick="saveEdit()">Enregistrer</button>
  </div>
</div>
</div>

<!-- Detail Modal -->
<div class="modal" id="detailModal">
<div class="modal-box" style="max-width:800px">
  <div class="modal-h"><h2 id="detailTitle">Détails</h2><button class="modal-close" onclick="closeDetail()">&times;</button></div>
  <div class="modal-body" id="detailContent"></div>
</div>
</div>

<!-- Confirm Dialog -->
<div class="modal" id="confirmDialog">
<div class="modal-box" style="max-width:400px">
  <div class="modal-h"><h2 id="confirmTitle">Confirmer</h2><button class="modal-close" onclick="closeConfirm()">&times;</button></div>
  <div class="modal-body"><p id="confirmText"></p></div>
  <div class="modal-footer"><button class="btn btn-ghost" onclick="closeConfirm()">Annuler</button><button class="btn btn-danger" id="confirmOk">Confirmer</button></div>
</div>
</div>

<!-- Toast -->
<div class="toast" id="toast"></div>

<script>
var secret=sessionStorage.getItem('gs_admin_secret')||'';
var restaurants=[];
var currentEditRid='';
var currentEditData={};
var currentTab='general';

// ===== AUTH =====
document.getElementById('loginBtn').onclick=doLogin;
document.getElementById('secretInput').onkeydown=function(e){if(e.key==='Enter')doLogin()};
function doLogin(fromStorage){
  // IMPORTANT : strict equality. Le bouton onclick passe un MouseEvent en
  // 1er arg, qui est truthy mais !== true → on lit bien l'input dans ce cas.
  if(fromStorage!==true){
    secret=document.getElementById('secretInput').value.trim();
  }
  if(!secret){document.getElementById('loginError').style.display='block';return}
  apiFetch('/api/admin/stats').then(function(r){
    if(r.status===401){
      // Stale secret in sessionStorage : purge silently and let user retype
      sessionStorage.removeItem('gs_admin_secret');
      secret='';
      if(fromStorage!==true){document.getElementById('loginError').style.display='block'}
      return;
    }
    return r.json();
  }).then(function(d){
    if(!d)return;
    sessionStorage.setItem('gs_admin_secret',secret);
    document.getElementById('loginOverlay').style.display='none';
    document.getElementById('app').classList.add('v');
    loadAll();setInterval(loadAll,15000);
  }).catch(function(){
    if(fromStorage!==true){document.getElementById('loginError').style.display='block'}
    sessionStorage.removeItem('gs_admin_secret');
    secret='';
  });
}
// Auto-login si on a un secret stocké en session (refresh page)
if(secret){doLogin(true)}
function apiFetch(url,opts){
  opts=opts||{};
  var sep=url.indexOf('?')>-1?'&':'?';
  return fetch(url+sep+'secret='+encodeURIComponent(secret),opts);
}

// ===== NAV =====
document.querySelectorAll('.sb-item[data-nav]').forEach(function(el){
  el.onclick=function(){
    document.querySelectorAll('.sb-item').forEach(function(e){e.classList.remove('active')});
    el.classList.add('active');
    var view=el.getAttribute('data-nav');
    document.querySelectorAll('.view').forEach(function(v){v.classList.remove('v')});
    document.getElementById('view-'+view).classList.add('v');
    if(view==='bookings')loadBookings();
    if(view==='conversations')loadConversations();
  }
});

// ===== DATA LOADING =====
function loadAll(){
  apiFetch('/api/admin/stats').then(function(r){return r.json()}).then(renderKPIs);
  apiFetch('/api/admin/metrics').then(function(r){return r.json()}).then(renderMetrics).catch(function(){});
  apiFetch('/api/admin/restaurants').then(function(r){return r.json()}).then(function(d){
    restaurants=d.restaurants||[];
    renderDashTable();
    renderRestoTable();
    renderFilters();
    // Auto-load bookings and conversations data
    if(document.getElementById('view-bookings').classList.contains('v'))loadBookings();
    if(document.getElementById('view-conversations').classList.contains('v'))loadConversations();
  });
}

function renderMetrics(m){
  if(!m||m.error)return;
  function fmtEur(v){return (v||0).toFixed(2).replace('.',',')+' \u20ac'}
  function mc(val,label,color){
    return '<div style="background:#fff;border-radius:12px;padding:18px 20px;box-shadow:0 1px 3px rgba(0,0,0,.06),0 2px 8px rgba(0,0,0,.03)"><div style="font-size:28px;font-weight:800;color:'+color+'">'+val+'</div><div style="font-size:12px;color:var(--tm);margin-top:4px">'+label+'</div></div>';
  }
  function ms(val,label){
    return '<div style="background:#fff;border-radius:10px;padding:14px 16px;box-shadow:0 1px 2px rgba(0,0,0,.04)"><div style="font-size:20px;font-weight:800;color:var(--t)">'+val+'</div><div style="font-size:11px;color:var(--tm);margin-top:2px">'+label+'</div></div>';
  }
  var churnColor=m.churn_rate_monthly>5?'#EF4444':'#22C55E';
  var h='<div class="card" style="margin-bottom:16px"><div class="card-h"><h2>Métriques Business</h2></div><div style="padding:20px">';
  // Ligne 1 — 4 KPIs principaux
  h+='<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:16px">';
  h+=mc(fmtEur(m.mrr),'Monthly Recurring Revenue','#22C55E');
  h+=mc(fmtEur(m.arr),'Annual Run Rate','#22C55E');
  h+=mc(m.total_clients_paying,'Abonnements actifs','#2D7DD2');
  h+=mc(m.churn_rate_monthly+'%','Taux de désabonnement mensuel',churnColor);
  h+='</div>';
  // Ligne 2 — 4 KPIs secondaires
  h+='<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px">';
  h+=ms(fmtEur(m.avg_revenue_per_user)+'/mois','Revenu moyen par client (ARPU)');
  h+=ms(fmtEur(m.ltv_estimate),'Lifetime Value (est. '+(m.churn_rate_monthly>0?'1/churn':'18 mois')+')');
  h+=ms(m.total_clients_trial,'Restaurants en essai gratuit');
  h+=ms(m.total_clients_expired,'Essais expirés (prospects)');
  h+='</div>';
  // Ligne 3 — Activité globale
  h+='<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px">';
  h+=ms(m.total_messages_sent,'Total messages IA');
  h+=ms(m.total_bookings,'Total réservations');
  h+=ms(m.total_contacts,'Total contacts CRM');
  h+=ms(fmtEur(m.wallet_revenue_total),'Revenus wallet WhatsApp');
  h+='</div>';
  h+='</div></div>';
  document.getElementById('metricsArea').innerHTML=h;
}

function renderKPIs(d){
  var h='';
  h+=kpi(d.mrr+'\u20ac','MRR','var(--ok)','mrr');
  h+=kpi(d.arr+'\u20ac','ARR','var(--ok)','arr');
  h+=kpi(d.active_count,'Actifs','var(--ok)','actifs');
  h+=kpi(d.trial_count,'En essai','var(--wa)','trial');
  h+=kpi(d.conversion_rate+'%','Conversion','var(--ac)','conversion');
  h+=kpi(d.churn_rate+'%','Churn','var(--da)','churn');
  h+=kpi(d.total_bookings,'Resas','var(--ac)','resas');
  h+=kpi(d.whatsapp_connected,'WhatsApp','#25D366','whatsapp');
  document.getElementById('kpis').innerHTML=h;
  lastStatsData=d;
  renderCharts(d);
}

function renderCharts(d){
  var c=document.getElementById('chartsArea');
  if(!c)return;
  var h='';
  h+='<div class="card" style="margin-bottom:16px"><div class="card-h"><h2>Revenue</h2></div><div style="padding:16px;display:grid;grid-template-columns:repeat(4,1fr);gap:12px;font-size:13px">';
  h+='<div><div style="color:var(--tm);font-size:10px;font-weight:700;text-transform:uppercase">MRR</div><div style="font-size:22px;font-weight:800;color:var(--ok)">'+d.mrr+'\u20ac</div></div>';
  h+='<div><div style="color:var(--tm);font-size:10px;font-weight:700;text-transform:uppercase">ARR</div><div style="font-size:22px;font-weight:800;color:var(--ok)">'+d.arr+'\u20ac</div></div>';
  h+='<div><div style="color:var(--tm);font-size:10px;font-weight:700;text-transform:uppercase">MRR potentiel</div><div style="font-size:22px;font-weight:800;color:var(--wa)">'+d.potential_mrr+'\u20ac</div></div>';
  h+='<div><div style="color:var(--tm);font-size:10px;font-weight:700;text-transform:uppercase">Prix/mois</div><div style="font-size:22px;font-weight:800;color:var(--ts)">'+d.price_per_month+'\u20ac</div></div>';
  h+='</div></div>';
  h+='<div class="card" style="margin-bottom:16px"><div class="card-h"><h2>Funnel</h2></div><div style="padding:16px;display:grid;grid-template-columns:repeat(5,1fr);gap:8px;text-align:center">';
  h+=fs(d.total_restaurants,'Total','var(--ac)');
  h+=fs(d.trial_count,'Trial','var(--wa)');
  h+=fs(d.active_count,'Actifs','var(--ok)');
  h+=fs(d.suspended_count||0,'Suspendus','var(--da)');
  h+=fs(d.cancelled_count||0,'Churned','#6B7280');
  h+='</div></div>';
  if(d.bookings_timeline&&d.bookings_timeline.length){
    h+='<div class="card" style="margin-bottom:16px"><div class="card-h"><h2>Reservations (30j)</h2></div><div style="padding:16px">'+mbc(d.bookings_timeline,'count','var(--ac)')+'</div></div>';
  }
  if(d.messages_timeline&&d.messages_timeline.length){
    h+='<div class="card" style="margin-bottom:16px"><div class="card-h"><h2>Messages IA (30j)</h2></div><div style="padding:16px">'+mbc(d.messages_timeline,'messages','var(--ok)')+'</div></div>';
  }
  h+='<div class="card" style="margin-bottom:16px"><div class="card-h"><h2>Usage</h2></div><div style="padding:16px;display:grid;grid-template-columns:repeat(4,1fr);gap:12px;font-size:13px">';
  h+='<div><div style="color:var(--tm);font-size:10px;font-weight:700;text-transform:uppercase">Messages total</div><div style="font-size:20px;font-weight:800">'+d.total_messages_alltime+'</div></div>';
  h+='<div><div style="color:var(--tm);font-size:10px;font-weight:700;text-transform:uppercase">Contacts</div><div style="font-size:20px;font-weight:800">'+d.total_contacts+'</div></div>';
  h+='<div><div style="color:var(--tm);font-size:10px;font-weight:700;text-transform:uppercase">Conversations</div><div style="font-size:20px;font-weight:800">'+d.total_conversations+'</div></div>';
  h+='<div><div style="color:var(--tm);font-size:10px;font-weight:700;text-transform:uppercase">Moy. resas/resto</div><div style="font-size:20px;font-weight:800">'+d.avg_bookings_per_resto+'</div></div>';
  h+='</div></div>';
  if(d.restaurant_performance&&d.restaurant_performance.length){
    h+='<div class="card"><div class="card-h"><h2>Performance par restaurant</h2></div>';
    h+='<table><thead><tr><th>Restaurant</th><th>Statut</th><th>Resas</th><th>Contacts</th><th>Messages</th><th>WhatsApp</th></tr></thead><tbody>';
    d.restaurant_performance.forEach(function(rp){
      var st=rp.status==='active'?'<span class="badge badge-ok">Actif</span>':rp.status==='trial'?'<span class="badge badge-wa">Essai</span>':'<span class="badge badge-da">'+rp.status+'</span>';
      var wa=rp.whatsapp?'<span style="color:var(--ok)">OK</span>':'--';
      h+='<tr><td style="font-weight:600">'+esc(rp.name)+'</td><td>'+st+'</td><td>'+rp.bookings+'</td><td>'+rp.contacts+'</td><td>'+rp.messages+'</td><td>'+wa+'</td></tr>';
    });
    h+='</tbody></table></div>';
  }
  c.innerHTML=h;
}
function fs(v,l,c){return '<div><div style="font-size:28px;font-weight:800;color:'+c+'">'+v+'</div><div style="font-size:10px;font-weight:700;color:var(--tm);text-transform:uppercase">'+l+'</div></div>'}
function mbc(data,field,color){
  if(!data||!data.length)return '';
  var max=Math.max.apply(null,data.map(function(d){return d[field]||0}));
  if(max===0)max=1;
  var bw=Math.max(4,Math.floor(600/data.length)-2);
  var h='<div style="display:flex;align-items:flex-end;gap:2px;height:80px">';
  data.forEach(function(d){var val=d[field]||0;var pct=Math.max(2,Math.round(val/max*100));h+='<div title="'+esc(d.date)+': '+val+'" style="width:'+bw+'px;height:'+pct+'%;background:'+color+';border-radius:3px 3px 0 0;opacity:0.8"></div>'});
  h+='</div><div style="display:flex;justify-content:space-between;font-size:9px;color:var(--tm);margin-top:4px"><span>'+esc(data[0].date||'')+'</span><span>'+esc(data[data.length-1].date||'')+'</span></div>';
  return h;
}

function kpi(v,l,c,key){return '<div class="kpi" data-kpi="'+(key||'')+'" style="cursor:pointer;transition:transform .1s"><div class="kpi-val" style="color:'+c+'">'+v+'</div><div class="kpi-label">'+l+'</div></div>'}

// ===== DASHBOARD TABLE =====
function renderDashTable(){
  var h='';
  restaurants.forEach(function(r){
    var st=statusBadge(r.status);
    var wa=r.whatsapp_connected?'<span style="color:var(--ok)">✓ Connecté</span>':'<span style="color:var(--tm)">—</span>';
    h+='<tr>';
    h+='<td><div style="font-weight:700">'+esc(r.name)+'</div><div style="font-size:11px;color:var(--tm)">/'+esc(r.slug)+'</div></td>';
    h+='<td>'+st+'</td>';
    h+='<td><strong>'+r.total_bookings+'</strong> <span style="font-size:10px;color:var(--tm)">('+r.bookings_today+' auj.)</span></td>';
    h+='<td>'+r.total_contacts+'</td>';
    h+='<td>'+r.messages_today+'</td>';
    h+='<td>'+wa+'</td>';
    h+='<td><div style="display:flex;gap:4px">';
    h+='<button class="btn btn-ghost btn-xs" data-action="detail" data-id="'+r.id+'">Détails</button>';
    h+='<button class="btn btn-ghost btn-xs" data-action="edit" data-id="'+r.id+'">Modifier</button>';
    h+='<button class="btn btn-danger btn-xs" data-action="delete" data-id="'+r.id+'" data-name="'+esc(r.name)+'">Suppr.</button>';
    h+='</div></td>';
    h+='</tr>';
  });
  if(!restaurants.length) h='<tr><td colspan="7" style="text-align:center;padding:30px;color:var(--tm)">Aucun restaurant</td></tr>';
  document.getElementById('dashTbody').innerHTML=h;
}

// ===== RESTAURANTS TABLE =====
function renderRestoTable(){
  var h='';
  restaurants.forEach(function(r){
    var st=statusBadge(r.effective_status||r.status);
    var wa=r.whatsapp_connected?'<span style="color:var(--ok)">✓</span>':'<span style="color:var(--tm)">—</span>';
    h+='<tr>';
    h+='<td style="font-weight:700">'+esc(r.name)+'</td>';
    h+='<td style="font-size:11px;color:var(--tm)">/'+esc(r.slug)+'</td>';
    h+='<td>'+st+'</td>';
    h+='<td style="font-size:11px">'+esc(r.owner_phone||'—')+'</td>';
    h+='<td>'+wa+'</td>';
    h+='<td>'+r.tables_count+'</td>';
    h+='<td><div style="display:flex;gap:4px;flex-wrap:wrap">';
    h+='<button class="btn btn-ghost btn-xs" data-action="edit" data-id="'+r.id+'">Modifier</button>';
    h+='<button class="btn btn-ok btn-xs" data-action="setstatus" data-id="'+r.id+'" data-newstatus="active">Activer</button>';
    h+='<button class="btn btn-ghost btn-xs" data-action="extendtrial" data-id="'+r.id+'" data-name="'+esc(r.name)+'">Offrir essai</button>';
    h+='<button class="btn btn-danger btn-xs" data-action="setstatus" data-id="'+r.id+'" data-newstatus="suspended">Suspendre</button>';
    h+='<button class="btn btn-danger btn-xs" data-action="delete" data-id="'+r.id+'" data-name="'+esc(r.name)+'">Supprimer</button>';
    h+='</div></td>';
    h+='</tr>';
  });
  document.getElementById('restoTbody').innerHTML=h;
}

function statusBadge(s){
  if(s==='trial') return '<span class="badge badge-wa">Essai</span>';
  if(s==='active') return '<span class="badge badge-ok">Actif</span>';
  if(s==='suspended') return '<span class="badge badge-da">Suspendu</span>';
  if(s==='expired') return '<span class="badge badge-da">Expiré</span>';
  if(s==='canceled'||s==='cancelled') return '<span class="badge badge-da">Résilié</span>';
  return '<span class="badge badge-ac">'+s+'</span>';
}

// ===== FILTERS =====
function renderFilters(){
  var opts='<option value="">Tous les restaurants</option>';
  restaurants.forEach(function(r){opts+='<option value="'+r.id+'">'+esc(r.name)+'</option>'});
  var bf=document.getElementById('bookingRestoFilter');
  var cf=document.getElementById('convRestoFilter');
  if(bf)bf.innerHTML=opts;
  if(cf)cf.innerHTML=opts;
}

// ===== EDIT RESTAURANT =====
function openEdit(rid){
  currentEditRid=rid;
  currentTab='general';
  apiFetch('/api/admin/restaurant/'+rid).then(function(r){return r.json()}).then(function(d){
    if(d.error){showToast('Erreur: '+d.error);return}
    currentEditData=d;
    document.getElementById('editTitle').textContent='Modifier — '+d.restaurant.name;
    renderEditTab();
    document.getElementById('editModal').classList.add('v');
  });
}
function closeEdit(){document.getElementById('editModal').classList.remove('v');currentEditRid=''}

document.getElementById('editTabs').onclick=function(e){
  var tab=e.target.getAttribute('data-tab');
  if(!tab)return;
  currentTab=tab;
  document.querySelectorAll('#editTabs .tab').forEach(function(t){t.classList.remove('active')});
  e.target.classList.add('active');
  renderEditTab();
};

function renderEditTab(){
  var r=currentEditData.restaurant;
  var s=r.settings||{};
  var u=currentEditData.user;
  var h='';
  if(currentTab==='general'){
    h+='<div class="form-row">';
    h+='<div class="form-group"><label>Nom</label><input id="ef-name" value="'+esc(r.name||'')+'"></div>';
    h+='<div class="form-group"><label>Slug</label><input id="ef-slug" value="'+esc(r.slug||'')+'"></div>';
    h+='</div>';
    h+='<div class="form-row">';
    h+='<div class="form-group"><label>Téléphone propriétaire</label><input id="ef-owner_phone" value="'+esc(r.owner_phone||'')+'"></div>';
    h+='<div class="form-group"><label>Google Review Link</label><input id="ef-google_review_link" value="'+esc(r.google_review_link||'')+'"></div>';
    h+='</div>';
    h+='<div class="form-row">';
    h+='<div class="form-group"><label>Statut</label><select id="ef-status"><option value="trial"'+(r.status==='trial'?' selected':'')+'>Essai</option><option value="active"'+(r.status==='active'?' selected':'')+'>Actif</option><option value="suspended"'+(r.status==='suspended'?' selected':'')+'>Suspendu</option><option value="cancelled"'+(r.status==='cancelled'?' selected':'')+'>Annulé</option></select></div>';
    h+='<div class="form-group"><label>Fin essai</label><input type="text" disabled value="'+(r.trial_ends_at?new Date(r.trial_ends_at).toLocaleDateString('fr-FR'):'—')+'"></div>';
    h+='</div>';
    if(u){
      h+='<div style="margin-top:16px;padding:14px;background:var(--bl);border-radius:8px">';
      h+='<div style="font-size:11px;font-weight:700;color:var(--ts);text-transform:uppercase;margin-bottom:6px">Propriétaire</div>';
      h+='<div style="font-size:13px"><strong>'+esc(u.first_name+' '+u.last_name)+'</strong> · '+esc(u.email)+'</div>';
      h+='</div>';
    }
  } else if(currentTab==='settings'){
    h+='<div class="form-group"><label>Description</label><textarea id="ef-description" rows="3">'+esc(s.description||'')+'</textarea></div>';
    h+='<div class="form-row">';
    h+='<div class="form-group"><label>Adresse</label><input id="ef-address" value="'+esc(s.address||'')+'"></div>';
    h+='<div class="form-group"><label>Téléphone restaurant</label><input id="ef-phone" value="'+esc(s.phone||'')+'"></div>';
    h+='</div>';
    h+='<div class="form-group"><label>Horaires</label><textarea id="ef-hours" rows="2">'+esc(s.hours||'')+'</textarea></div>';
    h+='<div class="form-group"><label>Menu</label><textarea id="ef-menu" rows="6">'+esc(s.menu||'')+'</textarea></div>';
    h+='<div class="form-group"><label>Ton IA</label><textarea id="ef-tone" rows="2">'+esc(s.tone||'')+'</textarea></div>';
    h+='<div class="form-row">';
    h+='<div class="form-group"><label>Langues</label><input id="ef-languages" value="'+esc(s.languages||'')+'"></div>';
    h+='<div class="form-group"><label>Lien de réservation</label><input id="ef-booking_link" value="'+esc(s.booking_link||'')+'"></div>';
    h+='</div>';
    h+='<div class="form-group"><label>Infos spéciales</label><textarea id="ef-special_info" rows="2">'+esc(s.special_info||'')+'</textarea></div>';
    h+='<div class="form-group"><label>Politique allergènes</label><input id="ef-allergens_policy" value="'+esc(s.allergens_policy||'')+'"></div>';
  } else if(currentTab==='whatsapp'){
    h+='<div class="form-group"><label>WhatsApp Phone Number ID</label><input id="ef-whatsapp_phone_number_id" value="'+esc(r.whatsapp_phone_number_id||'')+'"></div>';
    h+='<div class="form-group"><label>WhatsApp Access Token</label><textarea id="ef-whatsapp_access_token" rows="2" style="font-size:11px;word-break:break-all">'+esc(r.whatsapp_access_token||'')+'</textarea></div>';
    h+='<div class="form-group"><label>Numéro Twilio</label><input id="ef-twilio_number" value="'+esc(s.twilio_number||'')+'"></div>';
    h+='<div style="margin-top:16px;padding:14px;background:var(--bl);border-radius:8px">';
    h+='<div style="font-size:11px;font-weight:700;color:var(--ts);margin-bottom:6px">STATUT WHATSAPP</div>';
    h+='<div style="font-size:13px">'+(r.whatsapp_phone_number_id?'<span style="color:var(--ok)">✓ Connecté</span> — Phone ID: '+esc(r.whatsapp_phone_number_id):'<span style="color:var(--da)">✗ Non connecté</span>')+'</div>';
    h+='</div>';
  }
  document.getElementById('editTabContent').innerHTML=h;
}

function saveEdit(){
  var r=currentEditData.restaurant;
  var payload={};
  if(currentTab==='general'){
    payload.name=gv('ef-name');
    payload.slug=gv('ef-slug');
    payload.owner_phone=gv('ef-owner_phone');
    payload.google_review_link=gv('ef-google_review_link');
    var newStatus=gv('ef-status');
    if(newStatus!==r.status){
      apiFetch('/api/admin/restaurant/'+currentEditRid+'/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:newStatus})});
    }
  } else if(currentTab==='settings'){
    payload.settings={
      description:gv('ef-description'),address:gv('ef-address'),phone:gv('ef-phone'),
      hours:gv('ef-hours'),menu:gv('ef-menu'),tone:gv('ef-tone'),
      languages:gv('ef-languages'),booking_link:gv('ef-booking_link'),
      special_info:gv('ef-special_info'),allergens_policy:gv('ef-allergens_policy')
    };
  } else if(currentTab==='whatsapp'){
    payload.whatsapp_phone_number_id=gv('ef-whatsapp_phone_number_id');
    payload.whatsapp_access_token=gv('ef-whatsapp_access_token');
    payload.settings={twilio_number:gv('ef-twilio_number')};
  }
  apiFetch('/api/admin/restaurant/'+currentEditRid,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}).then(function(r){return r.json()}).then(function(d){
    if(d.status==='ok'){showToast('Restaurant mis à jour');closeEdit();loadAll()}
    else showToast('Erreur: '+(d.error||''));
  });
}

// ===== DETAIL =====
function openDetail(rid){
  apiFetch('/api/admin/restaurant/'+rid).then(function(r){return r.json()}).then(function(d){
    if(d.error){showToast(d.error);return}
    var r=d.restaurant;var s=r.settings||{};var u=d.user;
    var trial=r.trial_ends_at?new Date(r.trial_ends_at).toLocaleDateString('fr-FR',{day:'numeric',month:'long',year:'numeric'}):'—';
    var created=r.created_at?new Date(r.created_at).toLocaleDateString('fr-FR',{day:'numeric',month:'long',year:'numeric'}):'—';
    var h='';
    // KPIs
    h+='<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:20px">';
    h+='<div class="kpi"><div class="kpi-val" style="color:var(--ac)">'+d.bookings_count+'</div><div class="kpi-label">Réservations</div></div>';
    h+='<div class="kpi"><div class="kpi-val" style="color:var(--ok)">'+d.contacts_count+'</div><div class="kpi-label">Contacts</div></div>';
    h+='<div class="kpi"><div class="kpi-val" style="color:var(--wa)">'+(d.stats_today.messages_today||0)+'</div><div class="kpi-label">Messages auj.</div></div>';
    h+='<div class="kpi"><div class="kpi-val" style="color:var(--ts)">'+(d.tables||[]).length+'</div><div class="kpi-label">Tables</div></div>';
    h+='</div>';
    // Info
    h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:12px">';
    h+=drow('Nom',r.name);h+=drow('Slug','/'+r.slug);h+=drow('Statut',r.status);h+=drow('Créé le',created);
    h+=drow('Fin essai',trial);h+=drow('Adresse',s.address||'—');h+=drow('Téléphone',s.phone||'—');
    h+=drow('WhatsApp',r.whatsapp_phone_number_id?'Connecté':'Non');h+=drow('Google Review',r.google_review_link?'Oui':'Non');
    h+=drow('Menu',s.menu?'Oui ('+s.menu.length+' car.)':'Non');
    h+='</div>';
    if(u){
      h+='<div style="margin-top:16px;padding:12px;background:var(--bl);border-radius:8px;font-size:12px">';
      h+='<strong>Propriétaire :</strong> '+esc(u.first_name+' '+u.last_name)+' · '+esc(u.email);
      h+='</div>';
    }
    h+='<div style="margin-top:16px;display:flex;gap:8px">';
    h+='<button class="btn btn-primary btn-sm" data-action="editfromdetail" data-id="'+rid+'">Modifier</button>';
    h+='<a href="/dashboard/'+r.slug+'" target="_blank" class="btn btn-ghost btn-sm" style="text-decoration:none">Ouvrir le dashboard</a>';
    h+='</div>';
    document.getElementById('detailTitle').textContent=r.name;
    document.getElementById('detailContent').innerHTML=h;
    document.getElementById('detailModal').classList.add('v');
  });
}
function closeDetail(){document.getElementById('detailModal').classList.remove('v')}
function drow(k,v){return '<div style="padding:6px 0;border-bottom:1px solid var(--b)"><span style="color:var(--tm);font-weight:600">'+k+'</span><br><span>'+esc(String(v||'—'))+'</span></div>'}

// ===== BOOKINGS =====
function loadBookings(){
  var sel=document.getElementById('bookingRestoFilter');
  var rid=sel.value;
  if(!rid && restaurants.length){rid=restaurants[0].id;sel.value=rid}
  if(!rid)return;
  apiFetch('/api/admin/restaurant/'+rid+'/bookings').then(function(r){return r.json()}).then(function(d){
    var bks=(d.bookings||[]).slice().reverse();
    var h='';
    bks.forEach(function(b){
      var stClass=b.status==='confirmed'?'badge-ok':b.status==='cancelled'?'badge-da':'badge-wa';
      h+='<tr>';
      h+='<td style="font-size:11px;color:var(--tm)">'+esc(b.id||'')+'</td>';
      h+='<td><strong>'+esc(b.name||'')+'</strong><div style="font-size:10px;color:var(--tm)">'+esc(b.phone||'')+'</div></td>';
      h+='<td>'+esc(b.date||'')+'</td>';
      h+='<td>'+esc(b.booking_time||b.time||'—')+'</td>';
      h+='<td>'+esc(String(b.covers||''))+'</td>';
      h+='<td>'+esc(b.table||'—')+'</td>';
      h+='<td><span class="badge '+stClass+'">'+esc(b.status||'')+'</span></td>';
      h+='<td style="font-size:10px">'+esc(b.source||'')+'</td>';
      h+='<td><button class="btn btn-danger btn-xs" data-action="delbooking" data-rid="'+rid+'" data-bid="'+esc(b.id)+'">Suppr.</button></td>';
      h+='</tr>';
    });
    if(!bks.length) h='<tr><td colspan="9" style="text-align:center;padding:24px;color:var(--tm)">Aucune réservation</td></tr>';
    document.getElementById('bookingsTbody').innerHTML=h;
  });
}
document.getElementById('bookingRestoFilter').onchange=loadBookings;

function deleteBooking(rid,bid){
  if(!confirm('Supprimer la réservation '+bid+' ?'))return;
  apiFetch('/api/admin/restaurant/'+rid+'/booking/'+bid,{method:'DELETE'}).then(function(r){return r.json()}).then(function(d){
    if(d.status==='ok'){showToast('Réservation supprimée');loadBookings();loadAll()}
    else showToast('Erreur: '+(d.error||''));
  });
}

// ===== CONVERSATIONS =====
function loadConversations(){
  var sel=document.getElementById('convRestoFilter');
  var rid=sel.value;
  if(!rid && restaurants.length){rid=restaurants[0].id;sel.value=rid}
  if(!rid)return;
  apiFetch('/api/admin/restaurant/'+rid+'/conversations').then(function(r){return r.json()}).then(function(d){
    var convs=d.conversations||{};
    var keys=Object.keys(convs);
    var h='';
    if(!keys.length){h='<div style="text-align:center;padding:40px;color:var(--tm)">Aucune conversation</div>';document.getElementById('convList').innerHTML=h;return}
    keys.forEach(function(phone){
      var msgs=convs[phone];
      var last=msgs[msgs.length-1];
      h+='<div class="card conv-toggle" style="cursor:pointer">';
      h+='<div class="card-h"><h2>'+esc(phone)+'</h2><span style="font-size:11px;color:var(--tm)">'+msgs.length+' messages</span></div>';
      h+='<div class="conv-msgs" style="display:none;padding:16px;max-height:400px;overflow-y:auto">';
      msgs.forEach(function(m){
        var isUser=m.role==='user';
        var bg=isUser?'#EBF4FF':'#F0FDF4';
        var label=isUser?'Client':'IA';
        var time=m.timestamp?new Date(m.timestamp).toLocaleString('fr-FR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):'';
        h+='<div style="margin-bottom:8px;padding:10px 14px;background:'+bg+';border-radius:10px">';
        h+='<div style="font-size:10px;font-weight:700;color:var(--tm);margin-bottom:4px">'+label+' · '+time+'</div>';
        h+='<div style="font-size:12px">'+esc(m.content)+'</div>';
        h+='</div>';
      });
      h+='</div></div>';
    });
    document.getElementById('convList').innerHTML=h;
  });
}
document.getElementById('convRestoFilter').onchange=loadConversations;

// ===== STATUS =====
function setStatus(rid,s){
  apiFetch('/api/admin/restaurant/'+rid+'/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:s})}).then(function(r){return r.json()}).then(function(d){
    if(d.status==='ok'){showToast('Statut: '+s);loadAll()}
    else showToast('Erreur: '+(d.error||'inconnue'));
  });
}

// ===== EXTEND TRIAL =====
function extendTrial(rid,name){
  var input=window.prompt('Combien de jours d\\'essai offrir à '+name+' ?','30');
  if(input===null)return;
  var days=parseInt(input,10);
  if(!days||days<1||days>365){showToast('Nombre de jours invalide (1-365)');return}
  apiFetch('/api/admin/restaurant/'+rid+'/extend-trial',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({days:days})}).then(function(r){return r.json()}).then(function(d){
    if(d.status==='ok'){
      var endDate=new Date(d.trial_ends_at).toLocaleDateString('fr-FR',{day:'numeric',month:'long',year:'numeric'});
      showToast('Essai prolongé de '+days+' jours (jusqu\\'au '+endDate+')');
      loadAll();
    } else {
      showToast('Erreur: '+(d.error||'inconnue'));
    }
  });
}

// ===== DELETE =====
var pendingDeleteId='';
function confirmDelete(rid,name){
  pendingDeleteId=rid;
  document.getElementById('confirmTitle').textContent='Supprimer '+name+' ?';
  document.getElementById('confirmText').textContent='Action irréversible. Toutes les données seront supprimées.';
  document.getElementById('confirmDialog').classList.add('v');
}
function closeConfirm(){document.getElementById('confirmDialog').classList.remove('v');pendingDeleteId=''}
document.getElementById('confirmOk').onclick=function(){
  if(!pendingDeleteId)return;
  closeConfirm();
  apiFetch('/api/admin/restaurant/'+pendingDeleteId,{method:'DELETE'}).then(function(r){return r.json()}).then(function(d){
    if(d.status==='ok'){showToast('Restaurant supprimé');loadAll()}
    else showToast('Erreur');
    pendingDeleteId='';
  });
};

// ===== UTILS =====
function gv(id){var e=document.getElementById(id);return e?e.value:''}
function esc(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML}
function showToast(msg){var t=document.getElementById('toast');t.textContent=msg;t.classList.add('v');setTimeout(function(){t.classList.remove('v')},3000)}

// Close modals on backdrop click
document.querySelectorAll('.modal').forEach(function(m){
  m.onclick=function(e){if(e.target===this)this.classList.remove('v')}
});

// ===== KPI CLICK DETAIL =====
var lastStatsData=null;
document.getElementById('kpis').addEventListener('click',function(e){
  var kpiEl=e.target.closest('[data-kpi]');
  if(!kpiEl||!kpiEl.getAttribute('data-kpi'))return;
  var key=kpiEl.getAttribute('data-kpi');
  if(lastStatsData)showKPIDetail(key,lastStatsData);
});

function showKPIDetail(key,d){
  var h='';var title='';
  var rp=d.restaurant_performance||[];
  
  if(key==='mrr'){
    title='MRR - Monthly Recurring Revenue';
    h+='<div style="font-size:36px;font-weight:800;color:var(--ok);margin-bottom:16px">'+d.mrr+'\u20ac/mois</div>';
    h+='<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:20px">';
    h+='<div style="padding:12px;background:var(--bl);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">Actifs</div><div style="font-size:20px;font-weight:800">'+d.active_count+'</div></div>';
    h+='<div style="padding:12px;background:var(--bl);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">Prix unitaire</div><div style="font-size:20px;font-weight:800">'+d.price_per_month+'\u20ac</div></div>';
    h+='<div style="padding:12px;background:var(--bl);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">MRR potentiel</div><div style="font-size:20px;font-weight:800;color:var(--wa)">'+d.potential_mrr+'\u20ac</div></div>';
    h+='</div>';
    h+='<h3 style="font-size:13px;font-weight:700;margin-bottom:8px">Revenus par restaurant</h3>';
    h+='<table><thead><tr><th>Restaurant</th><th>Statut</th><th>MRR</th></tr></thead><tbody>';
    rp.forEach(function(r){
      var mrr=r.status==='active'?d.price_per_month:0;
      h+='<tr><td style="font-weight:600">'+esc(r.name)+'</td><td>'+statusBadge(r.status)+'</td><td style="font-weight:700;color:'+(mrr>0?'var(--ok)':'var(--tm)')+'">'+mrr+'\u20ac</td></tr>';
    });
    h+='</tbody></table>';
  }
  
  else if(key==='arr'){
    title='ARR - Annual Recurring Revenue';
    h+='<div style="font-size:36px;font-weight:800;color:var(--ok);margin-bottom:16px">'+d.arr+'\u20ac/an</div>';
    h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px">';
    h+='<div style="padding:12px;background:var(--bl);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">MRR actuel</div><div style="font-size:20px;font-weight:800">'+d.mrr+'\u20ac</div></div>';
    h+='<div style="padding:12px;background:var(--bl);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">ARR potentiel (si tous convertis)</div><div style="font-size:20px;font-weight:800;color:var(--wa)">'+(d.potential_mrr*12)+'\u20ac</div></div>';
    h+='</div>';
    h+='<div style="padding:16px;background:var(--bl);border-radius:8px;margin-bottom:16px">';
    h+='<div style="font-size:12px;color:var(--ts)">Projection : si chaque mois vous ajoutez <strong>5 restaurants</strong>, ARR dans 12 mois :</div>';
    var projected=0;for(var m=1;m<=12;m++){projected+=(d.active_count+m*5)*d.price_per_month}
    h+='<div style="font-size:28px;font-weight:800;color:var(--ac);margin-top:4px">~'+Math.round(projected/1000)+'K\u20ac</div>';
    h+='</div>';
  }
  
  else if(key==='actifs'){
    title='Restaurants actifs';
    h+='<table><thead><tr><th>Restaurant</th><th>Resas</th><th>Contacts</th><th>Messages</th><th>WhatsApp</th></tr></thead><tbody>';
    rp.filter(function(r){return r.status==='active'}).forEach(function(r){
      h+='<tr><td style="font-weight:600">'+esc(r.name)+'</td><td>'+r.bookings+'</td><td>'+r.contacts+'</td><td>'+r.messages+'</td><td>'+(r.whatsapp?'<span style="color:var(--ok)">Oui</span>':'Non')+'</td></tr>';
    });
    h+='</tbody></table>';
    if(!rp.filter(function(r){return r.status==='active'}).length) h='<p style="color:var(--tm);padding:20px;text-align:center">Aucun restaurant actif</p>';
  }
  
  else if(key==='trial'){
    title='Restaurants en essai';
    h+='<table><thead><tr><th>Restaurant</th><th>Resas</th><th>Messages</th><th>WhatsApp</th></tr></thead><tbody>';
    rp.filter(function(r){return r.status==='trial'}).forEach(function(r){
      h+='<tr><td style="font-weight:600">'+esc(r.name)+'</td><td>'+r.bookings+'</td><td>'+r.messages+'</td><td>'+(r.whatsapp?'<span style="color:var(--ok)">Oui</span>':'Non')+'</td></tr>';
    });
    h+='</tbody></table>';
  }
  
  else if(key==='conversion'){
    title='Taux de conversion';
    h+='<div style="font-size:36px;font-weight:800;color:var(--ac);margin-bottom:16px">'+d.conversion_rate+'%</div>';
    h+='<div style="display:flex;align-items:center;gap:16px;margin-bottom:20px">';
    h+='<div style="flex:1;text-align:center;padding:16px;background:var(--bl);border-radius:8px"><div style="font-size:24px;font-weight:800;color:var(--ac)">'+d.total_restaurants+'</div><div style="font-size:10px;color:var(--tm);font-weight:700">INSCRITS</div></div>';
    h+='<div style="font-size:24px;color:var(--tm)">&rarr;</div>';
    h+='<div style="flex:1;text-align:center;padding:16px;background:var(--bl);border-radius:8px"><div style="font-size:24px;font-weight:800;color:var(--wa)">'+d.trial_count+'</div><div style="font-size:10px;color:var(--tm);font-weight:700">EN ESSAI</div></div>';
    h+='<div style="font-size:24px;color:var(--tm)">&rarr;</div>';
    h+='<div style="flex:1;text-align:center;padding:16px;background:var(--okb);border-radius:8px"><div style="font-size:24px;font-weight:800;color:var(--ok)">'+d.active_count+'</div><div style="font-size:10px;color:var(--tm);font-weight:700">ACTIFS</div></div>';
    h+='</div>';
  }
  
  else if(key==='churn'){
    title='Churn';
    h+='<div style="font-size:36px;font-weight:800;color:var(--da);margin-bottom:16px">'+d.churn_rate+'%</div>';
    h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">';
    h+='<div style="padding:12px;background:var(--bl);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">Suspendus</div><div style="font-size:20px;font-weight:800;color:var(--wa)">'+(d.suspended_count||0)+'</div></div>';
    h+='<div style="padding:12px;background:var(--dab);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">Churned</div><div style="font-size:20px;font-weight:800;color:var(--da)">'+(d.cancelled_count||0)+'</div></div>';
    h+='</div>';
    var churned=rp.filter(function(r){return r.status==='cancelled'||r.status==='suspended'});
    if(churned.length){
      h+='<table><thead><tr><th>Restaurant</th><th>Statut</th><th>Resas</th></tr></thead><tbody>';
      churned.forEach(function(r){h+='<tr><td>'+esc(r.name)+'</td><td>'+statusBadge(r.status)+'</td><td>'+r.bookings+'</td></tr>'});
      h+='</tbody></table>';
    } else {
      h+='<p style="color:var(--ok);text-align:center;padding:20px">Aucun churn ! </p>';
    }
  }
  
  else if(key==='resas'){
    title='Reservations';
    h+='<div style="font-size:36px;font-weight:800;color:var(--ac);margin-bottom:16px">'+d.total_bookings+' total</div>';
    if(d.bookings_timeline&&d.bookings_timeline.length){
      h+='<div style="margin-bottom:16px">'+mbc(d.bookings_timeline,'count','var(--ac)')+'</div>';
    }
    h+='<h3 style="font-size:13px;font-weight:700;margin-bottom:8px">Par restaurant</h3>';
    h+='<table><thead><tr><th>Restaurant</th><th>Total</th><th>Auj.</th></tr></thead><tbody>';
    rp.sort(function(a,b){return b.bookings-a.bookings}).forEach(function(r){
      h+='<tr><td style="font-weight:600">'+esc(r.name)+'</td><td>'+r.bookings+'</td><td>'+r.bookings_today+'</td></tr>';
    });
    h+='</tbody></table>';
  }
  
  else if(key==='whatsapp'){
    title='WhatsApp';
    h+='<table><thead><tr><th>Restaurant</th><th>Statut</th><th>Messages</th></tr></thead><tbody>';
    rp.forEach(function(r){
      var wa=r.whatsapp?'<span class="badge badge-ok">Connecte</span>':'<span class="badge badge-da">Non</span>';
      h+='<tr><td style="font-weight:600">'+esc(r.name)+'</td><td>'+wa+'</td><td>'+r.messages+'</td></tr>';
    });
    h+='</tbody></table>';
  }
  
  document.getElementById('detailTitle').textContent=title;
  document.getElementById('detailContent').innerHTML=h;
  document.getElementById('detailModal').classList.add('v');
}

// ===== CONV TOGGLE =====
document.addEventListener('click',function(e){
  var card=e.target.closest('.conv-toggle');
  if(!card)return;
  var msgs=card.querySelector('.conv-msgs');
  if(msgs)msgs.style.display=msgs.style.display==='block'?'none':'block';
});

// ===== EVENT DELEGATION =====
document.addEventListener('click',function(e){
  var t=e.target.closest('[data-action]');
  if(!t)return;
  var action=t.getAttribute('data-action');
  var id=t.getAttribute('data-id');
  if(action==='detail')openDetail(id);
  else if(action==='edit'){closeDetail();openEdit(id)}
  else if(action==='delete')confirmDelete(id,t.getAttribute('data-name'));
  else if(action==='setstatus')setStatus(id,t.getAttribute('data-newstatus'));
  else if(action==='extendtrial')extendTrial(id,t.getAttribute('data-name'));
  else if(action==='delbooking')deleteBooking(t.getAttribute('data-rid'),t.getAttribute('data-bid'));
});

// ===== KPI CLICK DETAIL =====
var lastStatsData=null;
document.getElementById('kpis').addEventListener('click',function(e){
  var kpiEl=e.target.closest('[data-kpi]');
  if(!kpiEl||!kpiEl.getAttribute('data-kpi'))return;
  var key=kpiEl.getAttribute('data-kpi');
  if(lastStatsData)showKPIDetail(key,lastStatsData);
});

function showKPIDetail(key,d){
  var h='';var title='';
  var rp=d.restaurant_performance||[];
  
  if(key==='mrr'){
    title='MRR - Monthly Recurring Revenue';
    h+='<div style="font-size:36px;font-weight:800;color:var(--ok);margin-bottom:16px">'+d.mrr+'\u20ac/mois</div>';
    h+='<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:20px">';
    h+='<div style="padding:12px;background:var(--bl);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">Actifs</div><div style="font-size:20px;font-weight:800">'+d.active_count+'</div></div>';
    h+='<div style="padding:12px;background:var(--bl);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">Prix unitaire</div><div style="font-size:20px;font-weight:800">'+d.price_per_month+'\u20ac</div></div>';
    h+='<div style="padding:12px;background:var(--bl);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">MRR potentiel</div><div style="font-size:20px;font-weight:800;color:var(--wa)">'+d.potential_mrr+'\u20ac</div></div>';
    h+='</div>';
    h+='<h3 style="font-size:13px;font-weight:700;margin-bottom:8px">Revenus par restaurant</h3>';
    h+='<table><thead><tr><th>Restaurant</th><th>Statut</th><th>MRR</th></tr></thead><tbody>';
    rp.forEach(function(r){
      var mrr=r.status==='active'?d.price_per_month:0;
      h+='<tr><td style="font-weight:600">'+esc(r.name)+'</td><td>'+statusBadge(r.status)+'</td><td style="font-weight:700;color:'+(mrr>0?'var(--ok)':'var(--tm)')+'">'+mrr+'\u20ac</td></tr>';
    });
    h+='</tbody></table>';
  }
  
  else if(key==='arr'){
    title='ARR - Annual Recurring Revenue';
    h+='<div style="font-size:36px;font-weight:800;color:var(--ok);margin-bottom:16px">'+d.arr+'\u20ac/an</div>';
    h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px">';
    h+='<div style="padding:12px;background:var(--bl);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">MRR actuel</div><div style="font-size:20px;font-weight:800">'+d.mrr+'\u20ac</div></div>';
    h+='<div style="padding:12px;background:var(--bl);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">ARR potentiel (si tous convertis)</div><div style="font-size:20px;font-weight:800;color:var(--wa)">'+(d.potential_mrr*12)+'\u20ac</div></div>';
    h+='</div>';
    h+='<div style="padding:16px;background:var(--bl);border-radius:8px;margin-bottom:16px">';
    h+='<div style="font-size:12px;color:var(--ts)">Projection : si chaque mois vous ajoutez <strong>5 restaurants</strong>, ARR dans 12 mois :</div>';
    var projected=0;for(var m=1;m<=12;m++){projected+=(d.active_count+m*5)*d.price_per_month}
    h+='<div style="font-size:28px;font-weight:800;color:var(--ac);margin-top:4px">~'+Math.round(projected/1000)+'K\u20ac</div>';
    h+='</div>';
  }
  
  else if(key==='actifs'){
    title='Restaurants actifs';
    h+='<table><thead><tr><th>Restaurant</th><th>Resas</th><th>Contacts</th><th>Messages</th><th>WhatsApp</th></tr></thead><tbody>';
    rp.filter(function(r){return r.status==='active'}).forEach(function(r){
      h+='<tr><td style="font-weight:600">'+esc(r.name)+'</td><td>'+r.bookings+'</td><td>'+r.contacts+'</td><td>'+r.messages+'</td><td>'+(r.whatsapp?'<span style="color:var(--ok)">Oui</span>':'Non')+'</td></tr>';
    });
    h+='</tbody></table>';
    if(!rp.filter(function(r){return r.status==='active'}).length) h='<p style="color:var(--tm);padding:20px;text-align:center">Aucun restaurant actif</p>';
  }
  
  else if(key==='trial'){
    title='Restaurants en essai';
    h+='<table><thead><tr><th>Restaurant</th><th>Resas</th><th>Messages</th><th>WhatsApp</th></tr></thead><tbody>';
    rp.filter(function(r){return r.status==='trial'}).forEach(function(r){
      h+='<tr><td style="font-weight:600">'+esc(r.name)+'</td><td>'+r.bookings+'</td><td>'+r.messages+'</td><td>'+(r.whatsapp?'<span style="color:var(--ok)">Oui</span>':'Non')+'</td></tr>';
    });
    h+='</tbody></table>';
  }
  
  else if(key==='conversion'){
    title='Taux de conversion';
    h+='<div style="font-size:36px;font-weight:800;color:var(--ac);margin-bottom:16px">'+d.conversion_rate+'%</div>';
    h+='<div style="display:flex;align-items:center;gap:16px;margin-bottom:20px">';
    h+='<div style="flex:1;text-align:center;padding:16px;background:var(--bl);border-radius:8px"><div style="font-size:24px;font-weight:800;color:var(--ac)">'+d.total_restaurants+'</div><div style="font-size:10px;color:var(--tm);font-weight:700">INSCRITS</div></div>';
    h+='<div style="font-size:24px;color:var(--tm)">&rarr;</div>';
    h+='<div style="flex:1;text-align:center;padding:16px;background:var(--bl);border-radius:8px"><div style="font-size:24px;font-weight:800;color:var(--wa)">'+d.trial_count+'</div><div style="font-size:10px;color:var(--tm);font-weight:700">EN ESSAI</div></div>';
    h+='<div style="font-size:24px;color:var(--tm)">&rarr;</div>';
    h+='<div style="flex:1;text-align:center;padding:16px;background:var(--okb);border-radius:8px"><div style="font-size:24px;font-weight:800;color:var(--ok)">'+d.active_count+'</div><div style="font-size:10px;color:var(--tm);font-weight:700">ACTIFS</div></div>';
    h+='</div>';
  }
  
  else if(key==='churn'){
    title='Churn';
    h+='<div style="font-size:36px;font-weight:800;color:var(--da);margin-bottom:16px">'+d.churn_rate+'%</div>';
    h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">';
    h+='<div style="padding:12px;background:var(--bl);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">Suspendus</div><div style="font-size:20px;font-weight:800;color:var(--wa)">'+(d.suspended_count||0)+'</div></div>';
    h+='<div style="padding:12px;background:var(--dab);border-radius:8px"><div style="font-size:10px;color:var(--tm);font-weight:700;text-transform:uppercase">Churned</div><div style="font-size:20px;font-weight:800;color:var(--da)">'+(d.cancelled_count||0)+'</div></div>';
    h+='</div>';
    var churned=rp.filter(function(r){return r.status==='cancelled'||r.status==='suspended'});
    if(churned.length){
      h+='<table><thead><tr><th>Restaurant</th><th>Statut</th><th>Resas</th></tr></thead><tbody>';
      churned.forEach(function(r){h+='<tr><td>'+esc(r.name)+'</td><td>'+statusBadge(r.status)+'</td><td>'+r.bookings+'</td></tr>'});
      h+='</tbody></table>';
    } else {
      h+='<p style="color:var(--ok);text-align:center;padding:20px">Aucun churn ! </p>';
    }
  }
  
  else if(key==='resas'){
    title='Reservations';
    h+='<div style="font-size:36px;font-weight:800;color:var(--ac);margin-bottom:16px">'+d.total_bookings+' total</div>';
    if(d.bookings_timeline&&d.bookings_timeline.length){
      h+='<div style="margin-bottom:16px">'+mbc(d.bookings_timeline,'count','var(--ac)')+'</div>';
    }
    h+='<h3 style="font-size:13px;font-weight:700;margin-bottom:8px">Par restaurant</h3>';
    h+='<table><thead><tr><th>Restaurant</th><th>Total</th><th>Auj.</th></tr></thead><tbody>';
    rp.sort(function(a,b){return b.bookings-a.bookings}).forEach(function(r){
      h+='<tr><td style="font-weight:600">'+esc(r.name)+'</td><td>'+r.bookings+'</td><td>'+r.bookings_today+'</td></tr>';
    });
    h+='</tbody></table>';
  }
  
  else if(key==='whatsapp'){
    title='WhatsApp';
    h+='<table><thead><tr><th>Restaurant</th><th>Statut</th><th>Messages</th></tr></thead><tbody>';
    rp.forEach(function(r){
      var wa=r.whatsapp?'<span class="badge badge-ok">Connecte</span>':'<span class="badge badge-da">Non</span>';
      h+='<tr><td style="font-weight:600">'+esc(r.name)+'</td><td>'+wa+'</td><td>'+r.messages+'</td></tr>';
    });
    h+='</tbody></table>';
  }
  
  document.getElementById('detailTitle').textContent=title;
  document.getElementById('detailContent').innerHTML=h;
  document.getElementById('detailModal').classList.add('v');
}

// ===== CONV TOGGLE =====
document.addEventListener('click',function(e){
  var card=e.target.closest('.conv-toggle');
  if(!card)return;
  var msgs=card.querySelector('.conv-msgs');
  if(msgs)msgs.style.display=msgs.style.display==='block'?'none':'block';
});

// ===== EVENT DELEGATION =====
document.addEventListener('click',function(e){
  var t=e.target.closest('[data-action]');
  if(!t)return;
  var action=t.getAttribute('data-action');
  var id=t.getAttribute('data-id');
  if(action==='detail')openDetail(id);
  else if(action==='edit')openEdit(id);
  else if(action==='editfromdetail'){closeDetail();openEdit(id)}
  else if(action==='delete')confirmDelete(id,t.getAttribute('data-name'));
  else if(action==='setstatus')setStatus(id,t.getAttribute('data-newstatus'));
  else if(action==='extendtrial')extendTrial(id,t.getAttribute('data-name'));
  else if(action==='delbooking'){var rid=t.getAttribute('data-rid');var bid=t.getAttribute('data-bid');deleteBooking(rid,bid)}
});
</script>
</body>
</html>"""
