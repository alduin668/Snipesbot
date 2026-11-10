// All server calls go through here. The X-Requested-With header is what the
// server's CSRF check looks for on every state-changing request.
export async function api(path, { method = 'GET', body } = {}) {
  const init = { method, headers: { 'X-Requested-With': 'snipesbot' } };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  const res = await fetch('/api' + path, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (HTTP ${res.status})`);
  return data;
}

export const query = (params) => {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') q.set(k, v);
  const s = q.toString();
  return s ? '?' + s : '';
};
