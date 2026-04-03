import { createContext, useContext, useReducer, useCallback, useRef, useEffect } from 'react';
import { apiFetch } from './api';
import { fmtDate } from './dateUtils';

const StoreContext = createContext();

const initialState = {
  user: null,
  bookings: [],
  contacts: {},
  conversations: {},
  floorplan: [],
  floorSlots: {},
  reviewQueue: [],
  waitlistEntries: [],
  menuSections: [],
  restaurantConfig: {},
  dailyMsg: '',
  selectedDate: fmtDate(new Date()),
  currentPage: 'overview',
  cancelledCount: 0,
  overviewBlocks: { daily: true, stats: true, floor: true, bookings: true, contacts: true },
  lastVersion: 0,
};

function reducer(state, action) {
  switch (action.type) {
    case 'SET_USER': return { ...state, user: action.payload };
    case 'SET_DATA': return { ...state, ...action.payload };
    case 'SET_PAGE': return { ...state, currentPage: action.payload };
    case 'SET_DATE': return { ...state, selectedDate: action.payload };
    case 'SET_CONFIG': return { ...state, restaurantConfig: { ...state.restaurantConfig, ...action.payload } };
    case 'SET_DAILY': return { ...state, dailyMsg: action.payload };
    case 'SET_MENU': return { ...state, menuSections: action.payload };
    case 'SET_BOOKINGS': return { ...state, bookings: action.payload };
    case 'SET_FLOORPLAN': return { ...state, floorplan: action.payload };
    case 'SET_WAITLIST': return { ...state, waitlistEntries: action.payload };
    case 'SET_OVERVIEW_BLOCK': return { ...state, overviewBlocks: { ...state.overviewBlocks, [action.key]: action.value } };
    case 'SET_VERSION': return { ...state, lastVersion: action.payload };
    case 'SET_CONTACTS': return { ...state, contacts: action.payload };
    default: return state;
  }
}

export function StoreProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const versionRef = useRef(0);

  const fetchData = useCallback(async () => {
    const ok = r => r.ok ? r.json() : null;
    try {
      const res = await Promise.all([
        apiFetch('/api/bookings').then(ok).catch(() => null),
        apiFetch('/api/contacts').then(ok).catch(() => null),
        apiFetch('/api/conversations').then(ok).catch(() => null),
        apiFetch('/api/floorplan').then(ok).catch(() => null),
        apiFetch('/api/reviews').then(ok).catch(() => null),
        apiFetch('/api/config').then(ok).catch(() => null),
        apiFetch('/api/daily').then(ok).catch(() => null),
        apiFetch('/api/menu').then(ok).catch(() => null),
        apiFetch('/api/waitlist').then(ok).catch(() => null),
      ]);
      const contacts = {};
      (res[1]?.contacts || []).forEach(c => { if (c.phone) contacts[c.phone] = c; });
      const conversations = {};
      (res[2]?.conversations || []).forEach(cv => { conversations[cv.phone || cv.id] = cv; });

      dispatch({
        type: 'SET_DATA',
        payload: {
          bookings: res[0]?.bookings || [],
          contacts,
          conversations,
          floorplan: res[3]?.tables || [],
          floorSlots: res[3]?.slots || {},
          reviewQueue: res[4]?.queue || [],
          restaurantConfig: res[5] || {},
          dailyMsg: res[6]?.message || '',
          menuSections: res[7]?.sections || [],
          waitlistEntries: res[8]?.waitlist || [],
        }
      });

      // Load reminders setting
      apiFetch('/api/settings').then(r => r.ok ? r.json() : null).then(s => {
        if (s && typeof s.reminders_enabled !== 'undefined') {
          dispatch({ type: 'SET_CONFIG', payload: { _reminders_enabled: s.reminders_enabled } });
        }
      }).catch(() => {});
    } catch (err) {
      console.error('Load error:', err);
    }
  }, []);

  const fetchDataSilent = useCallback(async () => {
    const ok = r => r.ok ? r.json() : null;
    try {
      const res = await Promise.all([
        apiFetch('/api/bookings').then(ok).catch(() => null),
        apiFetch('/api/contacts').then(ok).catch(() => null),
        apiFetch('/api/conversations').then(ok).catch(() => null),
        apiFetch('/api/floorplan').then(ok).catch(() => null),
        apiFetch('/api/reviews').then(ok).catch(() => null),
        apiFetch('/api/daily').then(ok).catch(() => null),
        apiFetch('/api/menu').then(ok).catch(() => null),
        apiFetch('/api/waitlist').then(ok).catch(() => null),
      ]);
      const payload = {};
      if (res[0]) payload.bookings = res[0].bookings || [];
      if (res[1]) {
        const contacts = {};
        (res[1].contacts || []).forEach(c => { if (c.phone) contacts[c.phone] = c; });
        payload.contacts = contacts;
      }
      if (res[2]) {
        const conversations = {};
        (res[2].conversations || []).forEach(cv => { conversations[cv.phone || cv.id] = cv; });
        payload.conversations = conversations;
      }
      if (res[3]) { payload.floorplan = res[3].tables || []; payload.floorSlots = res[3].slots || {}; }
      if (res[4]) payload.reviewQueue = res[4].queue || [];
      if (res[5]) payload.dailyMsg = res[5].message || '';
      if (res[6]) payload.menuSections = res[6].sections || [];
      if (res[7]) payload.waitlistEntries = res[7].waitlist || [];
      dispatch({ type: 'SET_DATA', payload });
    } catch (e) { /* silent */ }
  }, []);

  // Poll for version changes
  useEffect(() => {
    if (!state.user) return;
    const interval = setInterval(async () => {
      try {
        const r = await apiFetch('/api/version');
        const d = await r.json();
        if (d.v && d.v !== versionRef.current) {
          versionRef.current = d.v;
          fetchDataSilent();
        }
      } catch (e) { /* */ }
    }, 3000);
    return () => clearInterval(interval);
  }, [state.user, fetchDataSilent]);

  return (
    <StoreContext.Provider value={{ state, dispatch, fetchData, fetchDataSilent }}>
      {children}
    </StoreContext.Provider>
  );
}

export function useStore() {
  return useContext(StoreContext);
}
