import { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useStore } from '../utils/store';
import { useToast } from '../components/Toast';
import Calendar from '../components/Calendar';
import HelpAssistant from '../components/HelpAssistant';
import { apiFetch, getToken, clearToken } from '../utils/api';
import { fmtDate, parseDateLocal, MONTH_NAMES } from '../utils/dateUtils';
import { SRC_COLORS, SRC_LABELS, PAGE_TITLES, LOGO_SVG } from '../utils/constants';
import { esc } from '../utils/esc';
import '../styles/layout.css';
import '../styles/components.css';
import '../styles/mobile.css';

const ZONE_COLORS = {
  salle: '#2D7DD2',
  terrasse: '#4ECDC4',
  bar: '#F59E0B',
  prive: '#8B5CF6',
  default: '#6B7280',
};

function zoneColor(zone) {
  return ZONE_COLORS[zone] || ZONE_COLORS.default;
}

const OCCASION_EMOJI = {
  anniversaire: '\u{1F382}',
  mariage: '\u{1F48D}',
  fiancailles: '\u{1F48D}',
  'saint-valentin': '\u{2764}\u{FE0F}',
  fete: '\u{1F389}',
};

function occasionBadge(occasion) {
  if (!occasion) return null;
  const emoji = OCCASION_EMOJI[occasion] || '\u{2728}';
  return <span title={occasion} style={{ marginLeft: 4 }}>{emoji}</span>;
}

function initials(name) {
  if (!name) return '?';
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return name.slice(0, 2).toUpperCase();
}

function timeStr(t) {
  if (!t) return '';
  if (t.length === 5) return t;
  if (t.length > 5) return t.slice(0, 5);
  return t;
}

export default function Dashboard() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { state, dispatch, fetchData } = useStore();
  const showToast = useToast();

  // Time display
  const [now, setNow] = useState(new Date());

  // Mobile more drawer
  const [moreOpen, setMoreOpen] = useState(false);

  // Modals
  const [newResaOpen, setNewResaOpen] = useState(false);
  const [editResaOpen, setEditResaOpen] = useState(false);
  const [editResaData, setEditResaData] = useState(null);

  // Daily message editing
  const [editingDaily, setEditingDaily] = useState(false);
  const [dailyDraft, setDailyDraft] = useState('');

  // Floor plan
  const [fpMode, setFpMode] = useState('resa');
  const [fpService, setFpService] = useState('midi');
  const [fpSelectedTable, setFpSelectedTable] = useState(null);
  const [fpEditTable, setFpEditTable] = useState(null);
  const [fpDrag, setFpDrag] = useState(null);

  // New resa form
  const [nrFirst, setNrFirst] = useState('');
  const [nrLast, setNrLast] = useState('');
  const [nrCovers, setNrCovers] = useState('2');
  const [nrTime, setNrTime] = useState('19:30');
  const [nrPhone, setNrPhone] = useState('');
  const [nrEmail, setNrEmail] = useState('');
  const [nrSource, setNrSource] = useState('phone');
  const [nrDate, setNrDate] = useState(state.selectedDate);

  // Edit resa form
  const [erName, setErName] = useState('');
  const [erCovers, setErCovers] = useState('');
  const [erTime, setErTime] = useState('');
  const [erPhone, setErPhone] = useState('');
  const [erTable, setErTable] = useState('');

  // Menu editing
  const [menuEditing, setMenuEditing] = useState(false);
  const [menuDraft, setMenuDraft] = useState([]);
  const scanRef = useRef(null);

  // Conversations
  const [selectedConv, setSelectedConv] = useState(null);

  // Contacts
  const [selectedContact, setSelectedContact] = useState(null);
  const [contactEditMode, setContactEditMode] = useState(false);
  const [contactPrefDraft, setContactPrefDraft] = useState('');
  const [contactNoteDraft, setContactNoteDraft] = useState('');
  const [contactTagDraft, setContactTagDraft] = useState('');
  const [contactSearch, setContactSearch] = useState('');
  const [contactTagFilter, setContactTagFilter] = useState('');
  const [contactSort, setContactSort] = useState('name');

  // Floorplan extras
  const [fpSearch, setFpSearch] = useState('');
  const [fpStatusMenu, setFpStatusMenu] = useState(null);
  const [fpDragResa, setFpDragResa] = useState(null);
  const [fpDragOver, setFpDragOver] = useState(null);

  // Waitlist form
  const [wlName, setWlName] = useState('');
  const [wlPhone, setWlPhone] = useState('');
  const [wlCovers, setWlCovers] = useState('2');
  const [wlService, setWlService] = useState('midi');
  const [wlDate, setWlDate] = useState(fmtDate(new Date()));
  const [wlTime, setWlTime] = useState('');

  // Config
  const [configEditField, setConfigEditField] = useState(null);

  // Stats
  const [statsHistory, setStatsHistory] = useState([]);

  // Account
  const [acOldPwd, setAcOldPwd] = useState('');
  const [acNewPwd, setAcNewPwd] = useState('');
  const [acConfirm, setAcConfirm] = useState('');

  // Onboarding
  const [onboarding, setOnboarding] = useState(false);
  const [obStep, setObStep] = useState(0);
  const [obName, setObName] = useState('');
  const [obAddress, setObAddress] = useState('');
  const [obPhone, setObPhone] = useState('');
  const [obHours, setObHours] = useState('');
  const [obDesc, setObDesc] = useState('');
  const [obTone, setObTone] = useState('');

  // Bookings view
  const [bkView, setBkView] = useState('day');
  const [bkSearch, setBkSearch] = useState('');
  const [nrSuggestions, setNrSuggestions] = useState([]);
  const [nrContactMatch, setNrContactMatch] = useState(null);

  // Inactivity timer
  const lastActivity = useRef(Date.now());

  // ---- Auth check and data load ----
  useEffect(() => {
    const token = getToken();
    if (!token) {
      navigate('/login');
      return;
    }
    apiFetch('/api/me')
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (!d || !d.user) {
          clearToken();
          navigate('/login');
          return;
        }
        dispatch({ type: 'SET_USER', payload: d.user });
        fetchData().then(() => {
          // Restore navigation from URL params
          const params = new URLSearchParams(window.location.search);
          const urlPage = params.get('p');
          if (urlPage && urlPage !== 'overview') dispatch({ type: 'SET_PAGE', payload: urlPage });
          const urlContact = params.get('contact');
          if (urlContact) setSelectedContact(urlContact);
          const urlConv = params.get('conv');
          if (urlConv) setSelectedConv(urlConv);
          // check onboarding
          apiFetch('/api/config').then(r => r.ok ? r.json() : null).then(cfg => {
            if (cfg && !cfg.name) {
              setOnboarding(true);
            }
          }).catch(() => {});
        });
      })
      .catch(() => {
        clearToken();
        navigate('/login');
      });
  }, [navigate, dispatch, fetchData]);

  // ---- Time update every 30s ----
  useEffect(() => {
    const iv = setInterval(() => setNow(new Date()), 30000);
    return () => clearInterval(iv);
  }, []);

  // ---- Auto-logout after 2h inactivity ----
  useEffect(() => {
    function touch() { lastActivity.current = Date.now(); }
    window.addEventListener('mousemove', touch);
    window.addEventListener('keydown', touch);
    window.addEventListener('touchstart', touch);
    const iv = setInterval(() => {
      if (Date.now() - lastActivity.current > 2 * 60 * 60 * 1000) {
        doLogout();
      }
    }, 60000);
    return () => {
      window.removeEventListener('mousemove', touch);
      window.removeEventListener('keydown', touch);
      window.removeEventListener('touchstart', touch);
      clearInterval(iv);
    };
  }, []);

  // ---- Helpers ----
  const page = state.currentPage;
  const setPage = useCallback((p) => {
    dispatch({ type: 'SET_PAGE', payload: p });
    setMoreOpen(false);
    const params = new URLSearchParams(window.location.search);
    params.set('p', p);
    if (p !== 'contacts') params.delete('contact');
    if (p !== 'conversations') params.delete('conv');
    window.history.replaceState(null, '', '?' + params.toString());
  }, [dispatch]);

  function openContact(phone) {
    setSelectedContact(phone);
    setContactEditMode(false);
    setContactNoteDraft('');
    const params = new URLSearchParams(window.location.search);
    if (phone) { params.set('contact', phone); } else { params.delete('contact'); }
    window.history.replaceState(null, '', '?' + params.toString());
  }

  function openConv(phone) {
    setSelectedConv(phone);
    const params = new URLSearchParams(window.location.search);
    if (phone) { params.set('conv', phone); } else { params.delete('conv'); }
    window.history.replaceState(null, '', '?' + params.toString());
  }

  function doLogout() {
    clearToken();
    navigate('/login');
  }

  const todayStr = fmtDate(new Date());
  const selDate = state.selectedDate;
  const bookingsForDate = state.bookings.filter(b => (b.date || '').startsWith(selDate));
  const convList = Object.entries(state.conversations);
  const contactList = Object.values(state.contacts);

  function statusPill() {
    const u = state.user;
    if (!u) return null;
    if (u.restaurant_status === 'trial') {
      const ends = u.trial_ends_at ? new Date(u.trial_ends_at) : null;
      const days = ends ? Math.max(0, Math.ceil((ends - new Date()) / 86400000)) : '?';
      return (
        <div className="sp" style={{ background: '#FFFBEB', color: '#D97706' }}>
          <div className="sd2" style={{ background: '#F59E0B' }} />
          Essai - {days}j
        </div>
      );
    }
    return (
      <div className="sp" style={{ background: '#E6FAF8', color: '#059669' }}>
        <div className="sd2" style={{ background: '#10B981' }} />
        Actif
      </div>
    );
  }

  function fmtNow() {
    const h = String(now.getHours()).padStart(2, '0');
    const m = String(now.getMinutes()).padStart(2, '0');
    return h + ':' + m;
  }

  // ---- Daily message save/broadcast ----
  async function saveDaily() {
    try {
      await apiFetch('/api/daily', { method: 'POST', body: JSON.stringify({ message: dailyDraft }) });
      dispatch({ type: 'SET_DAILY', payload: dailyDraft });
      setEditingDaily(false);
      showToast('Message du jour sauvegarde');
      await fetchData();
    } catch { showToast('Erreur lors de la sauvegarde'); }
  }

  async function broadcastDaily() {
    try {
      await apiFetch('/api/broadcast', { method: 'POST', body: JSON.stringify({ message: dailyDraft || state.dailyMsg }) });
      showToast('Message diffuse !');
      await fetchData();
    } catch { showToast('Erreur de diffusion'); }
  }

  // ---- New reservation ----
  function openNewResa() {
    setNrFirst(''); setNrLast(''); setNrCovers('2'); setNrTime('19:30');
    setNrPhone(''); setNrEmail(''); setNrSource('phone'); setNrDate(selDate);
    setNrSuggestions([]);
    setNrContactMatch(null);
    setNewResaOpen(true);
  }

  async function searchContacts(query) {
    if (query.length < 2) { setNrSuggestions([]); setNrContactMatch(null); return; }
    try {
        const r = await apiFetch('/api/contacts/search?q=' + encodeURIComponent(query));
        const d = await r.json();
        setNrSuggestions(d.results || []);
        setNrContactMatch(d.results?.length > 0 ? d.results[0] : null);
    } catch { setNrSuggestions([]); }
  }

  function selectContactSuggestion(ct) {
    const parts = (ct.name || '').split(' ');
    setNrFirst(parts[0] || '');
    setNrLast(parts.slice(1).join(' ') || '');
    setNrPhone(ct.phone || '');
    setNrEmail(ct.email || '');
    setNrSuggestions([]);
    setNrContactMatch(ct);
  }

  async function submitNewResa() {
    const name = (nrFirst + ' ' + nrLast).trim();
    if (!name) { showToast('Nom requis'); return; }
    try {
      const r = await apiFetch('/api/bookings/manual', {
        method: 'POST',
        body: JSON.stringify({
          name, covers: parseInt(nrCovers) || 2, time: nrTime,
          phone: nrPhone, email: nrEmail, source: nrSource, date: nrDate
        })
      });
      const d = await r.json();
      if (d.error) { showToast(d.error); return; }
      await fetchData();
      setNewResaOpen(false);
      showToast('Reservation creee');
    } catch { showToast('Erreur'); }
  }

  // ---- Edit reservation ----
  function openEditResa(bk) {
    setEditResaData(bk);
    setErName(bk.name || '');
    setErCovers(String(bk.covers || ''));
    setErTime(bk.time || bk.booking_time || '');
    setErPhone(bk.phone || '');
    setErTable(bk.table || '');
    setEditResaOpen(true);
  }

  async function submitEditResa() {
    if (!editResaData) return;
    try {
      await apiFetch('/api/bookings/update', {
        method: 'POST',
        body: JSON.stringify({
          booking_id: editResaData.id, name: erName, covers: parseInt(erCovers) || 2,
          time: erTime, phone: erPhone, table: erTable
        })
      });
      await fetchData();
      setEditResaOpen(false);
      showToast('Reservation modifiee');
    } catch { showToast('Erreur'); }
  }

  async function deleteResa() {
    if (!editResaData) return;
    if (!window.confirm('Supprimer la reservation de ' + (editResaData.name || '') + ' ?')) return;
    try {
      await apiFetch('/api/bookings/delete', {
        method: 'POST',
        body: JSON.stringify({ booking_id: editResaData.id })
      });
      await fetchData();
      setEditResaOpen(false);
      showToast('Reservation supprimee');
    } catch { showToast('Erreur'); }
  }

  // ---- Menu ----
  function startMenuEdit() {
    setMenuDraft(JSON.parse(JSON.stringify(state.menuSections)));
    setMenuEditing(true);
  }

  async function saveMenu() {
    try {
      await apiFetch('/api/menu', {
        method: 'POST',
        body: JSON.stringify({ sections: menuDraft })
      });
      dispatch({ type: 'SET_MENU', payload: menuDraft });
      setMenuEditing(false);
      showToast('Menu sauvegarde');
      await fetchData();
    } catch { showToast('Erreur'); }
  }

  async function scanMenu(e) {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async () => {
      const base64 = reader.result;
      showToast('Scan en cours...');
      try {
        const r = await apiFetch('/api/menu/scan', {
          method: 'POST',
          body: JSON.stringify({ image: base64 })
        });
        const d = await r.json();
        if (d.sections) {
          dispatch({ type: 'SET_MENU', payload: d.sections });
          showToast('Menu scanne !');
          await fetchData();
        } else {
          showToast(d.error || 'Echec du scan');
        }
      } catch { showToast('Erreur de scan'); }
    };
    reader.readAsDataURL(file);
  }

  // ---- Floorplan ----
  async function saveFloorplan() {
    try {
      await apiFetch('/api/floorplan', {
        method: 'POST',
        body: JSON.stringify({ tables: state.floorplan })
      });
      showToast('Plan sauvegarde');
      await fetchData();
    } catch { showToast('Erreur'); }
  }

  function addTable(shape) {
    const tables = [...state.floorplan];
    const newT = {
      id: 'T' + (tables.length + 1),
      seats: 4,
      shape: shape || 'rect',
      zone: 'salle',
      x: 40 + Math.random() * 20,
      y: 40 + Math.random() * 20,
    };
    tables.push(newT);
    dispatch({ type: 'SET_FLOORPLAN', payload: tables });
    setFpEditTable(newT);
  }

  function updateEditTable(field, val) {
    if (!fpEditTable) return;
    const tables = state.floorplan.map(t =>
      t.id === fpEditTable.id ? { ...t, [field]: val } : t
    );
    dispatch({ type: 'SET_FLOORPLAN', payload: tables });
    setFpEditTable({ ...fpEditTable, [field]: val });
  }

  function deleteEditTable() {
    if (!fpEditTable) return;
    const tables = state.floorplan.filter(t => t.id !== fpEditTable.id);
    dispatch({ type: 'SET_FLOORPLAN', payload: tables });
    setFpEditTable(null);
  }

  // Drag handlers for floorplan edit
  function handleFpMouseDown(e, table) {
    if (fpMode !== 'edit') return;
    e.preventDefault();
    const canvas = e.target.closest('.fp-canvas');
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    setFpDrag({ id: table.id, offsetX: e.clientX, offsetY: e.clientY, startX: table.x, startY: table.y, w: rect.width, h: rect.height });
  }

  function handleFpMouseMove(e) {
    if (!fpDrag) return;
    const dx = ((e.clientX - fpDrag.offsetX) / fpDrag.w) * 100;
    const dy = ((e.clientY - fpDrag.offsetY) / fpDrag.h) * 100;
    const newX = Math.max(5, Math.min(90, fpDrag.startX + dx));
    const newY = Math.max(5, Math.min(90, fpDrag.startY + dy));
    const tables = state.floorplan.map(t =>
      t.id === fpDrag.id ? { ...t, x: newX, y: newY } : t
    );
    dispatch({ type: 'SET_FLOORPLAN', payload: tables });
  }

  function handleFpMouseUp() {
    setFpDrag(null);
  }

  // ---- Waitlist ----
  async function addToWaitlist() {
    if (!wlName) { showToast('Nom requis'); return; }
    try {
      const r = await apiFetch('/api/waitlist/add', {
        method: 'POST',
        body: JSON.stringify({
          name: wlName, phone: wlPhone, covers: parseInt(wlCovers) || 2,
          service: wlService, date: wlDate, preferred_time: wlTime
        })
      });
      const d = await r.json();
      if (d.error) { showToast(d.error); return; }
      await fetchData();
      setWlName(''); setWlPhone(''); setWlCovers('2'); setWlTime('');
      showToast('Ajoute a la liste');
    } catch { showToast('Erreur'); }
  }

  async function notifyWaitlist(id) {
    try {
      await apiFetch('/api/waitlist/notify', { method: 'POST', body: JSON.stringify({ id }) });
      await fetchData();
      showToast('Notification envoyee');
    } catch { showToast('Erreur'); }
  }

  async function removeWaitlist(id) {
    try {
      await apiFetch('/api/waitlist/remove', { method: 'POST', body: JSON.stringify({ id }) });
      await fetchData();
      showToast('Retire de la liste');
    } catch { showToast('Erreur'); }
  }

  // ---- Config ----
  async function toggleReminders() {
    const current = state.restaurantConfig._reminders_enabled;
    try {
      await apiFetch('/api/settings', {
        method: 'POST',
        body: JSON.stringify({ reminders_enabled: !current })
      });
      dispatch({ type: 'SET_CONFIG', payload: { _reminders_enabled: !current } });
      showToast(current ? 'Rappels desactives' : 'Rappels actives');
      await fetchData();
    } catch { showToast('Erreur'); }
  }

  function editConfigField(field, label) {
    const current = state.restaurantConfig[field] || '';
    const val = prompt(label, current);
    if (val === null) return;
    const updated = { ...state.restaurantConfig, [field]: val };
    dispatch({ type: 'SET_CONFIG', payload: { [field]: val } });
    apiFetch('/api/config', { method: 'POST', body: JSON.stringify(updated) })
      .then(() => { showToast('Sauvegarde'); fetchData(); })
      .catch(() => showToast('Erreur'));
  }

  // ---- Floorplan status & drag-assign ----
  async function setTableStatus(tableId, status) {
    try {
      await apiFetch('/api/floorplan/status', {
        method: 'POST',
        body: JSON.stringify({ table_id: tableId, status, date: selDate, service: fpService })
      });
      setFpStatusMenu(null);
      await fetchData();
    } catch { showToast('Erreur'); }
  }

  async function assignTableDrop(bookingId, tableId) {
    const bk = state.bookings.find(b => b.id === bookingId);
    if (!bk) return;
    try {
      await apiFetch('/api/floorplan/assign', {
        method: 'POST',
        body: JSON.stringify({ booking_id: bookingId, table_id: tableId, slot_time: bk.booking_time || bk.time || '' })
      });
      setFpDragResa(null);
      setFpDragOver(null);
      await fetchData();
      showToast('Table assignee');
    } catch { showToast('Erreur'); }
  }

  // ---- Contacts ----
  async function saveContactNote(phone) {
    try {
      await apiFetch('/api/contacts/note', {
        method: 'POST',
        body: JSON.stringify({ phone, note: contactNoteDraft })
      });
      setContactNoteDraft('');
      await fetchData();
      showToast('Note sauvegardee');
    } catch { showToast('Erreur'); }
  }

  async function addContactTag(phone) {
    if (!contactTagDraft.trim()) return;
    try {
      await apiFetch('/api/contacts/tag', {
        method: 'POST',
        body: JSON.stringify({ phone, tag: contactTagDraft.trim() })
      });
      await fetchData();
      setContactTagDraft('');
      showToast('Tag ajoute');
    } catch { showToast('Erreur'); }
  }

  async function saveContactPreferences(phone) {
    try {
      await apiFetch('/api/contacts/preferences', {
        method: 'POST',
        body: JSON.stringify({ phone, preferences: contactPrefDraft })
      });
      await fetchData();
      setContactEditMode(false);
      showToast('Preferences sauvegardees');
    } catch { showToast('Erreur'); }
  }

  // ---- Stats ----
  useEffect(() => {
    if (page === 'stats') {
      apiFetch('/api/stats/history').then(r => r.ok ? r.json() : null).then(d => {
        if (d && d.history) setStatsHistory(d.history);
      }).catch(() => {});
    }
  }, [page]);

  // ---- Account ----
  async function changePassword() {
    if (!acOldPwd || !acNewPwd) { showToast('Remplissez tous les champs'); return; }
    if (acNewPwd !== acConfirm) { showToast('Les mots de passe ne correspondent pas'); return; }
    if (acNewPwd.length < 12) { showToast('Minimum 12 caracteres'); return; }
    try {
      const r = await apiFetch('/api/change-password', {
        method: 'POST',
        body: JSON.stringify({ old_password: acOldPwd, new_password: acNewPwd })
      });
      const d = await r.json();
      if (d.error) { showToast(d.error); return; }
      setAcOldPwd(''); setAcNewPwd(''); setAcConfirm('');
      showToast('Mot de passe modifie');
    } catch { showToast('Erreur'); }
  }

  // ---- Onboarding ----
  async function finishOnboarding() {
    const cfg = { name: obName, address: obAddress, phone: obPhone, hours: obHours, description: obDesc, tone: obTone };
    try {
      await apiFetch('/api/config', { method: 'POST', body: JSON.stringify(cfg) });
      dispatch({ type: 'SET_CONFIG', payload: cfg });
      setOnboarding(false);
      showToast('Configuration terminee !');
      await fetchData();
    } catch { showToast('Erreur de sauvegarde'); }
  }

  // ========================================
  // PAGES
  // ========================================

  function renderOverview() {
    const blocks = state.overviewBlocks;
    const totalMessages = convList.reduce((acc, [, cv]) => acc + (cv.count || cv.messages?.length || 0), 0);
    const bookingsToday = bookingsForDate.length;
    const totalConvs = convList.length;
    const totalContacts = contactList.length;

    return (
      <div>
        {blocks.daily && (
          <div className="db">
            <div className="db-top">
              <div className="di">&#9993;</div>
              <div style={{ flex: 1 }}>
                <div className="dlb">Message du jour</div>
                {editingDaily ? (
                  <textarea className="dtx-edit" value={dailyDraft}
                    onChange={e => setDailyDraft(e.target.value)} autoFocus />
                ) : (
                  <div className="dtx" onClick={() => { setDailyDraft(state.dailyMsg); setEditingDaily(true); }}>
                    {state.dailyMsg || 'Cliquez pour ajouter un message du jour...'}
                  </div>
                )}
                {!editingDaily && state.dailyMsg && (
                  <div className="dme">Visible par l&apos;equipe et diffusable aux clients</div>
                )}
              </div>
            </div>
            {editingDaily && (
              <div className="db-act">
                <button className="dbb dbb-s" onClick={saveDaily}>&#10003; Sauvegarder</button>
                <button className="dbb dbb-b" onClick={broadcastDaily}>&#9993; Diffuser</button>
                <button className="dbb dbb-c" onClick={() => setEditingDaily(false)}>Annuler</button>
              </div>
            )}
          </div>
        )}

        {blocks.stats && (
          <div className="sg">
            <div className="sc">
              <div className="sl">Messages</div>
              <div className="sv">{totalMessages}</div>
              <div className="ss2">Total echanges</div>
            </div>
            <div className="sc">
              <div className="sl">Reservations</div>
              <div className="sv">{bookingsToday}</div>
              <div className="ss2">{selDate === todayStr ? "Aujourd'hui" : selDate}</div>
            </div>
            <div className="sc">
              <div className="sl">Conversations</div>
              <div className="sv">{totalConvs}</div>
              <div className="ss2">Clients actifs</div>
            </div>
            <div className="sc">
              <div className="sl">Contacts</div>
              <div className="sv">{totalContacts}</div>
              <div className="ss2">Dans le CRM</div>
            </div>
          </div>
        )}

        <div className="ov-layout" style={{ display: 'flex', gap: 14 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            {blocks.floor && (
              <div className="fm" onClick={() => setPage('floorplan')}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 700 }}>Plan de salle</div>
                    <div style={{ fontSize: 12, color: 'var(--tm)', marginTop: 2 }}>
                      {state.floorplan.length} tables
                    </div>
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--ac)', fontWeight: 600 }}>Voir &rarr;</div>
                </div>
                <div className="fc">
                  {state.floorplan.map(t => {
                    const bk = bookingsForDate.find(b => b.table && b.table.split('+').includes(t.id));
                    return (
                      <div key={t.id} className="ftbl" style={{
                        left: (t.x || 20) + '%',
                        top: (t.y || 20) + '%',
                        width: 40 + (t.seats || 4) * 4,
                        height: 40 + (t.seats || 4) * 3,
                        borderRadius: t.shape === 'round' ? '50%' : 6,
                        borderColor: bk ? '#4ECDC4' : zoneColor(t.zone),
                        background: bk ? 'rgba(78,205,196,.1)' : 'rgba(45,125,210,.05)',
                        color: 'var(--t)',
                        transform: 'translate(-50%, -50%)',
                      }}>
                        <span>{t.id}</span>
                        <span style={{ fontSize: 8, color: 'var(--tm)' }}>{t.seats}p</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {blocks.bookings && (
              <div className="card" style={{ marginBottom: 14 }}>
                <div className="card-h">
                  <div>
                    <div className="card-t">Reservations</div>
                    <div className="card-s">{bookingsForDate.length} pour le {selDate}</div>
                  </div>
                  <button className="ba" onClick={openNewResa}>+ Nouvelle</button>
                </div>
                {bookingsForDate.slice(0, 5).map(bk => (
                  <div key={bk.id} className="rw" style={{ cursor: 'pointer' }} onClick={() => openEditResa(bk)}>
                    <div className="rl">
                      <div className="dot" style={{ background: SRC_COLORS[bk.source] || '#6B7280' }} />
                      <div>
                        <div style={{ fontSize: 13, fontWeight: 600 }}>{bk.name}{occasionBadge(bk.occasion)}</div>
                        <div style={{ fontSize: 11, color: 'var(--tm)' }}>
                          {timeStr(bk.booking_time || bk.time)} &middot; {bk.covers}p
                          {bk.table ? ' · ' + bk.table : ''}
                        </div>
                      </div>
                    </div>
                    <span className="src-badge" style={{
                      background: (SRC_COLORS[bk.source] || '#6B7280') + '18',
                      color: SRC_COLORS[bk.source] || '#6B7280'
                    }}>{SRC_LABELS[bk.source] || bk.source}</span>
                  </div>
                ))}
                {bookingsForDate.length === 0 && (
                  <div style={{ padding: 24, textAlign: 'center', color: 'var(--tm)', fontSize: 13 }}>
                    Aucune reservation pour cette date
                  </div>
                )}
              </div>
            )}

            <div className="card" style={{ marginBottom: 14 }}>
              <div className="card-h">
                <div>
                  <div className="card-t">Conversations recentes</div>
                  <div className="card-s">{convList.length} conversations</div>
                </div>
                <button className="ba" onClick={() => setPage('conversations')}>Voir tout</button>
              </div>
              {convList.slice(0, 4).map(([phone, cv]) => {
                const contact = state.contacts[phone];
                const name = contact?.name || phone;
                const lastMsg = cv.last_message || (cv.messages && cv.messages.length > 0 ? cv.messages[cv.messages.length - 1].content || cv.messages[cv.messages.length - 1].text : '');
                return (
                  <div key={phone} className="cr" style={{ cursor: 'pointer' }}
                    onClick={() => { openConv(phone); setPage('conversations'); }}>
                    <div className="cav" style={{ background: 'var(--acg)', color: '#fff' }}>{initials(name)}</div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 13, fontWeight: 600 }}>{name}</div>
                      <div className="cmsg">{lastMsg}</div>
                    </div>
                    {cv.count > 0 && (
                      <span className="nb-badge" style={{ background: 'var(--acg)', color: '#fff' }}>{cv.count}</span>
                    )}
                  </div>
                );
              })}
              {convList.length === 0 && (
                <div style={{ padding: 24, textAlign: 'center', color: 'var(--tm)', fontSize: 13 }}>
                  Aucune conversation
                </div>
              )}
            </div>
          </div>

          <div className="ov-cal-col" style={{ width: 300, flexShrink: 0 }}>
            <Calendar />
            {blocks.contacts && (
              <div className="card">
                <div className="card-h">
                  <div className="card-t">Contacts</div>
                  <button className="ba" onClick={() => setPage('contacts')}>Voir tout</button>
                </div>
                <div className="cg3" style={{ padding: 12 }}>
                  {contactList.slice(0, 6).map(c => (
                    <div key={c.phone} className="cc" style={{ cursor: 'pointer' }}
                      onClick={() => { openContact(c.phone); setPage('contacts'); }}>
                      <div className="cav" style={{
                        background: (SRC_COLORS[c.source] || '#6B7280') + '20',
                        color: SRC_COLORS[c.source] || '#6B7280',
                        width: 32, height: 32, fontSize: 11, marginBottom: 6
                      }}>{initials(c.name)}</div>
                      <div style={{ fontSize: 12, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {c.name || c.phone}
                      </div>
                      <div style={{ fontSize: 10, color: 'var(--tm)' }}>{c.visits || 0} visites</div>
                    </div>
                  ))}
                </div>
                {contactList.length === 0 && (
                  <div style={{ padding: 20, textAlign: 'center', color: 'var(--tm)', fontSize: 12 }}>
                    Aucun contact
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  function renderFloorplan() {
    const tables = state.floorplan;
    const statuses = state.floorStatuses || {};
    const midiBks = bookingsForDate.filter(b => {
      const t = b.booking_time || b.time || '';
      const h = parseInt(t.split(':')[0]);
      return fpService === 'midi' ? (h >= 11 && h < 15) : (h >= 18 || h < 4);
    });

    const STATUS_COLORS = {
      available: { border: '#10B981', bg: 'rgba(16,185,129,.08)', label: 'Libre', icon: '' },
      reserved: { border: '#2D7DD2', bg: 'rgba(45,125,210,.12)', label: 'Reserve', icon: '\u{1F550}' },
      seated: { border: '#F59E0B', bg: 'rgba(245,158,11,.12)', label: 'A table', icon: '\u{1F37D}' },
      dessert: { border: '#EAB308', bg: 'rgba(234,179,8,.1)', label: 'Dessert', icon: '\u{1F370}' },
      done: { border: '#6B7280', bg: 'rgba(107,114,128,.1)', label: 'Termine', icon: '\u2713' },
      noshow: { border: '#EF4444', bg: 'rgba(239,68,68,.12)', label: 'No-show', icon: '\u2717' },
    };

    const midiSlots = ['12:00','12:15','12:30','12:45','13:00','13:15','13:30','13:45','14:00','14:15','14:30'];
    const soirSlots = ['19:00','19:15','19:30','19:45','20:00','20:15','20:30','20:45','21:00','21:15','21:30','21:45','22:00','22:15','22:30'];
    const slots = fpService === 'midi' ? midiSlots : soirSlots;

    const searchLower = fpSearch.trim().toLowerCase();

    function tableMatchesSearch(t, booked) {
      if (!searchLower) return true;
      if (t.id.toLowerCase().includes(searchLower)) return true;
      if (booked) {
        if ((booked.name || '').toLowerCase().includes(searchLower)) return true;
        if ((booked.phone || '').includes(searchLower)) return true;
      }
      return false;
    }

    return (
      <div onClick={() => { if (fpStatusMenu) setFpStatusMenu(null); }}>
        <div style={{ display: 'flex', gap: 8, marginBottom: 14, alignItems: 'center', flexWrap: 'wrap' }}>
          <button className={'ba' + (fpMode === 'resa' ? '' : '')}
            style={{ background: fpMode === 'resa' ? 'var(--acg)' : 'var(--bg)', color: fpMode === 'resa' ? '#fff' : 'var(--ts)', border: '1px solid var(--b)' }}
            onClick={() => setFpMode('resa')}>Reservations</button>
          <button className={'ba'}
            style={{ background: fpMode === 'edit' ? 'var(--acg)' : 'var(--bg)', color: fpMode === 'edit' ? '#fff' : 'var(--ts)', border: '1px solid var(--b)' }}
            onClick={() => setFpMode('edit')}>Modifier plan</button>
          <div style={{ flex: 1 }} />
          <button className={'ba'}
            style={{ background: fpService === 'midi' ? 'var(--ac)' : 'var(--bg)', color: fpService === 'midi' ? '#fff' : 'var(--ts)', border: '1px solid var(--b)' }}
            onClick={() => setFpService('midi')}>Midi</button>
          <button className={'ba'}
            style={{ background: fpService === 'soir' ? 'var(--ac)' : 'var(--bg)', color: fpService === 'soir' ? '#fff' : 'var(--ts)', border: '1px solid var(--b)' }}
            onClick={() => setFpService('soir')}>Soir</button>
        </div>

        {fpMode === 'resa' && (
          <input className="finp" style={{ marginBottom: 10 }} placeholder="\uD83D\uDD0D Rechercher un client ou une table..."
            value={fpSearch} onChange={e => setFpSearch(e.target.value)} />
        )}

        {fpMode === 'resa' && (
          <div style={{ display: 'flex', gap: 6, marginBottom: 14, flexWrap: 'wrap' }}>
            {slots.map(s => (
              <div key={s} className="tsb" style={{ padding: '6px 10px', fontSize: 11 }}>{s}</div>
            ))}
          </div>
        )}

        <div className="fp-layout">
          <div className="fp-main">
            <div className="fp-canvas" id="fpCanvas"
              style={{ position: 'relative', height: 440, background: 'var(--bg)', borderRadius: 10, border: '1px solid var(--bl)', overflow: 'hidden' }}
              onMouseMove={fpMode === 'edit' ? handleFpMouseMove : undefined}
              onMouseUp={fpMode === 'edit' ? handleFpMouseUp : undefined}
              onMouseLeave={fpMode === 'edit' ? handleFpMouseUp : undefined}
            >
              {tables.map(t => {
                const booked = midiBks.find(b => b.table && b.table.split('+').includes(t.id));
                const isSelected = fpMode === 'edit' ? fpEditTable?.id === t.id : fpSelectedTable === t.id;
                const w = 50 + (t.seats || 4) * 5;
                const h = 50 + (t.seats || 4) * 4;

                const statusKey = `${selDate}:${fpService}:${t.id}`;
                const explicitStatus = statuses[statusKey];
                const tableStatus = explicitStatus || (booked ? 'reserved' : 'available');
                const sc = STATUS_COLORS[tableStatus] || STATUS_COLORS.available;

                const matches = tableMatchesSearch(t, booked);
                const isDragOver = fpDragOver === t.id;

                const contact = booked && booked.phone ? state.contacts[booked.phone] : null;
                const hasWarning = contact && (contact.preferences || contact.allergies);

                return (
                  <div key={t.id} className="ftbl" style={{
                    left: (t.x || 20) + '%',
                    top: (t.y || 20) + '%',
                    width: w,
                    height: h,
                    borderRadius: t.shape === 'round' ? '50%' : 8,
                    borderColor: isDragOver ? '#2DD4BF' : isSelected ? '#2D7DD2' : sc.border,
                    borderWidth: isSelected ? 3 : 2,
                    borderStyle: isDragOver ? 'dashed' : 'solid',
                    background: isSelected ? 'rgba(45,125,210,.1)' : sc.bg,
                    color: 'var(--t)',
                    cursor: fpMode === 'edit' ? 'grab' : 'pointer',
                    transform: 'translate(-50%, -50%)',
                    zIndex: isSelected || fpStatusMenu === t.id ? 10 : 1,
                    boxShadow: isSelected ? '0 0 0 3px rgba(45,125,210,.2)' : (searchLower && matches ? '0 0 0 2px ' + sc.border : 'none'),
                    transition: fpDrag?.id === t.id ? 'none' : 'all .15s',
                    opacity: searchLower && !matches ? 0.2 : 1,
                    animation: searchLower && matches ? 'fpPulse 1.5s infinite' : 'none',
                  }}
                    onMouseDown={fpMode === 'edit' ? (e) => handleFpMouseDown(e, t) : undefined}
                    onDragOver={fpMode === 'resa' ? (e) => { e.preventDefault(); setFpDragOver(t.id); } : undefined}
                    onDragLeave={fpMode === 'resa' ? () => setFpDragOver(null) : undefined}
                    onDrop={fpMode === 'resa' ? () => { if (fpDragResa) assignTableDrop(fpDragResa, t.id); } : undefined}
                    onClick={(e) => {
                      if (fpMode === 'edit') {
                        setFpEditTable(t);
                      } else {
                        // Mobile tap-to-assign
                        if (fpDragResa) {
                          e.stopPropagation();
                          assignTableDrop(fpDragResa, t.id);
                          return;
                        }
                        setFpSelectedTable(t.id);
                        if (booked) {
                          e.stopPropagation();
                          setFpStatusMenu(fpStatusMenu === t.id ? null : t.id);
                        } else {
                          setNrFirst(''); setNrLast(''); setNrCovers('2');
                          setNrTime(fpService === 'midi' ? '12:30' : '19:30');
                          setNrPhone(''); setNrEmail(''); setNrSource('phone');
                          setNewResaOpen(true);
                        }
                      }
                    }}
                  >
                    <span style={{ fontWeight: 700, fontSize: 11 }}>{t.id}</span>
                    <span style={{ fontSize: 8, color: 'var(--tm)' }}>{t.seats}p {sc.icon}</span>
                    {booked && (
                      <span style={{ fontSize: 7, color: sc.border, fontWeight: 700, marginTop: 1, textAlign: 'center', lineHeight: '1.2' }}>
                        {booked.name?.split(' ')[0]} {timeStr(booked.booking_time || booked.time)} {booked.covers}p
                        {hasWarning ? ' \u26A0\uFE0F' : ''}
                      </span>
                    )}

                    {fpStatusMenu === t.id && (
                      <div onClick={e => e.stopPropagation()} style={{
                        position: 'absolute',
                        ...((t.y || 20) > 50 ? { bottom: '100%', marginBottom: 4 } : { top: '100%', marginTop: 4 }),
                        left: '50%', transform: 'translateX(-50%)',
                        background: 'var(--bg)', border: '1px solid var(--bl)', borderRadius: 10,
                        padding: 8, zIndex: 100, minWidth: 140, boxShadow: '0 4px 16px rgba(0,0,0,.15)',
                        display: 'flex', flexDirection: 'column', gap: 4,
                      }}>
                        {Object.entries(STATUS_COLORS).map(([k, v]) => (
                          <button key={k} className="ba" style={{
                            fontSize: 10, padding: '4px 8px', textAlign: 'left',
                            background: tableStatus === k ? v.bg : 'transparent',
                            borderColor: v.border, color: 'var(--t)',
                            border: '1px solid ' + (tableStatus === k ? v.border : 'var(--bl)'),
                            borderRadius: 6, fontWeight: tableStatus === k ? 700 : 400,
                          }} onClick={() => setTableStatus(t.id, k)}>
                            {v.icon} {v.label}
                          </button>
                        ))}
                        <hr style={{ border: 'none', borderTop: '1px solid var(--bl)', margin: '4px 0' }} />
                        <button className="ba" style={{ fontSize: 10, padding: '4px 8px', background: 'var(--ac)', color: '#fff', border: 'none', borderRadius: 6 }}
                          onClick={() => { setFpStatusMenu(null); openEditResa(booked); }}>
                          Modifier resa
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
              {tables.length === 0 && (
                <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--tm)', fontSize: 13 }}>
                  Aucune table &mdash; passez en mode &ldquo;Modifier plan&rdquo;
                </div>
              )}
            </div>

            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 10, fontSize: 10, color: 'var(--tm)' }}>
              {Object.entries(STATUS_COLORS).map(([k, v]) => (
                <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <div style={{ width: 10, height: 10, borderRadius: 3, background: v.border }} />
                  {v.label}
                </div>
              ))}
            </div>

            {fpMode === 'resa' && (() => {
              const unassigned = midiBks.filter(b => !b.table);
              if (!unassigned.length) return null;
              return (
                <div style={{ marginTop: 14 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--tm)', marginBottom: 6 }}>Reservations sans table</div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {unassigned.map(b => (
                      <div key={b.id} draggable
                        onDragStart={() => setFpDragResa(b.id)}
                        onDragEnd={() => { setFpDragResa(null); setFpDragOver(null); }}
                        onClick={() => {
                          if (fpDragResa === b.id) { setFpDragResa(null); }
                          else { setFpDragResa(b.id); }
                        }}
                        style={{ padding: '6px 10px', borderRadius: 8,
                          background: fpDragResa === b.id ? 'var(--ac)' : 'var(--al)',
                          color: fpDragResa === b.id ? '#fff' : 'var(--ac)',
                          fontSize: 11, fontWeight: 600, cursor: 'grab', border: '1px dashed var(--ac)' }}>
                        {b.name?.split(' ')[0]} {b.booking_time || b.time} {b.covers}p
                      </div>
                    ))}
                  </div>
                </div>
              );
            })()}

            {fpMode === 'edit' && (
              <div style={{ marginTop: 14, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button className="ba" onClick={() => addTable('rect')}>+ Table rectangle</button>
                <button className="ba" onClick={() => addTable('round')}>+ Table ronde</button>
                <button className="ba" style={{ background: 'var(--acg)' }} onClick={saveFloorplan}>Sauvegarder</button>
              </div>
            )}

            {fpMode === 'edit' && fpEditTable && (
              <div className="card" style={{ marginTop: 14, padding: 18 }}>
                <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 12 }}>Table {fpEditTable.id}</div>
                <div className="finp-row">
                  <div className="finp-group">
                    <div className="finp-label">Nom / ID</div>
                    <input className="finp" value={fpEditTable.id}
                      onChange={e => updateEditTable('id', e.target.value)} />
                  </div>
                  <div className="finp-group">
                    <div className="finp-label">Places</div>
                    <input className="finp" type="number" value={fpEditTable.seats}
                      onChange={e => updateEditTable('seats', parseInt(e.target.value) || 2)} />
                  </div>
                </div>
                <div className="finp-row" style={{ marginTop: 8 }}>
                  <div className="finp-group">
                    <div className="finp-label">Forme</div>
                    <select className="finp" value={fpEditTable.shape}
                      onChange={e => updateEditTable('shape', e.target.value)}>
                      <option value="rect">Rectangle</option>
                      <option value="round">Ronde</option>
                    </select>
                  </div>
                  <div className="finp-group">
                    <div className="finp-label">Zone</div>
                    <select className="finp" value={fpEditTable.zone}
                      onChange={e => updateEditTable('zone', e.target.value)}>
                      <option value="salle">Salle</option>
                      <option value="terrasse">Terrasse</option>
                      <option value="bar">Bar</option>
                      <option value="prive">Prive</option>
                    </select>
                  </div>
                </div>
                <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
                  <button className="ba" onClick={saveFloorplan}>Sauvegarder</button>
                  <button className="ba" style={{ background: '#EF4444' }} onClick={deleteEditTable}>Supprimer</button>
                </div>
              </div>
            )}
          </div>

          {fpMode === 'resa' && (() => {
            const allDayBks = bookingsForDate;
            const midiAll = allDayBks.filter(b => {
              const h = parseInt((b.booking_time || b.time || '').split(':')[0]);
              return h >= 11 && h < 17;
            }).sort((a, b) => (a.booking_time || a.time || '').localeCompare(b.booking_time || b.time || ''));
            const soirAll = allDayBks.filter(b => {
              const h = parseInt((b.booking_time || b.time || '').split(':')[0]);
              return h >= 17 || h < 4;
            }).sort((a, b) => (a.booking_time || a.time || '').localeCompare(b.booking_time || b.time || ''));
            const noTime = allDayBks.filter(b => !(b.booking_time || b.time));
            const renderBk = (bk, color) => (
              <div key={bk.id} className={'fp-sb-item' + (fpSelectedTable === bk.table ? ' active' : '')}
                style={{ borderLeftColor: fpSelectedTable === bk.table ? color : 'transparent' }}
                onClick={() => { setFpSelectedTable(bk.table); openEditResa(bk); }}>
                <div className="fp-sb-name">{bk.name}{occasionBadge(bk.occasion)}</div>
                <div className="fp-sb-meta">
                  <span>{timeStr(bk.booking_time || bk.time)}</span>
                  <span>{bk.covers}p</span>
                  {bk.table ? (
                    <span className="fp-sb-table">{bk.table}</span>
                  ) : (
                    <span className="fp-sb-no-table">Sans table</span>
                  )}
                </div>
              </div>
            );
            return (
              <div className="fp-sidebar">
                <Calendar />
                <div className="fp-sb-header">
                  <div className="fp-sb-title">Reservations du jour</div>
                  <div className="fp-sb-count">{allDayBks.length}</div>
                </div>
                <div className="fp-sb-list">
                  {midiAll.length > 0 && (
                    <>
                      <div style={{ padding: '8px 16px', fontSize: 11, fontWeight: 700, color: '#2563EB', background: '#EFF6FF', borderBottom: '1px solid var(--bl)' }}>
                        &#9728;&#65039; Midi &middot; {midiAll.length} resa{midiAll.length > 1 ? 's' : ''}
                      </div>
                      {midiAll.map(bk => renderBk(bk, '#2563EB'))}
                    </>
                  )}
                  {soirAll.length > 0 && (
                    <>
                      <div style={{ padding: '8px 16px', fontSize: 11, fontWeight: 700, color: '#6366F1', background: '#EEF2FF', borderBottom: '1px solid var(--bl)' }}>
                        &#127769; Soir &middot; {soirAll.length} resa{soirAll.length > 1 ? 's' : ''}
                      </div>
                      {soirAll.map(bk => renderBk(bk, '#6366F1'))}
                    </>
                  )}
                  {noTime.map(bk => renderBk(bk, 'var(--tm)'))}
                  {allDayBks.length === 0 && (
                    <div className="fp-sb-empty">Aucune reservation ce jour</div>
                  )}
                </div>
                <div style={{ padding: 12, borderTop: '1px solid var(--bl)' }}>
                  <button className="ba" style={{ width: '100%' }} onClick={openNewResa}>+ Nouvelle reservation</button>
                </div>
              </div>
            );
          })()}
        </div>

        <style>{`
          @keyframes fpPulse {
            0%, 100% { box-shadow: 0 0 0 2px rgba(45,125,210,.3); }
            50% { box-shadow: 0 0 0 5px rgba(45,125,210,.15); }
          }
        `}</style>
      </div>
    );
  }

  function renderBookings() {
    const selD = parseDateLocal(selDate);
    const dateTitle = bkView === 'day'
        ? selD.toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
        : bkView === 'week' ? (() => {
            const dow = selD.getDay();
            const mon = new Date(selD); mon.setDate(mon.getDate() - ((dow + 6) % 7));
            const sun = new Date(mon); sun.setDate(sun.getDate() + 6);
            return mon.getDate() + ' ' + MONTH_NAMES[mon.getMonth()].slice(0,3) + ' — ' + sun.getDate() + ' ' + MONTH_NAMES[sun.getMonth()].slice(0,3) + ' ' + sun.getFullYear();
        })()
        : MONTH_NAMES[selD.getMonth()] + ' ' + selD.getFullYear();

    function navDate(dir) {
        const d = parseDateLocal(selDate);
        if (bkView === 'day') d.setDate(d.getDate() + dir);
        else if (bkView === 'week') d.setDate(d.getDate() + dir * 7);
        else d.setMonth(d.getMonth() + dir);
        dispatch({ type: 'SET_DATE', payload: fmtDate(d) });
    }

    return (
      <div>
        {/* View toggles + new resa button */}
        <div style={{ display: 'flex', gap: 8, marginBottom: 14, alignItems: 'center', flexWrap: 'wrap' }}>
            {['day', 'week', 'month'].map(v => (
                <button key={v} className="ba"
                    style={{ background: bkView === v ? 'var(--acg)' : 'var(--bg)', color: bkView === v ? '#fff' : 'var(--ts)', border: '1px solid var(--b)' }}
                    onClick={() => setBkView(v)}>
                    {v === 'day' ? 'Jour' : v === 'week' ? 'Semaine' : 'Mois'}
                </button>
            ))}
            <div style={{ flex: 1 }} />
            <button className="ba" onClick={openNewResa}>+ Nouvelle</button>
        </div>

        {/* Navigation arrows + title */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
            <button className="ba" style={{ padding: '6px 10px', background: 'var(--bg)', color: 'var(--ts)', border: '1px solid var(--b)' }}
                onClick={() => navDate(-1)}>&#8249;</button>
            <div style={{ fontSize: 15, fontWeight: 700, flex: 1, textAlign: 'center', textTransform: 'capitalize' }}>{dateTitle}</div>
            <button className="ba" style={{ padding: '6px 10px', background: 'var(--bg)', color: 'var(--ts)', border: '1px solid var(--b)' }}
                onClick={() => navDate(1)}>&#8250;</button>
            <button className="ba" style={{ fontSize: 11, padding: '6px 12px', background: 'var(--bg)', color: 'var(--ts)', border: '1px solid var(--b)' }}
                onClick={() => dispatch({ type: 'SET_DATE', payload: fmtDate(new Date()) })}>Aujourd&apos;hui</button>
        </div>

        {/* Search bar */}
        <input className="finp" style={{ marginBottom: 14 }}
            placeholder="🔍 Rechercher une reservation (nom, telephone, email...)"
            value={bkSearch} onChange={e => setBkSearch(e.target.value)} />

        {/* Conditional views */}
        {bkSearch.length >= 2 ? renderBookingSearch() : (
            <>
                {bkView === 'day' && renderDayView()}
                {bkView === 'week' && renderWeekView()}
                {bkView === 'month' && renderMonthView()}
            </>
        )}
      </div>
    );
  }

  function renderDayView() {
    return (
      <div className="fp-layout">
        <div className="fp-main">
          <div className="card">
            <div className="card-h">
              <div>
                <div className="card-t">Reservations du {selDate}</div>
                <div className="card-s">{bookingsForDate.length} reservations</div>
              </div>
            </div>
            {bookingsForDate.map(bk => (
              <div key={bk.id} className="rw" style={{ cursor: 'pointer' }} onClick={() => openEditResa(bk)}>
                <div className="rl">
                  <div className="dot" style={{ background: SRC_COLORS[bk.source] || '#6B7280' }} />
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 600 }}>{bk.name}{occasionBadge(bk.occasion)}</div>
                    <div style={{ fontSize: 11, color: 'var(--tm)' }}>
                      {timeStr(bk.booking_time || bk.time)} &middot; {bk.covers} couverts
                      {bk.table ? ' · Table ' + bk.table : ''}
                      {bk.zone ? ' · ' + bk.zone : ''}
                    </div>
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span className="src-badge" style={{
                    background: (SRC_COLORS[bk.source] || '#6B7280') + '18',
                    color: SRC_COLORS[bk.source] || '#6B7280'
                  }}>{SRC_LABELS[bk.source] || bk.source}</span>
                </div>
              </div>
            ))}
            {bookingsForDate.length === 0 && (
              <div style={{ padding: 40, textAlign: 'center', color: 'var(--tm)', fontSize: 13 }}>
                Aucune reservation pour le {selDate}
              </div>
            )}
          </div>
        </div>
        <div className="fp-sidebar">
          <Calendar />
        </div>
      </div>
    );
  }

  function renderBookingSearch() {
    const q = bkSearch.toLowerCase();
    const results = state.bookings.filter(b =>
        (b.name || '').toLowerCase().includes(q) ||
        (b.phone || '').includes(q) ||
        (b.email || '').toLowerCase().includes(q)
    );
    const byDate = {};
    results.forEach(b => {
        const d = b.date || 'Sans date';
        if (!byDate[d]) byDate[d] = [];
        byDate[d].push(b);
    });
    const sortedDates = Object.keys(byDate).sort().reverse();

    return (
        <div className="card">
            <div className="card-h">
                <div className="card-t">Resultats pour &ldquo;{bkSearch}&rdquo;</div>
                <div className="card-s">{results.length} reservation{results.length !== 1 ? 's' : ''}</div>
            </div>
            {sortedDates.map(d => (
                <div key={d}>
                    <div style={{ padding: '8px 18px', fontSize: 11, fontWeight: 700, color: 'var(--ac)', background: 'var(--bl)', borderBottom: '1px solid var(--b)' }}>{d}</div>
                    {byDate[d].map(bk => (
                        <div key={bk.id} className="rw" style={{ cursor: 'pointer' }} onClick={() => openEditResa(bk)}>
                            <div className="rl">
                                <div className="dot" style={{ background: SRC_COLORS[bk.source] || '#6B7280' }} />
                                <div>
                                    <div style={{ fontSize: 13, fontWeight: 600 }}>{bk.name}{occasionBadge(bk.occasion)}</div>
                                    <div style={{ fontSize: 11, color: 'var(--tm)' }}>
                                        {timeStr(bk.booking_time || bk.time)} · {bk.covers} couverts
                                        {bk.table ? ' · ' + bk.table : ''}
                                    </div>
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            ))}
            {results.length === 0 && (
                <div style={{ padding: 40, textAlign: 'center', color: 'var(--tm)', fontSize: 13 }}>
                    Aucune reservation trouvee pour &ldquo;{bkSearch}&rdquo;
                </div>
            )}
        </div>
    );
  }

  function renderWeekView() {
    const selD = parseDateLocal(selDate);
    const dayOfWeek = selD.getDay();
    const monday = new Date(selD);
    monday.setDate(monday.getDate() - ((dayOfWeek + 6) % 7));
    const days = [];
    for (let i = 0; i < 7; i++) {
      const d = new Date(monday);
      d.setDate(d.getDate() + i);
      days.push(fmtDate(d));
    }
    const dowLabels = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim'];

    return (
      <div className="card" style={{ overflow: 'auto' }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', borderBottom: '1px solid var(--bl)' }}>
          {days.map((ds, i) => {
            const d = parseDateLocal(ds);
            const isToday = ds === todayStr;
            const isSel = ds === selDate;
            const dayBks = state.bookings.filter(b => (b.date || '').startsWith(ds));
            return (
              <div key={ds} style={{
                padding: '12px 8px', textAlign: 'center', cursor: 'pointer',
                background: isSel ? 'var(--al)' : 'transparent',
                borderBottom: isSel ? '2px solid var(--ac)' : '2px solid transparent',
              }}
                onClick={() => dispatch({ type: 'SET_DATE', payload: ds })}>
                <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--tm)', textTransform: 'uppercase' }}>{dowLabels[i]}</div>
                <div style={{ fontSize: 18, fontWeight: 700, color: isToday ? 'var(--ac)' : 'var(--t)', marginTop: 2 }}>{d.getDate()}</div>
                <div style={{ fontSize: 10, color: 'var(--tm)', marginTop: 2 }}>{dayBks.length} resa</div>
              </div>
            );
          })}
        </div>
        <div>
          {days.map(ds => {
            const dayBks = state.bookings.filter(b => (b.date || '').startsWith(ds));
            if (dayBks.length === 0) return null;
            const d = parseDateLocal(ds);
            return (
              <div key={ds}>
                <div style={{ padding: '8px 18px', fontSize: 11, fontWeight: 700, color: 'var(--tm)', background: 'var(--bl)', letterSpacing: '.04em' }}>
                  {dowLabels[(d.getDay() + 6) % 7]} {d.getDate()} {MONTH_NAMES[d.getMonth()]}
                </div>
                {dayBks.map(bk => (
                  <div key={bk.id} className="rw" style={{ cursor: 'pointer' }} onClick={() => openEditResa(bk)}>
                    <div className="rl">
                      <div className="dot" style={{ background: SRC_COLORS[bk.source] || '#6B7280' }} />
                      <div>
                        <div style={{ fontSize: 13, fontWeight: 600 }}>{bk.name}{occasionBadge(bk.occasion)}</div>
                        <div style={{ fontSize: 11, color: 'var(--tm)' }}>
                          {timeStr(bk.booking_time || bk.time)} &middot; {bk.covers}p
                          {bk.table ? ' · ' + bk.table : ''}
                        </div>
                      </div>
                    </div>
                    <span className="src-badge" style={{
                      background: (SRC_COLORS[bk.source] || '#6B7280') + '18',
                      color: SRC_COLORS[bk.source] || '#6B7280'
                    }}>{SRC_LABELS[bk.source] || bk.source}</span>
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  function renderMonthView() {
    const selD = parseDateLocal(selDate);
    const year = selD.getFullYear();
    const month = selD.getMonth();
    const firstDay = new Date(year, month, 1).getDay();
    const startIdx = (firstDay + 6) % 7;
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const today = fmtDate(new Date());
    const DOW = ['L','M','M','J','V','S','D'];

    return (
        <div className="card" style={{ padding: 16 }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 2 }}>
                {DOW.map((d, i) => (
                    <div key={i} style={{ textAlign: 'center', fontSize: 10, fontWeight: 700, color: 'var(--tm)', padding: 4 }}>{d}</div>
                ))}
                {Array.from({ length: startIdx }).map((_, i) => <div key={'e' + i} />)}
                {Array.from({ length: daysInMonth }, (_, i) => {
                    const day = i + 1;
                    const ds = fmtDate(new Date(year, month, day));
                    const dayBks = state.bookings.filter(b => (b.date || '').startsWith(ds));
                    const covers = dayBks.reduce((s, b) => s + (b.covers || 0), 0);
                    const isToday = ds === today;
                    const isSel = ds === selDate;
                    return (
                        <div key={day} onClick={() => { dispatch({ type: 'SET_DATE', payload: ds }); setBkView('day'); }}
                            style={{
                                padding: 8, borderRadius: 8, cursor: 'pointer', textAlign: 'center',
                                background: isSel ? 'var(--ac)' : isToday ? 'var(--al)' : dayBks.length ? 'var(--bl)' : 'transparent',
                                color: isSel ? '#fff' : 'var(--t)',
                                border: isToday && !isSel ? '1px solid var(--b)' : '1px solid transparent',
                                transition: 'all .1s',
                            }}>
                            <div style={{ fontSize: 13, fontWeight: 700 }}>{day}</div>
                            {dayBks.length > 0 && (
                                <div style={{ fontSize: 9, color: isSel ? 'rgba(255,255,255,.8)' : 'var(--ac)', fontWeight: 700, marginTop: 2 }}>
                                    {dayBks.length} resa{dayBks.length > 1 ? 's' : ''} · {covers}p
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
  }

  function renderMenu() {
    const sections = menuEditing ? menuDraft : state.menuSections;

    return (
      <div>
        <div className="db" style={{ background: 'linear-gradient(135deg,#F0FDF4,#DCFCE7)', borderColor: '#BBF7D0' }}>
          <div className="db-top">
            <div className="di" style={{ background: 'linear-gradient(135deg,#059669,#10B981)' }}>&#9733;</div>
            <div style={{ flex: 1 }}>
              <div className="dlb" style={{ color: '#059669' }}>Message du jour</div>
              <div className="dtx" style={{ cursor: 'default' }}>{state.dailyMsg || 'Aucun message'}</div>
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', gap: 8, marginBottom: 14, alignItems: 'center' }}>
          {!menuEditing ? (
            <button className="ba" onClick={startMenuEdit}>Modifier le menu</button>
          ) : (
            <>
              <button className="ba" onClick={saveMenu}>Sauvegarder</button>
              <button className="ba" style={{ background: 'var(--bg)', color: 'var(--ts)', border: '1px solid var(--b)' }}
                onClick={() => setMenuEditing(false)}>Annuler</button>
            </>
          )}
          <button className="ba" style={{ background: 'var(--bg)', color: 'var(--ts)', border: '1px solid var(--b)' }}
            onClick={() => scanRef.current?.click()}>
            &#128247; Scanner
          </button>
          <input ref={scanRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={scanMenu} />
          {menuEditing && (
            <button className="ba" onClick={() => {
              setMenuDraft([...menuDraft, { title: 'Nouvelle section', items: [] }]);
            }}>+ Section</button>
          )}
        </div>

        {sections.map((sec, si) => (
          <div key={si} className="menu-sec">
            <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--bl)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              {menuEditing ? (
                <input className="finp" style={{ marginBottom: 0, fontWeight: 700, fontSize: 13, background: 'transparent', border: 'none', padding: 0 }}
                  value={sec.title} onChange={e => {
                    const d = [...menuDraft];
                    d[si] = { ...d[si], title: e.target.value };
                    setMenuDraft(d);
                  }} />
              ) : (
                <div className="mc" style={{ marginBottom: 0, borderBottom: 'none', paddingBottom: 0 }}>{sec.title}</div>
              )}
              {menuEditing && (
                <div style={{ display: 'flex', gap: 6 }}>
                  <button className="ba" style={{ fontSize: 11, padding: '4px 10px' }} onClick={() => {
                    const d = [...menuDraft];
                    d[si] = { ...d[si], items: [...d[si].items, { name: 'Nouveau plat', description: '', price: '0' }] };
                    setMenuDraft(d);
                  }}>+ Plat</button>
                  <button className="ba" style={{ fontSize: 11, padding: '4px 10px', background: '#EF4444' }} onClick={() => {
                    const d = menuDraft.filter((_, i) => i !== si);
                    setMenuDraft(d);
                  }}>&#10005;</button>
                </div>
              )}
            </div>
            {sec.items.map((item, ii) => (
              <div key={ii} className="mi-row" style={{ padding: '10px 18px' }}>
                <div style={{ flex: 1 }}>
                  {menuEditing ? (
                    <>
                      <input className="finp" style={{ marginBottom: 4, fontWeight: 600 }}
                        value={item.name} onChange={e => {
                          const d = [...menuDraft];
                          d[si].items[ii] = { ...d[si].items[ii], name: e.target.value };
                          setMenuDraft(d);
                        }} />
                      <input className="finp" style={{ marginBottom: 0, fontSize: 12 }}
                        placeholder="Description" value={item.description || ''} onChange={e => {
                          const d = [...menuDraft];
                          d[si].items[ii] = { ...d[si].items[ii], description: e.target.value };
                          setMenuDraft(d);
                        }} />
                    </>
                  ) : (
                    <>
                      <div className="mi-n">{item.name}</div>
                      {item.description && <div className="mi-d">{item.description}</div>}
                    </>
                  )}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  {menuEditing ? (
                    <>
                      <input className="finp" style={{ width: 70, marginBottom: 0, textAlign: 'right' }}
                        value={item.price} onChange={e => {
                          const d = [...menuDraft];
                          d[si].items[ii] = { ...d[si].items[ii], price: e.target.value };
                          setMenuDraft(d);
                        }} />
                      <button style={{ background: 'none', border: 'none', color: '#EF4444', cursor: 'pointer', fontSize: 14 }}
                        onClick={() => {
                          const d = [...menuDraft];
                          d[si] = { ...d[si], items: d[si].items.filter((_, i) => i !== ii) };
                          setMenuDraft(d);
                        }}>&#10005;</button>
                    </>
                  ) : (
                    <div className="mi-p">{item.price}&euro;</div>
                  )}
                </div>
              </div>
            ))}
            {sec.items.length === 0 && (
              <div style={{ padding: 20, textAlign: 'center', color: 'var(--tm)', fontSize: 12 }}>
                Aucun plat dans cette section
              </div>
            )}
          </div>
        ))}
        {sections.length === 0 && (
          <div className="ph">
            <div className="phi">&#128247;</div>
            <div style={{ fontSize: 14, fontWeight: 600 }}>Scannez votre carte !</div>
            <div style={{ fontSize: 12, color: 'var(--tm)', marginTop: 4, maxWidth: 300, margin: '4px auto 12px' }}>
              Prenez une photo de votre menu, notre IA le numerise automatiquement.
            </div>
            <label className="ba" style={{ cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              &#128247; Scanner mon menu
              <input type="file" accept="image/*" style={{ display: 'none' }} onChange={scanMenu} />
            </label>
          </div>
        )}
      </div>
    );
  }

  function renderConversations() {
    const sorted = [...convList].sort((a, b) => {
      const aLast = a[1].messages?.[a[1].messages.length - 1]?.time || '';
      const bLast = b[1].messages?.[b[1].messages.length - 1]?.time || '';
      return bLast.localeCompare(aLast);
    });

    const activeConv = selectedConv ? state.conversations[selectedConv] : null;
    const activeContact = selectedConv ? state.contacts[selectedConv] : null;

    return (
      <div className="card conv-split" style={{ display: 'flex', minHeight: 500 }}>
        <div className="conv-list-panel" style={{ width: 320, borderRight: '1px solid var(--bl)', overflowY: 'auto' }}>
          <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--bl)' }}>
            <div style={{ fontSize: 14, fontWeight: 700 }}>Conversations</div>
            <div style={{ fontSize: 12, color: 'var(--tm)' }}>{sorted.length} conversations</div>
          </div>
          {sorted.map(([phone, cv]) => {
            const contact = state.contacts[phone];
            const name = contact?.name || phone;
            const lastMsg = cv.last_message || (cv.messages?.length > 0 ? cv.messages[cv.messages.length - 1].content || cv.messages[cv.messages.length - 1].text : '');
            const lastTime = cv.messages?.length > 0 ? cv.messages[cv.messages.length - 1].time : '';
            return (
              <div key={phone}
                className={'conv-list-item' + (selectedConv === phone ? ' selected' : '')}
                onClick={() => openConv(phone)}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <div className="cav" style={{ background: 'var(--acg)', color: '#fff', width: 36, height: 36 }}>{initials(name)}</div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <div style={{ fontSize: 13, fontWeight: 600 }}>{name}</div>
                      {lastTime && <div style={{ fontSize: 10, color: 'var(--tm)' }}>{lastTime}</div>}
                    </div>
                    <div className="cmsg">{lastMsg}</div>
                  </div>
                </div>
              </div>
            );
          })}
          {sorted.length === 0 && (
            <div style={{ padding: 30, textAlign: 'center', color: 'var(--tm)', fontSize: 12 }}>
              Activez l&apos;agent WhatsApp pour commencer a recevoir des messages
            </div>
          )}
        </div>
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
          {activeConv ? (
            <>
              <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--bl)', display: 'flex', alignItems: 'center', gap: 10 }}>
                <div className="cav" style={{ background: 'var(--acg)', color: '#fff' }}>{initials(activeContact?.name || selectedConv)}</div>
                <div>
                  <div style={{ fontSize: 14, fontWeight: 700 }}>{activeContact?.name || selectedConv}</div>
                  <div style={{ fontSize: 11, color: 'var(--tm)' }}>{selectedConv}</div>
                </div>
              </div>
              <div style={{ flex: 1, overflowY: 'auto', padding: 18, display: 'flex', flexDirection: 'column', gap: 8 }}>
                {(activeConv.messages || []).map((msg, i) => (
                  <div key={i} className={'bubble ' + (msg.role === 'user' || msg.role === 'client' ? 'bubble-user' : 'bubble-bot')}>
                    {msg.content || msg.text}
                    {msg.time && <div style={{ fontSize: 9, opacity: 0.6, marginTop: 4 }}>{msg.time}</div>}
                  </div>
                ))}
                {(!activeConv.messages || activeConv.messages.length === 0) && (
                  <div style={{ textAlign: 'center', color: 'var(--tm)', fontSize: 12, marginTop: 40 }}>
                    Pas de messages
                  </div>
                )}
              </div>
            </>
          ) : (
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--tm)', fontSize: 13 }}>
              Selectionnez une conversation
            </div>
          )}
        </div>
      </div>
    );
  }

  function renderReviews() {
    const queue = state.reviewQueue;
    const total = queue.length;
    const sent = queue.filter(r => r.sent).length;
    const responded = queue.filter(r => r.responded).length;
    const positive = queue.filter(r => r.sentiment === 'positive').length;

    return (
      <div>
        <div className="sg">
          <div className="sc">
            <div className="sl">Total</div>
            <div className="sv">{total}</div>
            <div className="ss2">Demandes d&apos;avis</div>
          </div>
          <div className="sc">
            <div className="sl">Envoyes</div>
            <div className="sv">{sent}</div>
            <div className="ss2">SMS/WhatsApp</div>
          </div>
          <div className="sc">
            <div className="sl">Reponses</div>
            <div className="sv">{responded}</div>
            <div className="ss2">Clients</div>
          </div>
          <div className="sc">
            <div className="sl">Positifs</div>
            <div className="sv" style={{ color: '#4ECDC4' }}>{positive}</div>
            <div className="ss2">{total > 0 ? Math.round(positive / total * 100) : 0}% satisfaction</div>
          </div>
        </div>

        <div className="card">
          <div className="card-h">
            <div className="card-t">Demandes d&apos;avis</div>
          </div>
          {queue.map(r => (
            <div key={r.id} className="review-card">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div>
                  <div style={{ fontSize: 14, fontWeight: 600 }}>{r.name}</div>
                  <div style={{ fontSize: 11, color: 'var(--tm)', marginTop: 2 }}>
                    {r.phone} &middot; {r.booking_time || ''}
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  {r.sent && (
                    <span className="badge" style={{ background: '#E6FAF8', color: '#059669' }}>Envoye</span>
                  )}
                  {r.sentiment === 'positive' && (
                    <span className="badge" style={{ background: '#E6FAF8', color: '#4ECDC4' }}>&#9733; Positif</span>
                  )}
                  {r.sentiment === 'negative' && (
                    <span className="badge" style={{ background: '#FEE2E2', color: '#EF4444' }}>Negatif</span>
                  )}
                  {r.sentiment === 'neutral' && (
                    <span className="badge" style={{ background: '#FEF3C7', color: '#D97706' }}>Neutre</span>
                  )}
                  {!r.sent && !r.sentiment && (
                    <span className="badge" style={{ background: 'var(--bl)', color: 'var(--tm)' }}>En attente</span>
                  )}
                </div>
              </div>
              {r.response && (
                <div style={{ marginTop: 10, padding: 10, background: 'var(--bl)', borderRadius: 8, fontSize: 13, color: 'var(--ts)' }}>
                  &ldquo;{r.response}&rdquo;
                </div>
              )}
            </div>
          ))}
          {queue.length === 0 && (
            <div className="ph" style={{ border: 'none', boxShadow: 'none' }}>
              <div className="phi">&#9733;</div>
              <div style={{ fontSize: 14, fontWeight: 600 }}>Aucune demande d&apos;avis</div>
              <div style={{ fontSize: 12, color: 'var(--tm)', marginTop: 4, maxWidth: 340, margin: '4px auto 12px' }}>
                Les demandes d&apos;avis Google sont envoyees automatiquement apres chaque reservation. Configurez votre lien Google dans Configuration.
              </div>
              <button className="ba" onClick={() => setPage('config')}>Configurer les avis</button>
            </div>
          )}
        </div>
      </div>
    );
  }

  function renderContacts() {
    if (selectedContact) {
      const c = state.contacts[selectedContact];
      if (!c) { setSelectedContact(null); return null; }
      return renderContactCard(c);
    }

    // Compute unique tags for filter
    const allTags = [...new Set(contactList.flatMap(c => c.tags || []))].sort();
    // Filter + search + sort
    let filtered = contactList;
    if (contactSearch) {
      const q = contactSearch.toLowerCase();
      filtered = filtered.filter(c =>
        (c.name || '').toLowerCase().includes(q) ||
        (c.email || '').toLowerCase().includes(q) ||
        (c.phone || '').includes(q)
      );
    }
    if (contactTagFilter) {
      filtered = filtered.filter(c => (c.tags || []).includes(contactTagFilter));
    }
    filtered = [...filtered].sort((a, b) => {
      if (contactSort === 'visits') return (b.visits || 0) - (a.visits || 0);
      if (contactSort === 'recent') return (b.last_seen || '').localeCompare(a.last_seen || '');
      return (a.name || '').localeCompare(b.name || '');
    });

    return (
      <div>
        {/* Search + filters */}
        <div style={{ marginBottom: 14, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <input className="finp" style={{ flex: 1, minWidth: 0, marginBottom: 0 }}
            placeholder="Rechercher par nom, email ou telephone..."
            value={contactSearch} onChange={e => setContactSearch(e.target.value)} />
          <select className="finp" style={{ width: 'auto', marginBottom: 0, minWidth: 120 }}
            value={contactSort} onChange={e => setContactSort(e.target.value)}>
            <option value="name">Tri : Nom A-Z</option>
            <option value="visits">Tri : Visites</option>
            <option value="recent">Tri : Recent</option>
          </select>
        </div>
        {allTags.length > 0 && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 14 }}>
            <button className={'badge'} style={{
              cursor: 'pointer', border: 'none', fontFamily: 'var(--f)',
              background: !contactTagFilter ? 'var(--ac)' : 'var(--bl)', color: !contactTagFilter ? '#fff' : 'var(--ts)'
            }} onClick={() => setContactTagFilter('')}>Tous</button>
            {allTags.map(tag => (
              <button key={tag} className={'badge'} style={{
                cursor: 'pointer', border: 'none', fontFamily: 'var(--f)',
                background: contactTagFilter === tag ? 'var(--ac)' : 'var(--al)', color: contactTagFilter === tag ? '#fff' : 'var(--ac)'
              }} onClick={() => setContactTagFilter(contactTagFilter === tag ? '' : tag)}>{tag}</button>
            ))}
          </div>
        )}

        <div className="card">
          <div className="card-h">
            <div>
              <div className="card-t">Contacts</div>
              <div className="card-s">{filtered.length} / {contactList.length} contacts</div>
            </div>
          </div>
          {filtered.map(c => (
            <div key={c.phone} className="rw" style={{ cursor: 'pointer' }}
              onClick={() => openContact(c.phone)}>
              <div className="rl">
                <div className="cav" style={{
                  background: (SRC_COLORS[c.source] || '#6B7280') + '20',
                  color: SRC_COLORS[c.source] || '#6B7280',
                  width: 36, height: 36
                }}>{initials(c.name)}</div>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{c.name || c.phone}</div>
                  <div style={{ fontSize: 11, color: 'var(--tm)' }}>
                    {c.phone} &middot; {c.visits || 0} visites
                    {c.language ? ' · ' + c.language : ''}
                  </div>
                </div>
              </div>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <span className="src-badge" style={{
                  background: (SRC_COLORS[c.source] || '#6B7280') + '18',
                  color: SRC_COLORS[c.source] || '#6B7280'
                }}>{SRC_LABELS[c.source] || c.source || '?'}</span>
                {c.tags && c.tags.length > 0 && c.tags.slice(0, 2).map((tag, i) => (
                  <span key={i} className="badge" style={{ background: 'var(--al)', color: 'var(--ac)', fontSize: 10 }}>{tag}</span>
                ))}
              </div>
            </div>
          ))}
          {contactList.length === 0 && (
            <div className="ph" style={{ border: 'none', boxShadow: 'none' }}>
              <div className="phi">&#128100;</div>
              <div style={{ fontSize: 14, fontWeight: 600 }}>Aucun contact</div>
              <div style={{ fontSize: 12, color: 'var(--tm)', marginTop: 4, maxWidth: 340, margin: '4px auto' }}>
                Vos contacts apparaitront ici des qu&apos;un client reservera via WhatsApp ou le chat web.
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }

  function renderContactCard(c) {
    const conv = state.conversations[c.phone];
    const contactBookings = state.bookings.filter(b => b.phone === c.phone);

    return (
      <div>
        <button className="ba" style={{ marginBottom: 14, background: 'var(--bg)', color: 'var(--ts)', border: '1px solid var(--b)' }}
          onClick={() => openContact(null)}>&larr; Retour</button>

        <div className="card" style={{ marginBottom: 14 }}>
          <div style={{ padding: 20 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 16 }}>
              <div className="cav" style={{
                width: 56, height: 56, fontSize: 18,
                background: (SRC_COLORS[c.source] || '#6B7280') + '20',
                color: SRC_COLORS[c.source] || '#6B7280'
              }}>{initials(c.name)}</div>
              <div>
                <div style={{ fontSize: 18, fontWeight: 700 }}>{c.name}</div>
                <div style={{ fontSize: 13, color: 'var(--tm)' }}>{c.phone}</div>
                {c.email && <div style={{ fontSize: 12, color: 'var(--tm)' }}>{c.email}</div>}
              </div>
            </div>

            <div className="sg" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
              <div className="sc">
                <div className="sl">Visites</div>
                <div className="sv" style={{ fontSize: 22 }}>{c.visits || 0}</div>
              </div>
              <div className="sc">
                <div className="sl">Source</div>
                <div style={{ fontSize: 14, fontWeight: 600 }}>{SRC_LABELS[c.source] || c.source || '?'}</div>
              </div>
              <div className="sc">
                <div className="sl">Langue</div>
                <div style={{ fontSize: 14, fontWeight: 600 }}>{c.language || '?'}</div>
              </div>
            </div>
          </div>
        </div>

        <div className="g2">
          <div className="card">
            <div className="card-h">
              <div className="card-t">Preferences</div>
              <button className="ba" style={{ fontSize: 11, padding: '4px 10px' }}
                onClick={() => {
                  setContactEditMode(!contactEditMode);
                  setContactPrefDraft(c.preferences || '');
                }}>{contactEditMode ? 'Annuler' : 'Modifier'}</button>
            </div>
            <div style={{ padding: 14 }}>
              {contactEditMode ? (
                <>
                  <textarea className="dinp" value={contactPrefDraft}
                    onChange={e => setContactPrefDraft(e.target.value)}
                    placeholder="Preferences du client..." />
                  <button className="ba" style={{ marginTop: 8 }}
                    onClick={() => saveContactPreferences(c.phone)}>Sauvegarder</button>
                </>
              ) : (
                <div style={{ fontSize: 13, color: c.preferences ? 'var(--t)' : 'var(--tm)' }}>
                  {c.preferences || 'Aucune preference renseignee'}
                </div>
              )}
            </div>
          </div>

          <div className="card">
            <div className="card-h">
              <div className="card-t">Tags</div>
            </div>
            <div style={{ padding: 14 }}>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
                {(c.tags || []).map((tag, i) => (
                  <span key={i} className="badge" style={{ background: 'var(--al)', color: 'var(--ac)' }}>{tag}</span>
                ))}
                {(!c.tags || c.tags.length === 0) && (
                  <span style={{ fontSize: 12, color: 'var(--tm)' }}>Aucun tag</span>
                )}
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                <input className="finp" style={{ marginBottom: 0, flex: 1 }}
                  placeholder="Ajouter un tag" value={contactTagDraft}
                  onChange={e => setContactTagDraft(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && addContactTag(c.phone)} />
                <button className="ba" onClick={() => addContactTag(c.phone)}>+</button>
              </div>
            </div>
          </div>
        </div>

        <div className="card" style={{ marginTop: 14 }}>
          <div className="card-h">
            <div className="card-t">Notes</div>
            <div style={{ fontSize: 11, color: 'var(--tm)' }}>
              {(Array.isArray(c.notes) ? c.notes : (c.notes ? [{ text: c.notes, date: '' }] : [])).length} note{(Array.isArray(c.notes) ? c.notes : []).length !== 1 ? 's' : ''}
            </div>
          </div>
          <div style={{ padding: 14 }}>
            {(Array.isArray(c.notes) ? [...c.notes].reverse() : (c.notes ? [{ text: c.notes, date: '' }] : [])).map((n, i) => (
              <div key={i} style={{ padding: '8px 12px', background: 'var(--bg)', borderRadius: 8, marginBottom: 6, fontSize: 13 }}>
                <div style={{ color: 'var(--t)' }}>{n.text}</div>
                {n.date && <div style={{ fontSize: 10, color: 'var(--tm)', marginTop: 4 }}>
                  {new Date(n.date).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })}
                </div>}
              </div>
            ))}
            <textarea className="dinp" value={contactNoteDraft}
              onChange={e => setContactNoteDraft(e.target.value)}
              placeholder="Ajouter une note..."
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (contactNoteDraft.trim()) saveContactNote(c.phone); } }} />
            <button className="ba" style={{ marginTop: 8 }}
              onClick={() => { if (contactNoteDraft.trim()) saveContactNote(c.phone); }}>Ajouter la note</button>
          </div>
        </div>

        <div className="card" style={{ marginTop: 14 }}>
          <div className="card-h">
            <div className="card-t">Reservations</div>
            <div className="card-s">{contactBookings.length}</div>
          </div>
          {contactBookings.slice(0, 10).map(bk => (
            <div key={bk.id} className="rw">
              <div className="rl">
                <div className="dot" style={{ background: SRC_COLORS[bk.source] || '#6B7280' }} />
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{bk.date}</div>
                  <div style={{ fontSize: 11, color: 'var(--tm)' }}>
                    {timeStr(bk.booking_time || bk.time)} &middot; {bk.covers}p
                    {bk.table ? ' · ' + bk.table : ''}
                  </div>
                </div>
              </div>
            </div>
          ))}
          {contactBookings.length === 0 && (
            <div style={{ padding: 20, textAlign: 'center', color: 'var(--tm)', fontSize: 12 }}>
              Aucune reservation
            </div>
          )}
        </div>

        {conv && (
          <div className="card" style={{ marginTop: 14 }}>
            <div className="card-h">
              <div className="card-t">Historique de conversation</div>
            </div>
            <div style={{ padding: 14, maxHeight: 300, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 8 }}>
              {(conv.messages || []).slice(-20).map((msg, i) => (
                <div key={i} className={'bubble ' + (msg.role === 'user' || msg.role === 'client' ? 'bubble-user' : 'bubble-bot')}>
                  {msg.content || msg.text}
                  {msg.time && <div style={{ fontSize: 9, opacity: 0.6, marginTop: 4 }}>{msg.time}</div>}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  }

  function renderWaitlist() {
    const active = state.waitlistEntries.filter(w => w.status === 'waiting' || !w.status);
    const history = state.waitlistEntries.filter(w => w.status && w.status !== 'waiting');

    return (
      <div>
        <div className="card" style={{ marginBottom: 14 }}>
          <div className="card-h">
            <div className="card-t">Ajouter a la liste d&apos;attente</div>
          </div>
          <div style={{ padding: 18 }}>
            <div className="finp-row">
              <div className="finp-group">
                <div className="finp-label">Nom</div>
                <input className="finp" value={wlName} onChange={e => setWlName(e.target.value)} placeholder="Nom du client" />
              </div>
              <div className="finp-group">
                <div className="finp-label">Telephone</div>
                <input className="finp" value={wlPhone} onChange={e => setWlPhone(e.target.value)} placeholder="+33..." />
              </div>
            </div>
            <div className="finp-row">
              <div className="finp-group">
                <div className="finp-label">Couverts</div>
                <input className="finp" type="number" value={wlCovers} onChange={e => setWlCovers(e.target.value)} />
              </div>
              <div className="finp-group">
                <div className="finp-label">Service</div>
                <select className="finp" value={wlService} onChange={e => setWlService(e.target.value)}>
                  <option value="midi">Midi</option>
                  <option value="soir">Soir</option>
                </select>
              </div>
            </div>
            <div className="finp-row">
              <div className="finp-group">
                <div className="finp-label">Date</div>
                <input className="finp" type="date" value={wlDate} onChange={e => setWlDate(e.target.value)} />
              </div>
              <div className="finp-group">
                <div className="finp-label">Heure souhaitee</div>
                <input className="finp" type="time" value={wlTime} onChange={e => setWlTime(e.target.value)} />
              </div>
            </div>
            <button className="ba" style={{ marginTop: 12 }} onClick={addToWaitlist}>Ajouter</button>
          </div>
        </div>

        <div className="card" style={{ marginBottom: 14 }}>
          <div className="card-h">
            <div>
              <div className="card-t">En attente</div>
              <div className="card-s">{active.length} clients</div>
            </div>
          </div>
          {active.map(w => (
            <div key={w.id} className="rw">
              <div className="rl">
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{w.name}</div>
                  <div style={{ fontSize: 11, color: 'var(--tm)' }}>
                    {w.covers}p &middot; {w.service || 'midi'} &middot; {w.date}
                    {w.preferred_time ? ' · ' + w.preferred_time : ''}
                  </div>
                  {w.phone && <div style={{ fontSize: 11, color: 'var(--tm)' }}>{w.phone}</div>}
                </div>
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                <button className="ba" style={{ fontSize: 11, padding: '5px 10px' }}
                  onClick={() => notifyWaitlist(w.id)}>Notifier</button>
                <button className="ba" style={{ fontSize: 11, padding: '5px 10px', background: '#EF4444' }}
                  onClick={() => removeWaitlist(w.id)}>Retirer</button>
              </div>
            </div>
          ))}
          {active.length === 0 && (
            <div style={{ padding: 30, textAlign: 'center', color: 'var(--tm)', fontSize: 13 }}>
              Liste d&apos;attente vide
            </div>
          )}
        </div>

        {history.length > 0 && (
          <div className="card">
            <div className="card-h">
              <div className="card-t">Historique</div>
            </div>
            {history.map(w => (
              <div key={w.id} className="rw">
                <div className="rl">
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 600 }}>{w.name}</div>
                    <div style={{ fontSize: 11, color: 'var(--tm)' }}>
                      {w.covers}p &middot; {w.service || 'midi'} &middot; {w.date}
                    </div>
                  </div>
                </div>
                <span className="badge" style={{
                  background: w.status === 'notified' ? '#E6FAF8' : w.status === 'seated' ? '#EBF4FF' : 'var(--bl)',
                  color: w.status === 'notified' ? '#059669' : w.status === 'seated' ? '#2D7DD2' : 'var(--tm)',
                }}>{w.status === 'notified' ? 'Notifie' : w.status === 'seated' ? 'Place' : w.status === 'removed' ? 'Retire' : w.status}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  function renderConfig() {
    const cfg = state.restaurantConfig;
    const blocks = state.overviewBlocks;

    return (
      <div>
        <div className="cfs">
          <div className="cft">Blocs Vue d&apos;ensemble</div>
          <div className="cfsb">Choisissez les sections affichees sur la page d&apos;accueil</div>
          {[
            { key: 'daily', label: 'Message du jour' },
            { key: 'stats', label: 'Statistiques' },
            { key: 'floor', label: 'Plan de salle' },
            { key: 'bookings', label: 'Reservations' },
            { key: 'contacts', label: 'Contacts' },
          ].map(item => (
            <div key={item.key} className="cfr">
              <div className="cfl">{item.label}</div>
              <div className={'tog' + (blocks[item.key] ? ' on' : '')}
                onClick={() => dispatch({ type: 'SET_OVERVIEW_BLOCK', key: item.key, value: !blocks[item.key] })}>
                <div className="togd" />
              </div>
            </div>
          ))}
        </div>

        <div className="cfs">
          <div className="cft">Automatisations</div>
          <div className="cfsb">Configurez les actions automatiques</div>
          <div className="cfr">
            <div>
              <div className="cfl">Rappels de reservation</div>
              <div className="cfd">Envoyer un rappel WhatsApp avant chaque reservation</div>
            </div>
            <div className={'tog' + (cfg._reminders_enabled ? ' on' : '')} onClick={toggleReminders}>
              <div className="togd" />
            </div>
          </div>
        </div>

        <div className="cfs">
          <div className="cft">Informations du restaurant</div>
          <div className="cfsb">Gerez les informations de votre etablissement</div>
          {[
            { key: 'name', label: 'Nom', desc: cfg.name || 'Non renseigne' },
            { key: 'address', label: 'Adresse', desc: cfg.address || 'Non renseignee' },
            { key: 'phone', label: 'Telephone', desc: cfg.phone || 'Non renseigne' },
            { key: 'hours', label: 'Horaires', desc: cfg.hours || 'Non renseignes' },
            { key: 'description', label: 'Description', desc: cfg.description || 'Non renseignee' },
            { key: 'tone', label: "Ton de l'agent IA", desc: cfg.tone || 'Non renseigne' },
          ].map(item => (
            <div key={item.key} className="cfr" style={{ cursor: 'pointer' }}
              onClick={() => editConfigField(item.key, item.label)}>
              <div>
                <div className="cfl">{item.label}</div>
                <div className="cfd">{item.desc}</div>
              </div>
              <div style={{ fontSize: 12, color: 'var(--ac)', fontWeight: 600 }}>Modifier</div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  function renderStats() {
    const todayBookings = state.bookings.filter(b => (b.date || '').startsWith(todayStr));
    const todayCovers = todayBookings.reduce((s, b) => s + (b.covers || 0), 0);
    const sources = {};
    state.bookings.forEach(b => {
      const src = b.source || 'other';
      sources[src] = (sources[src] || 0) + 1;
    });
    const totalBookings = state.bookings.length;

    const maxHistory = statsHistory.length > 0 ? Math.max(...statsHistory.map(h => h.bookings || 0), 1) : 1;

    return (
      <div>
        <div className="card" style={{ marginBottom: 14 }}>
          <div className="card-h">
            <div className="card-t">Recap du jour</div>
          </div>
          <div style={{ padding: 18 }}>
            <div className="sg" style={{ gridTemplateColumns: 'repeat(4, 1fr)', marginBottom: 0 }}>
              <div className="sc">
                <div className="sl">Reservations</div>
                <div className="sv">{todayBookings.length}</div>
                <div className="ss2">Aujourd&apos;hui</div>
              </div>
              <div className="sc">
                <div className="sl">Couverts</div>
                <div className="sv">{todayCovers}</div>
                <div className="ss2">Total</div>
              </div>
              <div className="sc">
                <div className="sl">Conversations</div>
                <div className="sv">{convList.length}</div>
                <div className="ss2">Actives</div>
              </div>
              <div className="sc">
                <div className="sl">Contacts</div>
                <div className="sv">{contactList.length}</div>
                <div className="ss2">CRM</div>
              </div>
            </div>
          </div>
        </div>

        <div className="card" style={{ marginBottom: 14 }}>
          <div className="card-h">
            <div className="card-t">Historique des reservations</div>
          </div>
          <div style={{ padding: 18 }}>
            {statsHistory.length > 0 ? (
              <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, height: 160 }}>
                {statsHistory.slice(-30).map((h, i) => {
                  const pct = ((h.bookings || 0) / maxHistory) * 100;
                  return (
                    <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                      <div style={{
                        width: '100%', maxWidth: 24,
                        height: pct + '%', minHeight: 4,
                        background: 'var(--acg)', borderRadius: '4px 4px 0 0',
                      }} title={h.date + ': ' + (h.bookings || 0) + ' reservations'} />
                      {i % 5 === 0 && (
                        <div style={{ fontSize: 8, color: 'var(--tm)', marginTop: 4, whiteSpace: 'nowrap' }}>
                          {h.date ? h.date.slice(5) : ''}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : (
              <div style={{ textAlign: 'center', color: 'var(--tm)', fontSize: 13, padding: 30 }}>
                Aucune donnee historique
              </div>
            )}
          </div>
        </div>

        <div className="g2">
          <div className="card">
            <div className="card-h">
              <div className="card-t">Repartition par source</div>
            </div>
            <div style={{ padding: 14 }}>
              {Object.entries(sources).map(([src, count]) => (
                <div key={src} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
                  <div className="dot" style={{ background: SRC_COLORS[src] || '#6B7280', width: 8, height: 8 }} />
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 13, fontWeight: 600 }}>{SRC_LABELS[src] || src}</div>
                    <div style={{ height: 6, borderRadius: 3, background: 'var(--bl)', marginTop: 4 }}>
                      <div style={{
                        height: '100%', borderRadius: 3,
                        background: SRC_COLORS[src] || '#6B7280',
                        width: (totalBookings > 0 ? (count / totalBookings * 100) : 0) + '%',
                      }} />
                    </div>
                  </div>
                  <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--tm)', minWidth: 40, textAlign: 'right' }}>
                    {count} ({totalBookings > 0 ? Math.round(count / totalBookings * 100) : 0}%)
                  </div>
                </div>
              ))}
              {Object.keys(sources).length === 0 && (
                <div style={{ textAlign: 'center', color: 'var(--tm)', fontSize: 12 }}>Aucune donnee</div>
              )}
            </div>
          </div>

          <div className="card">
            <div className="card-h">
              <div className="card-t">Communications</div>
            </div>
            <div style={{ padding: 14 }}>
              <div className="cfr">
                <div className="cfl">Messages totaux</div>
                <div style={{ fontSize: 16, fontWeight: 700 }}>
                  {convList.reduce((a, [, cv]) => a + (cv.count || cv.messages?.length || 0), 0)}
                </div>
              </div>
              <div className="cfr">
                <div className="cfl">Conversations</div>
                <div style={{ fontSize: 16, fontWeight: 700 }}>{convList.length}</div>
              </div>
              <div className="cfr">
                <div className="cfl">Avis envoyes</div>
                <div style={{ fontSize: 16, fontWeight: 700 }}>{state.reviewQueue.filter(r => r.sent).length}</div>
              </div>
              <div className="cfr" style={{ borderBottom: 'none' }}>
                <div className="cfl">Rappels actifs</div>
                <div style={{ fontSize: 16, fontWeight: 700 }}>{state.restaurantConfig._reminders_enabled ? 'Oui' : 'Non'}</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  function renderAccount() {
    const u = state.user;

    return (
      <div>
        <div className="card" style={{ marginBottom: 14 }}>
          <div className="card-h">
            <div className="card-t">Mon compte</div>
          </div>
          <div style={{ padding: 18 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 20 }}>
              <div className="cav" style={{ width: 56, height: 56, fontSize: 18, background: 'var(--acg)', color: '#fff' }}>
                {u ? initials((u.first_name || '') + ' ' + (u.last_name || '')) : '?'}
              </div>
              <div>
                <div style={{ fontSize: 18, fontWeight: 700 }}>{u?.first_name} {u?.last_name}</div>
                <div style={{ fontSize: 13, color: 'var(--tm)' }}>{u?.email}</div>
                {u?.restaurant_name && (
                  <div style={{ fontSize: 12, color: 'var(--ac)', fontWeight: 600, marginTop: 2 }}>{u.restaurant_name}</div>
                )}
              </div>
            </div>

            {u?.restaurant_status && (
              <div className="cfr">
                <div className="cfl">Statut</div>
                <span className="badge" style={{
                  background: u.restaurant_status === 'active' ? '#E6FAF8' : '#FFFBEB',
                  color: u.restaurant_status === 'active' ? '#059669' : '#D97706'
                }}>{u.restaurant_status === 'active' ? 'Actif' : u.restaurant_status === 'trial' ? 'Essai' : u.restaurant_status}</span>
              </div>
            )}
            {u?.trial_ends_at && (
              <div className="cfr">
                <div className="cfl">Fin d&apos;essai</div>
                <div style={{ fontSize: 13, fontWeight: 600 }}>{new Date(u.trial_ends_at).toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' })}</div>
              </div>
            )}
          </div>
        </div>

        <div className="card" style={{ marginBottom: 14 }}>
          <div className="card-h">
            <div className="card-t">Changer le mot de passe</div>
          </div>
          <div style={{ padding: 18 }}>
            <div className="finp-group">
              <div className="finp-label">Mot de passe actuel</div>
              <input className="finp" type="password" value={acOldPwd}
                onChange={e => setAcOldPwd(e.target.value)} />
            </div>
            <div className="finp-group">
              <div className="finp-label">Nouveau mot de passe (min. 12 caracteres)</div>
              <input className="finp" type="password" value={acNewPwd}
                onChange={e => setAcNewPwd(e.target.value)} />
            </div>
            <div className="finp-group">
              <div className="finp-label">Confirmer</div>
              <input className="finp" type="password" value={acConfirm}
                onChange={e => setAcConfirm(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && changePassword()} />
            </div>
            <button className="ba" style={{ marginTop: 8 }} onClick={changePassword}>Modifier le mot de passe</button>
          </div>
        </div>

        <button className="ba" style={{ background: '#EF4444', width: '100%', padding: 14, fontSize: 14 }}
          onClick={doLogout}>Se deconnecter</button>
      </div>
    );
  }

  // ========================================
  // RENDER PAGE CONTENT
  // ========================================
  function renderPage() {
    switch (page) {
      case 'overview': return renderOverview();
      case 'floorplan': return renderFloorplan();
      case 'bookings': return renderBookings();
      case 'menu': return renderMenu();
      case 'conversations': return renderConversations();
      case 'reviews': return renderReviews();
      case 'contacts': return renderContacts();
      case 'waitlist': return renderWaitlist();
      case 'config': return renderConfig();
      case 'stats': return renderStats();
      case 'account': return renderAccount();
      default: return renderOverview();
    }
  }

  // ========================================
  // SIDEBAR NAV CONFIG
  // ========================================
  const navSections = [
    {
      label: 'PRINCIPAL',
      items: [
        { id: 'overview', icon: '\u25C8', label: "Vue d'ensemble" },
        { id: 'floorplan', icon: '\u229E', label: 'Plan de salle' },
        { id: 'bookings', icon: '\u25C9', label: 'Reservations', badge: bookingsForDate.length },
        { id: 'menu', icon: '\u25D0', label: 'Menu' },
      ]
    },
    {
      label: 'CLIENTS',
      items: [
        { id: 'conversations', icon: '\u25C8', label: 'Conversations', badge: convList.length },
        { id: 'reviews', icon: '\u2605', label: 'Avis', badge: state.reviewQueue.length },
        { id: 'contacts', icon: '\u25C7', label: 'Contacts' },
        { id: 'waitlist', icon: '\u23F1', label: "Liste d'attente", badge: state.waitlistEntries.filter(w => w.status === 'waiting' || !w.status).length },
      ]
    },
    {
      label: 'PARAMETRES',
      items: [
        { id: 'config', icon: '\u2699', label: 'Configuration' },
        { id: 'stats', icon: '\u26AB', label: 'Statistiques' },
        { id: 'account', icon: '\uD83D\uDC64', label: 'Mon compte' },
      ]
    },
  ];

  // Mobile nav config
  const mobileMainNav = [
    { id: 'overview', icon: '\u25C8', label: 'Accueil' },
    { id: 'bookings', icon: '\u25C9', label: 'Resas' },
    { id: 'floorplan', icon: '\u229E', label: 'Plan' },
    { id: 'conversations', icon: '\u25C8', label: 'Messages' },
  ];
  const mobileMoreItems = [
    { id: 'menu', icon: '\u25D0', label: 'Menu' },
    { id: 'reviews', icon: '\u2605', label: 'Avis' },
    { id: 'contacts', icon: '\u25C7', label: 'Contacts' },
    { id: 'waitlist', icon: '\u23F1', label: 'Attente' },
    { id: 'config', icon: '\u2699', label: 'Config' },
    { id: 'stats', icon: '\u26AB', label: 'Stats' },
    { id: 'account', icon: '\uD83D\uDC64', label: 'Compte' },
  ];

  // ========================================
  // MODALS
  // ========================================
  function renderNewResaModal() {
    if (!newResaOpen) return null;
    return (
      <div className="modal-bg show" onClick={() => setNewResaOpen(false)}>
        <div className="modal" onClick={e => e.stopPropagation()}>
          <h2>Nouvelle reservation</h2>
          <div className="finp-group" style={{ marginBottom: 12 }}>
            <div className="finp-label">Date</div>
            <input className="finp" type="date" value={nrDate} onChange={e => setNrDate(e.target.value)} />
          </div>
          <div className="finp-row">
            <div className="finp-group">
              <div className="finp-label">Prenom</div>
              <input className="finp" value={nrFirst} onChange={e => { setNrFirst(e.target.value); searchContacts(e.target.value); }} />
            </div>
            {nrSuggestions.length > 0 && (
                <div style={{ position: 'relative' }}>
                    <div style={{ position: 'absolute', top: 0, left: 0, right: 0, background: 'var(--card)', border: '1px solid var(--b)',
                        borderRadius: 8, boxShadow: '0 4px 12px rgba(0,0,0,.1)', zIndex: 10, maxHeight: 200, overflowY: 'auto' }}>
                        {nrSuggestions.map(ct => (
                            <div key={ct.phone} style={{ padding: '10px 14px', cursor: 'pointer', borderBottom: '1px solid var(--bl)',
                                fontSize: 13, transition: 'background .1s' }}
                                onMouseOver={e => e.currentTarget.style.background = 'var(--bl)'}
                                onMouseOut={e => e.currentTarget.style.background = ''}
                                onClick={() => selectContactSuggestion(ct)}>
                                <div style={{ fontWeight: 600 }}>{ct.name}</div>
                                <div style={{ fontSize: 11, color: 'var(--tm)' }}>{ct.phone} · {ct.visits || 0} visites</div>
                            </div>
                        ))}
                    </div>
                </div>
            )}
            {nrFirst.length >= 2 && nrSuggestions.length === 0 && nrContactMatch === null && (
                <div style={{ fontSize: 11, color: 'var(--tm)', marginTop: 2, marginBottom: 4 }}>Nouveau client</div>
            )}
            {nrContactMatch && (
                <div style={{ fontSize: 11, color: 'var(--ok)', marginTop: 2, marginBottom: 4 }}>
                    &#10003; Client connu — {nrContactMatch.visits || 0} visites
                </div>
            )}
            <div className="finp-group">
              <div className="finp-label">Nom</div>
              <input className="finp" value={nrLast} onChange={e => setNrLast(e.target.value)} />
            </div>
          </div>
          <div className="finp-row">
            <div className="finp-group">
              <div className="finp-label">Couverts</div>
              <input className="finp" type="number" value={nrCovers} onChange={e => setNrCovers(e.target.value)} />
            </div>
            <div className="finp-group">
              <div className="finp-label">Heure</div>
              <input className="finp" type="time" value={nrTime} onChange={e => setNrTime(e.target.value)} />
            </div>
          </div>
          <div className="finp-group">
            <div className="finp-label">Telephone</div>
            <input className="finp" value={nrPhone} onChange={e => setNrPhone(e.target.value)} placeholder="+33..." />
          </div>
          <div className="finp-group">
            <div className="finp-label">Email</div>
            <input className="finp" type="email" value={nrEmail} onChange={e => setNrEmail(e.target.value)} />
          </div>
          <div className="finp-group">
            <div className="finp-label">Source</div>
            <select className="finp" value={nrSource} onChange={e => setNrSource(e.target.value)}>
              <option value="phone">Telephone</option>
              <option value="whatsapp">WhatsApp</option>
              <option value="web">Chat web</option>
              <option value="walk-in">Walk-in</option>
              <option value="zenchef">Zenchef</option>
            </select>
          </div>
          <div className="modal-act">
            <button className="mbtn mbtn-s" onClick={() => setNewResaOpen(false)}>Annuler</button>
            <button className="mbtn mbtn-p" onClick={submitNewResa}>Creer</button>
          </div>
        </div>
      </div>
    );
  }

  function renderEditResaModal() {
    if (!editResaOpen || !editResaData) return null;
    return (
      <div className="modal-bg show" onClick={() => setEditResaOpen(false)}>
        <div className="modal" onClick={e => e.stopPropagation()}>
          <h2>Modifier la reservation</h2>
          <div style={{ fontSize: 12, color: 'var(--tm)', marginBottom: 16 }}>
            #{editResaData.id} &middot; {editResaData.date}
          </div>
          <div className="finp-group">
            <div className="finp-label">Nom</div>
            <input className="finp" value={erName} onChange={e => setErName(e.target.value)} />
          </div>
          <div className="finp-row">
            <div className="finp-group">
              <div className="finp-label">Couverts</div>
              <input className="finp" type="number" value={erCovers} onChange={e => setErCovers(e.target.value)} />
            </div>
            <div className="finp-group">
              <div className="finp-label">Heure</div>
              <input className="finp" type="time" value={erTime} onChange={e => setErTime(e.target.value)} />
            </div>
          </div>
          <div className="finp-group">
            <div className="finp-label">Telephone</div>
            <input className="finp" value={erPhone} onChange={e => setErPhone(e.target.value)} />
          </div>
          <div className="finp-group">
            <div className="finp-label">Table</div>
            <select className="finp" value={erTable} onChange={e => setErTable(e.target.value)}>
              <option value="">Auto (pas de table)</option>
              {state.floorplan.map(t => (
                <option key={t.id} value={t.id}>{t.id} ({t.seats}p - {t.zone})</option>
              ))}
            </select>
          </div>
          <div className="modal-act">
            <button className="mbtn mbtn-s" onClick={() => setEditResaOpen(false)}>Annuler</button>
            <button className="mbtn mbtn-p" onClick={submitEditResa}>Sauvegarder</button>
          </div>
          <button style={{
            marginTop: 12, width: '100%', padding: 10, border: '1px solid #FCA5A5',
            background: '#FEF2F2', color: '#EF4444', borderRadius: 8, fontSize: 13,
            fontWeight: 600, cursor: 'pointer', fontFamily: 'var(--f)',
          }} onClick={deleteResa}>Supprimer cette reservation</button>
          {editResaData?.phone && (
              <button style={{
                  marginTop: 8, width: '100%', padding: 10, border: '1px solid var(--b)',
                  background: 'var(--bg)', color: 'var(--ac)', borderRadius: 8, fontSize: 13,
                  fontWeight: 600, cursor: 'pointer', fontFamily: 'var(--f)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
              }} onClick={() => {
                  setEditResaOpen(false);
                  openContact(editResaData.phone);
                  setPage('contacts');
              }}>
                  &#128100; Fiche contact &rarr;
              </button>
          )}
        </div>
      </div>
    );
  }

  // ========================================
  // ONBOARDING WIZARD
  // ========================================
  function renderOnboarding() {
    if (!onboarding) return null;

    const steps = [
      { title: 'Bienvenue', desc: 'Bienvenue sur GuestScale ! Configurons votre restaurant en quelques etapes.' },
      { title: 'Informations', desc: 'Les informations de base de votre restaurant.' },
      { title: 'Horaires', desc: "Indiquez vos horaires d'ouverture." },
      { title: 'Personnalite', desc: "Definissez le ton de votre assistant IA." },
      { title: 'Termine !', desc: 'Votre restaurant est configure. Vous pouvez modifier ces informations a tout moment dans Configuration.' },
    ];

    const s = steps[obStep];

    return (
      <div className="ob-overlay">
        <div className="ob-card">
          <div className="ob-steps">
            {steps.map((_, i) => (
              <div key={i} className={'ob-step' + (i < obStep ? ' done' : '') + (i === obStep ? ' active' : '')} />
            ))}
          </div>
          <div className="ob-title">{s.title}</div>
          <div className="ob-desc">{s.desc}</div>

          {obStep === 0 && (
            <div style={{ textAlign: 'center', padding: 20 }}>
              <div style={{ fontSize: 48, marginBottom: 16 }}>&#127860;</div>
              <div style={{ fontSize: 14, color: 'var(--ts)' }}>
                Nous allons configurer votre espace en 4 etapes rapides.
              </div>
            </div>
          )}

          {obStep === 1 && (
            <>
              <div className="ob-field">
                <div className="ob-label">Nom du restaurant</div>
                <input className="ob-input" value={obName} onChange={e => setObName(e.target.value)} placeholder="ex: Le Bistrot Parisien" />
              </div>
              <div className="ob-field">
                <div className="ob-label">Adresse</div>
                <input className="ob-input" value={obAddress} onChange={e => setObAddress(e.target.value)} placeholder="ex: 12 rue de la Paix, 75002 Paris" />
              </div>
              <div className="ob-field">
                <div className="ob-label">Telephone</div>
                <input className="ob-input" value={obPhone} onChange={e => setObPhone(e.target.value)} placeholder="+33 1 23 45 67 89" />
              </div>
            </>
          )}

          {obStep === 2 && (
            <div className="ob-field">
              <div className="ob-label">Horaires d&apos;ouverture</div>
              <textarea className="ob-textarea" value={obHours} onChange={e => setObHours(e.target.value)}
                placeholder={"Lun-Ven: 12h-14h30, 19h-22h30\nSam: 19h-23h\nDim: Ferme"} />
            </div>
          )}

          {obStep === 3 && (
            <>
              <div className="ob-field">
                <div className="ob-label">Description courte</div>
                <textarea className="ob-textarea" value={obDesc} onChange={e => setObDesc(e.target.value)}
                  placeholder="Restaurant gastronomique francais avec terrasse..." />
              </div>
              <div className="ob-field">
                <div className="ob-label">Ton de l&apos;assistant IA</div>
                <textarea className="ob-textarea" value={obTone} onChange={e => setObTone(e.target.value)}
                  placeholder="Professionnel et chaleureux, tutoiement possible..." />
              </div>
            </>
          )}

          {obStep === 4 && (
            <div style={{ textAlign: 'center', padding: 20 }}>
              <div style={{ fontSize: 48, marginBottom: 16 }}>&#10003;</div>
              <div style={{ fontSize: 14, color: 'var(--ts)' }}>
                Tout est pret ! Explorez votre tableau de bord.
              </div>
            </div>
          )}

          <div className="ob-actions">
            {obStep > 0 && obStep < 4 && (
              <button className="ob-btn ob-btn-s" onClick={() => setObStep(obStep - 1)}>Retour</button>
            )}
            {obStep < 4 && (
              <button className="ob-btn ob-btn-p" onClick={() => setObStep(obStep + 1)}>
                {obStep === 0 ? 'Commencer' : 'Suivant'}
              </button>
            )}
            {obStep === 4 && (
              <button className="ob-btn ob-btn-p" onClick={finishOnboarding}>Terminer</button>
            )}
          </div>
          {obStep > 0 && obStep < 4 && (
            <div className="ob-skip" onClick={() => setObStep(4)}>Passer la configuration</div>
          )}
        </div>
      </div>
    );
  }

  // ========================================
  // MAIN RENDER
  // ========================================
  return (
    <div className="app">
      {/* Sidebar */}
      <div className="sidebar">
        <div className="sb-b">
          <div className="sb-logo">
            <div className="sb-icon" dangerouslySetInnerHTML={{ __html: LOGO_SVG }} />
            <div>
              <div className="sb-wm">Guest<span style={{ color: '#4ECDC4' }}>Scale</span></div>
              <div className="sb-s">Restaurant AI</div>
            </div>
          </div>
        </div>

        <div className="sb-n">
          {navSections.map(section => (
            <div key={section.label}>
              <div className="sb-l">{section.label}</div>
              {section.items.map(item => (
                <button key={item.id}
                  className={'nb' + (page === item.id ? ' on' : '')}
                  onClick={() => setPage(item.id)}>
                  <span className="ic">{item.icon}</span>
                  {item.label}
                  {item.badge > 0 && (
                    <span className="nb-badge" style={{ background: page === item.id ? 'rgba(255,255,255,.2)' : 'var(--acg)', color: '#fff' }}>
                      {item.badge}
                    </span>
                  )}
                </button>
              ))}
            </div>
          ))}
        </div>

        {state.user && (
          <div className="sb-u">
            <div className="uav">{initials((state.user.first_name || '') + ' ' + (state.user.last_name || ''))}</div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: '#E5E7EB', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {state.user.first_name} {state.user.last_name}
              </div>
              <div style={{ fontSize: 10, color: '#6B7280', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {state.user.email}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Main */}
      <div className="main">
        <div className="topbar">
          <h1>{PAGE_TITLES[page] || page}</h1>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            {statusPill()}
            <div style={{ fontSize: 13, color: 'var(--tm)', fontWeight: 600 }}>{fmtNow()}</div>
          </div>
        </div>
        <div className="content">
          {renderPage()}
        </div>
      </div>

      {/* Mobile nav */}
      <div className="mobile-nav">
        <div className="mobile-nav-items">
          {mobileMainNav.map(item => (
            <button key={item.id}
              className={'mobile-nav-btn' + (page === item.id ? ' active' : '')}
              onClick={() => setPage(item.id)}>
              <span>{item.icon}</span>
              {item.label}
            </button>
          ))}
          <button className={'mobile-nav-btn' + (moreOpen ? ' active' : '')}
            onClick={() => setMoreOpen(!moreOpen)}>
            <span>&#8943;</span>
            Plus
          </button>
        </div>
      </div>

      {/* Mobile more drawer */}
      <div className={'mobile-more-overlay' + (moreOpen ? ' show' : '')}
        onClick={() => setMoreOpen(false)} />
      <div className={'mobile-more-drawer' + (moreOpen ? ' show' : '')}>
        <div className="mobile-more-handle" />
        <div className="mobile-more-grid">
          {mobileMoreItems.map(item => (
            <button key={item.id}
              className={'mobile-more-item' + (page === item.id ? ' active' : '')}
              onClick={() => setPage(item.id)}>
              <span>{item.icon}</span>
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {/* Modals */}
      {renderNewResaModal()}
      {renderEditResaModal()}

      {/* Onboarding */}
      {renderOnboarding()}

      {/* Help Assistant */}
      <HelpAssistant />
    </div>
  );
}
