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
    media.addEventListener('error', () => media.replaceWith(el('span', { class: 'img-hint', text: '(file unavailable)' })));
    btn.replaceWith(media);
  });
  return btn;
}

const MISSING_FILE = "This photo's file isn't in the archive (it may have aged out of Slack, or wasn't "
  + 'copied over when migrating). On the server run:  python -m snipesbot diagnose';

/** The entry's main photo/video, or a plain explanation when the file can't be served. */
export function photoEl(entry) {
  if (!entry.photo_url) return el('div', { class: 'no-photo', text: 'No photo available for this one' });
  const media = el(entry.is_video ? 'video' : 'img', { src: entry.photo_url });
  if (entry.is_video) { media.controls = true; media.preload = 'metadata'; } else media.loading = 'lazy';
  const wrap = el('div', { class: 'photo-wrap' }, media);
  media.addEventListener('error', () => wrap.replaceWith(el('div', { class: 'no-photo', text: MISSING_FILE })));
  return wrap;
}

/**
 * The card used by the Review Queue, Manual Decisions, Search, and the Live
 * Log edit modal. All state lives in this closure -- no global ids or caches.
 *
 *   layout: 'stack'  chat order: earlier messages, photo, caption, later messages,
 *                    then the controls. Used when many cards are listed.
 *           'focus'  one screen: photo on the left; the conversation scrolls beside it;
 *                    the chips and buttons stay pinned underneath. Used for one-at-a-time.
 *   onChange(updatedEntry)  after a state button succeeds
 *   onNotable(updatedEntry) after the notable toggle (optional)
 */
export async function createEntryCard(entry, { onChange, onNotable, layout = 'stack' } = {}) {
  const users = await loadUsers();
  const focus = layout === 'focus';

  // ---- the parts (assembled differently below) ------------------------------------------------
  const header = fromHtml(html`
    <div class="entry-header">
      <span class="entry-title"></span>
      <span class="badge ${entry.kind}${entry.incomplete ? ' incomplete' : ''}">${entry.kind_label}</span>
      ${entry.manually_resolved ? html`<span class="badge manual">manual</span>` : ''}
      ${entry.no_points_ids.length ? html`<span class="badge nopoints" title="A snipe of someone on the do-not-snipe list is logged, but earns nobody points.">no points · ${entry.no_points_display.join(', ')} is do-not-snipe</span>` : ''}
      ${entry.permalink ? html`<a href="${entry.permalink}" target="_blank" rel="noopener">open in Slack</a>` : ''}
    </div>`);
  header.querySelector('.entry-title').textContent = `${entry.sniper}'s photo`;

  const beforeEl = el('div', { class: 'ctx-thread ctx-before' });
  const afterEl = el('div', { class: 'ctx-thread ctx-after' });
  const earlier = el('button', { type: 'button', class: 'btn load-more', text: '↑ Load earlier' });
  const later = el('button', { type: 'button', class: 'btn load-more', text: '↓ Load later' });
  const repliesEl = el('div', { class: 'replies-slot' });
  const photo = photoEl(entry);
  const extras = el('div', { class: 'extra-files' });
  entry.extra_files.forEach((f) => extras.append(filePlaceholder(f), ' '));
  const caption = fromHtml(html`<div class="ctx-msg caption-msg">📷 <strong class="who"></strong> <span class="caption"></span></div>`);
  caption.querySelector('.who').textContent = `${entry.sniper}:`;
  const cap = caption.querySelector('.caption');
  if (entry.caption) cap.textContent = entry.caption; else cap.append(el('em', { text: '(no caption)' }));

  const chips = createChipInput({
    users: () => users,
    initial: entry.guessed_ids.map((id, i) => ({ id, display: entry.guessed_display[i] })),
    placeholder: 'Sniped user(s) — type a name…',
  });
  const chipRow = el('div', { class: 'resolve-row' }, chips.el);
  const buttons = el('div', { class: 'review-controls state-buttons' });

  // ---- context + replies (fetched after render so the list itself stays light) -----------------------
  const counts = { before: CONTEXT_STEP, after: CONTEXT_STEP };
  const thread = focus ? el('div', { class: 'focus-thread' }) : null;
  async function loadContext() {
    try {
      const ctx = await api(`/entries/${entry.id}/context?before=${counts.before}&after=${counts.after}`);
      beforeEl.replaceChildren(...ctx.before.map((m) => messageEl(m)));
      afterEl.replaceChildren(...ctx.after.map((m) => messageEl(m)));
      // In the one-screen layout, keep the photo's own caption in view as context loads around it.
      if (thread) thread.scrollTop = Math.max(0, caption.offsetTop - thread.clientHeight / 3);
    } catch { /* context is best-effort */ }
  }
  earlier.addEventListener('click', () => { counts.before += CONTEXT_STEP; loadContext(); });
  later.addEventListener('click', () => { counts.after += CONTEXT_STEP; loadContext(); });
  api(`/entries/${entry.id}/replies`).then(({ replies }) => {
    if (!replies.length) return;
    repliesEl.replaceChildren(el('div', { class: 'thread-label', text: 'Thread replies' }),
      ...replies.map((m) => messageEl(m, 'reply')));
  }).catch(() => {});

  // ---- actions -----------------------------------------------------------------------------------------
  const card = el('div', { class: `card entry-card${focus ? ' focus' : ''}`, 'data-id': entry.id });
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

  // ---- assembly -------------------------------------------------------------------------------------------
  if (focus) {
    thread.append(beforeEl, earlier, caption, afterEl, later, repliesEl);
    card.append(header,
      el('div', { class: 'focus-body' },
        el('div', { class: 'focus-media' }, photo, extras),
        el('div', { class: 'focus-side' }, thread, el('div', { class: 'focus-controls' }, chipRow, buttons))));
  } else {
    card.append(header,
      el('div', { class: 'entry-stack' }, beforeEl, earlier, photo, extras, caption, afterEl, later, repliesEl),
      chipRow, buttons);
    if (!entry.extra_files.length) extras.remove();
  }
  loadContext();
  return card;
}
