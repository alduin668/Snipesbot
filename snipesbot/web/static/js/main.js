import { api } from './api.js';
import { $, $$ } from './dom.js';
import { initModal } from './modal.js';

const TABS = {
  livelog: () => import('./tabs/livelog.js'),
  review: () => import('./tabs/review.js'),
  manual: () => import('./tabs/manual.js'),
  special: () => import('./tabs/special.js'),
  search: () => import('./tabs/search.js'),
  dns: () => import('./tabs/dns.js'),
  analysis: () => import('./tabs/analysis.js'),
  matrix: () => import('./tabs/matrix.js'),
  adjustments: () => import('./tabs/adjustments.js'),
  maintenance: () => import('./tabs/maintenance.js'),
};

const mounted = {};

async function show(name) {
  $$('.tab-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
  $$('.tab-content').forEach((p) => p.classList.toggle('active', p.id === `tab-${name}`));
  history.replaceState(null, '', `#${name}`);
  try {
    if (!mounted[name]) mounted[name] = (await TABS[name]()).mount($(`#tab-${name}`));
    await mounted[name].activate();
  } catch (err) {
    $(`#tab-${name}`).append(Object.assign(document.createElement('div'), { className: 'empty', textContent: `Something went wrong: ${err.message}` }));
  }
}

async function refreshMeta() {
  try {
    const s = await api('/status');
    $('#meta').textContent = `${s.entries} entries • last sync ${s.last_sync ? new Date(s.last_sync).toLocaleString() : 'never'}`;
  } catch {
    $('#meta').textContent = '⚠️ couldn’t reach the server, retrying…';
  }
}

initModal();
$$('.tab-btn').forEach((b) => b.addEventListener('click', () => show(b.dataset.tab)));
show(location.hash.slice(1) in TABS ? location.hash.slice(1) : 'livelog');
refreshMeta();
setInterval(() => { if (!document.hidden) refreshMeta(); }, 15000);
