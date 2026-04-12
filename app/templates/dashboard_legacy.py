# Legacy DASHBOARD_HTML — extracted from main.py during refactoring
# This is the old inline dashboard served to restaurants not using the React frontend.

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GuestScale — Dashboard</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32' fill='none'%3E%3Ccircle cx='10' cy='10' r='4' fill='%232D7DD2'/%3E%3Ccircle cx='22' cy='10' r='4' fill='%234ECDC4'/%3E%3Ccircle cx='16' cy='22' r='4' fill='%234ECDC4'/%3E%3Cline x1='13' y1='11' x2='19' y2='11' stroke='%232D7DD2' stroke-width='2'/%3E%3Cline x1='11' y1='13' x2='15' y2='19' stroke='%232D7DD2' stroke-width='2'/%3E%3Cline x1='21' y1='13' x2='17' y2='19' stroke='%234ECDC4' stroke-width='2'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#F4F5F9;--card:#FFF;--sb:#0F1117;--sbh:#1A1D27;--sba:#252836;--sbt:#6B7280;
  --t:#111827;--ts:#6B7280;--tm:#9CA3AF;--b:#E5E7EB;--bl:#F3F4F6;
  --ac:#2D7DD2;--ac2:#4ECDC4;--acg:linear-gradient(135deg,#2D7DD2,#4ECDC4);
  --al:#EBF4FF;--ok:#4ECDC4;--okb:#E6FAF8;--wa:#F59E0B;--wab:#FFFBEB;
  --da:#EF4444;--bl2:#2D7DD2;--blb:#EBF4FF;
  --f:'Plus Jakarta Sans',-apple-system,BlinkMacSystemFont,sans-serif;
  --shadow:0 1px 3px rgba(0,0,0,.06),0 1px 2px rgba(0,0,0,.04);
  --shadow-md:0 4px 6px rgba(0,0,0,.05),0 2px 4px rgba(0,0,0,.04);
  --shadow-lg:0 10px 25px rgba(0,0,0,.08),0 4px 10px rgba(0,0,0,.04);
  --radius:12px;
}
body{font-family:var(--f);background:var(--bg);color:var(--t);min-height:100vh;-webkit-font-smoothing:antialiased}

/* === LOGIN === */
.lo{position:fixed;inset:0;background:#0F1117;display:flex;align-items:center;justify-content:center;z-index:100}
.lbox{text-align:center;width:360px}
.l-logo{display:flex;align-items:center;justify-content:center;gap:10px;margin-bottom:8px}
.l-icon{width:40px;height:40px;background:#1A1D27;border-radius:10px;display:flex;align-items:center;justify-content:center}
.l-icon svg{width:28px;height:28px}
.lwm{font-size:28px;font-weight:800;color:#fff;letter-spacing:-.03em}
.lsub{font-size:11px;color:#6B7280;letter-spacing:.12em;margin-bottom:36px;text-transform:uppercase}
.lcd{background:#1A1D27;border-radius:16px;padding:28px 24px;border:1px solid #252836}
.linp{width:100%;padding:13px 16px;border-radius:10px;background:#0F1117;border:1.5px solid #374151;font-size:14px;color:#F9FAFB;outline:none;font-family:var(--f);transition:border .2s}
.linp::placeholder{color:#6B7280}
.linp:focus{border-color:var(--ac)}
.lbtn{width:100%;padding:13px;border-radius:10px;border:none;background:var(--acg);color:#fff;font-size:14px;font-weight:700;cursor:pointer;font-family:var(--f);margin-top:12px;transition:opacity .2s}
.lbtn:hover{opacity:.9}
.lerr{color:var(--da);font-size:13px;margin-bottom:14px;display:none;background:#FEF2F220;padding:10px 14px;border-radius:10px;border:1px solid #EF444430}
@keyframes shake{0%,100%{transform:translateX(0)}20%,60%{transform:translateX(-6px)}40%,80%{transform:translateX(6px)}}
.shake{animation:shake .4s ease}

/* === APP LAYOUT === */
.app{display:none}.app.v{display:flex}
.sidebar{width:240px;background:var(--sb);position:fixed;height:100vh;display:flex;flex-direction:column;z-index:40;border-right:1px solid #1F2937}
.sb-b{padding:24px 20px 28px;border-bottom:1px solid #1F2937}
.sb-logo{display:flex;align-items:center;gap:10px}
.sb-icon{width:32px;height:32px;background:#1A1D27;border-radius:8px;display:flex;align-items:center;justify-content:center}
.sb-icon svg{width:22px;height:22px}
.sb-wm{font-size:17px;font-weight:800;color:#F9FAFB;letter-spacing:-.02em}
.sb-s{font-size:9px;color:#4B5563;letter-spacing:.15em;text-transform:uppercase;margin-top:1px}
.sb-n{padding:16px 12px;flex:1;overflow-y:auto}
.sb-l{font-size:10px;font-weight:700;color:#4B5563;letter-spacing:.1em;padding:0 8px;margin-bottom:8px;margin-top:16px;text-transform:uppercase}
.sb-l:first-child{margin-top:0}
.nb{width:100%;display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:8px;border:none;background:transparent;color:var(--sbt);font-size:13px;font-weight:500;text-align:left;font-family:var(--f);cursor:pointer;margin-bottom:1px;transition:all .15s}
.nb:hover{background:var(--sbh);color:#D1D5DB}
.nb.on{background:var(--sba);color:#F9FAFB;font-weight:600}
.nb .ic{font-size:14px;width:20px;text-align:center;opacity:.5}.nb.on .ic{opacity:1}
.nb-badge{margin-left:auto;min-width:18px;height:18px;border-radius:9px;font-size:10px;font-weight:700;display:flex;align-items:center;justify-content:center;padding:0 5px}
.sb-u{padding:16px 20px;border-top:1px solid #1F2937;display:flex;align-items:center;gap:10px}
.uav{width:32px;height:32px;border-radius:8px;background:var(--acg);display:flex;align-items:center;justify-content:center;color:#fff;font-size:11px;font-weight:700}

/* === MAIN CONTENT === */
.main{flex:1;margin-left:240px}
.topbar{padding:16px 32px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:30;background:rgba(244,245,249,.85);backdrop-filter:blur(20px);border-bottom:1px solid var(--b)}
.topbar h1{font-size:18px;font-weight:700;letter-spacing:-.02em;color:var(--t)}
.sp{display:flex;align-items:center;gap:6px;padding:5px 12px;border-radius:20px;font-size:12px;font-weight:600}
.sd2{width:7px;height:7px;border-radius:50%;box-shadow:0 0 6px rgba(16,185,129,.5)}
.content{padding:24px 32px;max-width:1120px}

/* === STAT GRID === */
.sg{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:20px}
.sc{background:var(--card);border-radius:var(--radius);padding:20px 18px;border:1px solid var(--b);transition:all .2s;cursor:default;box-shadow:var(--shadow)}
.sc:hover{box-shadow:var(--shadow-md);transform:translateY(-1px)}
.sl{font-size:11px;font-weight:600;color:var(--tm);letter-spacing:.06em;text-transform:uppercase;margin-bottom:10px}
.sv{font-size:30px;font-weight:800;letter-spacing:-.03em;line-height:1}
.ss2{font-size:12px;color:var(--ts);margin-top:6px;font-weight:500}

/* === CARDS === */
.g2{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}
.card{background:var(--card);border-radius:var(--radius);border:1px solid var(--b);overflow:hidden;box-shadow:var(--shadow)}
.card-h{padding:14px 18px;border-bottom:1px solid var(--bl);display:flex;justify-content:space-between;align-items:center}
.card-t{font-size:14px;font-weight:700;color:var(--t)}.card-s{font-size:12px;color:var(--tm);margin-top:1px;font-weight:500}

/* === BUTTONS === */
.ba{padding:6px 14px;border-radius:8px;border:none;background:var(--acg);color:#fff;font-size:12px;font-weight:600;cursor:pointer;font-family:var(--f);transition:opacity .2s;box-shadow:0 1px 3px rgba(99,102,241,.3)}
.ba:hover{opacity:.85}

/* === ROWS & BADGES === */
.rw{padding:11px 18px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--bl);transition:background .1s}
.rw:last-child{border-bottom:none}
.rw:hover{background:var(--bl)}
.rl{display:flex;align-items:center;gap:10px}
.dot{width:6px;height:6px;border-radius:50%}
.badge{font-size:11px;font-weight:600;padding:3px 8px;border-radius:6px}
.src-badge{font-size:10px;font-weight:600;padding:2px 7px;border-radius:4px}

/* === DAILY BANNER === */
.db{background:linear-gradient(135deg,#EEF2FF,#E0E7FF);border:1px solid #C7D2FE;border-radius:var(--radius);padding:16px 18px;margin-bottom:18px;box-shadow:var(--shadow)}
.db-top{display:flex;align-items:flex-start;gap:14px}
.di{width:38px;height:38px;border-radius:10px;background:var(--acg);display:flex;align-items:center;justify-content:center;color:#fff;font-size:16px;flex-shrink:0}
.dlb{font-size:10px;font-weight:700;color:var(--ac);letter-spacing:.08em;text-transform:uppercase}
.dtx{font-size:14px;font-weight:600;color:var(--t);margin-top:4px;cursor:pointer;padding:4px 8px;border-radius:8px;border:1.5px solid transparent;transition:border .2s}
.dtx:hover{border-color:#C7D2FE}
.dtx-edit{font-size:14px;font-weight:600;color:var(--t);margin-top:4px;padding:8px 10px;border-radius:10px;border:1.5px solid var(--ac);background:#fff;width:100%;outline:none;font-family:var(--f);resize:none;min-height:44px}
.dme{font-size:11px;color:var(--ts);margin-top:4px}
.db-act{display:flex;gap:8px;margin-top:12px;padding-top:12px;border-top:1px solid #C7D2FE40}
.dbb{padding:7px 14px;border-radius:8px;border:none;font-size:12px;font-weight:600;cursor:pointer;font-family:var(--f);display:flex;align-items:center;gap:5px;transition:opacity .2s}
.dbb-s{background:var(--acg);color:#fff}.dbb-s:hover{opacity:.85}
.dbb-b{background:var(--bl2);color:#fff}.dbb-b:hover{opacity:.85}
.dbb-c{background:#fff;color:var(--ts);border:1px solid var(--b)}.dbb-c:hover{background:var(--bl)}

/* === FLOORPLAN === */
.fm{background:var(--card);border-radius:var(--radius);border:1px solid var(--b);padding:18px;margin-bottom:14px;cursor:pointer;transition:all .2s;box-shadow:var(--shadow)}
.fm:hover{box-shadow:var(--shadow-md);transform:translateY(-1px)}
.fc{position:relative;height:180px;background:var(--bg);border-radius:10px;border:1px solid var(--bl);overflow:hidden;margin-top:10px}
.ftbl{position:absolute;display:flex;flex-direction:column;align-items:center;justify-content:center;border:2px solid;font-size:10px;font-weight:700}

/* === CONTACTS, CONVERSATIONS === */
.cr{padding:12px 18px;display:flex;align-items:center;gap:12px;border-bottom:1px solid var(--bl)}
.cr:last-child{border-bottom:none}
.cav{width:36px;height:36px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;flex-shrink:0}
.cmsg{font-size:12px;color:var(--ts);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cg3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.cc{padding:14px;border-radius:10px;background:var(--bg);border:1px solid var(--bl)}
.conv-list-item{padding:12px 14px;cursor:pointer;border-left:3px solid transparent;transition:all .15s}
.conv-list-item.selected{background:var(--al);border-left:3px solid var(--ac)}

/* === CHAT BUBBLES === */
.bubble{padding:10px 14px;border-radius:14px;max-width:80%;font-size:13px;line-height:1.5;margin-bottom:8px}
.bubble-user{background:var(--acg);color:#fff;margin-left:auto;border-bottom-right-radius:4px}
.bubble-bot{background:var(--bl);color:var(--t);margin-right:auto;border-bottom-left-radius:4px}

/* === MENU === */
.ms{margin-bottom:20px}
.mc{font-size:12px;font-weight:700;color:var(--ac);letter-spacing:.06em;text-transform:uppercase;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--bl)}
.mi-row{display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid var(--bl)}
.mi-row:last-child{border-bottom:none}
.mi-n{font-size:14px;font-weight:600}.mi-d{font-size:12px;color:var(--ts);margin-top:2px}.mi-p{font-size:14px;font-weight:700;color:var(--ac);white-space:nowrap}
.menu-sec{margin-bottom:24px;border:1px solid var(--b);border-radius:var(--radius);overflow:hidden;box-shadow:var(--shadow)}

/* === CONFIG === */
.cfs{margin-bottom:28px}
.cft{font-size:15px;font-weight:700;margin-bottom:4px}
.cfsb{font-size:12px;color:var(--ts);margin-bottom:16px}
.cfr{display:flex;align-items:center;justify-content:space-between;padding:12px 0;border-bottom:1px solid var(--bl)}
.cfr:last-child{border-bottom:none}
.cfl{font-size:14px;font-weight:500}.cfd{font-size:12px;color:var(--tm)}
.tog{position:relative;width:44px;height:24px;background:var(--b);border-radius:12px;cursor:pointer;transition:background .2s;flex-shrink:0}
.tog.on{background:var(--ac)}
.togd{position:absolute;top:3px;left:3px;width:18px;height:18px;border-radius:50%;background:#fff;transition:transform .2s;box-shadow:0 1px 3px rgba(0,0,0,.1)}
.tog.on .togd{transform:translateX(20px)}

/* === MODALS === */
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.5);backdrop-filter:blur(4px);display:none;align-items:center;justify-content:center;z-index:150}
.modal-bg.show{display:flex}
.modal{background:var(--card);border-radius:16px;padding:28px;width:420px;max-width:90vw;max-height:90vh;overflow-y:auto;box-shadow:var(--shadow-lg)}
.modal h2{font-size:17px;font-weight:700;margin-bottom:4px}
.finp{width:100%;padding:11px 14px;border-radius:8px;background:var(--bg);border:1.5px solid var(--b);font-size:13px;color:var(--t);outline:none;font-family:var(--f);margin-bottom:10px;transition:border .2s}
.finp:focus{border-color:var(--ac)}
.finp-row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.finp-label{font-size:10px;font-weight:700;color:var(--tm);letter-spacing:.08em;text-transform:uppercase;margin-bottom:4px}
.finp-group{margin-bottom:4px}
.modal-act{display:flex;gap:8px;margin-top:16px}
.mbtn{flex:1;padding:11px;border-radius:10px;border:none;font-size:13px;font-weight:600;cursor:pointer;font-family:var(--f);transition:opacity .2s}
.mbtn-p{background:var(--acg);color:#fff;box-shadow:0 1px 3px rgba(99,102,241,.3)}.mbtn-p:hover{opacity:.85}
.mbtn-s{background:var(--bg);color:var(--ts);border:1px solid var(--b)}

/* === MISC === */
.toast{position:fixed;bottom:24px;right:24px;background:var(--sb);color:#fff;padding:12px 24px;border-radius:10px;font-weight:600;font-size:13px;box-shadow:var(--shadow-lg);z-index:200;display:none;animation:su .3s ease;max-width:90vw}
@keyframes su{from{transform:translateY(20px);opacity:0}to{transform:translateY(0);opacity:1}}
.at-box{background:var(--okb);border:1px solid #BBF7D0;border-radius:10px;padding:12px 14px;margin-top:8px;display:none}
.at-l{font-size:11px;font-weight:600;color:var(--ok);letter-spacing:.06em;text-transform:uppercase}
.at-v{font-size:20px;font-weight:700;color:var(--ok);margin-top:4px}
.at-c{font-size:12px;color:var(--ac);cursor:pointer;font-weight:600;margin-top:4px}
.tsel{display:none;grid-template-columns:repeat(5,1fr);gap:6px;margin-top:8px}
.tsb{padding:8px;border-radius:8px;border:1.5px solid var(--b);background:var(--card);font-size:12px;font-weight:600;cursor:pointer;font-family:var(--f);text-align:center;transition:all .15s}
.tsb:hover{border-color:var(--ac);background:var(--al)}
.tsb.sel{border-color:var(--ok);background:var(--okb);color:var(--ok)}
.tsb.taken{opacity:.3;cursor:not-allowed}
.dinp{width:100%;padding:12px 14px;border-radius:10px;background:var(--bg);border:1.5px solid var(--b);font-size:13px;color:var(--t);outline:none;font-family:var(--f);resize:none;min-height:60px;transition:border .2s}
.dinp:focus{border-color:var(--ac)}
.msg-input{flex:1;padding:10px 14px;border-radius:8px;background:var(--bg);border:1.5px solid var(--b);color:var(--t);font-size:13px;outline:none;font-family:var(--f);transition:border .2s}
.msg-input:focus{border-color:var(--ac)}
.msg-btn{padding:10px 18px;border-radius:8px;border:none;background:var(--acg);color:#fff;font-weight:700;font-size:13px;cursor:pointer;font-family:var(--f);white-space:nowrap;transition:opacity .2s}
.msg-btn:hover{opacity:.85}
.star{color:var(--wa)}
.review-card{padding:14px 18px;border-bottom:1px solid var(--bl)}
.review-card:last-child{border-bottom:none}
.ph{background:var(--card);border-radius:var(--radius);padding:60px;border:1px solid var(--b);text-align:center;box-shadow:var(--shadow)}
.phi{font-size:36px;opacity:.2;margin-bottom:12px}

/* === MONTHLY CALENDAR === */
.cal-wrap{background:var(--card);border-radius:var(--radius);border:1px solid var(--b);box-shadow:var(--shadow);padding:16px;margin-bottom:14px}
.cal-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.cal-nav{display:flex;align-items:center;gap:4px}
.cal-arrow{width:28px;height:28px;border-radius:6px;border:1.5px solid var(--b);background:var(--card);display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:13px;color:var(--ts);transition:all .15s}
.cal-arrow:hover{border-color:var(--ac);color:var(--ac);background:var(--al)}
.cal-title{font-size:14px;font-weight:700;color:var(--t);cursor:pointer;padding:4px 10px;border-radius:6px;transition:all .15s}
.cal-title:hover{background:var(--al);color:var(--ac)}
.cal-today-btn{padding:4px 10px;border-radius:6px;border:1.5px solid var(--b);background:var(--card);font-size:11px;font-weight:700;color:var(--ts);cursor:pointer;font-family:var(--f);transition:all .15s}
.cal-today-btn:hover{border-color:var(--ac);color:var(--ac);background:var(--al)}
.cal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:2px}
.cal-dow{font-size:9px;font-weight:700;color:var(--tm);text-transform:uppercase;text-align:center;padding:4px 0;letter-spacing:.04em}
.cal-cell{position:relative;aspect-ratio:1;display:flex;flex-direction:column;align-items:center;justify-content:center;border-radius:8px;cursor:pointer;font-family:var(--f);transition:all .12s}
.cal-cell:hover{background:var(--bl)}
.cal-cell.other{opacity:.3}
.cal-cell.today{border:1.5px solid var(--b)}
.cal-cell.sel{background:var(--ac);border-color:var(--ac)}
.cal-cell.sel .cal-num{color:#fff}
.cal-cell.sel .cal-dot{background:#fff}
.cal-num{font-size:12px;font-weight:700;color:var(--t);line-height:1}
.cal-dot{width:4px;height:4px;border-radius:50%;background:var(--ac2);margin-top:2px;opacity:0}
.cal-dot.has{opacity:1}
.cal-picker{position:absolute;top:100%;left:0;right:0;background:var(--card);border:1px solid var(--b);border-radius:10px;box-shadow:var(--shadow-lg);padding:12px;z-index:20;display:none}
.cal-picker.show{display:block}
.cal-picker-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:4px}
.cal-picker-item{padding:8px;border-radius:6px;text-align:center;font-size:12px;font-weight:600;color:var(--t);cursor:pointer;transition:all .12s}
.cal-picker-item:hover{background:var(--al);color:var(--ac)}
.cal-picker-item.sel{background:var(--ac);color:#fff}

/* === FLOORPLAN WITH SIDEBAR === */
.fp-layout{display:flex;gap:14px;align-items:flex-start}
.fp-main{flex:1;min-width:0}
.fp-sidebar{width:300px;flex-shrink:0;background:var(--card);border-radius:var(--radius);border:1px solid var(--b);box-shadow:var(--shadow);overflow:hidden;display:flex;flex-direction:column}
.fp-sidebar .cal-wrap{border:none;box-shadow:none;border-radius:0;border-bottom:1px solid var(--bl);margin-bottom:0;padding:12px 16px}
.fp-sb-header{padding:14px 16px;border-bottom:1px solid var(--bl);display:flex;justify-content:space-between;align-items:center}
.fp-sb-title{font-size:13px;font-weight:700;color:var(--t)}
.fp-sb-count{font-size:11px;font-weight:600;color:var(--tm)}
.fp-sb-list{flex:1;overflow-y:auto;scrollbar-width:thin;max-height:280px}
.fp-sb-item{padding:10px 16px;border-bottom:1px solid var(--bl);cursor:pointer;transition:background .1s}
.fp-sb-item:last-child{border-bottom:none}
.fp-sb-item:hover{background:var(--bl)}
.fp-sb-item.active{background:var(--al);border-left:3px solid var(--ac)}
.fp-sb-name{font-size:13px;font-weight:600;color:var(--t)}
.fp-sb-meta{font-size:11px;color:var(--tm);margin-top:2px;display:flex;align-items:center;gap:6px}
.fp-sb-table{font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;background:var(--okb);color:var(--ok)}
.fp-sb-no-table{font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;background:var(--wab);color:var(--wa)}
.fp-sb-empty{padding:30px 16px;text-align:center;color:var(--tm);font-size:12px}

/* === MOBILE === */
/* === ONBOARDING WIZARD === */
.ob-overlay{position:fixed;inset:0;background:rgba(15,17,23,.92);backdrop-filter:blur(8px);z-index:200;display:flex;align-items:center;justify-content:center}
.ob-card{background:var(--card);border-radius:20px;width:520px;max-width:94vw;max-height:90vh;overflow-y:auto;box-shadow:var(--shadow-lg);padding:32px}
.ob-steps{display:flex;gap:4px;margin-bottom:24px}
.ob-step{flex:1;height:4px;border-radius:2px;background:var(--bl);transition:background .3s}
.ob-step.done{background:var(--ac)}
.ob-step.active{background:var(--acg)}
.ob-title{font-size:16px;font-weight:700;color:var(--t);margin-bottom:4px}
.ob-desc{font-size:13px;color:var(--ts);margin-bottom:18px}
.ob-field{margin-bottom:14px}
.ob-label{font-size:10px;font-weight:700;color:var(--tm);letter-spacing:.08em;text-transform:uppercase;margin-bottom:5px}
.ob-input{width:100%;padding:12px 14px;border-radius:10px;background:var(--bg);border:1.5px solid var(--b);font-size:14px;color:var(--t);outline:none;font-family:var(--f);transition:border .2s}
.ob-input:focus{border-color:var(--ac)}
.ob-textarea{width:100%;padding:12px 14px;border-radius:10px;background:var(--bg);border:1.5px solid var(--b);font-size:13px;color:var(--t);outline:none;font-family:var(--f);resize:none;min-height:80px;transition:border .2s}
.ob-textarea:focus{border-color:var(--ac)}
.ob-actions{display:flex;gap:8px;margin-top:20px}
.ob-btn{flex:1;padding:12px;border-radius:10px;border:none;font-size:14px;font-weight:700;cursor:pointer;font-family:var(--f);transition:opacity .2s}
.ob-btn-p{background:var(--acg);color:#fff}.ob-btn-p:hover{opacity:.85}
.ob-btn-s{background:var(--bg);color:var(--ts);border:1px solid var(--b)}.ob-btn-s:hover{background:var(--bl)}
.ob-skip{font-size:12px;color:var(--tm);text-align:center;margin-top:12px;cursor:pointer}
.ob-skip:hover{color:var(--ac)}

.mobile-nav{display:none;position:fixed;bottom:0;left:0;right:0;background:var(--sb);padding:6px 0 calc(env(safe-area-inset-bottom,0px) + 8px);z-index:50;border-top:1px solid #1F2937}
.mobile-nav-items{display:flex;justify-content:space-around}
.mobile-nav-btn{background:none;border:none;color:#6B7280;font-size:9px;font-weight:600;cursor:pointer;font-family:var(--f);display:flex;flex-direction:column;align-items:center;gap:2px;padding:6px 8px;transition:color .15s;min-width:52px}
.mobile-nav-btn.active{color:var(--ac)}
.mobile-nav-btn span{font-size:18px;line-height:1.2}
.mobile-more-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:55;opacity:0;transition:opacity .2s}
.mobile-more-overlay.show{display:block;opacity:1}
.mobile-more-drawer{position:fixed;bottom:0;left:0;right:0;background:var(--sb);z-index:56;border-radius:16px 16px 0 0;padding:8px 0 calc(env(safe-area-inset-bottom,0px) + 16px);transform:translateY(100%);transition:transform .25s cubic-bezier(.4,0,.2,1)}
.mobile-more-drawer.show{transform:translateY(0)}
.mobile-more-handle{width:36px;height:4px;background:#374151;border-radius:2px;margin:4px auto 12px}
.mobile-more-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:4px;padding:0 12px}
.mobile-more-item{background:none;border:none;color:#9CA3AF;font-size:10px;font-weight:600;font-family:var(--f);display:flex;flex-direction:column;align-items:center;gap:4px;padding:12px 4px;border-radius:12px;cursor:pointer;transition:all .15s}
.mobile-more-item:active,.mobile-more-item:hover{background:#1F2937;color:#E5E7EB}
.mobile-more-item.active{color:var(--ac)}
.mobile-more-item span{font-size:22px;line-height:1}
.mobile-more-item .mmi-badge{position:absolute;top:6px;right:50%;transform:translateX(140%);background:var(--ac);color:#fff;font-size:8px;font-weight:700;padding:1px 5px;border-radius:8px;min-width:14px;text-align:center}
.mobile-more-item{position:relative}
@media(max-width:768px){
  .sidebar{display:none}
  .main{margin-left:0}
  .mobile-nav{display:block}
  .content{padding:14px;padding-bottom:80px}
  .topbar{padding:12px 14px}
  .topbar h1{font-size:16px}
  .topbar .sp{padding:3px 8px;font-size:11px}
  /* Hide secondary topbar info on mobile */
  .topbar>div:last-child>div:first-child{display:none}

  /* Stats grid: 2 cols, compact */
  .sg{grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:14px}
  .sc{padding:14px 12px}
  .sv{font-size:24px}
  .sl{font-size:9px;margin-bottom:6px}
  .ss2{font-size:11px;margin-top:4px}

  /* Cards */
  .g2{grid-template-columns:1fr;gap:10px}
  .cg3{grid-template-columns:1fr 1fr}
  .card-h{padding:12px 14px}
  .rw{padding:10px 14px}

  /* Overview layout: stack vertically */
  .ov-layout{flex-direction:column!important}
  .ov-layout>div:last-child{width:100%!important}

  /* Calendar compact */
  .cal-wrap{padding:12px}
  .cal-num{font-size:11px}
  .cal-header{margin-bottom:8px}
  .cal-title{font-size:13px;padding:3px 8px}

  /* Floorplan */
  .fp-layout{flex-direction:column}
  .fp-sidebar{width:100%}
  .fp-sb-list{max-height:200px}
  #fpCanvas{height:320px!important}

  /* Conversations stacked */
  .conv-split{flex-direction:column!important}
  .conv-split>div:first-child{width:100%!important;max-height:200px;overflow-y:auto;border-right:none!important;border-bottom:1px solid var(--bl)}
  .conv-split>div:last-child{width:100%!important}

  /* Modals: wider on mobile */
  .modal{width:95vw;padding:20px;border-radius:12px}
  .finp-row{grid-template-columns:1fr}

  /* Daily banner compact */
  .db{padding:12px 14px}
  .di{width:32px;height:32px;font-size:14px}

  /* Floor mini on overview */
  .fc{height:140px}

  /* Touch: larger targets */
  .nb,.mobile-nav-btn{min-height:44px}
  .ba{min-height:36px;padding:8px 14px}
  .cal-cell{min-height:32px}

  /* Toast above mobile nav */
  .toast{bottom:80px;right:50%;transform:translateX(50%);text-align:center}
}

/* Extra small screens */
@media(max-width:380px){
  .content{padding:10px;padding-bottom:80px}
  .sg{grid-template-columns:1fr 1fr;gap:8px}
  .sv{font-size:20px}
  .sc{padding:12px 10px}
  .cal-num{font-size:10px}
  .cal-dow{font-size:8px}
  #fpCanvas{height:260px!important}
  .fp-sb-list{max-height:160px}
  .modal{padding:16px}
}
</style>
</style>
</head>
<body>

<div class="lo" id="loginOverlay">
<div class="lbox">
  <div class="l-logo"><div class="l-icon"><svg viewBox="0 0 32 32" fill="none"><circle cx="10" cy="10" r="4" fill="#2D7DD2"/><circle cx="22" cy="10" r="4" fill="#4ECDC4"/><circle cx="16" cy="22" r="4" fill="#4ECDC4"/><line x1="13" y1="11" x2="19" y2="11" stroke="#2D7DD2" stroke-width="2"/><line x1="11" y1="13" x2="15" y2="19" stroke="#2D7DD2" stroke-width="2"/><line x1="21" y1="13" x2="17" y2="19" stroke="#4ECDC4" stroke-width="2"/></svg></div><div class="lwm">Guest<span style="color:#4ECDC4">Scale</span></div></div>
  <div class="lsub">Plateforme IA pour restaurants</div>
  <div class="lcd">
    <div class="lerr" id="loginError">Identifiants incorrects. Veuillez reessayer.</div>
    <input class="linp" type="email" id="loginEmail" placeholder="Email" autocomplete="email" style="margin-bottom:10px" oninput="document.getElementById('loginError').style.display='none'">
    <div style="position:relative">
      <input class="linp" type="password" id="loginPwd" placeholder="Mot de passe" autocomplete="current-password" onkeydown="if(event.key==='Enter')doLogin()" oninput="document.getElementById('loginError').style.display='none';this.style.borderColor='#374151'">
      <button data-togglePwd onclick="togglePwdVis()" style="position:absolute;right:12px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;font-size:16px;color:#6B7280;padding:4px" id="pwdToggle" type="button" title="Afficher le mot de passe">&#128065;</button>
    </div>
    <button class="lbtn" type="button" onclick="doLogin()" data-doLogin>Se connecter</button>
    <div style="text-align:center;margin-top:12px">
      <a href="#" onclick="showForgotPwd();return false" style="font-size:12px;color:#6B7280;text-decoration:none">Mot de passe oublié ?</a>
    </div>
    <div style="text-align:center;margin-top:16px">
      <span style="font-size:12px;color:#6B7280">Pas encore de compte ?</span>
      <a href="https://guestscale.com#inscription" style="font-size:12px;color:#4ECDC4;text-decoration:none;font-weight:600;margin-left:4px">Essai gratuit 30 jours</a>
    </div>
  </div>
  <!-- Forgot password form (hidden by default) -->
  <div id="forgotPwdForm" style="display:none">
    <div class="lerr" id="forgotError" style="display:none"></div>
    <div class="lerr" id="forgotSuccess" style="display:none;color:#10B981;border-color:#6EE7B7;background:#ECFDF520"></div>
    <div id="forgotStep1">
      <p style="font-size:13px;color:#9CA3AF;margin-bottom:12px">Entrez votre email pour recevoir un code de reinitialisation.</p>
      <input class="linp" type="email" id="forgotEmail" placeholder="Email" style="margin-bottom:8px">
      <button class="lbtn" type="button" onclick="sendResetCode()">Envoyer le code</button>
    </div>
    <div id="forgotStep2" style="display:none">
      <p style="font-size:13px;color:#9CA3AF;margin-bottom:12px">Entrez le code recu par email et votre nouveau mot de passe.</p>
      <input class="linp" type="text" id="resetCode" placeholder="Code a 6 chiffres" style="margin-bottom:8px">
      <input class="linp" type="password" id="newPwd" placeholder="Nouveau mot de passe (min. 12 car.)" style="margin-bottom:8px">
      <button class="lbtn" type="button" onclick="doResetPwd()">Changer le mot de passe</button>
    </div>
    <div style="text-align:center;margin-top:12px">
      <a href="#" onclick="hideForgotPwd();return false" style="font-size:12px;color:#6B7280;text-decoration:none">Retour a la connexion</a>
    </div>
  </div>
</div>
</div>

<div class="app" id="app">
<div class="sidebar">
  <div class="sb-b"><div class="sb-logo"><div class="sb-icon"><svg viewBox="0 0 32 32" fill="none"><circle cx="10" cy="10" r="4" fill="#2D7DD2"/><circle cx="22" cy="10" r="4" fill="#4ECDC4"/><circle cx="16" cy="22" r="4" fill="#4ECDC4"/><line x1="13" y1="11" x2="19" y2="11" stroke="#2D7DD2" stroke-width="2"/><line x1="11" y1="13" x2="15" y2="19" stroke="#2D7DD2" stroke-width="2"/><line x1="21" y1="13" x2="17" y2="19" stroke="#4ECDC4" stroke-width="2"/></svg></div><div><div class="sb-wm">Guest<span style="color:#4ECDC4">Scale</span></div><div class="sb-s">Restaurant AI</div></div></div></div>
  <div class="sb-n">
    <div class="sb-l">PRINCIPAL</div>
    <button class="nb on" data-pg="overview"><span class="ic">&#9672;</span> Vue d&#39;ensemble</button>
    <button class="nb" data-pg="floorplan"><span class="ic">&#8862;</span> Plan de salle</button>
    <button class="nb" data-pg="bookings"><span class="ic">&#9673;</span> Réservations <span class="nb-badge" id="bookBadge" style="background:var(--wa);color:#fff">0</span></button>
    <button class="nb" data-pg="menu"><span class="ic">&#9680;</span> Menu</button>
    <div class="sb-l">CLIENTS</div>
    <button class="nb" data-pg="conversations"><span class="ic">&#9672;</span> Conversations <span class="nb-badge" id="convBadge" style="background:var(--ac);color:#fff">0</span></button>
    <button class="nb" data-pg="reviews"><span class="ic">&#9733;</span> Avis <span class="nb-badge" id="reviewBadge" style="background:var(--ac);color:#fff">0</span></button>
    <button class="nb" data-pg="contacts"><span class="ic">&#9671;</span> Contacts</button>
    <button class="nb" data-pg="waitlist"><span class="ic">&#9201;</span> Liste d'attente <span class="nb-badge" id="waitBadge" style="background:var(--wa);color:#fff">0</span></button>
    <div class="sb-l">PARAMÈTRES</div>
    <button class="nb" data-pg="config"><span class="ic">&#9881;</span> Configuration</button>
    <button class="nb" data-pg="stats"><span class="ic">&#9899;</span> Statistiques</button>
    <button class="nb" data-pg="account"><span class="ic">&#128100;</span> Mon compte</button>
  </div>
  <div class="sb-u" id="sidebarUser">
    <div class="uav">GS</div>
    <div><div style="color:#E5E7EB;font-size:13px;font-weight:600" id="sbRestName">Restaurant</div><div style="color:#6B7280;font-size:11px" id="sbUserEmail">Admin</div></div>
  </div>
</div>

<div class="main">
  <div class="topbar">
    <div><h1 id="pageTitle">Vue d&#39;ensemble</h1><span style="font-size:12px;color:var(--tm);font-weight:500" id="currentDate"></span></div>
    <div style="display:flex;align-items:center;gap:14px">
      <div style="display:flex;align-items:center;gap:8px;font-size:11px;color:var(--tm);font-weight:500">
        <span style="display:flex;align-items:center;gap:3px"><span class="dot" style="background:#25D366"></span> WhatsApp</span>
        <span style="display:flex;align-items:center;gap:3px"><span class="dot" style="background:#F59E0B"></span> Zenchef</span>
      </div>
      <div class="sp" id="statusPill" style="background:var(--okb)"><div class="sd2" id="statusDot" style="background:var(--ok)"></div> <span id="statusLabel" style="color:var(--ok);font-size:12px;font-weight:600">En ligne</span></div>
      <span style="font-size:13px;color:var(--tm);font-weight:500" id="currentTime"></span>
    </div>
  </div>

  <div class="content" id="mainContent">
  </div>
</div>
</div>

<!-- RESERVATION MODAL -->
<div class="modal-bg" id="resaModal" onclick="if(event.target===this)closeResaModal()">
<div class="modal">
  <h2>Nouvelle reservation</h2>
  <div class="card-s" style="margin-bottom:20px">Remplissez les informations du client <span id="resaDateLabel" style="font-weight:600;color:var(--ac)"></span></div>
  <div class="finp-row"><div class="finp-group"><div class="finp-label">Prenom</div><input class="finp" id="resaFirst" placeholder="Marie"></div><div class="finp-group"><div class="finp-label">Nom</div><input class="finp" id="resaLast" placeholder="Laurent"></div></div>
  <div class="finp-row"><div class="finp-group"><div class="finp-label">Personnes</div><input class="finp" id="resaCovers" type="number" min="1" max="20" value="2" onchange="resaAutoAssign()"></div><div class="finp-group"><div class="finp-label">Heure</div><input class="finp" id="resaTime" type="time" value="20:00" onchange="resaAutoAssign()"></div></div>
  <div class="finp-row"><div class="finp-group"><div class="finp-label">Telephone</div><input class="finp" id="resaPhone" placeholder="+33 6 ..."></div><div class="finp-group"><div class="finp-label">Email</div><input class="finp" id="resaEmail" placeholder="marie@email.com"></div></div>
  <div class="finp-group"><div class="finp-label">Source</div><select class="finp" id="resaSource" style="cursor:pointer"><option value="phone">Telephone</option><option value="walk-in">Walk-in</option><option value="whatsapp">WhatsApp</option><option value="web">Chat web</option><option value="zenchef">Zenchef</option></select></div>
  <div class="at-box" id="resaTableBox"><div class="at-l">Table assignee automatiquement</div><div class="at-v" id="resaTableVal"></div><div class="at-c" onclick="showResaTableSelect()">Changer de table</div></div>
  <div class="tsel" id="resaTableSel"></div>
  <div class="modal-act"><button class="mbtn mbtn-s" onclick="closeResaModal()">Annuler</button><button class="mbtn mbtn-p" onclick="submitResa()">Confirmer</button></div>
</div>
</div>

<!-- EDIT RESERVATION MODAL -->
<div class="modal-bg" id="editResaModal" onclick="if(event.target===this)closeEditResa()">
<div class="modal">
  <h2>Modifier la reservation</h2>
  <div class="finp-group"><div class="finp-label">Nom</div><input class="finp" id="editResaName"></div>
  <div class="finp-row"><div class="finp-group"><div class="finp-label">Personnes</div><input class="finp" id="editResaCovers" type="number" min="1" max="20"></div><div class="finp-group"><div class="finp-label">Heure</div><input class="finp" id="editResaTime" type="time"></div></div>
  <div class="finp-group"><div class="finp-label">Telephone</div><input class="finp" id="editResaPhone"></div>
  <div class="finp-group"><div class="finp-label">Table</div><select class="finp" id="editResaTable" style="cursor:pointer"></select></div>
  <div class="modal-act"><button class="mbtn mbtn-s" onclick="deleteResa()" style="color:#EF4444">Supprimer</button><button class="mbtn mbtn-s" onclick="closeEditResa()">Annuler</button><button class="mbtn mbtn-p" onclick="saveEditResa()">Enregistrer</button></div>
</div>
</div>

<div class="mobile-nav" id="mobileNav">
  <div class="mobile-nav-items">
    <button class="mobile-nav-btn active" data-pg="overview"><span>&#9673;</span>Accueil</button>
    <button class="mobile-nav-btn" data-pg="bookings"><span>&#128197;</span>Resas</button>
    <button class="mobile-nav-btn" data-pg="conversations"><span>&#128172;</span>Chat</button>
    <button class="mobile-nav-btn" data-pg="contacts"><span>&#128101;</span>Contacts</button>
    <button class="mobile-nav-btn" id="mobileMoreBtn" onclick="toggleMobileMore()"><span>&#8943;</span>Plus</button>
  </div>
</div>
<div class="mobile-more-overlay" id="mobileMoreOverlay" onclick="closeMobileMore()"></div>
<div class="mobile-more-drawer" id="mobileMoreDrawer">
  <div class="mobile-more-handle"></div>
  <div class="mobile-more-grid">
    <button class="mobile-more-item" data-pg="floorplan"><span>&#8862;</span>Plan</button>
    <button class="mobile-more-item" data-pg="menu"><span>&#9680;</span>Menu</button>
    <button class="mobile-more-item" data-pg="reviews"><span>&#9733;</span>Avis</button>
    <button class="mobile-more-item" data-pg="waitlist"><span>&#9201;</span>Attente</button>
    <button class="mobile-more-item" data-pg="stats"><span>&#9899;</span>Stats</button>
    <button class="mobile-more-item" data-pg="config"><span>&#9881;</span>Config</button>
    <button class="mobile-more-item" data-pg="account"><span>&#128100;</span>Compte</button>
  </div>
</div>

<div class="toast" id="toast"></div>
<div id="onboardingOverlay" style="display:none"></div>

<!-- HELP ASSISTANT -->
<style>
.help-btn{position:fixed;bottom:20px;right:20px;z-index:600;width:48px;height:48px;border-radius:50%;background:var(--acg);color:white;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:20px;box-shadow:0 4px 16px rgba(45,125,210,.3);transition:all .2s}
@media(max-width:768px){.help-btn{bottom:76px}.help-panel{bottom:134px}}
.help-btn:hover{transform:scale(1.08)}
.help-btn.open{transform:rotate(45deg)}
.help-panel{position:fixed;bottom:78px;right:20px;z-index:600;width:340px;max-height:460px;border-radius:14px;background:var(--c);border:1.5px solid var(--b);box-shadow:0 16px 48px rgba(0,0,0,.25);display:none;flex-direction:column;overflow:hidden}
.help-panel.show{display:flex}
.help-hd{padding:14px 18px;background:var(--acg);display:flex;align-items:center;gap:10px}
.help-hd-title{font-size:14px;font-weight:700;color:white}
.help-hd-sub{font-size:10px;color:rgba(255,255,255,.7)}
.help-msgs{flex:1;padding:14px;overflow-y:auto;display:flex;flex-direction:column;gap:8px;min-height:180px;max-height:300px}
.help-msg{max-width:85%;padding:8px 12px;border-radius:10px;font-size:12px;line-height:1.5}
.help-msg.bot{background:var(--bg);color:var(--t);align-self:flex-start;border-bottom-left-radius:3px}
.help-msg.user{background:var(--acg);color:white;align-self:flex-end;border-bottom-right-radius:3px}
.help-inp{padding:10px 14px;border-top:1px solid var(--b);display:flex;gap:6px}
.help-inp input{flex:1;padding:8px 12px;border-radius:16px;border:1px solid var(--b);background:var(--bg);color:var(--t);font-size:12px;font-family:var(--f);outline:none}
.help-inp input:focus{border-color:var(--ac)}
.help-inp button{width:32px;height:32px;border-radius:50%;background:var(--acg);color:white;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.help-quick{padding:6px 14px;display:flex;flex-wrap:wrap;gap:4px}
.help-quick button{padding:4px 10px;border-radius:12px;border:1px solid var(--b);background:transparent;color:var(--ts);font-size:10px;font-family:var(--f);cursor:pointer}
.help-quick button:hover{border-color:var(--ac);color:var(--ac)}
</style>

<button class="help-btn" id="helpBtn" onclick="toggleHelp()">?</button>
<div class="help-panel" id="helpPanel">
<div class="help-hd"><div><div class="help-hd-title">Assistant GuestScale</div><div class="help-hd-sub">Je peux vous aider</div></div></div>
<div class="help-msgs" id="helpMsgs"></div>
<div class="help-quick" id="helpQuick">
<button onclick="helpSend('Ajouter une table')">Ajouter une table</button>
<button onclick="helpSend('Modifier les horaires')">Modifier les horaires</button>
<button onclick="helpSend('Voir les stats')">Voir les stats</button>
<button onclick="helpSend('Gérer la liste d attente')">Liste d'attente</button>
</div>
<div class="help-inp">
<input type="text" id="helpInput" placeholder="Posez une question..." onkeydown="if(event.key==='Enter')helpSendInput()">
<button onclick="helpSendInput()">&#10148;</button>
</div>
</div>

<script>
// === AUTH ===
var TOKEN=null;
var USER_DATA=null;
var dailyMsg='';
var resaSelTable=null;
var selectedDate=fmtDate(new Date());

function fmtDate(d){var y=d.getFullYear();var m=String(d.getMonth()+1).padStart(2,'0');var day=String(d.getDate()).padStart(2,'0');return y+'-'+m+'-'+day}
function parseDateLocal(s){var p=s.split('-');return new Date(parseInt(p[0]),parseInt(p[1])-1,parseInt(p[2]))}
var MONTH_NAMES=["Janvier","Fevrier","Mars","Avril","Mai","Juin","Juillet","Aout","Septembre","Octobre","Novembre","Decembre"];
var MONTH_SHORT=["Jan","Fev","Mar","Avr","Mai","Jun","Jul","Aou","Sep","Oct","Nov","Dec"];
var DOW_NAMES=["L","M","M","J","V","S","D"];
var calPickerMode=null;

function buildCalendar(){
  var sel=parseDateLocal(selectedDate);
  var today=fmtDate(new Date());
  var year=sel.getFullYear();
  var month=sel.getMonth();
  var firstDay=new Date(year,month,1).getDay();
  var startIdx=(firstDay+6)%7;
  var daysInMonth=new Date(year,month+1,0).getDate();
  var daysInPrev=new Date(year,month,0).getDate();
  var h='<div class="cal-wrap" id="calWidget">';
  h+='<div class="cal-header">';
  h+='<div class="cal-nav"><div class="cal-arrow" data-calShift="-1">&#8249;</div>';
  h+='<div class="cal-title" data-calTogglePicker>'+MONTH_NAMES[month]+' '+year+'</div>';
  h+='<div class="cal-arrow" data-calShift="1">&#8250;</div></div>';
  h+='<div class="cal-today-btn" data-calToday>Aujourd&#39;hui</div>';
  h+='</div>';
  h+='<div class="cal-picker" id="calPicker"></div>';
  h+='<div class="cal-grid">';
  DOW_NAMES.forEach(function(d){h+='<div class="cal-dow">'+d+'</div>'});
  for(var i=startIdx-1;i>=0;i--){
    var day=daysInPrev-i;
    var pm=month===0?11:month-1;var py=month===0?year-1:year;
    var ds=fmtDate(new Date(py,pm,day));
    var cnt=bookings.filter(function(b){return(b.date||"").startsWith(ds)}).length;
    h+='<div class="cal-cell other" data-calDate="'+ds+'"><span class="cal-num">'+day+'</span><span class="cal-dot'+(cnt>0?" has":"")+'"></span></div>';
  }
  for(var d=1;d<=daysInMonth;d++){
    var ds=fmtDate(new Date(year,month,d));
    var isToday=ds===today;var isSel=ds===selectedDate;
    var cnt=bookings.filter(function(b){return(b.date||"").startsWith(ds)}).length;
    h+='<div class="cal-cell'+(isSel?" sel":"")+(isToday&&!isSel?" today":"")+'\" data-calDate="'+ds+'"><span class="cal-num">'+d+'</span><span class="cal-dot'+(cnt>0?" has":"")+'"></span></div>';
  }
  var total=startIdx+daysInMonth;var remaining=(7-total%7)%7;
  for(var i=1;i<=remaining;i++){
    var nm=month===11?0:month+1;var ny=month===11?year+1:year;
    var ds=fmtDate(new Date(ny,nm,i));
    var cnt=bookings.filter(function(b){return(b.date||"").startsWith(ds)}).length;
    h+='<div class="cal-cell other" data-calDate="'+ds+'"><span class="cal-num">'+i+'</span><span class="cal-dot'+(cnt>0?" has":"")+'"></span></div>';
  }
  h+='</div></div>';
  return h;
}

function showCalPicker(mode){
  var el=document.getElementById("calPicker");
  if(!el)return;
  calPickerMode=mode;
  var sel=parseDateLocal(selectedDate);
  var h='<div class="cal-picker-grid">';
  if(mode==="month"){
    MONTH_SHORT.forEach(function(m,i){
      h+='<div class="cal-picker-item'+(i===sel.getMonth()?" sel":"")+'\" data-calPickMonth="'+i+'">'+m+'</div>';
    });
  }else{
    var cy=sel.getFullYear();
    for(var y=cy-4;y<=cy+4;y++){
      h+='<div class="cal-picker-item'+(y===cy?" sel":"")+'\" data-calPickYear="'+y+'">'+y+'</div>';
    }
  }
  h+='</div>';
  el.innerHTML=h;
  el.classList.add("show");
}

function getBookingsForDate(dateStr){
  return bookings.filter(function(b){return(b.date||"").startsWith(dateStr)});
}

function getToken(){
  if(TOKEN)return TOKEN;
  try{TOKEN=sessionStorage.getItem('gs_token')}catch(e){}
  return TOKEN;
}

function apiFetch(url,opts){
  opts=opts||{};
  opts.headers=opts.headers||{};
  var t=getToken();
  if(t)opts.headers['Authorization']='Bearer '+t;
  if(!opts.headers['Content-Type']&&opts.body)opts.headers['Content-Type']='application/json';
  return fetch(url,opts);
}

function doLogin(){
  var email=document.getElementById('loginEmail').value.trim();
  var pwd=document.getElementById('loginPwd').value;
  var err=document.getElementById('loginError');
  if(!email||!pwd){err.style.display='block';err.textContent='Veuillez remplir email et mot de passe.';return}
  fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email,password:pwd})})
  .then(function(r){return r.json()})
  .then(function(d){
    if(d.token){
      TOKEN=d.token;
      USER_DATA=d.user;
      try{sessionStorage.setItem('gs_token',d.token)}catch(e){}
      document.getElementById('loginOverlay').style.display='none';
      document.getElementById('app').classList.add('v');
      if(USER_DATA){
        document.getElementById('sbRestName').textContent=USER_DATA.restaurant_name||'Restaurant';
        document.getElementById('sbUserEmail').textContent=USER_DATA.email||'';
      }
      loadAll();
    }else{
      err.style.display='block';
      err.textContent=d.error||'Identifiants incorrects.';
      document.getElementById('loginPwd').style.borderColor='var(--da)';
      document.getElementById('loginPwd').classList.remove('shake');
      void document.getElementById('loginPwd').offsetWidth;
      document.getElementById('loginPwd').classList.add('shake');
    }
  })
  .catch(function(){
    err.style.display='block';
    err.textContent='Erreur de connexion au serveur.';
  });
}

// Auto-login if token exists
(function(){
  var t=null;
  try{t=sessionStorage.getItem('gs_token')}catch(e){}
  if(t){
    TOKEN=t;
    apiFetch('/api/me').then(function(r){return r.json()}).then(function(d){
      if(d.user){
        USER_DATA=d.user;
        document.getElementById('loginOverlay').style.display='none';
        document.getElementById('app').classList.add('v');
        document.getElementById('sbRestName').textContent=d.user.restaurant_name||'Restaurant';
        document.getElementById('sbUserEmail').textContent=d.user.email||'';
        loadAll();
      }else{
        TOKEN=null;
        try{sessionStorage.removeItem('gs_token')}catch(e){}
      }
    }).catch(function(){TOKEN=null;try{sessionStorage.removeItem('gs_token')}catch(e){}});
  }
})();

function togglePwdVis(){
  var inp=document.getElementById('loginPwd');
  var btn=document.getElementById('pwdToggle');
  if(inp.type==='password'){inp.type='text';btn.textContent='🔒'}
  else{inp.type='password';btn.textContent='👁'}
}

function showForgotPwd(){
  document.querySelector('.lcd').style.display='none';
  document.getElementById('forgotPwdForm').style.display='block';
}
function hideForgotPwd(){
  document.getElementById('forgotPwdForm').style.display='none';
  document.querySelector('.lcd').style.display='block';
  document.getElementById('forgotStep1').style.display='block';
  document.getElementById('forgotStep2').style.display='none';
  document.getElementById('forgotError').style.display='none';
  document.getElementById('forgotSuccess').style.display='none';
}
function sendResetCode(){
  var email=document.getElementById('forgotEmail').value.trim();
  if(!email){document.getElementById('forgotError').textContent='Entrez votre email';document.getElementById('forgotError').style.display='block';return}
  document.getElementById('forgotError').style.display='none';
  fetch('/api/forgot-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email})})
  .then(function(r){return r.json()}).then(function(d){
    if(d.error){document.getElementById('forgotError').textContent=d.error;document.getElementById('forgotError').style.display='block';return}
    document.getElementById('forgotSuccess').textContent='Code envoye ! Verifiez votre email.';
    document.getElementById('forgotSuccess').style.display='block';
    document.getElementById('forgotStep1').style.display='none';
    document.getElementById('forgotStep2').style.display='block';
  });
}
function doResetPwd(){
  var code=document.getElementById('resetCode').value.trim();
  var pwd=document.getElementById('newPwd').value;
  if(!code||!pwd){document.getElementById('forgotError').textContent='Code et mot de passe requis';document.getElementById('forgotError').style.display='block';return}
  document.getElementById('forgotError').style.display='none';
  fetch('/api/reset-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:code,new_password:pwd})})
  .then(function(r){return r.json()}).then(function(d){
    if(d.error){document.getElementById('forgotError').textContent=d.error;document.getElementById('forgotError').style.display='block';return}
    document.getElementById('forgotSuccess').textContent='Mot de passe modifie ! Vous pouvez vous connecter.';
    document.getElementById('forgotSuccess').style.display='block';
    document.getElementById('forgotStep2').style.display='none';
    setTimeout(hideForgotPwd,3000);
  });
}

function doLogout(){
  TOKEN=null;USER_DATA=null;
  try{sessionStorage.removeItem('gs_token')}catch(e){}
  location.reload();
}

/* Auto-logout after 2h of inactivity */
var _idleTimer=null;
function resetIdleTimer(){
  if(_idleTimer)clearTimeout(_idleTimer);
  _idleTimer=setTimeout(function(){
    if(TOKEN){showToast('Session expirée — reconnexion nécessaire');doLogout()}
  },7200000); /* 2 hours */
}
['mousemove','keydown','click','scroll','touchstart'].forEach(function(ev){
  document.addEventListener(ev,resetIdleTimer,{passive:true});
});
resetIdleTimer();

/* Global error handler */
window.addEventListener('unhandledrejection',function(e){
  if(e.reason&&e.reason.message&&e.reason.message.indexOf('401')!==-1){doLogout()}
});

/* HTML escape — sanitize user data before innerHTML injection */
function esc(s){if(!s)return '';var d=document.createElement('div');d.textContent=String(s);return d.innerHTML}

var pageTitles={overview:"Vue d'ensemble",floorplan:"Plan de salle",bookings:"Réservations",menu:"Menu",conversations:"Conversations",reviews:"Avis",contacts:"Contacts",config:"Configuration",stats:"Statistiques",account:"Mon compte",waitlist:"Liste d'attente"};

function toggleMobileMore(){
  var ov=document.getElementById('mobileMoreOverlay');
  var dr=document.getElementById('mobileMoreDrawer');
  var isOpen=dr.classList.contains('show');
  if(isOpen){closeMobileMore()}
  else{ov.classList.add('show');dr.classList.add('show')}
}
function closeMobileMore(){
  document.getElementById('mobileMoreOverlay').classList.remove('show');
  document.getElementById('mobileMoreDrawer').classList.remove('show');
}

var morePages=['floorplan','menu','reviews','waitlist','stats','config','account'];

function switchPage(id,btn){
  currentPage=id;
  document.getElementById('pageTitle').textContent=pageTitles[id]||id;
  document.querySelectorAll('.nb').forEach(function(b){b.classList.remove('on')});
  if(btn&&btn.classList&&!btn.classList.contains('mobile-nav-btn')&&!btn.classList.contains('mobile-more-item'))btn.classList.add('on');
  else{var b=document.querySelector('.sidebar [data-pg="'+id+'"]');if(b)b.classList.add('on')}
  /* Mobile bottom nav */
  document.querySelectorAll('.mobile-nav-btn').forEach(function(b){b.classList.remove('active')});
  var mb=document.querySelector('.mobile-nav-btn[data-pg="'+id+'"]');
  if(mb){mb.classList.add('active')}
  else if(morePages.indexOf(id)!==-1){
    document.getElementById('mobileMoreBtn').classList.add('active');
  }
  /* Mobile more drawer items */
  document.querySelectorAll('.mobile-more-item').forEach(function(b){b.classList.remove('active')});
  var mi=document.querySelector('.mobile-more-item[data-pg="'+id+'"]');
  if(mi)mi.classList.add('active');
  closeMobileMore();
  renderPage(id);
  window.scrollTo({top:0,behavior:'smooth'});
}

function showToast(msg){var t=document.getElementById('toast');t.textContent=msg;t.style.display='block';setTimeout(function(){t.style.display='none'},2500)}

function updateTime(){var n=new Date();document.getElementById('currentDate').textContent=n.toLocaleDateString('fr-FR',{weekday:'long',day:'numeric',month:'long'});document.getElementById('currentTime').textContent=n.toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'})}

// ===== DATA =====
var bookings=[],contacts={},conversations={},floorplan=[],reviewQueue=[],waitlistEntries=[];
var floorSlots={};
var cancelledCount=0;
var restaurantConfig={};
var overviewBlocks={daily:true,stats:true,floor:true,bookings:true,contacts:true};

function mergeBookingsIntoFloor(){
  var tableBookings={};
  bookings.forEach(function(b){
    if(b.table && (b.date||'').startsWith(selectedDate)){
      b.table.split('+').forEach(function(tid){tableBookings[tid.trim()]=b.name});
    }
  });
  floorplan.forEach(function(t){
    t.booking_name=tableBookings[t.id]||null;
  });
}

var currentPage='overview';
var lastVersion=0;

function loadAll(){
  updateTime();setInterval(updateTime,30000);
  fetchData();
  setInterval(checkUpdates,3000);
}

function checkUpdates(){
  apiFetch('/api/version').then(function(r){return r.json()}).then(function(d){
    if(d.v&&d.v!==lastVersion){
      lastVersion=d.v;
      fetchDataSilent();
    }
  }).catch(function(){});
}

function fetchDataSilent(){
  var ok=function(r){return r.ok?r.json():Promise.resolve(null)};
  Promise.all([
    apiFetch('/api/bookings').then(ok).catch(function(){return null}),
    apiFetch('/api/contacts').then(ok).catch(function(){return null}),
    apiFetch('/api/conversations').then(ok).catch(function(){return null}),
    apiFetch('/api/floorplan').then(ok).catch(function(){return null}),
    apiFetch('/api/reviews').then(ok).catch(function(){return null}),
    apiFetch('/api/daily').then(ok).catch(function(){return null}),
    apiFetch('/api/menu').then(ok).catch(function(){return null}),
    apiFetch('/api/waitlist').then(ok).catch(function(){return null})
  ]).then(function(res){
    if(res[0])bookings=(res[0].bookings)||[];
    if(res[1]){contacts={};(res[1].contacts||[]).forEach(function(c){if(c.phone)contacts[c.phone]=c})}
    if(res[2]){conversations={};(res[2].conversations||[]).forEach(function(cv){conversations[cv.phone||cv.id]=cv})}
    if(res[3]){floorplan=(res[3].tables||[]);floorSlots=(res[3].slots||{});mergeBookingsIntoFloor()}
    if(res[4])reviewQueue=(res[4].queue||[]);
    if(res[5])dailyMsg=(res[5].message)||'';
    if(res[6])menuSections=(res[6].sections)||[];
    if(res[7])waitlistEntries=(res[7].waitlist)||[];
    updateBadges();
    if(currentPage==='overview'||currentPage==='bookings'||currentPage==='conversations'||currentPage==='floorplan'||currentPage==='waitlist')renderPage(currentPage);
  }).catch(function(){});
}

function fetchData(){
  var ok=function(r){return r.ok?r.json():Promise.resolve(null)};
  Promise.all([
    apiFetch('/api/bookings').then(ok).catch(function(){return []}),
    apiFetch('/api/contacts').then(ok).catch(function(){return {}}),
    apiFetch('/api/conversations').then(ok).catch(function(){return {}}),
    apiFetch('/api/floorplan').then(ok).catch(function(){return []}),
    apiFetch('/api/reviews').then(ok).catch(function(){return []}),
    apiFetch('/api/config').then(ok).catch(function(){return {}}),
    apiFetch('/api/daily').then(ok).catch(function(){return {message:''}}),
    apiFetch('/api/menu').then(ok).catch(function(){return {sections:[]}}),
    apiFetch('/api/waitlist').then(ok).catch(function(){return {waitlist:[]}})
  ]).then(function(res){
    bookings=(res[0]&&res[0].bookings)||[];
    var ctData=res[1]||{};
    contacts={};
    (ctData.contacts||[]).forEach(function(c){if(c.phone)contacts[c.phone]=c});
    var convData=res[2]||{};
    conversations={};
    (convData.conversations||[]).forEach(function(cv){conversations[cv.phone||cv.id]=cv});
    var fpData=res[3]||{};
    floorplan=(fpData.tables||[]);
    floorSlots=(fpData.slots||{});
    mergeBookingsIntoFloor();
    var rvData=res[4]||{};
    reviewQueue=(rvData.queue||[]);
    restaurantConfig=res[5]||{};
    // Load reminders setting
    apiFetch('/api/settings').then(function(r){return r.ok?r.json():null}).then(function(s){
      if(s&&typeof s.reminders_enabled!=='undefined')restaurantConfig._reminders_enabled=s.reminders_enabled;
    }).catch(function(){});
    dailyMsg=(res[6]&&res[6].message)||'';
    menuSections=(res[7]&&res[7].sections)||[];
    waitlistEntries=(res[8]&&res[8].waitlist)||[];
    updateBadges();
    renderPage(currentPage||'overview');
    checkOnboarding();
  }).catch(function(err){
    console.error('Load error:',err);
    renderPage(currentPage||'overview');
  });
}

function updateBadges(){
  var today=fmtDate(new Date());
  var todayBookings=bookings.filter(function(b){return(b.date||'').startsWith(today)});
  document.getElementById('bookBadge').textContent=todayBookings.length;
  var convCount=Object.keys(conversations).length;
  document.getElementById('convBadge').textContent=convCount;
  var pendingReviews=reviewQueue.filter(function(r){return!r.sent}).length;
  document.getElementById('reviewBadge').textContent=pendingReviews;
  var waitingCount=waitlistEntries.filter(function(w){return w.status==='waiting'||w.status==='notified'}).length;
  var wb=document.getElementById('waitBadge');if(wb)wb.textContent=waitingCount;
}

// ===== PAGE RENDERER =====
function renderPage(id){
  var c=document.getElementById('mainContent');
  if(id==='overview') renderOverview(c);
  else if(id==='floorplan') renderFloorplan(c);
  else if(id==='bookings') renderBookings(c);
  else if(id==='menu') renderMenu(c);
  else if(id==='conversations') renderConversations(c);
  else if(id==='reviews') renderReviews(c);
  else if(id==='contacts') renderContacts(c);
  else if(id==='config') renderConfig(c);
  else if(id==='stats') renderStats(c);
  else if(id==='account') renderAccount(c);
  else if(id==='waitlist') renderWaitlist(c);
}

// ===== ACCOUNT PAGE =====
function renderAccount(c){
  var u=USER_DATA||{};
  var h='';
  h+='<div class="card" style="padding:24px;margin-bottom:16px"><div class="cfs"><div class="cft">Informations du compte</div><div class="cfsb">Gérez votre compte et votre abonnement</div>';
  h+='<div class="cfr"><div><div class="cfl">Email</div><div class="cfd">'+(u.email||'—')+'</div></div></div>';
  h+='<div class="cfr"><div><div class="cfl">Nom</div><div class="cfd">'+(u.first_name||'')+' '+(u.last_name||'')+'</div></div></div>';
  h+='<div class="cfr"><div><div class="cfl">Restaurant</div><div class="cfd">'+(u.restaurant_name||'—')+'</div></div></div>';
  h+='<div class="cfr"><div><div class="cfl">Statut</div><div class="cfd"><span class="badge" style="background:'+(u.restaurant_status==='active'?'var(--okb)':'var(--wab)')+';color:'+(u.restaurant_status==='active'?'var(--ok)':'var(--wa)')+'">'+(u.restaurant_status==='active'?'Actif':'Essai gratuit')+'</span></div></div></div>';
  if(u.trial_ends_at){
    var te=new Date(u.trial_ends_at);
    var now=new Date();
    var days=Math.ceil((te-now)/(1000*60*60*24));
    if(days>0){
      h+='<div class="cfr"><div><div class="cfl">Fin de l essai</div><div class="cfd">'+te.toLocaleDateString('fr-FR')+' ('+days+' jours restants)</div></div></div>';
    }
  }
  h+='</div></div>';
  // Change password
  h+='<div class="card" style="padding:24px;margin-bottom:16px"><div class="cfs"><div class="cft">Changer le mot de passe</div>';
  h+='<div class="finp-group" style="margin-top:12px"><div class="finp-label">Mot de passe actuel</div><input class="finp" type="password" id="accCurPwd"></div>';
  h+='<div class="finp-group"><div class="finp-label">Nouveau mot de passe</div><input class="finp" type="password" id="accNewPwd"></div>';
  h+='<div class="finp-group"><div class="finp-label">Confirmer</div><input class="finp" type="password" id="accNewPwd2"></div>';
  h+='<button class="ba" style="margin-top:8px" onclick="changePassword()">Modifier le mot de passe</button>';
  h+='</div></div>';
  // Logout
  h+='<div class="card" style="padding:24px"><button style="padding:10px 20px;border-radius:8px;border:1px solid var(--da);background:transparent;color:var(--da);font-size:13px;font-weight:700;cursor:pointer;font-family:var(--f)" onclick="doLogout()">Se deconnecter</button></div>';
  c.innerHTML=h;
}

function changePassword(){
  var cur=document.getElementById('accCurPwd').value;
  var np=document.getElementById('accNewPwd').value;
  var np2=document.getElementById('accNewPwd2').value;
  if(!cur||!np){showToast('Remplissez tous les champs');return}
  if(np!==np2){showToast('Les mots de passe ne correspondent pas');return}
  if(np.length<12){showToast('Minimum 12 caractères');return}
  apiFetch('/api/change-password',{method:'POST',body:JSON.stringify({current_password:cur,new_password:np})})
  .then(function(r){return r.json()}).then(function(d){
    if(d.status==='ok')showToast('Mot de passe modifie');
    else showToast(d.error||'Erreur');
  }).catch(function(){showToast('Erreur')});
}

// ===== WAITLIST =====
function renderWaitlist(c){
  var today=fmtDate(new Date());
  var active=waitlistEntries.filter(function(w){return w.status==='waiting'||w.status==='notified'});
  var past=waitlistEntries.filter(function(w){return w.status==='accepted'||w.status==='declined'||w.status==='expired'});
  var h='';
  // Add to waitlist form
  h+='<div class="card" style="padding:20px;margin-bottom:16px"><div class="card-t" style="margin-bottom:14px">Ajouter a la liste d&#39;attente</div>';
  h+='<div class="finp-row"><div class="finp-group"><div class="finp-label">Nom</div><input class="finp" id="wlName" placeholder="Marie Laurent"></div><div class="finp-group"><div class="finp-label">Telephone</div><input class="finp" id="wlPhone" placeholder="+33 6 ..."></div></div>';
  h+='<div class="finp-row"><div class="finp-group"><div class="finp-label">Personnes</div><input class="finp" id="wlCovers" type="number" min="1" max="20" value="2"></div><div class="finp-group"><div class="finp-label">Service</div><select class="finp" id="wlService"><option value="midi">Midi</option><option value="soir" selected>Soir</option></select></div></div>';
  h+='<div class="finp-row"><div class="finp-group"><div class="finp-label">Date</div><input class="finp" id="wlDate" type="date" value="'+today+'"></div><div class="finp-group"><div class="finp-label">Heure souhaitee</div><input class="finp" id="wlTime" type="time" value="20:00"></div></div>';
  h+='<button class="ba" style="margin-top:8px" onclick="addToWaitlist()">Ajouter</button></div>';
  // Active waitlist
  h+='<div class="card" style="margin-bottom:16px"><div class="card-h"><div><div class="card-t">En attente</div><div class="card-s">'+active.length+' personnes</div></div></div>';
  if(!active.length){
    h+='<div style="padding:24px;text-align:center;color:var(--tm);font-size:13px">Aucune personne en liste d&#39;attente</div>';
  }else{
    active.forEach(function(w,i){
      var statusBg=w.status==='notified'?'var(--al)':'var(--wab)';
      var statusCol=w.status==='notified'?'var(--ac)':'var(--wa)';
      var statusLabel=w.status==='notified'?'Notifie':'En attente';
      h+='<div style="padding:14px 16px;border-bottom:1px solid var(--bl);display:flex;justify-content:space-between;align-items:center">';
      h+='<div><div style="font-size:14px;font-weight:600">'+w.name+'</div>';
      h+='<div style="font-size:12px;color:var(--tm);margin-top:2px">'+w.covers+'p · '+(w.service==='midi'?'Midi':'Soir')+' · '+w.date+(w.preferred_time?' · '+w.preferred_time:'')+'</div>';
      if(w.phone)h+='<div style="font-size:11px;color:var(--ts);margin-top:2px">'+w.phone+'</div>';
      h+='</div>';
      h+='<div style="display:flex;align-items:center;gap:8px">';
      h+='<span class="badge" style="background:'+statusBg+';color:'+statusCol+'">'+statusLabel+'</span>';
      if(w.status==='waiting')h+='<button style="padding:4px 10px;border-radius:6px;border:1px solid var(--ac);background:var(--al);color:var(--ac);font-size:11px;font-weight:700;cursor:pointer;font-family:var(--f)" data-wlNotify="'+w.id+'|'+w.date+'|'+w.service+'|'+w.covers+'">Notifier</button>';
      h+='<button style="padding:4px 10px;border-radius:6px;border:1px solid var(--b);background:var(--card);color:var(--da);font-size:11px;font-weight:700;cursor:pointer;font-family:var(--f)" data-wlRemove="'+w.id+'">Retirer</button>';
      h+='</div></div>';
    });
  }
  h+='</div>';
  // History
  if(past.length){
    h+='<div class="card"><div class="card-h"><div><div class="card-t">Historique</div><div class="card-s">'+past.length+' entries</div></div></div>';
    past.slice(-20).reverse().forEach(function(w){
      var sCol=w.status==='accepted'?'var(--ok)':w.status==='declined'?'var(--da)':'var(--tm)';
      var sLabel=w.status==='accepted'?'Accepte':w.status==='declined'?'Decline':'Expire';
      h+='<div style="padding:10px 16px;border-bottom:1px solid var(--bl);display:flex;justify-content:space-between;align-items:center;opacity:.7">';
      h+='<div><span style="font-weight:600">'+w.name+'</span> <span style="color:var(--tm);font-size:12px">'+w.covers+'p · '+w.date+'</span></div>';
      h+='<span style="font-size:12px;font-weight:600;color:'+sCol+'">'+sLabel+'</span></div>';
    });
    h+='</div>';
  }
  c.innerHTML=h;
}

function addToWaitlist(){
  var name=document.getElementById('wlName').value.trim();
  var phone=document.getElementById('wlPhone').value.trim();
  var covers=document.getElementById('wlCovers').value;
  var service=document.getElementById('wlService').value;
  var wdate=document.getElementById('wlDate').value;
  var wtime=document.getElementById('wlTime').value;
  if(!name){showToast('Nom requis');return}
  apiFetch('/api/waitlist/add',{method:'POST',body:JSON.stringify({name:name,phone:phone,covers:parseInt(covers),service:service,date:wdate,time:wtime})})
  .then(function(r){return r.json()}).then(function(d){
    if(d.status==='ok'){showToast(name+' ajoute a la liste');fetchData();}
    else showToast(d.error||'Erreur');
  });
}

function removeWaitlist(id){
  apiFetch('/api/waitlist/remove',{method:'POST',body:JSON.stringify({id:id})})
  .then(function(r){return r.json()}).then(function(d){
    if(d.status==='removed'){showToast('Retire de la liste');fetchData();}
  });
}

function notifyWaitlist(id,wdate,service,covers){
  apiFetch('/api/waitlist/notify',{method:'POST',body:JSON.stringify({date:wdate,service:service,covers:parseInt(covers)})})
  .then(function(r){return r.json()}).then(function(d){
    if(d.status==='ok'){showToast('Notification envoyee');fetchData();}
  });
}

// ===== OVERVIEW =====
function renderOverview(c){
  var tb=getBookingsForDate(selectedDate);
  var convArr=Object.entries(conversations);
  var ctArr=Object.entries(contacts);
  var totalSeats=floorplan.reduce(function(a,t){return a+t.seats},0);
  var today=fmtDate(new Date());
  var isToday=selectedDate===today;
  var dateLabel=isToday?"auj.":parseDateLocal(selectedDate).toLocaleDateString('fr-FR',{weekday:'short',day:'numeric',month:'short'});
  
  var h='';
  
  // Top layout: stats left + calendar right
  h+='<div class="ov-layout" style="display:flex;gap:14px;align-items:flex-start;margin-bottom:14px">';
  h+='<div style="flex:1;min-width:0">';
  
  // Daily message
  if(overviewBlocks.daily&&isToday){
    h+='<div class="db" id="ov-daily"><div class="db-top"><div class="di">📢</div><div style="flex:1"><div class="dlb">Message du jour <span style="font-weight:400;text-transform:none;letter-spacing:0;color:var(--ts)">— cliquez pour modifier</span></div>';
    h+='<div class="dtx" id="dailyView" onclick="editDaily()">'+(dailyMsg||'Aucun message — cliquez pour ajouter')+'</div>';
    h+='<textarea class="dtx-edit" id="dailyEdit" style="display:none"></textarea>';
    h+='<div class="dme" id="dailyMeta">Transmis automatiquement par l agent IA aux clients</div></div></div>';
    h+='<div class="db-act" id="dailyActions" style="display:none"><button class="dbb dbb-s" onclick="saveDaily()">💾 Enregistrer</button><button class="dbb dbb-b" onclick="broadcastDaily()">📤 Envoyer aux contacts</button><button class="dbb dbb-c" onclick="cancelDaily()">Annuler</button></div></div>';
  }
  
  // Stats
  if(overviewBlocks.stats){
    h+='<div class="sg" id="ov-stats">';
    h+='<div class="sc" data-nav="conversations" style="cursor:pointer"><div class="sl">Messages</div><div class="sv" style="color:var(--ac)">'+convArr.reduce(function(a,e){var d=e[1];return a+((d.messages&&d.messages.length)||d.count||0)},0)+'</div><div class="ss2">total</div></div>';
    h+='<div class="sc" data-nav="bookings" style="cursor:pointer"><div class="sl">Réservations</div><div class="sv" style="color:var(--ok)">'+tb.length+'</div><div class="ss2">'+dateLabel+'</div></div>';
    h+='<div class="sc" data-nav="conversations" style="cursor:pointer"><div class="sl">Conversations</div><div class="sv" style="color:var(--bl2)">'+convArr.length+'</div><div class="ss2">clients actifs</div></div>';
    h+='<div class="sc" data-nav="contacts" style="cursor:pointer"><div class="sl">Contacts</div><div class="sv" style="color:var(--wa)">'+ctArr.length+'</div><div class="ss2">en base</div></div>';
    h+='</div>';
  }
  h+='</div>'; // close left column
  
  // Calendar right column
  h+='<div style="width:280px;flex-shrink:0">';
  h+=buildCalendar();
  h+='</div>';
  h+='</div>'; // close ov-layout
  
  // Floor plan mini
  if(overviewBlocks.floor){
    h+='<div class="fm" id="ov-floor" data-nav="floorplan"><div style="display:flex;justify-content:space-between;align-items:center"><div><div class="card-t">Plan de salle</div><div class="card-s">'+floorplan.length+' tables · '+totalSeats+' places</div></div><span style="font-size:12px;color:var(--ac);font-weight:600">Modifier →</span></div><div class="fc" id="floorMiniCanvas"></div></div>';
  }
  
  // Bookings + Conversations
  if(overviewBlocks.bookings){
    var srcColors={whatsapp:'#25D366',web:'#2563EB',phone:'#A8A29E','walk-in':'#78716C',zenchef:'#FF6B35'};
    h+='<div class="g2" id="ov-book"><div class="card"><div class="card-h"><div><div class="card-t">Réservations</div><div class="card-s">'+tb.length+' '+dateLabel+'</div></div><button class="ba" onclick="openResaModal()">+ Nouvelle réservation</button></div>';
    tb.slice(0,5).forEach(function(b){
      h+='<div class="rw"><div class="rl"><div class="dot" style="background:'+(srcColors[b.source]||'#A8A29E')+'"></div><div><div style="font-size:14px;font-weight:600">'+b.name+'</div><div style="font-size:12px;color:var(--tm)">'+b.covers+'p · '+(b.booking_time||b.time||'')+'</div></div></div><span class="badge" style="background:var(--okb);color:var(--ok)">'+(b.table||'—')+'</span></div>';
    });
    if(tb.length===0) h+='<div style="padding:20px;text-align:center;color:var(--tm);font-size:13px">Aucune réservation '+dateLabel+'</div>';
    h+='</div>';
    
    // Conversations
    h+='<div class="card"><div class="card-h"><div><div class="card-t">Conversations</div><div class="card-s">'+convArr.length+' actives</div></div></div>';
    convArr.slice(0,4).forEach(function(e,i){
      var phone=e[0],data=e[1];
      var name=(contacts[phone]&&contacts[phone].name)||phone;
      var lastMsg=data.last_message||((data.messages&&data.messages.length)?data.messages[data.messages.length-1].content:'...');
      var colors=['var(--al)','var(--blb)','var(--okb)','var(--wab)'];
      var tcolors=['var(--ac)','var(--bl2)','var(--ok)','var(--wa)'];
      h+='<div class="cr"><div class="cav" style="background:'+colors[i%4]+';color:'+tcolors[i%4]+'">'+name.charAt(0).toUpperCase()+'</div><div style="flex:1;min-width:0"><div style="display:flex;justify-content:space-between"><span style="font-size:14px;font-weight:600">'+name+'</span></div><div class="cmsg">'+lastMsg+'</div></div></div>';
    });
    if(convArr.length===0) h+='<div style="padding:20px;text-align:center;color:var(--tm);font-size:13px">Aucune conversation</div>';
    h+='</div></div>';
  }
  
  // Contacts
  if(overviewBlocks.contacts){
    h+='<div class="card" style="padding:20px" id="ov-contacts"><div class="card-t" style="margin-bottom:4px">Base de contacts</div><div class="card-s" style="margin-bottom:16px">'+ctArr.length+' clients</div><div class="cg3">';
    ctArr.slice(0,6).forEach(function(e){
      var phone=e[0],ct=e[1];
      var srcColors2={whatsapp:'#25D366',web:'#2563EB',phone:'#A8A29E','walk-in':'#78716C',zenchef:'#FF6B35'};
      var srcLabels={whatsapp:'WhatsApp',web:'Web',phone:'Tél','walk-in':'Walk-in',zenchef:'Zenchef'};
      var src=ct.source||'phone';
      h+='<div class="cc"><div style="font-size:14px;font-weight:600">'+(ct.name||phone)+'</div><div style="font-size:12px;color:var(--tm);margin-top:4px">'+phone+'</div><div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px"><span style="font-size:11px;color:var(--ts)">'+(ct.visits||0)+' visite'+((ct.visits||0)>1?'s':'')+'</span><span class="src-badge" style="color:'+(srcColors2[src]||'#A8A29E')+';background:'+(srcColors2[src]||'#A8A29E')+'15">'+(srcLabels[src]||src)+'</span></div></div>';
    });
    h+='</div></div>';
  }
  
  c.innerHTML=h;
  if(overviewBlocks.floor&&floorplan.length>0) drawFloorMini();
}

// Daily message inline edit
function editDaily(){
  document.getElementById('dailyView').style.display='none';
  var ed=document.getElementById('dailyEdit');ed.style.display='block';ed.value=dailyMsg;ed.focus();
  ed.onkeydown=function(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();saveDaily()}};
  document.getElementById('dailyActions').style.display='flex';
  document.getElementById('dailyMeta').style.display='none';
}
function saveDaily(){
  dailyMsg=document.getElementById('dailyEdit').value.trim();
  // Save to backend
  apiFetch('/api/daily',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:dailyMsg})});
  document.getElementById('dailyView').textContent=dailyMsg||'Aucun message — cliquez pour ajouter';
  cancelDaily();
  showToast('💾 Message du jour enregistré');
}
function cancelDaily(){
  document.getElementById('dailyView').style.display='block';
  document.getElementById('dailyEdit').style.display='none';
  document.getElementById('dailyActions').style.display='none';
  document.getElementById('dailyMeta').style.display='block';
}
function broadcastDaily(){
  saveDaily();
  apiFetch('/api/broadcast',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:dailyMsg})});
  showToast('📤 Message envoyé aux contacts');
}

// Floor plan mini drawing
function drawFloorMini(){
  var el=document.getElementById('floorMiniCanvas');
  if(!el||!floorplan.length)return;
  el.querySelectorAll('.ftbl').forEach(function(e){e.remove()});
  var zoneColors={salle:'#2563EB',terrasse:'#16A34A',bar:'#D97706'};
  floorplan.forEach(function(t){
    var d=document.createElement('div');d.className='ftbl';
    var w=(t.shape==='round'?(t.seats<=2?34:t.seats<=4?40:48):(t.seats<=2?34:t.seats<=4?44:t.seats<=6?52:60))*.85;
    var h2=(t.shape==='round'?w:(t.seats<=4?34:38))*.85;
    var c=zoneColors[t.zone]||'#2563EB';
    var bk=t.booking_name;
    d.style.cssText='left:calc('+t.x+'% - '+w/2+'px);top:calc('+t.y+'% - '+h2/2+'px);width:'+w+'px;height:'+h2+'px;border-radius:'+(t.shape==='round'?'50%':'8px')+';border-color:'+(bk?'#DC262660':c+'50')+';background:'+(bk?'#DC262610':c+'08')+';color:'+(bk?'#DC2626':c);
    d.innerHTML='<div style="font-size:8px;font-weight:800">'+t.id+'</div><div style="font-size:7px;color:'+(bk?'#DC2626':'var(--tm)')+'">'+( bk||t.seats+'p')+'</div>';
    el.appendChild(d);
  });
}

// ===== FLOORPLAN PAGE =====
// ===== FLOORPLAN - DUAL MODE =====
var fpSelected=null;
var fpDragging=null;
var fpMode='resa';
var fpService='midi';
var fpSlot='all';
var fpZones=[{id:'salle',label:'Salle',color:'#6366F1'},{id:'terrasse',label:'Terrasse',color:'#10B981'},{id:'bar',label:'Bar',color:'#F59E0B'}];

function fpMergeForService(){
  var filtered=bookings.filter(function(b){
    // Filter by selected date first
    if(!(b.date||'').startsWith(selectedDate))return false;
    var bt=b.booking_time||b.time||'';if(!bt||!b.table)return false;
    var bh=parseInt(bt.split(':')[0])||0;
    if(fpService==='midi'&&bh>=17)return false;
    if(fpService==='soir'&&bh<17)return false;
    if(fpSlot!=='all'){var sh=parseInt(fpSlot.split(':')[0])||0;var sm=parseInt(fpSlot.split(':')[1])||0;var bm=parseInt(bt.split(':')[1])||0;if(Math.abs((bh*60+bm)-(sh*60+sm))>90)return false}
    return true;
  });
  var tb={};filtered.forEach(function(b){if(b.table){b.table.split('+').forEach(function(tid){tb[tid.trim()]=b.name})}});
  floorplan.forEach(function(t){t.booking_name=tb[t.id]||null});
}

var fpServiceInitDone=false;

function renderFloorplan(c){
  var nowH=new Date().getHours();
  if(!fpServiceInitDone&&fpSlot==='all'&&nowH>=17){fpService='soir';fpServiceInitDone=true;}
  fpMergeForService();
  var totalSeats=floorplan.reduce(function(a,t){return a+(t.seats||0)},0);
  var booked=floorplan.filter(function(t){return t.booking_name}).length;
  var free=floorplan.length-booked;

  // Get bookings for selected date + service for sidebar
  var sidebarBookings=bookings.filter(function(b){
    if(!(b.date||'').startsWith(selectedDate))return false;
    var bt=b.booking_time||b.time||'';
    if(!bt)return true; // show unassigned too
    var bh=parseInt(bt.split(':')[0])||0;
    if(fpService==='midi'&&bh>=17)return false;
    if(fpService==='soir'&&bh<17)return false;
    return true;
  }).sort(function(a,b){return(a.booking_time||a.time||'').localeCompare(b.booking_time||b.time||'')});
  var srcColors={whatsapp:'#25D366',web:'#2563EB',phone:'#A8A29E','walk-in':'#78716C',zenchef:'#FF6B35'};

  var h='';

  h+='<div class="card" style="padding:20px;margin-bottom:14px">';
  h+='<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px"><div><div class="card-t">Plan de salle</div><div class="card-s" id="fpSummary">'+floorplan.length+' tables \u00b7 '+totalSeats+' places \u00b7 <span style="color:var(--ok)">'+free+' libres</span> \u00b7 <span style="color:var(--da)">'+booked+' occupees</span></div></div>';
  h+='<div style="display:flex;gap:6px"><button style="padding:8px 16px;border-radius:8px;border:1.5px solid '+(fpMode==='resa'?'var(--ac)':'var(--b)')+';background:'+(fpMode==='resa'?'var(--al)':'var(--card)')+';color:'+(fpMode==='resa'?'var(--ac)':'var(--ts)')+';font-size:12px;font-weight:700;cursor:pointer;font-family:var(--f)" data-fpModeResa>Réservations</button>';
  h+='<button style="padding:8px 16px;border-radius:8px;border:1.5px solid '+(fpMode==='edit'?'var(--ac)':'var(--b)')+';background:'+(fpMode==='edit'?'var(--al)':'var(--card)')+';color:'+(fpMode==='edit'?'var(--ac)':'var(--ts)')+';font-size:12px;font-weight:700;cursor:pointer;font-family:var(--f)" data-fpModeEdit>Modifier plan</button></div></div>';
  if(fpMode==='edit'){
    h+='<div style="display:flex;gap:5px;margin-bottom:10px;padding:8px 12px;background:var(--bg);border-radius:10px;overflow-x:auto;align-items:center"><span style="font-size:11px;font-weight:700;color:var(--tm);white-space:nowrap;margin-right:4px">Ajouter :</span>';
    [{s:'round',n:2},{s:'round',n:4},{s:'round',n:6},{s:'rect',n:2},{s:'rect',n:4},{s:'rect',n:6},{s:'rect',n:8}].forEach(function(p){h+='<button style="padding:5px 10px;border-radius:7px;border:1.5px solid var(--b);background:var(--card);color:var(--t);font-size:11px;font-weight:700;cursor:pointer;font-family:var(--f);white-space:nowrap;display:flex;align-items:center;gap:3px" data-fpAdd="'+p.s+'-'+p.n+'"><span style="width:'+(p.s==='round'?12:16)+'px;height:12px;border-radius:'+(p.s==='round'?'50%':'2px')+';border:2px solid var(--ac);display:inline-block"></span>'+p.n+'p</button>'});
    h+='<div style="margin-left:auto"><button class="ba" data-fpSave>Enregistrer le plan</button></div></div>';
  }
  if(fpMode==='resa'){
    h+='<div style="display:flex;gap:0;margin-bottom:10px"><button style="flex:1;padding:10px;border:1.5px solid '+(fpService==='midi'?'var(--ac)':'var(--b)')+';border-right:none;border-radius:8px 0 0 8px;background:'+(fpService==='midi'?'var(--al)':'var(--card)')+';color:'+(fpService==='midi'?'var(--ac)':'var(--ts)')+';font-size:13px;font-weight:700;cursor:pointer;font-family:var(--f)" data-fpSvc="midi">&#9728; Midi</button>';
    h+='<button style="flex:1;padding:10px;border:1.5px solid '+(fpService==='soir'?'var(--ac)':'var(--b)')+';border-radius:0 8px 8px 0;background:'+(fpService==='soir'?'var(--al)':'var(--card)')+';color:'+(fpService==='soir'?'var(--ac)':'var(--ts)')+';font-size:13px;font-weight:700;cursor:pointer;font-family:var(--f)" data-fpSvc="soir">&#9790; Soir</button></div>';
    var slots=fpService==='midi'?["all","12:00","12:15","12:30","12:45","13:00","13:15","13:30","13:45","14:00","14:15","14:30"]:["all","19:00","19:15","19:30","19:45","20:00","20:15","20:30","20:45","21:00","21:15","21:30","21:45","22:00","22:15","22:30"];
    h+='<div style="display:flex;gap:4px;margin-bottom:10px;overflow-x:auto;padding-bottom:4px">';
    slots.forEach(function(s){var label=s==='all'?'Tous':s;var active=fpSlot===s;var cnt=0;if(s!=='all'){var sh=parseInt(s.split(':')[0]);var sm=parseInt(s.split(':')[1]);bookings.forEach(function(b){if(!(b.date||'').startsWith(selectedDate))return;var bt=b.booking_time||b.time||'';if(!bt||!b.table)return;var bh=parseInt(bt.split(':')[0])||0;var bm=parseInt(bt.split(':')[1])||0;if(Math.abs((bh*60+bm)-(sh*60+sm))<=15)cnt++})}h+='<button style="padding:6px 12px;border-radius:20px;border:1.5px solid '+(active?'var(--ac)':'var(--b)')+';background:'+(active?'var(--ac)':'var(--card)')+';color:'+(active?'#fff':'var(--ts)')+';font-size:11px;font-weight:700;cursor:pointer;font-family:var(--f);white-space:nowrap" data-fpSlot="'+s+'">'+label+(s!=='all'&&cnt>0?'<span style="margin-left:4px;padding:1px 5px;border-radius:10px;background:'+(active?'#fff3':'var(--da)')+';color:#fff;font-size:9px;font-weight:800">'+cnt+'</span>':'')+'</button>'});
    h+='</div>';
  }

  // LAYOUT: canvas + sidebar (only in resa mode)
  if(fpMode==='resa'){
    h+='<div class="fp-layout">';
    h+='<div class="fp-main">';
  }

  h+='<div style="position:relative;height:440px;background:var(--bg);border-radius:12px;border:2px solid var(--b);overflow:hidden;touch-action:none;user-select:none" id="fpCanvas">';
  fpZones.forEach(function(z,i){var xMin=i===0?0:i===1?46:84;var xMax=i===0?46:i===1?84:100;h+='<div style="position:absolute;left:'+xMin+'%;top:0;width:'+(xMax-xMin)+'%;height:100%;pointer-events:none"><div style="position:absolute;top:10px;left:50%;transform:translateX(-50%);font-size:10px;color:var(--tm);font-weight:700;letter-spacing:.06em;white-space:nowrap">'+z.label.toUpperCase()+'</div>';if(i<fpZones.length-1)h+='<div style="position:absolute;right:0;top:0;bottom:0;width:1px;border-right:1px dashed var(--b)"></div>';h+='</div>'});
  h+='</div>';
  if(fpMode==='edit'){h+='<div id="fpEditor" style="display:none;margin-top:12px;padding:16px;background:var(--card);border:1px solid var(--b);border-radius:12px"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px"><div style="font-size:15px;font-weight:700" id="fpEdTitle">Table</div><button style="padding:4px 10px;border-radius:6px;border:none;background:#EF444415;color:#EF4444;font-size:11px;font-weight:700;cursor:pointer;font-family:var(--f)" data-fpDel>Supprimer</button></div><div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px"><div><div class="finp-label">Nom</div><input class="finp" id="fpEdName" style="margin-bottom:0" placeholder="T1"></div><div><div class="finp-label">Places</div><select class="finp" id="fpEdSeats" style="margin-bottom:0;cursor:pointer"><option>2</option><option>4</option><option>6</option><option>8</option><option>10</option><option>12</option></select></div><div><div class="finp-label">Forme</div><select class="finp" id="fpEdShape" style="margin-bottom:0;cursor:pointer"><option value="round">Ronde</option><option value="rect">Rectangle</option></select></div></div><div style="margin-top:10px"><div class="finp-label">Zone</div><div style="display:flex;gap:4px" id="fpEdZones"></div></div></div>'}

  if(fpMode==='resa'){
    // Close fp-main, open sidebar
    h+='<div id="fpResaPopup" style="display:none;margin-top:12px;padding:16px;background:var(--card);border:1px solid var(--b);border-radius:12px"><div id="fpResaContent"></div></div>';
    h+='</div>'; // close fp-main

    // Sidebar with calendar + reservation list
    h+='<div class="fp-sidebar">';
    h+=buildCalendar();
    h+='<div class="fp-sb-header"><div class="fp-sb-title">Réservations</div><div class="fp-sb-count">'+sidebarBookings.length+' '+(fpService==='midi'?'midi':'soir')+'</div></div>';
    h+='<div class="fp-sb-list" id="fpSbList">';
    if(sidebarBookings.length===0){
      h+='<div class="fp-sb-empty">Aucune réservation pour ce service</div>';
    } else {
      sidebarBookings.forEach(function(b,i){
        var srcCol=srcColors[b.source]||'#A8A29E';
        h+='<div class="fp-sb-item'+(fpSelected!==null&&floorplan[fpSelected]&&floorplan[fpSelected].id===b.table?' active':'')+'" data-fpSbResa="'+i+'" data-fpSbTable="'+(b.table||'')+'" data-fpSbId="'+b.id+'">';
        h+='<div style="display:flex;justify-content:space-between;align-items:flex-start">';
        h+='<div class="fp-sb-name"><span class="dot" style="background:'+srcCol+';display:inline-block;vertical-align:middle;margin-right:6px"></span>'+b.name+'</div>';
        if(b.table){h+='<span class="fp-sb-table">'+b.table+'</span>'}
        else{h+='<span class="fp-sb-no-table">Sans table</span>'}
        h+='</div>';
        h+='<div class="fp-sb-meta">';
        h+='<span>'+(b.booking_time||b.time||'—')+'</span>';
        h+='<span>'+b.covers+'p</span>';
        if(b.phone)h+='<span>'+b.phone+'</span>';
        h+='</div>';
        h+='</div>';
      });
    }
    h+='</div>';
    // Add new resa button at bottom of sidebar
    h+='<div style="padding:10px 16px;border-top:1px solid var(--bl)"><button class="ba" style="width:100%;padding:10px;font-size:12px" onclick="openResaModal()">+ Nouvelle réservation</button></div>';
    h+='</div>'; // close fp-sidebar
    h+='</div>'; // close fp-layout
  }

  h+='</div>';c.innerHTML=h;fpSelected=null;fpDrawTables();
  if(fpMode==='edit')fpInitDrag();else fpInitResaMode();
  fpInitSidebarClicks();
}

function fpDrawTables(){
  var el=document.getElementById('fpCanvas');
  if(!el)return;
  el.querySelectorAll('.ftbl').forEach(function(e){e.remove()});
  var zc={salle:'#6366F1',terrasse:'#10B981',bar:'#F59E0B'};
  floorplan.forEach(function(t,i){
    var d=document.createElement('div');
    d.className='ftbl';
    d.setAttribute('data-fpTbl',i);
    var w=t.shape==='round'?(t.seats<=2?44:t.seats<=4?52:60):(t.seats<=2?44:t.seats<=4?56:t.seats<=6?66:76);
    var h2=t.shape==='round'?w:(t.seats<=4?44:48);
    var co=zc[t.zone]||'#6366F1';
    var sel=fpSelected===i;
    var bk=t.booking_name;

    if(fpMode==='resa'){
      // Resa mode: green=free, red=occupied
      var bg=bk?'#EF444418':'#10B98112';
      var bc=bk?'#EF4444':'#10B981';
      var tc=bk?'#EF4444':co;
      d.style.cssText='left:calc('+t.x+'% - '+w/2+'px);top:calc('+t.y+'% - '+h2/2+'px);width:'+w+'px;height:'+h2+'px;border-radius:'+(t.shape==='round'?'50%':'10px')+';border:2px solid '+(sel?co:bc)+';background:'+(sel?co+'25':bg)+';color:'+tc+';cursor:pointer;box-shadow:'+(sel?'0 4px 14px '+co+'40':'0 1px 3px rgba(0,0,0,.08)')+';z-index:'+(sel?10:1)+';transition:all .15s';
      d.innerHTML='<div style="font-size:11px;font-weight:800">'+t.id+'</div><div style="font-size:9px;font-weight:600;color:'+(bk?'#EF4444':'#10B981')+'">'+(bk?bk.split(' ')[0]:t.seats+'p')+'</div>';
    } else {
      // Edit mode: zone colors
      d.style.cssText='left:calc('+t.x+'% - '+w/2+'px);top:calc('+t.y+'% - '+h2/2+'px);width:'+w+'px;height:'+h2+'px;border-radius:'+(t.shape==='round'?'50%':'10px')+';border:2px solid '+(sel?co:co+'50')+';background:'+(sel?co+'20':co+'08')+';color:'+co+';cursor:grab;box-shadow:'+(sel?'0 4px 14px '+co+'40':'none')+';z-index:'+(sel?10:1)+';transition:all .15s';
      d.innerHTML='<div style="font-size:11px;font-weight:800">'+t.id+'</div><div style="font-size:9px;color:var(--tm)">'+t.seats+'p</div>';
    }
    el.appendChild(d);
  });
}

// === EDIT MODE: drag & drop ===
function fpInitDrag(){
  var canvas=document.getElementById('fpCanvas');
  if(!canvas)return;
  function getPos(e){
    var r=canvas.getBoundingClientRect();
    var cx=e.clientX!==undefined?e.clientX:(e.touches?e.touches[0].clientX:0);
    var cy=e.clientY!==undefined?e.clientY:(e.touches?e.touches[0].clientY:0);
    return{x:Math.max(3,Math.min(97,(cx-r.left)/r.width*100)),y:Math.max(5,Math.min(95,(cy-r.top)/r.height*100))};
  }
  function detectZone(x){if(x<46)return 'salle';if(x<84)return 'terrasse';return 'bar'}
  canvas.addEventListener('mousedown',function(e){
    var tbl=e.target.closest('[data-fpTbl]');
    if(tbl){e.preventDefault();fpDragging=parseInt(tbl.getAttribute('data-fpTbl'));fpSelected=fpDragging;fpShowEditor();fpDrawTables()}
  });
  canvas.addEventListener('touchstart',function(e){
    var tbl=e.target.closest('[data-fpTbl]');
    if(tbl){e.preventDefault();fpDragging=parseInt(tbl.getAttribute('data-fpTbl'));fpSelected=fpDragging;fpShowEditor();fpDrawTables()}
  },{passive:false});
  function onMove(e){if(fpDragging===null)return;e.preventDefault();var p=getPos(e.touches?e.touches[0]:e);var t=floorplan[fpDragging];if(t){t.x=p.x;t.y=p.y;t.zone=detectZone(p.x);fpDrawTables()}}
  function onUp(){if(fpDragging!==null){fpShowEditor();fpDragging=null}}
  document.addEventListener('mousemove',onMove);
  document.addEventListener('mouseup',onUp);
  document.addEventListener('touchmove',onMove,{passive:false});
  document.addEventListener('touchend',onUp);
}

// === RESA MODE: click to book/view ===
function fpInitResaMode(){
  var canvas=document.getElementById('fpCanvas');
  if(!canvas)return;
  canvas.addEventListener('click',function(e){
    var tbl=e.target.closest('[data-fpTbl]');
    if(!tbl)return;
    var idx=parseInt(tbl.getAttribute('data-fpTbl'));
    var t=floorplan[idx];
    if(!t)return;
    fpSelected=idx;
    fpDrawTables();
    fpHighlightSidebarItem(t.id);
    if(t.booking_name){
      fpShowResaInfo(idx);
    } else {
      fpBookTable(idx);
    }
  });
}

function fpInitSidebarClicks(){
  var list=document.getElementById('fpSbList');
  if(!list)return;
  list.addEventListener('click',function(e){
    var item=e.target.closest('[data-fpSbTable]');
    if(!item)return;
    var tableId=item.getAttribute('data-fpSbTable');
    if(!tableId)return;
    // Find the table index
    var idx=-1;
    floorplan.forEach(function(t,i){if(t.id===tableId)idx=i});
    if(idx===-1)return;
    fpSelected=idx;
    fpDrawTables();
    // Highlight sidebar item
    list.querySelectorAll('.fp-sb-item').forEach(function(el){el.classList.remove('active')});
    item.classList.add('active');
    // Show resa info popup
    if(floorplan[idx].booking_name){
      fpShowResaInfo(idx);
    }
  });
}

function fpHighlightSidebarItem(tableId){
  var list=document.getElementById('fpSbList');
  if(!list)return;
  list.querySelectorAll('.fp-sb-item').forEach(function(el){
    el.classList.toggle('active',el.getAttribute('data-fpSbTable')===tableId);
  });
}

function fpShowResaInfo(idx){
  var t=floorplan[idx];
  var popup=document.getElementById('fpResaPopup');
  if(!popup)return;
  var bk=null;
  // Find booking matching this table AND current service
  bookings.forEach(function(b){
    if(b.table!==t.id)return;
    var bt=b.booking_time||b.time||'';
    var bh=parseInt((bt||'0').split(':')[0])||0;
    if(fpService==='midi'&&bh>=17)return;
    if(fpService==='soir'&&bh<17)return;
    bk=b;
  });
  if(!bk){popup.style.display='none';return}
  popup.style.display='block';
  var h='<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:14px">';
  h+='<div><div style="font-size:16px;font-weight:700">'+bk.name+'</div>';
  if(bk.phone)h+='<div style="font-size:12px;color:var(--ts);margin-top:2px">'+bk.phone+'</div>';
  h+='</div>';
  h+='<div style="display:flex;gap:6px"><button style="padding:4px 10px;border-radius:6px;border:none;background:#EF444412;color:#EF4444;font-size:11px;font-weight:600;cursor:pointer;font-family:var(--f)" data-fpCancelResa="'+bk.id+'">Annuler</button><button style="padding:4px 10px;border-radius:6px;border:none;background:var(--bg);color:var(--ts);font-size:11px;font-weight:600;cursor:pointer;font-family:var(--f)" data-fpClosePopup>Fermer</button></div>';
  h+='</div>';
  // Inline edit: time + covers + table info
  h+='<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:14px">';
  h+='<div><div class="finp-label">Heure</div><input type="time" class="finp" style="margin-bottom:0" id="fpResaTime" value="'+(bk.booking_time||bk.time||'20:00')+'"></div>';
  h+='<div><div class="finp-label">Couverts</div><input type="number" class="finp" style="margin-bottom:0" id="fpResaCovers" value="'+(bk.covers||2)+'" min="1" max="20"></div>';
  h+='<div><div class="finp-label">Table</div><div style="padding:11px 14px;background:var(--bg);border-radius:8px;font-size:13px;font-weight:600;color:var(--ac)">'+t.id+' ('+t.seats+'p, '+t.zone+')</div></div>';
  h+='</div>';
  h+='<button style="padding:7px 16px;border-radius:8px;border:none;background:var(--ac);color:#fff;font-size:12px;font-weight:600;cursor:pointer;font-family:var(--f);margin-bottom:14px" data-fpSaveResa="'+bk.id+'">Enregistrer les modifications</button>';
  // Move table
  h+='<div class="finp-label" style="margin-bottom:6px">Deplacer vers une autre table</div>';
  h+='<div style="display:flex;gap:4px;flex-wrap:wrap">';
  floorplan.forEach(function(ot,oi){
    if(oi===idx)return;
    var otBk=null;bookings.forEach(function(b2){if(b2.table===ot.id){var bt2=b2.booking_time||b2.time||'';var bh2=parseInt((bt2||'0').split(':')[0])||0;if(fpService==='midi'&&bh2<17)otBk=b2;if(fpService==='soir'&&bh2>=17)otBk=b2}});
    var color=otBk?'#F59E0B':'#10B981';
    h+='<button style="padding:5px 10px;border-radius:6px;border:1.5px solid '+color+'40;background:'+color+'08;color:'+color+';font-size:11px;font-weight:700;cursor:pointer;font-family:var(--f)" data-fpSwap="'+bk.id+'-'+ot.id+'" title="'+(otBk?'Swap avec '+otBk.name:'Libre')+'">'+ot.id+' ('+ot.seats+'p)</button>';
  });
  h+='</div>';
  document.getElementById('fpResaContent').innerHTML=h;
}

function fpSaveResaInline(bookingId){
  var timeEl=document.getElementById('fpResaTime');
  var coversEl=document.getElementById('fpResaCovers');
  if(!timeEl||!coversEl)return;
  var data={booking_id:bookingId,time:timeEl.value,covers:parseInt(coversEl.value)||2};
  apiFetch('/api/bookings/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).then(function(){
    // Update locally
    bookings.forEach(function(b){if(b.id===bookingId){b.booking_time=data.time;b.time=data.time;b.covers=data.covers}});
    fpMergeForService();fpDrawTables();
    showToast('Reservation modifiee');
  });
}

function fpBookTable(idx){
  // Open reservation modal pre-filled with this table
  var t=floorplan[idx];
  resaSelTable=t.id;
  var el=document.getElementById('resaFirst');if(el)el.value='';
  el=document.getElementById('resaLast');if(el)el.value='';
  document.getElementById('resaCovers').value=String(Math.min(t.seats,4));
  document.getElementById('resaTime').value='20:00';
  document.getElementById('resaPhone').value='';
  document.getElementById('resaEmail').value='';
  document.getElementById('resaSource').value='phone';
  document.getElementById('resaTableBox').style.display='block';
  document.getElementById('resaTableVal').textContent=t.id+' ('+t.seats+'p, '+t.zone+')';
  document.getElementById('resaTableSel').style.display='none';
  document.getElementById('resaModal').classList.add('show');
}

function fpSwapTable(bookingId,newTableId){
  var oldBooking=null;
  bookings.forEach(function(b){if(b.id===bookingId)oldBooking=b});
  if(!oldBooking)return;
  var newTableBooking=null;
  bookings.forEach(function(b){if(b.table===newTableId)newTableBooking=b});
  var oldTable=oldBooking.table;
  if(newTableBooking){
    apiFetch('/api/bookings/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({booking_id:newTableBooking.id,table:oldTable})});
    newTableBooking.table=oldTable;
  }
  apiFetch('/api/bookings/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({booking_id:bookingId,table:newTableId})});
  oldBooking.table=newTableId;
  mergeBookingsIntoFloor();
  fpDrawTables();
  var newIdx=floorplan.findIndex(function(t){return t.id===newTableId});
  fpSelected=newIdx;
  fpDrawTables();
  fpShowResaInfo(newIdx);
  showToast('Table changee'+(newTableBooking?' (swap avec '+newTableBooking.name+')':''));
}

function fpCancelResa(bookingId){
  if(!confirm('Annuler cette reservation ?'))return;
  apiFetch('/api/bookings/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({booking_id:bookingId})}).then(function(){
    // Remove locally
    for(var i=0;i<bookings.length;i++){if(bookings[i].id===bookingId){bookings.splice(i,1);break}}
    // Track cancellation
    cancelledCount=(cancelledCount||0)+1;
    mergeBookingsIntoFloor();
    fpSelected=null;
    fpDrawTables();
    var pp=document.getElementById('fpResaPopup');if(pp)pp.style.display='none';
    fpUpdateSummary();
    showToast('Reservation annulee');
  });
}

function fpShowEditor(){
  var ed=document.getElementById('fpEditor');
  if(!ed)return;
  if(fpSelected===null||!floorplan[fpSelected]){ed.style.display='none';return}
  var t=floorplan[fpSelected];
  ed.style.display='block';
  document.getElementById('fpEdTitle').textContent='Table '+t.id;
  document.getElementById('fpEdName').value=t.id;
  document.getElementById('fpEdSeats').value=String(t.seats);
  document.getElementById('fpEdShape').value=t.shape||'rect';
  var zhtml='';
  fpZones.forEach(function(z){
    zhtml+='<button style="flex:1;padding:7px;border-radius:7px;border:2px solid '+(t.zone===z.id?z.color:'var(--b)')+';background:'+(t.zone===z.id?z.color+'10':'var(--card)')+';color:'+(t.zone===z.id?z.color:'var(--ts)')+';font-size:11px;font-weight:700;cursor:pointer;font-family:var(--f)" data-fpSetZone="'+z.id+'">'+z.label+'</button>';
  });
  document.getElementById('fpEdZones').innerHTML=zhtml;
}

function fpAddTable(shape,seats){
  var id='T'+(floorplan.length+1);
  floorplan.push({id:id,seats:seats,shape:shape,zone:'salle',x:20+Math.random()*20,y:30+Math.random()*30});
  fpSelected=floorplan.length-1;
  fpDrawTables();fpShowEditor();fpUpdateSummary();
}
function fpDeleteSelected(){
  if(fpSelected===null)return;
  floorplan.splice(fpSelected,1);fpSelected=null;
  fpDrawTables();fpShowEditor();fpUpdateSummary();
  showToast('Table supprimee');
}
function fpUpdateSelected(key,val){
  if(fpSelected===null)return;
  var t=floorplan[fpSelected];
  if(key==='seats')t.seats=parseInt(val)||2;
  else if(key==='shape')t.shape=val;
  else if(key==='zone')t.zone=val;
  else if(key==='id')t.id=val;
  fpDrawTables();fpShowEditor();fpUpdateSummary();
}
function fpUpdateSummary(){
  var el=document.getElementById('fpSummary');
  if(!el)return;
  var booked=floorplan.filter(function(t){return t.booking_name}).length;
  var free=floorplan.length-booked;
  el.innerHTML=floorplan.length+' tables · '+floorplan.reduce(function(a,t){return a+(t.seats||0)},0)+' places · <span style="color:var(--ok)">'+free+' libres</span> · <span style="color:var(--da)">'+booked+' occupees</span>';
}
function fpSave(){
  apiFetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tables:floorplan})}).then(function(){
    showToast('Plan de salle enregistre');drawFloorMini();
  });
}
function drawFloorFull(){fpDrawTables()}

// ===== BOOKINGS =====
var bookingsView='day'; // 'day' or 'week'

function renderBookings(c){
  var srcColors={whatsapp:'#25D366',web:'#2563EB',phone:'#A8A29E','walk-in':'#78716C',zenchef:'#FF6B35'};
  var srcLabels={whatsapp:'WhatsApp',web:'Chat web',phone:'Tél','walk-in':'Walk-in',zenchef:'Zenchef'};
  var today=fmtDate(new Date());
  var isToday=selectedDate===today;
  var dateLabel=isToday?"auj.":parseDateLocal(selectedDate).toLocaleDateString('fr-FR',{weekday:'short',day:'numeric',month:'short'});

  var h='<div style="display:flex;gap:8px;margin-bottom:14px">';
  h+='<button class="ba'+(bookingsView==='day'?' on':'')+'" style="padding:6px 16px;font-size:12px;'+(bookingsView==='day'?'background:var(--acg);color:white;border:none;':'')+'" data-bkView="day">Jour</button>';
  h+='<button class="ba'+(bookingsView==='week'?' on':'')+'" style="padding:6px 16px;font-size:12px;'+(bookingsView==='week'?'background:var(--acg);color:white;border:none;':'')+'" data-bkView="week">Semaine</button>';
  h+='</div>';

  if(bookingsView==='week'){
    h+=renderWeekView(srcColors, srcLabels);
  } else {
    h+='<div class="ov-layout" style="display:flex;gap:14px;align-items:flex-start">';
    h+='<div style="flex:1;min-width:0">';
    var filtered=getBookingsForDate(selectedDate);
    h+='<div class="card"><div class="card-h"><div><div class="card-t">Réservations</div><div class="card-s">'+filtered.length+' '+dateLabel+'</div></div><button class="ba" onclick="openResaModal()">+ Nouvelle réservation</button></div>';
    filtered.forEach(function(b){
      var globalIdx=bookings.indexOf(b);
      h+='<div class="rw" data-editResa="'+globalIdx+'" style="cursor:pointer"><div class="rl"><div class="dot" style="background:'+(srcColors[b.source]||'#A8A29E')+'"></div><div><div style="font-size:14px;font-weight:600">'+b.name+'</div><div style="font-size:12px;color:var(--tm)">'+b.covers+'p · '+(b.booking_time||b.time||'')+(b.phone?' · '+b.phone:'')+'</div></div></div><div style="display:flex;align-items:center;gap:6px"><span class="src-badge" style="color:'+(srcColors[b.source]||'#A8A29E')+';background:'+(srcColors[b.source]||'#A8A29E')+'15">'+(srcLabels[b.source]||b.source)+'</span><span class="badge" style="background:var(--okb);color:var(--ok)">'+(b.table||'—')+'</span></div></div>';
    });
    if(!filtered.length) h+='<div style="padding:30px;text-align:center;color:var(--tm)">Aucune réservation '+dateLabel+'</div>';
    h+='</div>';
    h+='</div>';
    h+='<div style="width:280px;flex-shrink:0">';
    h+=buildCalendar();
    h+='</div>';
    h+='</div>';
  }
  c.innerHTML=h;
}

function renderWeekView(srcColors, srcLabels){
  var sel=parseDateLocal(selectedDate);
  var dow=sel.getDay();
  var mondayOffset=dow===0?-6:1-dow;
  var monday=new Date(sel);
  monday.setDate(sel.getDate()+mondayOffset);

  var days=[];
  for(var i=0;i<7;i++){
    var d=new Date(monday);
    d.setDate(monday.getDate()+i);
    days.push(fmtDate(d));
  }

  var dayNames=['Lun','Mar','Mer','Jeu','Ven','Sam','Dim'];
  var today=fmtDate(new Date());

  var h='<div class="card" style="padding:0;overflow:hidden">';

  // Header row
  h+='<div style="display:grid;grid-template-columns:repeat(7,1fr);border-bottom:2px solid var(--b)">';
  days.forEach(function(ds,i){
    var d=parseDateLocal(ds);
    var isToday=ds===today;
    var isSel=ds===selectedDate;
    var dayBookings=bookings.filter(function(b){return(b.date||'').startsWith(ds)});
    var totalCovers=0;dayBookings.forEach(function(b){totalCovers+=(b.covers||0)});
    var midiCount=dayBookings.filter(function(b){var t=b.booking_time||b.time||'';return t&&parseInt(t.split(':')[0])<15}).length;
    var soirCount=dayBookings.length-midiCount;

    h+='<div style="padding:12px 8px;text-align:center;cursor:pointer;border-right:'+(i<6?'1px solid var(--b)':'none')+';background:'+(isToday?'linear-gradient(135deg,#EBF4FF,#E6FAF8)':isSel?'var(--bg)':'white')+'" data-calDate="'+ds+'">';
    h+='<div style="font-size:10px;font-weight:700;color:var(--tm);text-transform:uppercase">'+dayNames[i]+'</div>';
    h+='<div style="font-size:20px;font-weight:800;color:'+(isToday?'var(--ac)':'var(--t)')+';margin:2px 0">'+d.getDate()+'</div>';
    h+='<div style="font-size:10px;color:var(--tm)">'+d.toLocaleDateString('fr-FR',{month:'short'})+'</div>';
    if(dayBookings.length){
      h+='<div style="margin-top:6px;padding:4px 6px;background:'+(isToday?'var(--ac)':'var(--ok)')+';color:white;border-radius:6px;font-size:11px;font-weight:700">'+dayBookings.length+' résa'+(dayBookings.length>1?'s':'')+'</div>';
      h+='<div style="font-size:10px;color:var(--ts);margin-top:2px">'+totalCovers+' couverts</div>';
    } else {
      h+='<div style="margin-top:6px;font-size:11px;color:var(--tm);font-style:italic">Aucune</div>';
    }
    h+='</div>';
  });
  h+='</div>';

  // Detail rows per day
  h+='<div style="max-height:500px;overflow-y:auto">';
  days.forEach(function(ds,i){
    var d=parseDateLocal(ds);
    var isToday=ds===today;
    var dayBookings=bookings.filter(function(b){return(b.date||'').startsWith(ds)});
    if(!dayBookings.length) return;

    // Sort by time
    dayBookings.sort(function(a,b){return(a.booking_time||a.time||'').localeCompare(b.booking_time||b.time||'')});

    var dayLabel=dayNames[i]+' '+d.getDate()+' '+d.toLocaleDateString('fr-FR',{month:'short'});
    h+='<div style="padding:10px 16px;background:'+(isToday?'#F0F9FF':'var(--bg)')+';font-size:12px;font-weight:700;color:'+(isToday?'var(--ac)':'var(--ts)')+';border-bottom:1px solid var(--b);display:flex;justify-content:space-between">';
    h+='<span>'+dayLabel+'</span>';
    var totalCov=0;dayBookings.forEach(function(b){totalCov+=(b.covers||0)});
    h+='<span>'+dayBookings.length+' résa'+(dayBookings.length>1?'s':'')+' · '+totalCov+' couverts</span>';
    h+='</div>';

    dayBookings.forEach(function(b){
      var globalIdx=bookings.indexOf(b);
      h+='<div class="rw" data-editResa="'+globalIdx+'" style="cursor:pointer;padding:8px 16px"><div class="rl"><div class="dot" style="background:'+(srcColors[b.source]||'#A8A29E')+'"></div><div><div style="font-size:13px;font-weight:600">'+b.name+'</div><div style="font-size:11px;color:var(--tm)">'+b.covers+'p · '+(b.booking_time||b.time||'')+(b.zone?' · '+b.zone:'')+'</div></div></div><div style="display:flex;align-items:center;gap:6px"><span class="src-badge" style="font-size:10px;color:'+(srcColors[b.source]||'#A8A29E')+';background:'+(srcColors[b.source]||'#A8A29E')+'15">'+(srcLabels[b.source]||b.source)+'</span><span class="badge" style="background:var(--okb);color:var(--ok);font-size:10px">'+(b.table||'—')+'</span></div></div>';
    });
  });

  // Empty week message
  var weekTotal=0;days.forEach(function(ds){weekTotal+=bookings.filter(function(b){return(b.date||'').startsWith(ds)}).length});
  if(!weekTotal){
    h+='<div style="padding:40px;text-align:center;color:var(--tm)">Aucune réservation cette semaine</div>';
  }

  h+='</div></div>';

  // Week navigation
  h+='<div style="display:flex;justify-content:center;gap:12px;margin-top:12px">';
  h+='<button class="ba" style="font-size:12px;padding:6px 14px" data-weekShift="-1">&#8249; Semaine préc.</button>';
  h+='<button class="ba" style="font-size:12px;padding:6px 14px" data-weekToday>Cette semaine</button>';
  h+='<button class="ba" style="font-size:12px;padding:6px 14px" data-weekShift="1">Semaine suiv. &#8250;</button>';
  h+='</div>';

  return h;
}

var editResaIdx=null;
function openEditResa(idx){
  editResaIdx=idx;
  var b=bookings[idx];
  if(!b)return;
  document.getElementById('editResaName').value=b.name||'';
  document.getElementById('editResaCovers').value=b.covers||2;
  document.getElementById('editResaTime').value=(b.booking_time||b.time||'20:00');
  document.getElementById('editResaPhone').value=b.phone||'';
  document.getElementById('editResaTable').value=b.table||'';
  // Build table options
  var sel=document.getElementById('editResaTable');
  sel.innerHTML='<option value="">— Aucune —</option>';
  floorplan.forEach(function(t){
    var opt=document.createElement('option');
    opt.value=t.id;opt.textContent=t.id+' ('+t.seats+'p, '+t.zone+')';
    if(t.id===b.table)opt.selected=true;
    sel.appendChild(opt);
  });
  document.getElementById('editResaModal').classList.add('show');
}
function closeEditResa(){document.getElementById('editResaModal').classList.remove('show');editResaIdx=null}
function saveEditResa(){
  if(editResaIdx===null)return;
  var b=bookings[editResaIdx];
  var data={
    booking_id:b.id,
    name:document.getElementById('editResaName').value.trim(),
    covers:parseInt(document.getElementById('editResaCovers').value)||2,
    time:document.getElementById('editResaTime').value,
    phone:document.getElementById('editResaPhone').value.trim(),
    table:document.getElementById('editResaTable').value
  };
  apiFetch('/api/bookings/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).then(function(){
    fetchData();
    closeEditResa();
    showToast('Reservation modifiee');
  });
}
function deleteResa(){
  if(editResaIdx===null)return;
  var b=bookings[editResaIdx];
  if(!confirm('Supprimer la reservation de '+b.name+' ?'))return;
  apiFetch('/api/bookings/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({booking_id:b.id})}).then(function(){
    fetchData();
    closeEditResa();
    showToast('Reservation supprimee');
  });
}

// ===== MENU =====
// ===== MENU EDITOR =====
var menuSections=[];

function loadMenu(){
  apiFetch('/api/menu').then(function(r){return r.json()}).then(function(d){
    menuSections=d.sections||[];
  }).catch(function(){menuSections=[]});
}

function renderMenu(c){
  var h='';
  h+='<div class="db"><div class="db-top"><div class="di">📢</div><div style="flex:1"><div class="dlb">Message du jour</div><div style="font-size:15px;font-weight:600;color:var(--t);margin-top:4px">'+(dailyMsg||'Aucun message')+'</div><div class="dme">Transmis automatiquement par l&#39;agent IA</div></div></div></div>';

  h+='<div class="card" style="padding:20px;margin-bottom:16px"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px"><div><div class="card-t">La Carte</div><div class="card-s">'+(restaurantConfig.name||'Restaurant')+'</div></div><div style="display:flex;gap:8px"><label style="padding:6px 14px;border-radius:8px;border:none;background:var(--bg);color:var(--t);font-size:12px;font-weight:700;cursor:pointer;font-family:var(--f);display:flex;align-items:center;gap:4px" id="menuScanBtn">📸 Scanner<input type="file" accept="image/*" style="display:none" id="menuScanInput" multiple></label><button class="ba" data-addSection>+ Section</button></div></div>';

  if(!menuSections.length){
    h+='<div style="text-align:center;padding:40px;color:var(--tm)"><div style="font-size:32px;margin-bottom:8px">📋</div><div style="font-size:14px">Aucune section. Cliquez "+ Section" pour commencer.</div></div>';
  }

  menuSections.forEach(function(sec,si){
    h+='<div class="menu-sec" style="margin-bottom:24px;border:1px solid var(--bl);border-radius:12px;overflow:hidden">';
    h+='<div style="display:flex;justify-content:space-between;align-items:center;padding:12px 16px;background:var(--bg)">';
    h+='<div style="display:flex;align-items:center;gap:8px"><input style="font-size:14px;font-weight:700;color:var(--ac);border:none;background:transparent;outline:none;font-family:var(--f);text-transform:uppercase;letter-spacing:.04em;width:200px" value="'+sec.title+'" data-secTitle="'+si+'" placeholder="Nom de la section"></div>';
    h+='<div style="display:flex;gap:6px"><button class="ba" style="font-size:11px;padding:4px 10px" data-addItem="'+si+'">+ Plat</button><button style="font-size:11px;padding:4px 10px;border-radius:6px;border:1px solid var(--b);background:var(--card);color:var(--da);cursor:pointer;font-family:var(--f);font-weight:600" data-delSection="'+si+'">Supprimer</button></div>';
    h+='</div>';

    if(!sec.items||!sec.items.length){
      h+='<div style="padding:20px;text-align:center;color:var(--tm);font-size:13px">Aucun plat dans cette section</div>';
    } else {
      sec.items.forEach(function(item,ii){
        h+='<div style="display:flex;align-items:center;gap:10px;padding:10px 16px;border-top:1px solid var(--bl)">';
        h+='<div style="flex:1"><input style="font-size:14px;font-weight:600;color:var(--t);border:none;background:transparent;outline:none;font-family:var(--f);width:100%" value="'+(item.name||'')+'" data-itemName="'+si+'-'+ii+'" placeholder="Nom du plat">';
        h+='<input style="font-size:12px;color:var(--ts);border:none;background:transparent;outline:none;font-family:var(--f);width:100%;margin-top:2px" value="'+(item.description||'')+'" data-itemDesc="'+si+'-'+ii+'" placeholder="Description (optionnel)"></div>';
        h+='<input style="font-size:14px;font-weight:700;color:var(--ac);border:none;background:transparent;outline:none;font-family:var(--f);width:60px;text-align:right" value="'+(item.price||'')+'" data-itemPrice="'+si+'-'+ii+'" placeholder="Prix">';
        h+='<button style="background:none;border:none;cursor:pointer;font-size:14px;color:var(--tm);padding:4px" data-delItem="'+si+'-'+ii+'">✕</button>';
        h+='</div>';
      });
    }
    h+='</div>';
  });

  h+='</div>';

  if(menuSections.length){
    h+='<div style="display:flex;gap:8px"><button class="ba" style="padding:10px 20px" data-saveMenu>Enregistrer le menu</button></div>';
  }

  c.innerHTML=h;
  menuScanInit();
}

function menuCollectData(){
  menuSections.forEach(function(sec,si){
    var titleEl=document.querySelector('[data-secTitle="'+si+'"]');
    if(titleEl) sec.title=titleEl.value.trim();
    (sec.items||[]).forEach(function(item,ii){
      var nEl=document.querySelector('[data-itemName="'+si+'-'+ii+'"]');
      var dEl=document.querySelector('[data-itemDesc="'+si+'-'+ii+'"]');
      var pEl=document.querySelector('[data-itemPrice="'+si+'-'+ii+'"]');
      if(nEl) item.name=nEl.value.trim();
      if(dEl) item.description=dEl.value.trim();
      if(pEl) item.price=pEl.value.trim();
    });
  });
}

function menuAddSection(){
  menuCollectData();
  menuSections.push({title:'Nouvelle section',items:[]});
  renderMenu(document.getElementById('mainContent'));
}

function menuDelSection(si){
  menuCollectData();
  menuSections.splice(si,1);
  renderMenu(document.getElementById('mainContent'));
}

function menuAddItem(si){
  menuCollectData();
  if(!menuSections[si].items) menuSections[si].items=[];
  menuSections[si].items.push({name:'',description:'',price:''});
  renderMenu(document.getElementById('mainContent'));
}

function menuDelItem(si,ii){
  menuCollectData();
  menuSections[si].items.splice(ii,1);
  renderMenu(document.getElementById('mainContent'));
}

function menuSave(){
  menuCollectData();
  apiFetch('/api/menu',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sections:menuSections})}).then(function(){
    showToast('Menu enregistre');
  });
}

function menuScanInit(){
  var input=document.getElementById('menuScanInput');
  if(!input)return;
  input.addEventListener('change',function(){
    var files=input.files;
    if(!files.length)return;
    var scanBtn=document.getElementById('menuScanBtn');
    scanBtn.innerHTML='⏳ Analyse en cours...';
    scanBtn.style.opacity='0.6';
    var pending=files.length;
    var allSections=[];
    Array.from(files).forEach(function(file){
      var reader=new FileReader();
      reader.onload=function(e){
        var b64=e.target.result;
        var mt=file.type||'image/jpeg';
        apiFetch('/api/menu/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image:b64,media_type:mt})}).then(function(r){return r.json()}).then(function(d){
          if(d.sections&&d.sections.length){
            d.sections.forEach(function(s){allSections.push(s)});
          }
          pending--;
          if(pending<=0){
            scanBtn.innerHTML='📸 Scanner';
            scanBtn.style.opacity='1';
            if(allSections.length){
              menuCollectData();
              allSections.forEach(function(s){menuSections.push(s)});
              renderMenu(document.getElementById('mainContent'));
              showToast(allSections.length+' sections ajoutees depuis image');
            }else{
              showToast('Aucun plat detecte');
            }
          }
        }).catch(function(){
          pending--;
          if(pending<=0){scanBtn.innerHTML='📸 Scanner';scanBtn.style.opacity='1';showToast('Erreur de scan')}
        });
      };
      reader.readAsDataURL(file);
    });
  });
}

// ===== CONVERSATIONS =====
function renderConversations(c){
  var entries=Object.entries(conversations);
  if(!entries.length){c.innerHTML='<div class="ph"><div class="phi">◈</div><div style="font-size:18px;font-weight:600;margin-bottom:4px">Conversations</div><div style="font-size:14px;color:var(--tm)">Aucune conversation pour le moment</div></div>';return}
  var h='<div class="card" style="display:grid;grid-template-columns:280px 1fr;height:500px"><div style="border-right:1px solid var(--bl);overflow-y:auto">';
  entries.forEach(function(e,i){
    var phone=e[0],data=e[1];
    var name=(contacts[phone]&&contacts[phone].name)||phone;
    var lastMsg=data.last_message||'';
    h+='<div class="conv-list-item'+(i===0?' selected':'')+'" data-conv="'+phone+'"><div style="font-size:13px;font-weight:600">'+name+'</div><div style="font-size:11px;color:var(--tm);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'+lastMsg+'</div></div>';
  });
  h+='</div><div id="convMessages" style="padding:20px;overflow-y:auto;display:flex;flex-direction:column"></div></div>';
  c.innerHTML=h;
  if(entries.length)selectConv(entries[0][0]);
}
function selectConv(phone,el){
  if(el){document.querySelectorAll('.conv-list-item').forEach(function(e){e.classList.remove('selected')});el.classList.add('selected')}
  var data=conversations[phone];if(!data)return;
  var mc=document.getElementById('convMessages');
  var h='';
  (data.messages||[]).forEach(function(m){
    h+='<div class="bubble '+(m.role==='user'?'bubble-user':'bubble-bot')+'">'+esc(m.content||m.text||'')+'</div>';
  });
  mc.innerHTML=h;
  mc.scrollTop=mc.scrollHeight;
}

// ===== REVIEWS =====
function renderReviews(c){
  if(!reviewQueue.length){c.innerHTML='<div class="ph"><div class="phi">★</div><div style="font-size:18px;font-weight:600;margin-bottom:4px">Avis</div><div style="font-size:14px;color:var(--tm)">Aucun avis en attente</div></div>';return}
  var stats={total:reviewQueue.length,sent:0,responded:0,positive:0,negative:0,neutral:0};
  reviewQueue.forEach(function(r){if(r.sent)stats.sent++;if(r.responded){stats.responded++;if(r.sentiment==='POSITIVE')stats.positive++;else if(r.sentiment==='NEGATIVE')stats.negative++;else stats.neutral++;}});
  var h='';
  // Stats bar
  h+='<div class="sg" style="margin-bottom:16px">';
  h+='<div class="sc"><div class="sl">Total</div><div class="sv" style="color:var(--ac)">'+stats.total+'</div><div class="ss2">demandes</div></div>';
  h+='<div class="sc"><div class="sl">Envoyés</div><div class="sv" style="color:var(--ok)">'+stats.sent+'</div><div class="ss2">messages</div></div>';
  h+='<div class="sc"><div class="sl">Réponses</div><div class="sv" style="color:var(--bl2)">'+stats.responded+'</div><div class="ss2">clients</div></div>';
  h+='<div class="sc"><div class="sl">Positifs</div><div class="sv" style="color:#10B981">'+stats.positive+'</div><div class="ss2">😊</div></div>';
  h+='</div>';
  // Review list
  h+='<div class="card">';
  reviewQueue.slice().reverse().forEach(function(r){
    var sentimentColor=r.sentiment==='POSITIVE'?'#10B981':r.sentiment==='NEGATIVE'?'#EF4444':'#F59E0B';
    var sentimentLabel=r.sentiment==='POSITIVE'?'😊 Positif':r.sentiment==='NEGATIVE'?'😔 Négatif':r.sentiment?'😐 Neutre':'';
    var statusLabel=r.responded?sentimentLabel:r.sent?'Envoyé':'En attente';
    var statusBg=r.responded?(r.sentiment==='POSITIVE'?'#E6FAF8':r.sentiment==='NEGATIVE'?'#FEF2F2':'#FFFBEB'):r.sent?'var(--okb)':'var(--wab)';
    var statusCol=r.responded?sentimentColor:r.sent?'var(--ok)':'var(--wa)';
    h+='<div style="padding:16px;border-bottom:1px solid var(--bl)">';
    h+='<div style="display:flex;justify-content:space-between;align-items:center">';
    h+='<div><div style="font-size:14px;font-weight:600">'+(r.name||r.phone)+'</div>';
    h+='<div style="font-size:12px;color:var(--tm);margin-top:2px">'+(r.booking_time||'')+'</div></div>';
    h+='<span class="badge" style="background:'+statusBg+';color:'+statusCol+'">'+statusLabel+'</span></div>';
    if(r.response){
      h+='<div style="margin-top:10px;padding:10px 14px;background:var(--bg);border-radius:10px;border-left:3px solid '+sentimentColor+'">';
      h+='<div style="font-size:11px;font-weight:700;color:var(--tm);margin-bottom:4px;text-transform:uppercase;letter-spacing:.06em">Réponse du client</div>';
      h+='<div style="font-size:13px;color:var(--t)">'+r.response+'</div>';
      h+='</div>';
    }
    h+='</div>';
  });
  h+='</div>';
  c.innerHTML=h;
}

// ===== CONTACTS =====
function renderContacts(c){
  var entries=Object.entries(contacts);
  var srcColors={whatsapp:'#25D366',web:'#2563EB',phone:'#A8A29E','walk-in':'#78716C',zenchef:'#FF6B35'};
  var srcLabels={whatsapp:'WhatsApp',web:'Web',phone:'Tel','walk-in':'Walk-in',zenchef:'Zenchef'};
  var h='<div class="card"><div class="card-h"><div><div class="card-t">Tous les contacts</div><div class="card-s">'+entries.length+' clients</div></div></div>';
  entries.forEach(function(e){
    var phone=e[0],ct=e[1];
    var src=ct.source||'phone';
    h+='<div class="rw" data-contact="'+phone+'" style="cursor:pointer"><div class="rl"><div style="width:36px;height:36px;border-radius:50%;background:var(--al);display:flex;align-items:center;justify-content:center;color:var(--ac);font-size:13px;font-weight:700">'+(ct.name||'?').charAt(0).toUpperCase()+'</div><div><div style="font-size:14px;font-weight:600">'+(ct.name||phone)+'</div><div style="font-size:12px;color:var(--tm)">'+phone+(ct.email?' · '+ct.email:'')+'</div></div></div><div style="display:flex;align-items:center;gap:8px"><span style="font-size:12px;color:var(--ts)">'+(ct.visits||0)+' visite'+((ct.visits||0)>1?'s':'')+'</span><span class="src-badge" style="color:'+(srcColors[src]||'#A8A29E')+';background:'+(srcColors[src]||'#A8A29E')+'15">'+(srcLabels[src]||src)+'</span></div></div>';
  });
  if(!entries.length) h+='<div style="padding:30px;text-align:center;color:var(--tm)">Aucun contact</div>';
  h+='</div>';
  c.innerHTML=h;
}

function openContactCard(phone){
  var ct=contacts[phone];
  if(!ct)return;
  var conv=conversations[phone];
  var msgs=(conv&&conv.messages)?conv.messages:(conv||[]);
  var resas=bookings.filter(function(b){return b.phone===phone});
  var srcColors={whatsapp:'#25D366',web:'#2563EB',phone:'#A8A29E','walk-in':'#78716C',zenchef:'#FF6B35'};

  var h='<div style="margin-bottom:16px"><button class="ba" data-nav="contacts" style="font-size:12px;padding:4px 12px">← Retour</button></div>';
  // Client header
  h+='<div class="card" style="padding:24px;margin-bottom:16px">';
  h+='<div style="display:flex;align-items:center;gap:16px;margin-bottom:16px"><div style="width:56px;height:56px;border-radius:50%;background:var(--al);display:flex;align-items:center;justify-content:center;color:var(--ac);font-size:22px;font-weight:700">'+(ct.name||'?').charAt(0).toUpperCase()+'</div>';
  h+='<div><div style="font-size:20px;font-weight:700;color:var(--t)">'+(ct.name||phone)+'</div>';
  h+='<div style="font-size:13px;color:var(--tm)">'+phone+(ct.email?' · '+ct.email:'')+'</div></div></div>';
  // Stats row
  h+='<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px">';
  h+='<div style="text-align:center;padding:12px;background:var(--bg);border-radius:10px"><div style="font-size:22px;font-weight:800;color:var(--ac)">'+(ct.visits||0)+'</div><div style="font-size:11px;color:var(--tm);font-weight:600">Visites</div></div>';
  h+='<div style="text-align:center;padding:12px;background:var(--bg);border-radius:10px"><div style="font-size:22px;font-weight:800;color:var(--ok)">'+resas.length+'</div><div style="font-size:11px;color:var(--tm);font-weight:600">Réservations</div></div>';
  h+='<div style="text-align:center;padding:12px;background:var(--bg);border-radius:10px"><div style="font-size:22px;font-weight:800;color:var(--bl2)">'+msgs.length+'</div><div style="font-size:11px;color:var(--tm);font-weight:600">Messages</div></div>';
  h+='</div></div>';

  // Preferences, tags, notes — always show section with edit buttons
  h+='<div class="card" style="padding:20px;margin-bottom:16px"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px"><div class="card-t" style="margin:0">Profil client</div><div style="display:flex;gap:6px"><button class="ba" style="font-size:11px;padding:3px 10px" data-editprefs="'+phone+'">Modifier</button></div></div>';
  if(ct.preferences){h+='<div style="margin-bottom:8px"><span style="font-size:11px;font-weight:700;color:var(--tm);text-transform:uppercase;letter-spacing:.06em">Préférences</span><div style="margin-top:4px;display:flex;flex-wrap:wrap;gap:4px">';
    ct.preferences.split(',').forEach(function(p){if(p.trim())h+='<span style="padding:3px 8px;border-radius:6px;background:var(--al);color:var(--ac);font-size:11px;font-weight:600">'+p.trim()+'</span>'});
    h+='</div></div>'}
  if(ct.tags&&ct.tags.length){h+='<div style="margin-bottom:8px"><span style="font-size:11px;font-weight:700;color:var(--tm);text-transform:uppercase;letter-spacing:.06em">Tags</span><div style="margin-top:4px;display:flex;flex-wrap:wrap;gap:4px">';
    ct.tags.forEach(function(t){h+='<span style="padding:3px 8px;border-radius:6px;background:var(--okb);color:var(--ok);font-size:11px;font-weight:600">'+t+'</span>'});
    h+='</div></div>'}
  if(ct.notes){h+='<div style="margin-bottom:8px"><span style="font-size:11px;font-weight:700;color:var(--tm);text-transform:uppercase;letter-spacing:.06em">Notes</span><div style="margin-top:4px;font-size:13px;color:var(--ts);background:var(--bg);padding:10px;border-radius:8px">'+ct.notes+'</div></div>'}
  if(!ct.notes){h+='<div style="margin-bottom:8px"><span style="font-size:11px;font-weight:700;color:var(--tm);text-transform:uppercase;letter-spacing:.06em">Notes</span><div style="margin-top:4px;font-size:12px;color:var(--tm);font-style:italic">Aucune note. Cliquez Modifier pour ajouter.</div></div>'}
  if(ct.language){h+='<div><span style="font-size:11px;font-weight:700;color:var(--tm);text-transform:uppercase;letter-spacing:.06em">Langue</span><span style="margin-left:8px;font-size:13px;color:var(--ts)">'+ct.language+'</span></div>'}
  h+='</div>';

  // Reservations with dates
  if(resas.length){
    h+='<div class="card" style="padding:20px;margin-bottom:16px"><div class="card-t" style="margin-bottom:12px">Historique réservations</div>';
    resas.forEach(function(b){
      var dateLabel=b.date||'';
      if(dateLabel){
        try{
          var parts=dateLabel.split('-');
          var d=new Date(parseInt(parts[0]),parseInt(parts[1])-1,parseInt(parts[2]));
          var days=['Dim','Lun','Mar','Mer','Jeu','Ven','Sam'];
          var months=['jan','fév','mar','avr','mai','jun','jul','aoû','sep','oct','nov','déc'];
          dateLabel=days[d.getDay()]+' '+d.getDate()+' '+months[d.getMonth()]+' '+d.getFullYear();
        }catch(e){}
      }
      h+='<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid var(--bl)">';
      h+='<div><div style="font-weight:600;font-size:14px">'+b.covers+'p · '+(b.booking_time||b.time||'')+'</div>';
      h+='<div style="font-size:11px;color:var(--tm);margin-top:2px">'+(dateLabel||'Date non renseignée')+(b.source?' · '+b.source:'')+'</div></div>';
      h+='<div style="display:flex;gap:6px;align-items:center">';
      if(b.zone)h+='<span style="font-size:10px;color:var(--tm)">'+b.zone+'</span>';
      h+='<span class="badge" style="background:var(--okb);color:var(--ok)">'+(b.table||'—')+'</span></div>';
      h+='</div>';
    });
    h+='</div>';
  }

  // Conversation history
  if(msgs.length){
    h+='<div class="card" style="padding:20px"><div class="card-t" style="margin-bottom:12px">Conversation</div>';
    msgs.slice(-15).forEach(function(m){
      var isBot=m.role==='assistant';
      h+='<div style="display:flex;flex-direction:column;align-items:'+(isBot?'flex-start':'flex-end')+';margin-bottom:8px">';
      h+='<div style="max-width:80%;padding:8px 12px;border-radius:12px;background:'+(isBot?'var(--bg)':'var(--ac)')+';color:'+(isBot?'var(--t)':'white')+';font-size:13px">'+esc((m.content||m.text||'').substring(0,200))+'</div>';
      h+='<div style="font-size:10px;color:var(--tm);margin-top:2px">'+(m.time||'')+'</div>';
      h+='</div>';
    });
    h+='</div>';
  }

  document.getElementById('mainContent').innerHTML=h;
}

function editContactPrefs(phone){
  var ct=contacts[phone];
  if(!ct)return;
  var h='<div style="margin-bottom:16px"><button class="ba" style="font-size:12px;padding:4px 12px" data-backcontact="'+phone+'">&#8592; Retour</button></div>';
  h+='<div class="card" style="padding:24px;margin-bottom:16px">';
  h+='<div class="card-t" style="margin-bottom:16px">Modifier le profil client</div>';
  h+='<div style="font-size:14px;font-weight:600;color:var(--t);margin-bottom:16px">'+(ct.name||phone)+'</div>';

  h+='<div style="margin-bottom:14px"><label style="font-size:11px;font-weight:700;color:var(--tm);text-transform:uppercase;display:block;margin-bottom:4px">Préférences</label>';
  h+='<input id="editPrefs" type="text" value="'+(ct.preferences||'')+'" placeholder="ex: terrasse, viande, Chateau Miraval rosé, table 2" style="width:100%;padding:10px 12px;border:1.5px solid var(--b);border-radius:8px;font-size:13px;font-family:var(--f);outline:none">';
  h+='<div style="font-size:10px;color:var(--tm);margin-top:3px">Séparez par des virgules</div></div>';

  h+='<div style="margin-bottom:14px"><label style="font-size:11px;font-weight:700;color:var(--tm);text-transform:uppercase;display:block;margin-bottom:4px">Notes</label>';
  h+='<textarea id="editNotes" rows="4" placeholder="ex: Anniversaire en juin, aime les desserts, vient souvent le vendredi soir" style="width:100%;padding:10px 12px;border:1.5px solid var(--b);border-radius:8px;font-size:13px;font-family:var(--f);outline:none;resize:vertical">'+(ct.notes||'')+'</textarea></div>';

  h+='<div style="margin-bottom:14px"><label style="font-size:11px;font-weight:700;color:var(--tm);text-transform:uppercase;display:block;margin-bottom:4px">Tags</label>';
  h+='<input id="editTags" type="text" value="'+((ct.tags||[]).join(', '))+'" placeholder="ex: VIP, fidèle, allergique gluten" style="width:100%;padding:10px 12px;border:1.5px solid var(--b);border-radius:8px;font-size:13px;font-family:var(--f);outline:none">';
  h+='<div style="font-size:10px;color:var(--tm);margin-top:3px">Séparez par des virgules</div></div>';

  h+='<div style="display:flex;gap:10px;margin-top:20px">';
  h+='<button class="ba" style="background:var(--acg);color:white;border:none;padding:10px 24px;font-weight:700" data-saveprefs="'+phone+'">Enregistrer</button>';
  h+='<button class="ba" style="padding:10px 24px" data-backcontact="'+phone+'">Annuler</button>';
  h+='</div></div>';

  document.getElementById('mainContent').innerHTML=h;
}

function saveContactPrefs(phone){
  var prefs=document.getElementById('editPrefs').value.trim();
  var notes=document.getElementById('editNotes').value.trim();
  var tags=document.getElementById('editTags').value.trim();
  var tagsArr=tags?tags.split(',').map(function(t){return t.trim()}).filter(function(t){return t}):[];

  // Update locally
  if(contacts[phone]){
    contacts[phone].preferences=prefs;
    contacts[phone].notes=notes;
    contacts[phone].tags=tagsArr;
  }

  // Save to server
  apiFetch('/api/contacts/note',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:phone,note:notes})});
  apiFetch('/api/contacts/tag',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:phone,tags:tagsArr})});
  // Save preferences via a new endpoint or reuse note
  apiFetch('/api/contacts/preferences',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:phone,preferences:prefs})});

  showToast('Profil mis à jour');
  openContactCard(phone);
}

// ===== CONFIG =====
function renderConfig(c){
  var h='';
  // Overview toggles
  h+='<div class="card" style="padding:20px;margin-bottom:16px"><div class="cfs"><div class="cft">Personnaliser la vue d&#39;ensemble</div><div class="cfsb">Cochez les blocs à afficher sur votre page d&#39;accueil</div>';
  var blocks=[{k:'daily',l:'📢 Message du jour'},{k:'stats',l:'📊 Statistiques'},{k:'floor',l:'⊞ Plan de salle'},{k:'bookings',l:'◉ Réservations & Conversations'},{k:'contacts',l:'◇ Contacts'}];
  blocks.forEach(function(b){
    h+='<div class="cfr"><div><div class="cfl">'+b.l+'</div></div><div class="tog'+(overviewBlocks[b.k]?' on':'')+'" data-blk="'+b.k+'" onclick="toggleOverviewBlock(this)"><div class="togd"></div></div></div>';
  });
  h+='</div></div>';

  // Automations section
  h+='<div class="card" style="padding:20px;margin-bottom:16px"><div class="cfs"><div class="cft">Automatisations</div><div class="cfsb">Configurez les messages automatiques envoyés à vos clients</div>';
  var remOn=(restaurantConfig._reminders_enabled!==false);
  h+='<div class="cfr"><div><div class="cfl">🔔 Rappels de réservation</div><div class="cfd">Déjeuner : rappel la veille à 19h · Dîner : rappel le jour même à 11h</div></div><div class="tog'+(remOn?' on':'')+'" onclick="toggleReminders(this)"><div class="togd"></div></div></div>';
  h+='</div></div>';
  
  // Restaurant config
  h+='<div class="card" style="padding:20px;margin-bottom:16px"><div class="cfs"><div class="cft">Informations du restaurant</div><div class="cfsb">Utilisées par l&#39;agent IA pour répondre aux clients</div>';
  var fields=[{k:'name',l:'Nom'},{k:'address',l:'Adresse'},{k:'phone',l:'Téléphone'},{k:'hours',l:'Horaires'},{k:'description',l:'Description'},{k:'tone',l:'Ton de l agent IA'}];
  fields.forEach(function(f){
    h+='<div class="cfr"><div><div class="cfl">'+f.l+'</div><div class="cfd">'+(restaurantConfig[f.k]||'Non configure')+'</div></div><span style="font-size:12px;color:var(--ac);font-weight:600;cursor:pointer" data-cfgkey="'+f.k+'" data-cfglabel="'+f.l+'">Modifier</span></div>';
  });
  h+='</div></div>';
  
  c.innerHTML=h;
}

function toggleOverviewBlock(el){
  el.classList.toggle('on');
  var k=el.getAttribute('data-blk');
  overviewBlocks[k]=el.classList.contains('on');
  showToast(el.classList.contains('on')?'Bloc active':'Bloc masque');
}

function toggleReminders(el){
  el.classList.toggle('on');
  var enabled=el.classList.contains('on');
  restaurantConfig._reminders_enabled=enabled;
  apiFetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reminders_enabled:enabled})});
  showToast(enabled?'Rappels activés':'Rappels désactivés');
}

function editConfigField(key,label){
  var val=prompt(label+' :',restaurantConfig[key]||'');
  if(val!==null){
    restaurantConfig[key]=val;
    apiFetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(restaurantConfig)});
    renderConfig(document.getElementById('mainContent'));
    showToast(label+' mis a jour');
  }
}

// ===== ONBOARDING WIZARD =====
var obStep=0;
var obData={};
var OB_STEPS=[
  {id:'welcome',title:'Bienvenue sur GuestScale',desc:'Configurons votre restaurant en quelques etapes.',fields:[]},
  {id:'info',title:'Informations du restaurant',desc:'Ces infos seront utilisees par votre agent IA.',fields:[
    {k:'name',l:'Nom du restaurant',type:'input',placeholder:'Le Cosi Nice'},
    {k:'address',l:'Adresse',type:'input',placeholder:'12 rue de la Paix, 06000 Nice'},
    {k:'phone',l:'Telephone',type:'input',placeholder:'+33 4 93 XX XX XX'}
  ]},
  {id:'hours',title:'Horaires et description',desc:'Aidez votre agent IA a renseigner les clients.',fields:[
    {k:'hours',l:'Horaires d ouverture',type:'textarea',placeholder:'Lundi-Vendredi 12h-14h30, 19h-22h30\\nSamedi 19h-23h\\nFerme le dimanche'},
    {k:'description',l:'Description courte',type:'textarea',placeholder:'Restaurant italien au coeur du Vieux-Nice, cuisine traditionnelle et produits frais.'}
  ]},
  {id:'tone',title:'Personnalite de votre agent IA',desc:'Definissez comment votre assistant parle aux clients.',fields:[
    {k:'tone',l:'Ton de communication',type:'textarea',placeholder:'Chaleureux et professionnel, tutoie les clients reguliers, utilise des emojis avec parcimonie.'},
    {k:'languages',l:'Langues parlees',type:'input',placeholder:'francais, anglais, italien'}
  ]},
  {id:'done',title:'Votre restaurant est configure !',desc:'Vous pouvez maintenant recevoir des reservations et configurer votre plan de salle.',fields:[]}
];

function checkOnboarding(){
  // Check if onboarding was already completed (session)
  try{if(sessionStorage.getItem('ob_done')==='1')return}catch(e){}
  // Check if restaurant has minimal config (name + address set and not default)
  var name=restaurantConfig.name||'';
  var addr=restaurantConfig.address||'';
  if(name&&name!=='Le Cosi Nice'&&addr){
    try{sessionStorage.setItem('ob_done','1')}catch(e){}
    return;
  }
  // Also check server-side flag
  apiFetch('/api/settings').then(function(r){return r.json()}).then(function(d){
    if(d.onboarding_done==='1'){
      try{sessionStorage.setItem('ob_done','1')}catch(e){}
      return;
    }
    // Show onboarding
    obStep=0;
    obData={
      name:restaurantConfig.name||'',
      address:restaurantConfig.address||'',
      phone:restaurantConfig.phone||'',
      hours:restaurantConfig.hours||'',
      description:restaurantConfig.description||'',
      tone:restaurantConfig.tone||'',
      languages:restaurantConfig.languages||'francais, anglais, italien'
    };
    renderOnboarding();
  }).catch(function(){});
}

function renderOnboarding(){
  var el=document.getElementById('onboardingOverlay');
  var step=OB_STEPS[obStep];
  var total=OB_STEPS.length;

  var h='<div class="ob-overlay"><div class="ob-card">';
  // Logo
  h+='<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px"><div style="width:36px;height:36px;background:#0F1117;border-radius:9px;display:flex;align-items:center;justify-content:center"><svg viewBox="0 0 32 32" fill="none" style="width:22px;height:22px"><circle cx="10" cy="10" r="4" fill="#2D7DD2"/><circle cx="22" cy="10" r="4" fill="#4ECDC4"/><circle cx="16" cy="22" r="4" fill="#4ECDC4"/><line x1="13" y1="11" x2="19" y2="11" stroke="#2D7DD2" stroke-width="2"/><line x1="11" y1="13" x2="15" y2="19" stroke="#2D7DD2" stroke-width="2"/><line x1="21" y1="13" x2="17" y2="19" stroke="#4ECDC4" stroke-width="2"/></svg></div><div style="font-size:18px;font-weight:800;color:var(--t);letter-spacing:-.02em">Guest<span style="color:#4ECDC4">Scale</span></div></div>';
  h+='<div style="font-size:12px;color:var(--tm);margin-bottom:24px">Configuration de votre restaurant</div>';

  // Progress steps
  h+='<div class="ob-steps">';
  for(var i=0;i<total;i++){
    h+='<div class="ob-step'+(i<obStep?' done':'')+(i===obStep?' active':'')+'"></div>';
  }
  h+='</div>';

  // Content
  h+='<div class="ob-title">'+step.title+'</div>';
  h+='<div class="ob-desc">'+step.desc+'</div>';

  if(step.id==='welcome'){
    h+='<div style="padding:20px;background:var(--bg);border-radius:12px;margin-bottom:10px">';
    h+='<div style="font-size:13px;color:var(--ts);line-height:1.6">';
    h+='&#10003; Agent IA WhatsApp pour vos clients<br>';
    h+='&#10003; Gestion des reservations et plan de salle<br>';
    h+='&#10003; CRM et suivi des contacts<br>';
    h+='&#10003; Statistiques et recap quotidien';
    h+='</div></div>';
  }

  if(step.id==='done'){
    h+='<div style="padding:24px;background:var(--bg);border-radius:12px;text-align:center;margin-bottom:10px">';
    h+='<div style="font-size:40px;margin-bottom:8px">&#127881;</div>';
    h+='<div style="font-size:14px;color:var(--t);font-weight:600">Prochaines etapes :</div>';
    h+='<div style="font-size:13px;color:var(--ts);margin-top:8px;line-height:1.6">';
    h+='1. Configurez votre plan de salle<br>';
    h+='2. Ajoutez votre menu<br>';
    h+='3. Testez l agent IA sur WhatsApp';
    h+='</div></div>';
  }

  // Fields
  step.fields.forEach(function(f){
    h+='<div class="ob-field"><div class="ob-label">'+f.l+'</div>';
    if(f.type==='textarea'){
      h+='<textarea class="ob-textarea" id="ob_'+f.k+'" placeholder="'+f.placeholder+'">'+(obData[f.k]||'')+'</textarea>';
    }else{
      h+='<input class="ob-input" id="ob_'+f.k+'" placeholder="'+f.placeholder+'" value="'+(obData[f.k]||'')+'">';
    }
    h+='</div>';
  });

  // Actions
  h+='<div class="ob-actions">';
  if(obStep>0&&step.id!=='done'){
    h+='<button class="ob-btn ob-btn-s" data-obPrev>Retour</button>';
  }
  if(step.id==='done'){
    h+='<button class="ob-btn ob-btn-p" data-obFinish>Commencer</button>';
  }else if(step.id==='welcome'){
    h+='<button class="ob-btn ob-btn-p" data-obNext>Configurer mon restaurant</button>';
  }else{
    h+='<button class="ob-btn ob-btn-p" data-obNext>Continuer</button>';
  }
  h+='</div>';

  if(step.id!=='done'&&step.id!=='welcome'){
    h+='<div class="ob-skip" data-obSkipAll>Passer la configuration</div>';
  }

  h+='</div></div>';
  el.innerHTML=h;
  el.style.display='block';
}

function obSaveStepData(){
  var step=OB_STEPS[obStep];
  step.fields.forEach(function(f){
    var el=document.getElementById('ob_'+f.k);
    if(el)obData[f.k]=el.value.trim();
  });
}

function obNext(){
  obSaveStepData();
  obStep++;
  if(obStep>=OB_STEPS.length)obStep=OB_STEPS.length-1;
  renderOnboarding();
}

function obPrev(){
  obSaveStepData();
  obStep--;
  if(obStep<0)obStep=0;
  renderOnboarding();
}

function obFinish(){
  // Save all config
  var cfg={};
  for(var k in obData){if(obData[k])cfg[k]=obData[k]}
  apiFetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)}).then(function(){
    // Update local config
    for(var k in cfg){restaurantConfig[k]=cfg[k]}
    // Mark onboarding done
    try{sessionStorage.setItem('ob_done','1')}catch(e){}
    apiFetch('/api/settings?set=onboarding_done&value=1');
    // Hide overlay
    document.getElementById('onboardingOverlay').style.display='none';
    fetchData();
    showToast('Restaurant configure avec succes !');
  });
}

function obSkipAll(){
  try{sessionStorage.setItem('ob_done','1')}catch(e){}
  apiFetch('/api/settings?set=onboarding_done&value=1');
  document.getElementById('onboardingOverlay').style.display='none';
}

// ===== STATS =====
function renderStats(c){
  c.innerHTML='<div style="text-align:center;padding:40px;color:var(--tm)">Chargement des statistiques...</div>';
  apiFetch('/api/stats/history').then(function(r){return r.json()}).then(function(data){
    var t=data.today||{};
    var history=data.history||[];
    var srcLabels={whatsapp:'WhatsApp',web:'Chat web',phone:'Telephone','walk-in':'Walk-in',zenchef:'Zenchef'};
    var srcColors={whatsapp:'#25D366',web:'#2D7DD2',phone:'#9CA3AF','walk-in':'#6B7280',zenchef:'#F59E0B'};

    var h='';

    // Today's recap card
    h+='<div class="card" style="padding:20px;margin-bottom:14px;background:linear-gradient(135deg,#EBF4FF,#E6FAF8);border-color:#B8D8F8">';
    h+='<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:16px"><div><div style="font-size:16px;font-weight:800;color:var(--t)">Recap du jour</div><div style="font-size:12px;color:var(--ts);margin-top:2px">'+new Date().toLocaleDateString("fr-FR",{weekday:"long",day:"numeric",month:"long"})+'</div></div>';
    if(t.tomorrow_bookings>0){h+='<div style="padding:8px 14px;background:var(--card);border-radius:8px;border:1px solid var(--b)"><div style="font-size:18px;font-weight:800;color:var(--ac)">'+t.tomorrow_bookings+'</div><div style="font-size:10px;color:var(--tm);font-weight:600">DEMAIN</div></div>'}
    h+='</div>';

    // KPI row inside recap
    h+='<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px">';
    h+='<div style="padding:12px;background:var(--card);border-radius:10px;text-align:center"><div style="font-size:24px;font-weight:800;color:var(--ok)">'+t.bookings+'</div><div style="font-size:9px;color:var(--tm);font-weight:700;text-transform:uppercase;margin-top:2px">Resas</div></div>';
    h+='<div style="padding:12px;background:var(--card);border-radius:10px;text-align:center"><div style="font-size:24px;font-weight:800;color:var(--ac)">'+t.covers+'</div><div style="font-size:9px;color:var(--tm);font-weight:700;text-transform:uppercase;margin-top:2px">Couverts</div></div>';
    h+='<div style="padding:12px;background:var(--card);border-radius:10px;text-align:center"><div style="font-size:24px;font-weight:800;color:var(--bl2)">'+t.occ_rate+'%</div><div style="font-size:9px;color:var(--tm);font-weight:700;text-transform:uppercase;margin-top:2px">Occupation</div></div>';
    h+='<div style="padding:12px;background:var(--card);border-radius:10px;text-align:center"><div style="font-size:24px;font-weight:800;color:var(--wa)">'+t.messages+'</div><div style="font-size:9px;color:var(--tm);font-weight:700;text-transform:uppercase;margin-top:2px">Messages</div></div>';
    h+='</div>';

    // Extra info row
    h+='<div style="display:flex;gap:12px;margin-top:12px;font-size:12px;color:var(--ts)">';
    if(t.new_contacts)h+='<span>👤 '+t.new_contacts+' nouveaux contacts</span>';
    if(t.pending_reviews)h+='<span>⭐ '+t.pending_reviews+' avis en attente</span>';
    h+='<span>'+t.tables_occupied+'/'+t.tables_total+' tables occupees</span>';
    h+='</div>';
    h+='</div>';

    // History chart (bar chart with bookings per day)
    if(history.length>0){
      h+='<div class="card" style="padding:20px;margin-bottom:14px"><div class="card-t" style="margin-bottom:16px">Historique des reservations</div>';
      h+='<div style="display:flex;align-items:flex-end;gap:3px;height:140px;padding-bottom:24px;position:relative">';
      var maxB=Math.max.apply(null,history.map(function(d){return d.bookings||0}).concat([t.bookings||1]));
      // Show history + today
      var allDays=history.concat([{date:t.date,bookings:t.bookings,covers:t.covers}]);
      var last14=allDays.slice(-14);
      last14.forEach(function(d,i){
        var pct=maxB?Math.round((d.bookings||0)/maxB*100):0;
        var isToday=d.date===t.date;
        var dayLabel=d.date?d.date.slice(8,10):"";
        var dow="";try{var dt=new Date(d.date+"T12:00:00");dow=["D","L","M","M","J","V","S"][dt.getDay()]}catch(e){}
        h+='<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:2px">';
        h+='<div style="font-size:9px;font-weight:700;color:'+(isToday?'var(--ac)':'var(--tm)')+'">'+((d.bookings||0)||"")+'</div>';
        h+='<div style="width:100%;height:'+Math.max(pct,4)+'%;background:'+(isToday?'var(--acg)':'var(--ac)30')+';border-radius:4px 4px 0 0;min-height:4px;transition:height .3s"></div>';
        h+='<div style="font-size:8px;color:'+(isToday?'var(--ac)':'var(--tm)')+';font-weight:'+(isToday?'800':'600')+'">'+dow+'</div>';
        h+='<div style="font-size:8px;color:'+(isToday?'var(--ac)':'var(--tm)')+';font-weight:'+(isToday?'800':'500')+'">'+dayLabel+'</div>';
        h+='</div>';
      });
      h+='</div></div>';
    }

    // Source breakdown + Communication (2 cols)
    h+='<div class="g2" style="margin-bottom:14px">';

    // Sources
    var sources=t.sources||{};
    var totalSrc=Object.values(sources).reduce(function(a,v){return a+v},0)||1;
    h+='<div class="card" style="padding:20px"><div class="card-t" style="margin-bottom:16px">Réservations par canal</div>';
    var srcEntries=Object.entries(sources).sort(function(a,b){return b[1]-a[1]});
    if(srcEntries.length){
      srcEntries.forEach(function(e){
        var pct=Math.round(e[1]/totalSrc*100);
        var col=srcColors[e[0]]||"#9CA3AF";
        h+='<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">';
        h+='<div style="width:80px;font-size:12px;font-weight:600;color:var(--ts)">'+(srcLabels[e[0]]||e[0])+'</div>';
        h+='<div style="flex:1;height:28px;background:var(--bg);border-radius:6px;overflow:hidden;position:relative"><div style="width:'+Math.max(pct,2)+'%;height:100%;background:'+col+';border-radius:6px;transition:width .3s"></div><span style="position:absolute;right:8px;top:50%;transform:translateY(-50%);font-size:11px;font-weight:700;color:var(--t)">'+e[1]+' ('+pct+'%)</span></div>';
        h+='</div>';
      });
    }else{
      h+='<div style="text-align:center;color:var(--tm);padding:20px;font-size:13px">Aucune donnee</div>';
    }
    h+='</div>';

    // Communication
    var convArr=Object.entries(conversations);
    var totalMsgs=0;convArr.forEach(function(e){var d=e[1];totalMsgs+=((d.messages&&d.messages.length)||d.count||0)});
    h+='<div class="card" style="padding:20px"><div class="card-t" style="margin-bottom:16px">Communication</div>';
    h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px">';
    h+='<div style="padding:14px;background:var(--bg);border-radius:10px;text-align:center"><div style="font-size:22px;font-weight:800;color:var(--ac)">'+totalMsgs+'</div><div style="font-size:10px;color:var(--tm);font-weight:600">Messages total</div></div>';
    h+='<div style="padding:14px;background:var(--bg);border-radius:10px;text-align:center"><div style="font-size:22px;font-weight:800;color:var(--ok)">'+convArr.length+'</div><div style="font-size:10px;color:var(--tm);font-weight:600">Conversations</div></div>';
    h+='</div>';
    h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">';
    h+='<div style="padding:14px;background:var(--bg);border-radius:10px;text-align:center"><div style="font-size:22px;font-weight:800;color:var(--wa)">'+Object.keys(contacts).length+'</div><div style="font-size:10px;color:var(--tm);font-weight:600">Contacts CRM</div></div>';
    h+='<div style="padding:14px;background:var(--bg);border-radius:10px;text-align:center"><div style="font-size:22px;font-weight:800;color:var(--bl2)">'+(convArr.length?Math.round(totalMsgs/convArr.length):0)+'</div><div style="font-size:10px;color:var(--tm);font-weight:600">Msg/client</div></div>';
    h+='</div>';
    h+='</div>';

    h+='</div>';

    c.innerHTML=h;
  }).catch(function(err){
    console.error('Stats error:',err);
    c.innerHTML='<div style="text-align:center;padding:40px;color:var(--tm)">Erreur de chargement des statistiques</div>';
  });
}

// ===== RESERVATION MODAL =====
function openResaModal(){
  resaSelTable=null;
  ['resaFirst','resaLast','resaPhone','resaEmail'].forEach(function(id){document.getElementById(id).value=''});
  document.getElementById('resaCovers').value='2';
  document.getElementById('resaTime').value='20:00';
  document.getElementById('resaSource').value='phone';
  document.getElementById('resaTableBox').style.display='none';
  document.getElementById('resaTableSel').style.display='none';
  // Show selected date in modal
  var today=fmtDate(new Date());
  var dl=document.getElementById('resaDateLabel');
  if(dl){
    if(selectedDate===today)dl.textContent='';
    else dl.textContent='— '+parseDateLocal(selectedDate).toLocaleDateString('fr-FR',{weekday:'long',day:'numeric',month:'long'});
  }
  document.getElementById('resaModal').classList.add('show');
  resaAutoAssign();
}
function closeResaModal(){document.getElementById('resaModal').classList.remove('show')}

function resaAutoAssign(){
  var covers=parseInt(document.getElementById('resaCovers').value)||2;
  var time=document.getElementById('resaTime').value||'20:00';
  var best=null;
  // Only block tables that are booked at the SAME time (within 2h window)
  var th=parseInt(time.split(':')[0]);
  var tm=parseInt(time.split(':')[1]);
  var tMin=th*60+tm;
  var bookedTables=[];
  bookings.forEach(function(b){
    if(!(b.date||'').startsWith(selectedDate))return;
    if(!b.table)return;
    var bt=(b.booking_time||b.time||'');
    if(!bt)return;
    var bh=parseInt(bt.split(':')[0])||0;
    var bm=parseInt(bt.split(':')[1])||0;
    var bMin=bh*60+bm;
    if(Math.abs(bMin-tMin)<120)bookedTables.push(b.table);
  });
  floorplan.forEach(function(t){
    if(bookedTables.indexOf(t.id)===-1&&t.seats>=covers){if(!best||t.seats<best.seats)best=t}
  });
  if(best){
    resaSelTable=best.id;
    document.getElementById('resaTableBox').style.display='block';
    document.getElementById('resaTableVal').textContent=best.id+' ('+best.seats+'p, '+best.zone+')';
  }else{
    resaSelTable=null;
    document.getElementById('resaTableBox').style.display='block';
    document.getElementById('resaTableVal').textContent='Aucune table disponible';
  }
  document.getElementById('resaTableSel').style.display='none';
}

function showResaTableSelect(){
  var covers=parseInt(document.getElementById('resaCovers').value)||2;
  var time=document.getElementById('resaTime').value||'20:00';
  var th=parseInt(time.split(':')[0]);
  var tm=parseInt(time.split(':')[1]);
  var tMin=th*60+tm;
  var bookedTables=[];
  bookings.forEach(function(b){
    if(!(b.date||'').startsWith(selectedDate))return;
    if(!b.table)return;
    var bt=(b.booking_time||b.time||'');
    if(!bt)return;
    var bh=parseInt(bt.split(':')[0])||0;
    var bm=parseInt(bt.split(':')[1])||0;
    if(Math.abs(bh*60+bm-tMin)<120)bookedTables.push(b.table);
  });
  var h='';
  floorplan.forEach(function(t){
    var taken=bookedTables.indexOf(t.id)!==-1;
    h+='<div class="tsb'+(taken?' taken':t.id===resaSelTable?' sel':'')+'" '+(taken?'':'data-pick="'+t.id+'"')+'>'+t.id+'<br><span style="font-size:10px;color:var(--tm)">'+t.seats+'p</span></div>';
  });
  document.getElementById('resaTableSel').innerHTML=h;
  document.getElementById('resaTableSel').style.display='grid';
}

function pickResaTable(id){
  resaSelTable=id;
  var t=floorplan.find(function(x){return x.id===id});
  document.getElementById('resaTableVal').textContent=t.id+' ('+t.seats+'p, '+t.zone+')';
  document.getElementById('resaTableSel').style.display='none';
}

function submitResa(){
  var first=document.getElementById('resaFirst').value.trim();
  var last=document.getElementById('resaLast').value.trim();
  if(!first||!last){showToast('Veuillez remplir le nom et prenom');return}
  var data={
    name:first+' '+last,
    covers:parseInt(document.getElementById('resaCovers').value)||2,
    time:document.getElementById('resaTime').value,
    phone:document.getElementById('resaPhone').value.trim(),
    email:document.getElementById('resaEmail').value.trim(),
    source:document.getElementById('resaSource').value,
    table:resaSelTable||'',
    date:selectedDate
  };
  apiFetch('/api/bookings/manual',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).then(function(r){return r.json()}).then(function(d){
    fetchData();
    closeResaModal();
    showToast(data.name+' — '+(d.table?'Table '+d.table:'Sans table'));
  }).catch(function(){
    showToast('Erreur lors de la creation');
  });
}

// === EVENT DELEGATION ===
document.addEventListener('click',function(e){
  // Login buttons
  if(e.target.closest('[data-doLogin]')){doLogin();return}
  if(e.target.closest('[data-togglePwd]')){togglePwdVis();return}

  // Onboarding wizard
  if(e.target.closest('[data-obNext]')){obNext();return}
  if(e.target.closest('[data-obPrev]')){obPrev();return}
  if(e.target.closest('[data-obFinish]')){obFinish();return}
  if(e.target.closest('[data-obSkipAll]')){obSkipAll();return}


  // Bookings view toggle
  var bkt=e.target.closest("[data-bkView]");
  if(bkt){bookingsView=bkt.getAttribute("data-bkView");renderPage(currentPage);return}
  var wst=e.target.closest("[data-weekShift]");
  if(wst){var shift=parseInt(wst.getAttribute("data-weekShift"));var d=parseDateLocal(selectedDate);d.setDate(d.getDate()+shift*7);selectedDate=fmtDate(d);renderPage(currentPage);return}
  var wtt=e.target.closest("[data-weekToday]");
  if(wtt){selectedDate=fmtDate(new Date());renderPage(currentPage);return}
  // Calendar navigation
  var t=e.target.closest('[data-calDate]');
  if(t){selectedDate=t.getAttribute('data-calDate');mergeBookingsIntoFloor();renderPage(currentPage);return}
  t=e.target.closest('[data-calShift]');
  if(t){var shift=parseInt(t.getAttribute('data-calShift'));var d=parseDateLocal(selectedDate);d.setMonth(d.getMonth()+shift);selectedDate=fmtDate(d);mergeBookingsIntoFloor();renderPage(currentPage);return}
  t=e.target.closest('[data-calToday]');
  if(t){selectedDate=fmtDate(new Date());mergeBookingsIntoFloor();renderPage(currentPage);return}
  t=e.target.closest('[data-calTogglePicker]');
  if(t){var pk=document.getElementById('calPicker');if(pk&&pk.classList.contains('show')){pk.classList.remove('show');calPickerMode=null}else if(calPickerMode==='month'){showCalPicker('year')}else{showCalPicker('month')}return}
  t=e.target.closest('[data-calPickMonth]');
  if(t){var m=parseInt(t.getAttribute('data-calPickMonth'));var d=parseDateLocal(selectedDate);d.setMonth(m);selectedDate=fmtDate(d);calPickerMode=null;mergeBookingsIntoFloor();renderPage(currentPage);return}
  t=e.target.closest('[data-calPickYear]');
  if(t){var y=parseInt(t.getAttribute('data-calPickYear'));var d=parseDateLocal(selectedDate);d.setFullYear(y);selectedDate=fmtDate(d);calPickerMode=null;mergeBookingsIntoFloor();renderPage(currentPage);return}

  t=e.target.closest('[data-pg]');
  if(t){switchPage(t.getAttribute('data-pg'),t);return}
  t=e.target.closest('[data-nav]');
  if(t){switchPage(t.getAttribute('data-nav'));return}
  t=e.target.closest('[data-conv]');
  if(t){selectConv(t.getAttribute('data-conv'),t);return}
  t=e.target.closest('[data-pick]');
  if(t&&!t.classList.contains('taken')){pickResaTable(t.getAttribute('data-pick'));return}
  t=e.target.closest('[data-cfgkey]');
  if(t){editConfigField(t.getAttribute('data-cfgkey'),t.getAttribute('data-cfglabel'));return}
  t=e.target.closest('[data-blk]');
  if(t&&t.classList.contains('tog')){toggleOverviewBlock(t);return}
  // Menu events
  if(e.target.closest('[data-addSection]')){menuAddSection();return}
  t=e.target.closest('[data-delSection]');
  if(t){menuDelSection(parseInt(t.getAttribute('data-delSection')));return}
  t=e.target.closest('[data-addItem]');
  if(t){menuAddItem(parseInt(t.getAttribute('data-addItem')));return}
  t=e.target.closest('[data-delItem]');
  if(t){var p=t.getAttribute('data-delItem').split('-');menuDelItem(parseInt(p[0]),parseInt(p[1]));return}
  if(e.target.closest('[data-saveMenu]')){menuSave();return}
  // Floor plan events
  if(e.target.closest('[data-fpSave]')){fpSave();return}
  if(e.target.closest('[data-fpDel]')){fpDeleteSelected();return}
  if(e.target.closest('[data-fpModeEdit]')){fpMode='edit';fpSlot='all';renderFloorplan(document.getElementById('mainContent'));return}
  if(e.target.closest('[data-fpModeResa]')){fpMode='resa';renderFloorplan(document.getElementById('mainContent'));return}
  t=e.target.closest('[data-fpSvc]');
  if(t){fpService=t.getAttribute('data-fpSvc');fpSlot='all';renderFloorplan(document.getElementById('mainContent'));return}
  t=e.target.closest('[data-fpSlot]');
  if(t){fpSlot=t.getAttribute('data-fpSlot');fpMergeForService();fpDrawTables();
    // Re-render slot pills
    renderFloorplan(document.getElementById('mainContent'));return}
  t=e.target.closest('[data-fpSaveResa]');
  if(t){fpSaveResaInline(t.getAttribute('data-fpSaveResa'));return}
  if(e.target.closest('[data-fpClosePopup]')){var pp=document.getElementById('fpResaPopup');if(pp)pp.style.display='none';fpSelected=null;fpDrawTables();return}
  t=e.target.closest('[data-fpCancelResa]');
  if(t){fpCancelResa(t.getAttribute('data-fpCancelResa'));return}
  t=e.target.closest('[data-fpSwap]');
  if(t){var parts=t.getAttribute('data-fpSwap').split('-');fpSwapTable(parts[0],parts[1]);return}
  t=e.target.closest('[data-fpAdd]');
  if(t){var ps=t.getAttribute('data-fpAdd').split('-');fpAddTable(ps[0],parseInt(ps[1]));return}
  t=e.target.closest('[data-fpSetZone]');
  if(t){fpUpdateSelected('zone',t.getAttribute('data-fpSetZone'));return}
  // Booking edit
  t=e.target.closest('[data-editResa]');
  if(t){openEditResa(parseInt(t.getAttribute('data-editResa')));return}
  // Contact click
  t=e.target.closest('[data-contact]');
  if(t){openContactCard(t.getAttribute('data-contact'));return}
  // Contact edit prefs
  t=e.target.closest('[data-editprefs]');
  if(t){editContactPrefs(t.getAttribute('data-editprefs'));return}
  // Save prefs
  t=e.target.closest('[data-saveprefs]');
  if(t){saveContactPrefs(t.getAttribute('data-saveprefs'));return}
  // Back to contact
  t=e.target.closest('[data-backcontact]');
  if(t){openContactCard(t.getAttribute('data-backcontact'));return}
});
function submitResa(){
  var first=document.getElementById('resaFirst').value.trim();
  var last=document.getElementById('resaLast').value.trim();
  if(!first||!last){showToast('Veuillez remplir le nom et prenom');return}
  var data={
    name:first+' '+last,
    covers:parseInt(document.getElementById('resaCovers').value)||2,
    time:document.getElementById('resaTime').value,
    phone:document.getElementById('resaPhone').value.trim(),
    email:document.getElementById('resaEmail').value.trim(),
    source:document.getElementById('resaSource').value,
    table:resaSelTable||'',
    date:selectedDate
  };
  apiFetch('/api/bookings/manual',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).then(function(r){return r.json()}).then(function(d){
    fetchData();
    closeResaModal();
    showToast(data.name+' — '+(d.table?'Table '+d.table:'Sans table'));
  }).catch(function(){
    showToast('Erreur lors de la creation');
  });
}

// === EVENT DELEGATION ===
document.addEventListener('click',function(e){
  // Login buttons
  if(e.target.closest('[data-doLogin]')){doLogin();return}
  if(e.target.closest('[data-togglePwd]')){togglePwdVis();return}

  // Onboarding wizard
  if(e.target.closest('[data-obNext]')){obNext();return}
  if(e.target.closest('[data-obPrev]')){obPrev();return}
  if(e.target.closest('[data-obFinish]')){obFinish();return}
  if(e.target.closest('[data-obSkipAll]')){obSkipAll();return}


  // Bookings view toggle
  var bkt2=e.target.closest("[data-bkView]");
  if(bkt2){bookingsView=bkt2.getAttribute("data-bkView");renderPage(currentPage);return}
  var wst2=e.target.closest("[data-weekShift]");
  if(wst2){var shift=parseInt(wst2.getAttribute("data-weekShift"));var d=parseDateLocal(selectedDate);d.setDate(d.getDate()+shift*7);selectedDate=fmtDate(d);renderPage(currentPage);return}
  var wtt2=e.target.closest("[data-weekToday]");
  if(wtt2){selectedDate=fmtDate(new Date());renderPage(currentPage);return}
  // Calendar navigation
  var t=e.target.closest('[data-calDate]');
  if(t){selectedDate=t.getAttribute('data-calDate');mergeBookingsIntoFloor();renderPage(currentPage);return}
  t=e.target.closest('[data-calShift]');
  if(t){var shift=parseInt(t.getAttribute('data-calShift'));var d=parseDateLocal(selectedDate);d.setMonth(d.getMonth()+shift);selectedDate=fmtDate(d);mergeBookingsIntoFloor();renderPage(currentPage);return}
  t=e.target.closest('[data-calToday]');
  if(t){selectedDate=fmtDate(new Date());mergeBookingsIntoFloor();renderPage(currentPage);return}
  t=e.target.closest('[data-calTogglePicker]');
  if(t){var pk=document.getElementById('calPicker');if(pk&&pk.classList.contains('show')){pk.classList.remove('show');calPickerMode=null}else if(calPickerMode==='month'){showCalPicker('year')}else{showCalPicker('month')}return}
  t=e.target.closest('[data-calPickMonth]');
  if(t){var m=parseInt(t.getAttribute('data-calPickMonth'));var d=parseDateLocal(selectedDate);d.setMonth(m);selectedDate=fmtDate(d);calPickerMode=null;mergeBookingsIntoFloor();renderPage(currentPage);return}
  t=e.target.closest('[data-calPickYear]');
  if(t){var y=parseInt(t.getAttribute('data-calPickYear'));var d=parseDateLocal(selectedDate);d.setFullYear(y);selectedDate=fmtDate(d);calPickerMode=null;mergeBookingsIntoFloor();renderPage(currentPage);return}

  t=e.target.closest('[data-pg]');
  if(t){switchPage(t.getAttribute('data-pg'),t);return}
  t=e.target.closest('[data-nav]');
  if(t){switchPage(t.getAttribute('data-nav'));return}
  t=e.target.closest('[data-conv]');
  if(t){selectConv(t.getAttribute('data-conv'),t);return}
  t=e.target.closest('[data-pick]');
  if(t&&!t.classList.contains('taken')){pickResaTable(t.getAttribute('data-pick'));return}
  t=e.target.closest('[data-cfgkey]');
  if(t){editConfigField(t.getAttribute('data-cfgkey'),t.getAttribute('data-cfglabel'));return}
  t=e.target.closest('[data-blk]');
  if(t&&t.classList.contains('tog')){toggleOverviewBlock(t);return}
  // Menu events
  if(e.target.closest('[data-addSection]')){menuAddSection();return}
  t=e.target.closest('[data-delSection]');
  if(t){menuDelSection(parseInt(t.getAttribute('data-delSection')));return}
  t=e.target.closest('[data-addItem]');
  if(t){menuAddItem(parseInt(t.getAttribute('data-addItem')));return}
  t=e.target.closest('[data-delItem]');
  if(t){var p=t.getAttribute('data-delItem').split('-');menuDelItem(parseInt(p[0]),parseInt(p[1]));return}
  if(e.target.closest('[data-saveMenu]')){menuSave();return}
  // Floor plan events
  if(e.target.closest('[data-fpSave]')){fpSave();return}
  if(e.target.closest('[data-fpDel]')){fpDeleteSelected();return}
  if(e.target.closest('[data-fpModeEdit]')){fpMode='edit';fpSlot='all';renderFloorplan(document.getElementById('mainContent'));return}
  if(e.target.closest('[data-fpModeResa]')){fpMode='resa';renderFloorplan(document.getElementById('mainContent'));return}
  t=e.target.closest('[data-fpSvc]');
  if(t){fpService=t.getAttribute('data-fpSvc');fpSlot='all';renderFloorplan(document.getElementById('mainContent'));return}
  t=e.target.closest('[data-fpSlot]');
  if(t){fpSlot=t.getAttribute('data-fpSlot');fpMergeForService();fpDrawTables();
    // Re-render slot pills
    renderFloorplan(document.getElementById('mainContent'));return}
  t=e.target.closest('[data-fpSaveResa]');
  if(t){fpSaveResaInline(t.getAttribute('data-fpSaveResa'));return}
  if(e.target.closest('[data-fpClosePopup]')){var pp=document.getElementById('fpResaPopup');if(pp)pp.style.display='none';fpSelected=null;fpDrawTables();return}
  t=e.target.closest('[data-fpCancelResa]');
  if(t){fpCancelResa(t.getAttribute('data-fpCancelResa'));return}
  t=e.target.closest('[data-fpSwap]');
  if(t){var parts=t.getAttribute('data-fpSwap').split('-');fpSwapTable(parts[0],parts[1]);return}
  t=e.target.closest('[data-fpAdd]');
  if(t){var ps=t.getAttribute('data-fpAdd').split('-');fpAddTable(ps[0],parseInt(ps[1]));return}
  t=e.target.closest('[data-fpSetZone]');
  if(t){fpUpdateSelected('zone',t.getAttribute('data-fpSetZone'));return}
  // Booking edit
  t=e.target.closest('[data-editResa]');
  if(t){openEditResa(parseInt(t.getAttribute('data-editResa')));return}
  // Contact click
  t=e.target.closest('[data-contact]');
  if(t){openContactCard(t.getAttribute('data-contact'));return}
  // Contact edit prefs
  t=e.target.closest('[data-editprefs]');
  if(t){editContactPrefs(t.getAttribute('data-editprefs'));return}
  // Save prefs
  t=e.target.closest('[data-saveprefs]');
  if(t){saveContactPrefs(t.getAttribute('data-saveprefs'));return}
  // Back to contact
  t=e.target.closest('[data-backcontact]');
  if(t){openContactCard(t.getAttribute('data-backcontact'));return}
  // Waitlist buttons
  t=e.target.closest('[data-wlNotify]');
  if(t){var p=t.getAttribute('data-wlNotify').split('|');notifyWaitlist(p[0],p[1],p[2],p[3]);return}
  t=e.target.closest('[data-wlRemove]');
  if(t){removeWaitlist(t.getAttribute('data-wlRemove'));return}
});

// Login Enter key
document.addEventListener('keydown',function(e){
  if(e.key==='Enter'&&e.target&&e.target.id==='loginPwd'){doLogin()}
});
// Floor editor input listeners
document.addEventListener('change',function(e){
  if(e.target.id==='fpEdName')fpUpdateSelected('id',e.target.value);
  if(e.target.id==='fpEdSeats')fpUpdateSelected('seats',e.target.value);
  if(e.target.id==='fpEdShape')fpUpdateSelected('shape',e.target.value);
});

// === HELP ASSISTANT ===
var helpOpen=false;
var helpGreeted=false;

function toggleHelp(){
  helpOpen=!helpOpen;
  document.getElementById('helpPanel').classList.toggle('show',helpOpen);
  document.getElementById('helpBtn').classList.toggle('open',helpOpen);
  document.getElementById('helpBtn').textContent=helpOpen?'+':'?';
  if(!helpGreeted){
    helpGreeted=true;
    setTimeout(function(){helpAddBot("Bonjour ! Je suis l&#39;assistant GuestScale. Comment puis-je vous aider ?")},400);
  }
}

function helpAddBot(text){
  var d=document.createElement('div');d.className='help-msg bot';
  d.innerHTML=text;document.getElementById('helpMsgs').appendChild(d);
  document.getElementById('helpMsgs').scrollTop=99999;
}
function helpAddUser(text){
  var d=document.createElement('div');d.className='help-msg user';
  d.textContent=text;document.getElementById('helpMsgs').appendChild(d);
  document.getElementById('helpMsgs').scrollTop=99999;
}

function helpMatch(text){
  var t=text.toLowerCase();
  if(t.match(/table|plan.*salle|ajouter.*table/))return "Pour g\u00e9rer vos tables, allez dans <b>Plan de salle</b> (menu gauche). Cliquez <b>Modifier plan</b> puis <b>+ Ajouter</b> pour cr\u00e9er une table. Vous pouvez d\u00e9finir la zone (salle, terrasse, bar), la capacit\u00e9 et la forme.";
  if(t.match(/horaire|heure|ouvert/))return "Allez dans <b>Configuration</b> (menu gauche). Modifiez le champ <b>Horaires</b>. L'agent IA utilisera ces horaires pour informer les clients.";
  if(t.match(/stat|statistiq|chiffre/))return "Cliquez sur <b>Statistiques</b> dans le menu gauche. Vous verrez l'historique jour par jour : r\u00e9servations, couverts, messages, langues des clients.";
  if(t.match(/menu|carte|plat|scan/))return "Allez dans <b>Menu</b> (menu gauche). Vous pouvez ajouter des sections et des plats manuellement, ou cliquer <b>Scanner</b> pour photographier votre carte et l'importer automatiquement.";
  if(t.match(/reserv|resa|book/))return "Les r\u00e9servations sont dans l'onglet <b>R\u00e9servations</b>. Vous avez une vue <b>Jour</b> et <b>Semaine</b>. Cliquez <b>+ Nouvelle</b> pour ajouter une r\u00e9sa manuellement. Les r\u00e9sas WhatsApp arrivent automatiquement.";
  if(t.match(/contact|crm|client|fiche/))return "L'onglet <b>Contacts</b> liste tous vos clients. Cliquez sur un contact pour voir sa fiche : visites, r\u00e9servations, pr\u00e9f\u00e9rences. Vous pouvez modifier les pr\u00e9f\u00e9rences et ajouter des notes manuellement.";
  if(t.match(/avis|review|google/))return "Les demandes d'avis Google sont envoy\u00e9es automatiquement 2h apr\u00e8s chaque repas. Configurez votre lien Google dans <b>Configuration</b>. Les r\u00e9ponses apparaissent dans l'onglet <b>Avis</b>.";
  if(t.match(/attente|waitlist|liste/))return "La <b>Liste d'attente</b> est dans le menu gauche. Quand l'IA d\u00e9tecte que c'est complet, elle propose automatiquement la liste d&#39;attente au client. Vous pouvez aussi ajouter manuellement des entr\u00e9es et notifier les clients quand une place se lib\u00e8re.";
  if(t.match(/whatsapp|message|conversation/))return "Les conversations WhatsApp sont dans l'onglet <b>Conversations</b>. Vous voyez tous les \u00e9changes entre l'IA et vos clients en temps r\u00e9el.";
  if(t.match(/config|param|person/))return "Tout se configure dans <b>Configuration</b> : nom, adresse, t\u00e9l\u00e9phone, horaires, description, ton de l'agent IA. L'IA utilise ces infos pour r\u00e9pondre aux clients.";
  if(t.match(/mot.*passe|password|connexion|login/))return "Pour changer votre mot de passe, allez dans <b>Mon compte</b> (en bas du menu gauche).";
  return "Je ne suis pas s\u00fbr de comprendre votre question. Essayez de me demander comment g\u00e9rer les <b>tables</b>, les <b>r\u00e9servations</b>, le <b>menu</b>, les <b>contacts</b>, ou les <b>param\u00e8tres</b>.";
}

function helpSend(text){
  helpAddUser(text);
  document.getElementById('helpQuick').style.display='none';
  setTimeout(function(){helpAddBot(helpMatch(text))},800);
}
function helpSendInput(){
  var inp=document.getElementById('helpInput');
  var text=inp.value.trim();if(!text)return;
  inp.value='';helpSend(text);
}
</script>
</body>
</html>
"""
