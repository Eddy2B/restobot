import { useState, useRef } from 'react';

function helpMatch(text) {
  const t = text.toLowerCase();
  if (t.match(/table|plan.*salle|ajouter.*table/)) return "Pour g\u00e9rer vos tables, allez dans <b>Plan de salle</b> (menu gauche). Cliquez <b>Modifier plan</b> puis <b>+ Ajouter</b> pour cr\u00e9er une table.";
  if (t.match(/horaire|heure|ouvert/)) return "Allez dans <b>Configuration</b> (menu gauche). Modifiez le champ <b>Horaires</b>.";
  if (t.match(/stat|statistiq|chiffre/)) return "Cliquez sur <b>Statistiques</b> dans le menu gauche.";
  if (t.match(/menu|carte|plat|scan/)) return "Allez dans <b>Menu</b>. Vous pouvez ajouter des sections ou cliquer <b>Scanner</b> pour photographier votre carte.";
  if (t.match(/reserv|resa|book/)) return "Les r\u00e9servations sont dans l'onglet <b>R\u00e9servations</b>. Vue <b>Jour</b> et <b>Semaine</b> disponibles.";
  if (t.match(/contact|crm|client|fiche/)) return "L'onglet <b>Contacts</b> liste tous vos clients avec leurs fiches.";
  if (t.match(/avis|review|google/)) return "Les demandes d'avis Google sont envoy\u00e9es automatiquement. Voir l'onglet <b>Avis</b>.";
  if (t.match(/attente|waitlist|liste/)) return "La <b>Liste d'attente</b> est dans le menu gauche.";
  if (t.match(/whatsapp|message|conversation/)) return "Les conversations WhatsApp sont dans l'onglet <b>Conversations</b>.";
  if (t.match(/config|param|person/)) return "Tout se configure dans <b>Configuration</b> : nom, adresse, horaires, ton de l'agent IA.";
  if (t.match(/mot.*passe|password|connexion|login/)) return "Pour changer votre mot de passe, allez dans <b>Mon compte</b>.";
  return "Essayez de me demander comment g\u00e9rer les <b>tables</b>, les <b>r\u00e9servations</b>, le <b>menu</b>, les <b>contacts</b>, ou les <b>param\u00e8tres</b>.";
}

export default function HelpAssistant() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([]);
  const [greeted, setGreeted] = useState(false);
  const [input, setInput] = useState('');
  const [showQuick, setShowQuick] = useState(true);
  const msgsRef = useRef(null);

  function addMsg(role, text) {
    setMessages(prev => [...prev, { role, text }]);
    setTimeout(() => { if (msgsRef.current) msgsRef.current.scrollTop = 99999; }, 50);
  }

  function toggle() {
    setOpen(o => !o);
    if (!greeted) {
      setGreeted(true);
      setTimeout(() => addMsg('bot', "Bonjour ! Je suis l'assistant GuestScale. Comment puis-je vous aider ?"), 400);
    }
  }

  function send(text) {
    addMsg('user', text);
    setShowQuick(false);
    setTimeout(() => addMsg('bot', helpMatch(text)), 800);
  }

  function sendInput() {
    if (!input.trim()) return;
    send(input.trim());
    setInput('');
  }

  return (
    <>
      <button className={'help-btn' + (open ? ' open' : '')} onClick={toggle}>{open ? '+' : '?'}</button>
      <div className={'help-panel' + (open ? ' show' : '')}>
        <div className="help-hd">
          <div>
            <div className="help-hd-title">Assistant GuestScale</div>
            <div className="help-hd-sub">Je peux vous aider</div>
          </div>
        </div>
        <div className="help-msgs" ref={msgsRef}>
          {messages.map((m, i) => (
            <div key={i} className={'help-msg ' + m.role}
              dangerouslySetInnerHTML={m.role === 'bot' ? { __html: m.text } : undefined}>
              {m.role === 'user' ? m.text : undefined}
            </div>
          ))}
        </div>
        {showQuick && (
          <div className="help-quick">
            <button onClick={() => send('Ajouter une table')}>Ajouter une table</button>
            <button onClick={() => send('Modifier les horaires')}>Modifier les horaires</button>
            <button onClick={() => send('Voir les stats')}>Voir les stats</button>
            <button onClick={() => send("Gérer la liste d'attente")}>Liste d&apos;attente</button>
          </div>
        )}
        <div className="help-inp">
          <input type="text" placeholder="Posez une question..." value={input}
            onChange={e => setInput(e.target.value)} onKeyDown={e => e.key === 'Enter' && sendInput()} />
          <button onClick={sendInput}>&#10148;</button>
        </div>
      </div>
    </>
  );
}
