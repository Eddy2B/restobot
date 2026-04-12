# WIDGET_JS — embeddable booking widget JavaScript
# Extracted from main.py during refactoring

WIDGET_JS = """
(function(){
  var BASE='__BASE_URL__';
  var COLOR='__COLOR__';
  var WELCOME='__WELCOME__';
  var RESTAURANT='__RESTAURANT__';
  var SLUG='__SLUG__';
  var SESSION=localStorage.getItem('gs_sid')||('gs_'+Math.random().toString(36).substr(2,12));
  localStorage.setItem('gs_sid',SESSION);
  var open=false,loaded=false;
  var style=document.createElement('style');
  style.textContent=`
    #rb-bubble{position:fixed;bottom:24px;right:24px;width:60px;height:60px;border-radius:50%;background:${COLOR};color:white;display:flex;align-items:center;justify-content:center;cursor:pointer;box-shadow:0 4px 20px rgba(0,0,0,.2);z-index:99999;font-size:28px;transition:transform .2s;border:none}
    #rb-bubble:hover{transform:scale(1.08)}
    #rb-badge{position:absolute;top:-2px;right:-2px;width:18px;height:18px;border-radius:50%;background:#EF4444;color:white;font-size:10px;font-weight:800;display:none;align-items:center;justify-content:center}
    #rb-window{position:fixed;bottom:96px;right:24px;width:380px;height:520px;background:white;border-radius:16px;box-shadow:0 8px 40px rgba(0,0,0,.15);z-index:99999;display:none;flex-direction:column;overflow:hidden;font-family:'Inter',-apple-system,sans-serif}
    #rb-header{background:${COLOR};color:white;padding:16px 20px;display:flex;align-items:center;gap:12px}
    #rb-header-icon{width:36px;height:36px;border-radius:50%;background:rgba(255,255,255,.2);display:flex;align-items:center;justify-content:center;font-size:18px}
    #rb-header-name{font-size:15px;font-weight:700}
    #rb-header-status{font-size:11px;opacity:.8}
    #rb-close{margin-left:auto;background:none;border:none;color:white;font-size:20px;cursor:pointer;opacity:.7}
    #rb-close:hover{opacity:1}
    #rb-messages{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:8px}
    .rb-msg{max-width:80%;padding:10px 14px;border-radius:14px;font-size:13px;line-height:1.5;word-wrap:break-word}
    .rb-msg-bot{background:#FAF9F7;color:#1C1917;align-self:flex-start;border-bottom-left-radius:4px}
    .rb-msg-user{background:${COLOR};color:white;align-self:flex-end;border-bottom-right-radius:4px}
    .rb-typing{align-self:flex-start;background:#FAF9F7;padding:10px 16px;border-radius:14px;font-size:13px;color:#A8A29E}
    #rb-input-area{padding:12px;border-top:1px solid #E7E5E4;display:flex;gap:8px}
    #rb-input{flex:1;padding:10px 14px;border-radius:10px;border:1.5px solid #E7E5E4;font-size:13px;outline:none;font-family:inherit}
    #rb-input:focus{border-color:${COLOR}}
    #rb-send{background:${COLOR};color:white;border:none;border-radius:10px;padding:10px 16px;font-weight:700;cursor:pointer;font-size:13px;font-family:inherit}
    @media(max-width:480px){#rb-window{bottom:0;right:0;width:100%;height:100%;border-radius:0}}
  `;
  document.head.appendChild(style);
  var bubble=document.createElement('button');
  bubble.id='rb-bubble';
  bubble.innerHTML='💬<div id="rb-badge">1</div>';
  bubble.onclick=function(){toggleChat()};
  document.body.appendChild(bubble);
  var win=document.createElement('div');
  win.id='rb-window';
  win.innerHTML=`
    <div id="rb-header">
      <div id="rb-header-icon"></div>
      <div><div id="rb-header-name">${RESTAURANT}</div><div id="rb-header-status">En ligne — Reponse instantanee</div></div>
      <button id="rb-close" onclick="document.getElementById('rb-window').style.display='none'">&times;</button>
    </div>
    <div id="rb-messages"></div>
    <div id="rb-input-area">
      <input id="rb-input" type="text" placeholder="Votre message..." onkeydown="if(event.key==='Enter')document.getElementById('rb-send').click()">
      <button id="rb-send">Envoyer</button>
    </div>
  `;
  document.body.appendChild(win);
  function toggleChat(){
    open=!open;
    win.style.display=open?'flex':'none';
    document.getElementById('rb-badge').style.display='none';
    if(!loaded){addBotMsg(WELCOME);loaded=true;}
    if(open)document.getElementById('rb-input').focus();
  }
  function addBotMsg(text){var el=document.getElementById('rb-messages');var d=document.createElement('div');d.className='rb-msg rb-msg-bot';d.textContent=text;el.appendChild(d);el.scrollTop=el.scrollHeight;}
  function addUserMsg(text){var el=document.getElementById('rb-messages');var d=document.createElement('div');d.className='rb-msg rb-msg-user';d.textContent=text;el.appendChild(d);el.scrollTop=el.scrollHeight;}
  function showTyping(){var el=document.getElementById('rb-messages');var d=document.createElement('div');d.className='rb-typing';d.id='rb-typing';d.textContent='En train de taper...';el.appendChild(d);el.scrollTop=el.scrollHeight;}
  function hideTyping(){var t=document.getElementById('rb-typing');if(t)t.remove();}
  document.getElementById('rb-send').onclick=async function(){
    var input=document.getElementById('rb-input');
    var msg=input.value.trim();
    if(!msg)return;
    input.value='';
    addUserMsg(msg);
    showTyping();
    try{
      var r=await fetch(BASE+'/api/webchat/message',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:SESSION,message:msg,slug:SLUG})});
      if(!r.ok){hideTyping();addBotMsg('Erreur de connexion ('+r.status+').');return;}
      var d=await r.json();
      hideTyping();
      if(d.reply)addBotMsg(d.reply);
    }catch(e){
      hideTyping();
      addBotMsg('Desole, un probleme technique. Reessayez.');
    }
  };
  setTimeout(function(){if(!open)document.getElementById('rb-badge').style.display='flex';},3000);
})();
"""
