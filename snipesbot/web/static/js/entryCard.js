import { api } from './api.js';
import { createChipInput } from './chipInput.js';
import { el, fromHtml, html } from './dom.js';
import { loadUsers } from './users.js';

// Every one of these mirrors what a Slack reaction can do, but -- the webapp
// being the admin surface -- none is restricted by the entry's current kind.
export const STATE_BUTTONS = [
  { kind: 'snipe', label: 'Snipe', cls: 'primary' },
  { kind: 'likely_snipe', label: 'Likely snipe', cls: '' },
  { kind: 'snope', label: 'Snope', cls: 'purple' },
  { kind: 'not_a_snipe', label: 'NAS', cls: 'danger' },
  { kind: 'unidentified', label: 'Unidentified', cls: '' },
  { kind: 'incorrect_snipe', label: 'Missnipe', cls: 'danger' },
  { kind: 'special', label: '🔎 Special', cls: '' },
];

const CONTEXT_STEP = 2;

/** One context message. Images never auto-load: they're click-to-load
 *  placeholders so a long queue doesn't pull down a pile of unseen photos. */
export function messageEl(m, extraClass = '') {
  const row = el('div', { class: 'ctx-msg ' + extraClass }, `${m.user}: `);
  for (const f of m.files || []) row.append(filePlaceholder(f), ' ');
  if (m.text) row.append(el('span', { text: m.text }));
  else if (!(m.files || []).length) row.append(el('em', { text: '(no text)' }));
  return row;
}

export function filePlaceholder(f) {
  const label = f.is_video ? 'VIDEO' : 'IMAGE';
  const btn = el('button', { type: 'button', class: 'img-placeholder' },
    `${f.is_video ? '🎥' : '📷'} [${label}]${f.name ? ' ' + f.name : ''} `,
    el('span', { class: 'img-hint', text: '(click to load)' }));
  btn.addEventListener('click', () => {
    const media = el(f.is_video ? 'video' : 'img', { class: 'ctx-inline-img', src: `/api/files/${encodeURIComponent(f.id)}` });
    if (f.is_video) { media.controls = true; media.preload = 'metadata'; } else media.loading = 'lazy';
    btn.replaceWith(media);
  });
  return btn;
}

function photoEl(entry) {
  if (!entry.photo_url) return el('div', { class: 'no-photo', text: 'No photo available for this one' });
  const media = el(entry.is_video ? 'video' : 'img', { src: entry.photo_url });
  if (entry.is_video) { media.controls = true; media.preload = 'metadata'; } else media.loading = 'lazy';
  return el('div', { class: 'photo-wrap' }, media);
}

/**
 * The card used by the Review Queue, Manual Decisions, Search, and the Live
 * Log edit modal. All state lives in this closure -- no global ids or caches.
 *
 *   onChange(updatedEntry)  after a state button succeeds
 *   onNotable(updatedEntry) after the notable toggle (optional)
 */
export async function createEntryCard(entry, { onChange, onNotable } = {}) {
  const users = await loadUsers();
  const card = fromHtml(html`
    <div class="card entry-card" data-id="${entry.id}">
      <div class="entry-header">
        <span>${entry.sniper}'s photo</span>
        <span class="badge ${entry.kind}">${entry.kind_label}</span>
        ${entry.manually_resolved ? html`<span class="badge manual">manual</span>` : ''}
        ${entry.permalink ? html`<a href="${entry.permalink}" target="_blank" rel="noopener">open in Slack</a>` : ''}
      </div>
      <div class="entry-stack">
        <div class="ctx-thread ctx-before"></div>
        <button type="button" class="btn load-more" data-more="before">↑ Load earlier</button>
        <div class="photo-slot"></div>
        <div class="extra-files"></div>
        <div class="ctx-msg caption-msg">📷 <strong class="who"></strong> <span class="caption"></span></div>
        <div class="ctx-thread ctx-after"></div>
        <button type="button" class="btn load-more" data-more="after">↓ Load later</button>
        <div class="replies-slot"></div>
      </div>
      <div class="resolve-row">
        <div class="chip-slot"></div>
      </div>
      <div class="review-controls state-buttons"></div>
    </div>`);

  card.querySelector('.who').textContent = `${entry.sniper}:`;
  const cap = card.querySelector('.caption');
  if (entry.caption) cap.textContent = entry.caption; else cap.append(el('em', { text: '(no caption)' }));
  card.querySelector('.photo-slot').replaceWith(photoEl(entry));
  const extras = card.querySelector('.extra-files');
  entry.extra_files.forEach((f) => extras.append(filePlaceholder(f), ' '));
  if (!entry.extra_files.length) extras.remove();

  // -- who was sniped
  const chips = createChipInput({
    users: () => users,
    initial: entry.guessed_ids.map((id, i) => ({ id, display: entry.guessed_display[i] })),
    placeholder: 'Sniped user(s) — type a name…',
  });
  card.querySelector('.chip-slot').replaceWith(chips.el);

  // -- context + replies (fetched after render so the list itself stays light)
  const counts = { before: CONTEXT_STEP, after: CONTEXT_STEP };
  const beforeEl = card.querySelector('.ctx-before');
  const afterEl = card.querySelector('.ctx-after');
  async function loadContext() {
    try {
      const ctx = await api(`/entries/${entry.id}/context?before=${counts.before}&after=${counts.after}`);
      beforeEl.replaceChildren(...ctx.before.map((m) => messageEl(m)));
      afterEl.replaceChildren(...ctx.after.map((m) => messageEl(m)));
    } catch { /* context is best-effort */ }
  }
  card.querySelectorAll('[data-more]').forEach((btn) => btn.addEventListener('click', () => {
    counts[btn.dataset.more] += CONTEXT_STEP;
    loadContext();
  }));
  loadContext();
  api(`/entries/${entry.id}/replies`).then(({ replies }) => {
    if (!replies.length) return;
    card.querySelector('.replies-slot').replaceChildren(
      el('div', { class: 'thread-label', text: 'Thread replies' }),
      ...replies.map((m) => messageEl(m, 'reply')));
  }).catch(() => {});

  // -- actions
  const buttons = card.querySelector('.state-buttons');
  const setBusy = (busy) => card.querySelectorAll('button').forEach((b) => { b.disabled = busy; });

  for (const s of STATE_BUTTONS) {
    const btn = el('button', { type: 'button', class: `btn ${s.cls}${s.kind === entry.kind ? ' active' : ''}`, text: s.label });
    btn.addEventListener('click', async () => {
      const sniped = s.kind === 'snipe' || s.kind === 'likely_snipe' ? chips.ids() : [];
      if (s.kind === 'snipe' && !sniped.length) {
        alert('Add at least one sniped user before confirming a snipe.');
        return;
      }
      setBusy(true);
      try {
        onChange?.(await api(`/entries/${entry.id}/kind`, { method: 'POST', body: { kind: s.kind, sniped } }));
      } catch (err) {
        alert(`Couldn't update that entry: ${err.message}`);
        setBusy(false);
      }
    });
    buttons.append(btn);
  }

  const star = el('button', { type: 'button', class: 'btn notable-toggle' });
  const paintStar = (on) => {
    star.classList.toggle('active', on);
    star.textContent = on ? '⭐ Notable' : '☆ Mark notable';
    star.dataset.on = on ? '1' : '0';
  };
  paintStar(entry.notable);
  star.addEventListener('click', async () => {
    star.disabled = true;
    try {
      const updated = await api(`/entries/${entry.id}/notable`, { method: 'POST', body: { notable: star.dataset.on !== '1' } });
      paintStar(updated.notable);   // notable is independent of kind: never removes the card
      onNotable?.(updated);
    } catch (err) {
      alert(`Couldn't update notable status: ${err.message}`);
    }
    star.disabled = false;
  });
  buttons.append(star);
  return card;
}
