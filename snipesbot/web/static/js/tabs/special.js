import { api } from '../api.js';
import { createChipInput } from '../chipInput.js';
import { filePlaceholder } from '../entryCard.js';
import { el, empty, fromHtml, html, render } from '../dom.js';
import { loadUsers } from '../users.js';

const NEEDS_SNIPED = new Set(['snipe', 'snope']);

function card(entry, users, reload) {
  const node = fromHtml(html`
    <div class="card entry-card">
      <div class="entry-header"><span>${entry.sniper}'s photo</span> <span class="badge special">Special</span></div>
      <div class="entry-stack"><div class="media"></div><div class="ctx-msg caption-msg">📷 <strong>${entry.sniper}:</strong> <span class="caption"></span></div></div>
      <div class="derived"></div>
      <div class="special-form"></div>
      <div class="resolve-row"></div>
    </div>`);
  const media = node.querySelector('.media');
  if (entry.photo_url) {
    const m = el(entry.is_video ? 'video' : 'img', { src: entry.photo_url });
    if (entry.is_video) m.controls = true; else m.loading = 'lazy';
    media.append(el('div', { class: 'photo-wrap' }, m));
  } else media.append(el('div', { class: 'no-photo', text: 'No photo available for this one' }));
  entry.extra_files.forEach((f) => media.append(filePlaceholder(f), ' '));
  const cap = node.querySelector('.caption');
  if (entry.caption) cap.textContent = entry.caption; else cap.append(el('em', { text: '(no caption)' }));

  const derived = node.querySelector('.derived');
  if (!entry.derived_entries.length) derived.append(el('div', { class: 'review-mode-label', text: 'Nothing created from this one yet.' }));
  else {
    derived.className = 'special-derived';
    entry.derived_entries.forEach((d) => derived.append(el('div', { class: 'special-derived-row' },
      el('span', { class: `badge ${d.kind}`, text: d.kind_label }), el('strong', { text: d.sniper }),
      d.sniped_display.length ? el('span', { text: '→ ' + d.sniped_display.join(', ') }) : '')));
  }

  const kind = el('select', { class: 'special-kind' },
    ...[['snipe', 'Snipe'], ['snope', 'Snope'], ['not_a_snipe', 'NAS'], ['incorrect_snipe', 'Missnipe']]
      .map(([v, l]) => el('option', { value: v, text: l })));
  const sniper = createChipInput({ users: () => users, single: true, allowCustom: true, placeholder: 'Sniper — pick, or type + Enter for custom…' });
  const sniped = createChipInput({ users: () => users, allowCustom: true, placeholder: 'Sniped name(s) — pick, or type + Enter for custom…' });
  const create = el('button', { type: 'button', class: 'btn primary', text: 'Create' });
  const dim = () => { sniped.el.style.opacity = NEEDS_SNIPED.has(kind.value) ? '1' : '0.45'; };
  kind.addEventListener('change', dim);
  dim();
  node.querySelector('.special-form').append(kind, sniper.el, sniped.el, create);

  create.addEventListener('click', async () => {
    const [who] = sniper.ids();
    if (!who) { alert('Add a sniper first.'); return; }
    if (NEEDS_SNIPED.has(kind.value) && !sniped.ids().length) { alert('Add at least one sniped name for a snipe or snope.'); return; }
    create.disabled = true;
    try {
      await api(`/special/${entry.id}/create`, { method: 'POST', body: { kind: kind.value, sniper: who, sniped: sniped.ids() } });
      reload();
    } catch (err) { alert(`Couldn't create that: ${err.message}`); create.disabled = false; }
  });

  const dismiss = el('button', { type: 'button', class: 'btn', text: 'Dismiss — nothing here' });
  dismiss.addEventListener('click', async () => {
    if (!confirm('Dismiss this photo as not a snipe at all?')) return;
    dismiss.disabled = true;
    try { await api(`/special/${entry.id}/dismiss`, { method: 'POST' }); reload(); }
    catch (err) { alert(`Couldn't dismiss that: ${err.message}`); dismiss.disabled = false; }
  });
  node.querySelector('.resolve-row').append(dismiss);
  return node;
}

export function mount(panel) {
  const refresh = el('button', { type: 'button', class: 'btn', text: 'Refresh' });
  const list = el('div', { style: 'margin-top:14px' });
  panel.append(el('div', { class: 'review-controls' }, refresh,
    el('span', { class: 'review-mode-label', text: 'Photos flagged :fingerprint: — too niche for the detector. Build whatever snipe/snope/NAS/missnipe records fit, with any sniper/sniped name (a real person or anything else).' })), list);

  async function load() {
    render(list, 'Loading…');
    const [users, data] = await Promise.all([loadUsers(), api('/special')]);
    if (!data.entries.length) { render(list, empty('Nothing flagged special right now.')); return; }
    list.replaceChildren(...data.entries.map((e) => card(e, users, load)));
  }
  refresh.addEventListener('click', load);
  return { activate: load };
}
