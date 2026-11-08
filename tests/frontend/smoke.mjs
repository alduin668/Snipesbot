import { JSDOM } from 'jsdom';

const BASE = process.env.BASE_URL || 'http://127.0.0.1:8766';
const page = await (await fetch(BASE + '/')).text();
const dom = new JSDOM(page.replace(/<script[^>]*><\/script>/, ''), { url: BASE + '/', pretendToBeVisual: true });
const { window } = dom;
Object.assign(globalThis, { window, document: window.document, location: window.location, history: window.history });
const alerts = [];
globalThis.alert = (m) => alerts.push(String(m));
globalThis.confirm = () => true;
globalThis.fetch = ((orig) => (u, init) => orig(typeof u === 'string' && u.startsWith('/') ? BASE + u : u, init))(globalThis.fetch);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
let failures = 0;
const check = (name, ok, extra = '') => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : '  ' + extra}`); if (!ok) failures++; };
const api = async (p, o) => (await fetch(BASE + '/api' + p, { headers: { 'X-Requested-With': 'snipesbot' }, ...o })).json();
const tab = async (name, wait = 600) => { $(`.tab-btn[data-tab="${name}"]`).click(); await sleep(wait); return $(`#tab-${name}`); };

await import(process.env.JS_MAIN || '../../snipesbot/web/static/js/main.js');
await sleep(800);

// ---------------- every tab renders without error ----------------
for (const name of ['livelog', 'review', 'manual', 'special', 'search', 'dns', 'analysis', 'matrix', 'adjustments', 'maintenance']) {
  const panel = await tab(name, 900);
  const text = panel.textContent;
  check(`tab "${name}" renders`, panel.classList.contains('active') && text.trim().length > 0 && !text.includes('Something went wrong'), text.slice(0, 120));
}

// ---------------- hostile Slack content never becomes markup ----------------
const imgsX = () => $$('img').filter((i) => i.getAttribute('src') === 'x');
await tab('livelog'); await tab('review'); await tab('manual', 1200); await tab('search', 1200);
check('no <img src=x> injected from captions/context/file names', imgsX().length === 0, `${imgsX().length} found`);
check('no <script> injected from display names', !$$('script').some((s) => s.textContent.includes('__pwned')));
check('window.__pwned never set', window.__pwned === undefined);
check('hostile caption is shown as literal text', document.body.textContent.includes('<img src=x onerror=window.__pwned=1> hostile caption'));
check('hostile display name is shown as literal text', document.body.textContent.includes('<script>window.__pwned=1</script>Bob'));

// ---------------- Live Log ----------------
const live = await tab('livelog');
check('live log lists all 5 entries', $$('tbody tr', live).length === 5, String($$('tbody tr', live).length));
check('live log has the export link', !!$('a[href="/api/export.xlsx"]', live));
$$('button', live).find((b) => b.textContent === 'Edit').click();
await sleep(1200);
check('Edit opens a card in the modal', $('#modal-overlay').style.display === 'flex' && !!$('#modal-body .entry-card'));
check('modal card loaded context', $$('#modal-body .ctx-msg').length >= 2);
$('#modal-close').click();

// ---------------- Review Queue: resolve with guessed chip ----------------
let review = await tab('review', 1200);
check('review queue shows the 3 pending photos', $$('.entry-card', review).length === 3, String($$('.entry-card', review).length));
const likely = $$('.entry-card', review).find((c) => c.querySelector('.badge.likely_snipe'));
check('guessed mention is pre-filled as a chip', !!likely && likely.querySelectorAll('.chip').length === 1);
$$('button', likely).find((b) => b.textContent === 'Snipe').click();
await sleep(1200);
check('resolving removes the card from the queue', $$('.entry-card', review).length === 2);
const after = await api('/entries?pending=1');
check('server agrees: two pending left', after.total === 2);

// snipe with nobody selected is blocked client-side
const unid = $$('.entry-card', review).find((c) => c.querySelector('.badge.unidentified'));
alerts.length = 0;
$$('button', unid).find((b) => b.textContent === 'Snipe').click();
await sleep(200);
check('confirming a snipe with no sniped user is refused', alerts.some((a) => a.includes('at least one sniped user')));

// one-at-a-time paging
const sel = $('select', review); sel.value = '1'; sel.dispatchEvent(new window.Event('change')); await sleep(800);
check('"One at a time" shows a single card with an i / n counter', $$('.entry-card', review).length === 1 && /\d+ \/ \d+/.test(review.textContent));

// ---------------- Manual Decisions: edit in place ----------------
let manual = await tab('manual', 1200);
const before = $$('.entry-card', manual).length;
check('manual decisions lists the resolved snipe and the special photo', before === 2, String(before));
const snipeCard = $$('.entry-card', manual).find((c) => c.querySelector('.badge.snipe'));
$$('button', snipeCard).find((b) => b.textContent === 'NAS').click();
await sleep(1200);
check('changing a manual decision redraws the card in place', $$('.entry-card', manual).length === before && !!$('.badge.not_a_snipe', manual));

// ---------------- Notable toggle never removes the card ----------------
const star = $('.notable-toggle', manual); star.click(); await sleep(800);
check('notable toggle updates in place', $$('.entry-card', manual).length === before && $('.notable-toggle', manual).textContent.includes('Notable'));

// ---------------- Search ----------------
const search = await tab('search', 1500);
check('search lists everything by default', $$('.entry-card', search).length === 5, String($$('.entry-card', search).length));
$('#s-q', search).value = 'hostile';
$$('button', search).find((b) => b.textContent === 'Search').click(); await sleep(1200);
check('caption search narrows results', $$('.entry-card', search).length === 1 && search.textContent.includes('1 match'));
$$('button', search).find((b) => b.textContent === 'Clear filters').click(); await sleep(1200);
check('clear filters restores results', $$('.entry-card', search).length === 5);
check('sniper filter is populated from cached names', $$('#s-sniper option', search).length > 1);

// ---------------- Special tab ----------------
const special = await tab('special', 1200);
check('special tab shows the flagged photo', $$('.entry-card', special).length === 1);
const sc = $('.entry-card', special);
const inputs = $$('.chip-search', sc);
inputs[0].value = 'deer'; inputs[0].dispatchEvent(new window.Event('input')); await sleep(50);
$('.chip-option-custom', sc).dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true }));
inputs[1].value = 'Alice'; inputs[1].dispatchEvent(new window.Event('input')); await sleep(50);
$$('.chip-option', sc).find((o) => !o.classList.contains('chip-option-custom')).dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true }));
$$('button', sc).find((b) => b.textContent === 'Create').click(); await sleep(1500);
check('special create adds a derived record', $$('.special-derived-row', $('#tab-special')).length === 1 && $('#tab-special').textContent.includes('deer'));

// ---------------- Do Not Snipe ----------------
const dns = await tab('dns', 900);
const dIn = $('.chip-search', dns); dIn.value = 'Alice'; dIn.dispatchEvent(new window.Event('input')); await sleep(50);
$('.chip-option', dns).dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true })); await sleep(900);
check('adding to the do-not-snipe list works', (await api('/dns')).users.length === 1 && dns.textContent.includes('Alice'));
$$('button', dns).find((b) => b.textContent === 'Remove').click(); await sleep(800);
check('removing works', (await api('/dns')).users.length === 0);

// ---------------- Matrix + Adjustments ----------------
const matrix = await tab('matrix', 900);
check('coded matrix is read-only', $$('.cell-input', matrix).length === 0 && !!$('table.matrix-table', matrix));
$$('.matrix-subtab-btn', matrix).find((b) => b.textContent === 'True').click(); await sleep(900);
const cell = $('.cell-input', matrix);
check('true matrix is editable', !!cell);
cell.value = '5'; cell.dispatchEvent(new window.Event('change')); await sleep(800);
check('editing a true cell persists', JSON.stringify((await api('/matrix/true')).cells).includes(':5'));
const adj = await tab('adjustments', 900);
const ai = $('.adj-input', adj); ai.value = '2.5'; ai.dispatchEvent(new window.Event('change')); await sleep(800);
check('adjustment saves', (await api('/adjustments')).users.some((u) => u.adjustment === 2.5));
const an = await tab('analysis', 900);
check('analysis shows the leaderboard', an.textContent.includes('Leaderboard') && an.textContent.includes('Rivalries'));

// ---------------- Maintenance ----------------
const m = await tab('maintenance', 900);
check('maintenance lists the consolidated tasks', $$('.card', m).length === 5, String($$('.card', m).length));
const diag = $$('.card', m).find((c) => c.textContent.includes('Diagnose'));
$('button', diag).click(); await sleep(2800);
check('running a task streams its output', $('.console', m).textContent.includes('auth.test OK') && $('.console', m).textContent.includes('[finished]'));

check('no unexpected alert() dialogs', alerts.filter((a) => !a.includes('at least one sniped user')).length === 0, alerts.join(' | '));
console.log(failures ? `\n${failures} FAILED` : '\nall frontend checks passed');
process.exit(failures ? 1 : 0);
