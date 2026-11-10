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

const clock = (seconds) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;

let pillTicker = null;

/** The pill in the header: visible from every tab whenever something is running.
 *  It ticks once a second on its own between server updates. */
function showActivity(activity) {
  const pill = $('#activity');
  const running = activity?.running;
  clearInterval(pillTicker);
  if (!running && !activity?.external_sync) { pill.hidden = true; return; }
  pill.hidden = false;
  const text = pill.querySelector('.activity-text');
  if (!running) { text.textContent = 'Sync running (bot)'; return; }
  const started = Date.now() - running.elapsed * 1000;
  const paint = () => { text.textContent = `${running.label} running · ${clock((Date.now() - started) / 1000)}`; };
  paint();
  pillTicker = setInterval(paint, 1000);
}

async function refreshMeta() {
  try {
    const s = await api('/status');
    const review = s.needs_review ? ` • ${s.needs_review} to review` : '';
    $('#meta').textContent = `${s.entries} entries${review} • last sync ${s.last_sync ? new Date(s.last_sync).toLocaleString() : 'never'}`;
    showActivity(s.activity);
  } catch {
    $('#meta').textContent = '⚠️ couldn’t reach the server, retrying…';
  }
}

initModal();
$$('.tab-btn').forEach((b) => b.addEventListener('click', () => show(b.dataset.tab)));
show(location.hash.slice(1) in TABS ? location.hash.slice(1) : 'livelog');
refreshMeta();
setInterval(() => { if (!document.hidden) refreshMeta(); }, 5000);
window.addEventListener('snipesbot:activity', refreshMeta);          // a task just started or finished
$('#activity').addEventListener('click', () => show('maintenance'));
