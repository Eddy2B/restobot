let TOKEN = null;

export function getToken() {
  if (TOKEN) return TOKEN;
  try { TOKEN = sessionStorage.getItem('gs_token'); } catch (e) { /* */ }
  return TOKEN;
}

export function setToken(t) {
  TOKEN = t;
  try { sessionStorage.setItem('gs_token', t); } catch (e) { /* */ }
}

export function clearToken() {
  TOKEN = null;
  try { sessionStorage.removeItem('gs_token'); } catch (e) { /* */ }
}

export function apiFetch(url, opts = {}) {
  opts.headers = opts.headers || {};
  opts.credentials = 'include';  // Send httpOnly cookies
  const t = getToken();
  if (t) opts.headers['Authorization'] = 'Bearer ' + t;
  if (!opts.headers['Content-Type'] && opts.body) opts.headers['Content-Type'] = 'application/json';
  return fetch(url, opts);
}
