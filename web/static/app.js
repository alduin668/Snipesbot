// ---------- tabs ----------
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'review') loadReviewQueue();
    if (btn.dataset.tab === 'manual') loadManualDecisions();
    if (btn.dataset.tab === 'special') loadSpecial();
    if (btn.dataset.tab === 'search') loadSearchTab();
    if (btn.dataset.tab === 'dns') loadDns();
    if (btn.dataset.tab === 'analysis') loadAnalysis();
    if (btn.dataset.tab === 'matrix') loadMatrix();
    if (btn.dataset.tab === 'adjustments') loadAdjustments();
    if (btn.dataset.tab === 'scripts') loadScripts();
  });
});

// ---------- shared: entry state buttons ----------
// Every one of these mirrors what a Slack reaction can do (see
// bot/reactions.py) -- these are just buttons instead, and unlike the
// reactions, none of them are restricted by the entry's current kind (e.g.
// Missnipe works from anything, not just a confirmed snipe) -- the webapp
// is the admin surface, so every state is reachable from every other one.
const STATE_BUTTONS = [
  { kind: 'snipe', label: "Snipe", cls: 'primary' },
  { kind: 'likely_snipe', label: "Likely snipe", cls: '' },
  { kind: 'snope', label: "Snope", cls: 'purple' },
  { kind: 'not_a_snipe', label: "NAS", cls: 'danger' },
  { kind: 'unidentified', label: "Unidentified", cls: '' },
  { kind: 'incorrect_snipe', label: "Missnipe", cls: 'danger' },
  { kind: 'special', label: "🔎 Special", cls: '' },
];

const KIND_LABELS = {
  unidentified: 'Unidentified', likely_snipe: 'Likely snipe', snipe: 'Snipe',
  snope: 'Snope', not_a_snipe: 'NAS', incorrect_snipe: 'Missnipe', special: 'Special',
};

let ALL_USERS = [];       // [{id, name, display}], for the sniped-user autocomplete
let SNIPED_CHIPS = {};    // scoped-id -> [{id, display}] currently selected
let SPECIAL_CHIPS = {};   // "special-sniped-<id>" -> [{id, display}] -- see the Special tab below
let REPLIES_CACHE = {};   // entry id -> Promise<replies[]>, so navigating back never re-fetches

async function ensureUsersLoaded() {
  if (ALL_USERS.length) return;
  const res = await fetch('/api/users');
  ALL_USERS = (await res.json()).users || [];
}

// Thread replies are fetched one entry at a time via /api/review-queue/<id>/replies
// (see routes.py -- fetching them inline for a whole list meant one Slack call
// per entry before the page could render). Capping concurrency keeps a big
// page from firing 100+ requests at Slack simultaneously.
const REPLY_FETCH_CONCURRENCY = 4;
let _replyQueue = [];
let _replyActive = 0;

function fetchReplies(entryId) {
  if (REPLIES_CACHE[entryId]) return REPLIES_CACHE[entryId];
  const p = new Promise(resolve => {
    _replyQueue.push({ entryId, resolve });
    _drainReplyQueue();
  });
  REPLIES_CACHE[entryId] = p;
  return p;
}

function _drainReplyQueue() {
  while (_replyActive < REPLY_FETCH_CONCURRENCY && _replyQueue.length) {
    const { entryId, resolve } = _replyQueue.shift();
    _replyActive++;
    fetch(`/api/review-queue/${entryId}/replies`)
      .then(r => r.json())
      .then(d => resolve(d.replies || []))
      .catch(() => resolve([]))
      .finally(() => { _replyActive--; _drainReplyQueue(); });
  }
}

function scopedId(scope, suffix, id) { return `${scope}-${suffix}-${id}`; }

// One context message: text and/or an image. Images never auto-load --
// they render as a "[IMAGE]" button and only fetch bytes on click, so
// scrolling through a big queue doesn't pull down a pile of photos nobody
// asked to see yet.
// A message can carry more than one image/video -- this renders a
// click-to-load placeholder for each ADDITIONAL one beyond whichever is
// already shown as the primary photo/video. Reuses the exact same
// .img-placeholder markup wireImagePlaceholders() already knows how to
// wire up, so nothing extra is needed wherever this gets inserted as long
// as the container it lands in already gets passed to that function.
function extraFilesHtml(files) {
  if (!files || !files.length) return '';
  return files.map(f => {
    const icon = f.is_video ? '🎥' : '📷';
    const label = f.is_video ? 'VIDEO' : 'IMAGE';
    return `<button type="button" class="img-placeholder" data-file="${f.id}" data-video="${f.is_video ? '1' : '0'}">${icon} [${label}]${f.name ? ' ' + f.name : ''} <span class="img-hint">(click to load)</span></button>`;
  }).join(' ');
}

function ctxMsgHtml(m, extraClass) {
  const parts = [];
  if (m.file_id) {
    const isVideo = !!m.file_is_video;
    const icon = isVideo ? '🎥' : '📷';
    const label = isVideo ? 'VIDEO' : 'IMAGE';
    parts.push(
      `<button type="button" class="img-placeholder" data-file="${m.file_id}" data-video="${isVideo ? '1' : '0'}">${icon} [${label}]${m.file_name ? ' ' + m.file_name : ''} <span class="img-hint">(click to load)</span></button>`
    );
  }
  if (m.extra_files && m.extra_files.length) parts.push(extraFilesHtml(m.extra_files));
  if (m.text) parts.push(`<span>${m.text}</span>`);
  if (!parts.length) parts.push('<em>(no text)</em>');
  return `<div class="ctx-msg${extraClass ? ' ' + extraClass : ''}">${m.user}: ${parts.join(' ')}</div>`;
}

function wireImagePlaceholders(container) {
  container.querySelectorAll('.img-placeholder').forEach(btn => {
    btn.addEventListener('click', () => {
      const fileId = btn.dataset.file;
      const el = document.createElement(btn.dataset.video === '1' ? 'video' : 'img');
      el.className = 'ctx-inline-img';
      if (btn.dataset.video === '1') {
        el.controls = true;
        el.preload = 'metadata';
      } else {
        el.loading = 'lazy';
      }
      el.src = `/api/slack-file/${fileId}`;
      btn.replaceWith(el);
    });
  });
}

function contextSectionHtml(scope, e, direction) {
  const msgs = direction === 'before' ? e.context_before : e.context_after;
  const cls = direction === 'before' ? 'ctx-before' : 'ctx-after';
  const arrow = direction === 'before' ? '↑' : '↓';
  const label = direction === 'before' ? 'Load earlier' : 'Load later';
  const body = msgs.map(m => ctxMsgHtml(m)).join('');
  return `
    <div class="ctx-thread ${cls}" id="${scopedId(scope, cls, e.id)}">${body}</div>
    <button type="button" class="btn load-more" data-loadmore="${direction}" data-scope="${scope}" data-id="${e.id}">${arrow} ${label}</button>`;
}

// ---------- shared: entry card ----------
function entryCardHtml(scope, e) {
  const photo = e.photo_url
    ? (e.is_video
        ? `<div class="photo-wrap"><video src="${e.photo_url}" controls preload="metadata"></video></div>`
        : `<div class="photo-wrap"><img src="${e.photo_url}" loading="lazy"></div>`)
    : `<div class="no-photo">No photo downloaded for this one</div>`;
  const extra = (e.extra_files && e.extra_files.length)
    ? `<div class="extra-files">${extraFilesHtml(e.extra_files)}</div>`
    : '';
  const badge = `<span class="badge ${e.kind}">${KIND_LABELS[e.kind] || e.kind}</span>`;
  const manualBadge = e.manually_resolved ? `<span class="badge manual">manual</span>` : '';
  const notableBtn = `<button type="button" class="btn notable-toggle${e.notable ? ' active' : ''}" data-notable="${e.notable ? '0' : '1'}" data-scope="${scope}" data-id="${e.id}">${e.notable ? '⭐ Notable' : '☆ Mark notable'}</button>`;

  const buttons = STATE_BUTTONS.map(b => {
    const active = b.kind === e.kind ? ' active' : '';
    return `<button class="btn ${b.cls}${active}" data-action="${b.kind}" data-scope="${scope}" data-id="${e.id}">${b.label}</button>`;
  }).join('');

  return `
    <div class="card entry-card" id="${scopedId(scope, 'entry', e.id)}">
      <div class="entry-header">${e.sniper}'s photo ${badge}${manualBadge} ${notableBtn}</div>
      <div class="entry-stack">
        ${contextSectionHtml(scope, e, 'before')}
        ${photo}
        ${extra}
        <div class="ctx-msg caption-msg">📷 <strong>${e.sniper}:</strong> ${e.photo_text || '<em>(no caption)</em>'}</div>
        ${contextSectionHtml(scope, e, 'after')}
        <div id="${scopedId(scope, 'replies', e.id)}" class="replies-slot"></div>
      </div>
      <div class="resolve-row">
        <div class="chip-input" id="${scopedId(scope, 'chipwrap', e.id)}">
          <div class="chips" id="${scopedId(scope, 'chips', e.id)}"></div>
          <input type="text" class="chip-search" id="${scopedId(scope, 'chipsearch', e.id)}" placeholder="Sniped user(s)…" autocomplete="off">
          <div class="chip-dropdown" id="${scopedId(scope, 'chipdropdown', e.id)}"></div>
        </div>
      </div>
      <div class="resolve-row state-buttons">${buttons}</div>
    </div>`;
}

function renderChips(scope, id) {
  const key = scopedId(scope, 'chips', id);
  const container = document.getElementById(key);
  if (!container) return;
  container.innerHTML = (SNIPED_CHIPS[key] || []).map(c =>
    `<span class="chip">${c.display}<button type="button" class="chip-x" data-remove="${c.id}">×</button></span>`
  ).join('');
  container.querySelectorAll('.chip-x').forEach(btn => {
    btn.addEventListener('click', () => {
      SNIPED_CHIPS[key] = (SNIPED_CHIPS[key] || []).filter(c => c.id !== btn.dataset.remove);
      renderChips(scope, id);
    });
  });
}

function wireChipInput(scope, e) {
  const key = scopedId(scope, 'chips', e.id);
  SNIPED_CHIPS[key] = (e.guessed_sniped_ids || []).map((id, i) => ({ id, display: e.guessed_sniped_display[i] }));
  renderChips(scope, e.id);

  const searchInput = document.getElementById(scopedId(scope, 'chipsearch', e.id));
  const dropdown = document.getElementById(scopedId(scope, 'chipdropdown', e.id));
  if (!searchInput) return;

  function closeDropdown() { dropdown.style.display = 'none'; dropdown.innerHTML = ''; }
  function selectUser(u) {
    SNIPED_CHIPS[key] = SNIPED_CHIPS[key] || [];
    if (!SNIPED_CHIPS[key].some(c => c.id === u.id)) {
      SNIPED_CHIPS[key].push({ id: u.id, display: u.display });
      renderChips(scope, e.id);
    }
    searchInput.value = '';
    closeDropdown();
    searchInput.focus();
  }

  searchInput.addEventListener('input', () => {
    const q = searchInput.value.trim().toLowerCase();
    if (!q) { closeDropdown(); return; }
    const chosen = new Set((SNIPED_CHIPS[key] || []).map(c => c.id));
    const matches = ALL_USERS
      .filter(u => !chosen.has(u.id) && (u.name || u.id).toLowerCase().includes(q))
      .slice(0, 8);
    if (!matches.length) {
      dropdown.innerHTML = '<div class="chip-empty">No matching names yet</div>';
      dropdown.style.display = 'block';
      return;
    }
    dropdown.innerHTML = matches.map(u => `<div class="chip-option" data-id="${u.id}">${u.display}</div>`).join('');
    dropdown.style.display = 'block';
    dropdown.querySelectorAll('.chip-option').forEach(opt => {
      opt.addEventListener('mousedown', (ev) => {
        ev.preventDefault();
        const u = ALL_USERS.find(u => u.id === opt.dataset.id);
        if (u) selectUser(u);
      });
    });
  });

  searchInput.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') ev.preventDefault();
    if (ev.key === 'Escape') closeDropdown();
  });
  searchInput.addEventListener('blur', () => closeDropdown());
}

// `onResolved(entryId, updatedEntry)` lets each tab decide what happens to
// its own list (remove from the queue, refresh in place, etc).
function wireEntryCard(scope, e, onResolved) {
  wireChipInput(scope, e);
  loadRepliesInto(scope, e);
  ensureContextInto(scope, e);

  const card = document.getElementById(scopedId(scope, 'entry', e.id));
  if (!card) return;

  wireImagePlaceholders(card);

  card.querySelectorAll('button[data-loadmore]').forEach(btn => {
    btn.addEventListener('click', () => loadMoreContext(scope, btn.dataset.id, btn.dataset.loadmore));
  });

  const notableBtn = card.querySelector('button.notable-toggle');
  if (notableBtn) {
    notableBtn.addEventListener('click', async () => {
      const wantNotable = notableBtn.dataset.notable === '1';
      notableBtn.disabled = true;
      const res = await fetch(`/api/entries/${e.id}/set-notable`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notable: wantNotable }),
      });
      if (res.ok) {
        const updated = await res.json();
        // Notable is independent of an entry's kind -- unlike the state
        // buttons, toggling it should never remove a card from whichever
        // list it's in, so this updates in place instead of going through
        // onResolved (which Review Queue treats as "this entry is done").
        e.notable = updated.notable;
        notableBtn.classList.toggle('active', updated.notable);
        notableBtn.textContent = updated.notable ? '⭐ Notable' : '☆ Mark notable';
        notableBtn.dataset.notable = updated.notable ? '0' : '1';
        notableBtn.disabled = false;
        refreshLiveLog();
      } else {
        alert("Couldn't update notable status.");
        notableBtn.disabled = false;
      }
    });
  }

  card.querySelectorAll('button[data-action]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const kind = btn.dataset.action;
      const sniped = (kind === 'snipe' || kind === 'likely_snipe')
        ? (SNIPED_CHIPS[scopedId(scope, 'chips', e.id)] || []).map(c => c.id)
        : [];
      // Confirming a snipe with nobody selected as sniped is exactly how
      // the log ends up with malformed rows (a sniper but no victim) --
      // require at least one name in the box before it's allowed through.
      if (kind === 'snipe' && sniped.length === 0) {
        alert("Add at least one sniped user before confirming a snipe.");
        return;
      }
      card.querySelectorAll('button').forEach(b => b.disabled = true);
      const res = await fetch(`/api/entries/${e.id}/set-kind`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, sniped }),
      });
      if (res.ok) {
        const updated = await res.json();
        onResolved(e.id, updated);
      } else {
        const err = await res.json().catch(() => ({}));
        alert(`Couldn't update that entry${err.error ? ': ' + err.error : ''}.`);
        card.querySelectorAll('button').forEach(b => b.disabled = false);
      }
    });
  });
}

async function loadRepliesInto(scope, e) {
  const replies = await fetchReplies(e.id);
  const slot = document.getElementById(scopedId(scope, 'replies', e.id));
  if (!slot) return;
  slot.innerHTML = repliesHtml(replies);
}

function repliesHtml(replies) {
  if (!replies || !replies.length) return '';
  return `<div class="thread-label">Thread replies</div>`
    + replies.map(m => `<div class="ctx-msg reply">${m.user}: ${m.text || '<em>(no text)</em>'}</div>`).join('');
}

// First time a card is opened, ask the server to backfill context_before/
// after for historical entries that never had it captured live (this is
// the fix for "-2/+2 messages not working" -- see detection.py's
// ensure_initial_context). Cheap no-op on repeat views.
async function ensureContextInto(scope, e) {
  if (e.context_before.length && e.context_after.length) return;
  if (e.context_before_checked && e.context_after_checked) return;
  try {
    const res = await fetch(`/api/entries/${e.id}/context`);
    if (!res.ok) return;
    const data = await res.json();
    if (data.context_before && data.context_before.length && !e.context_before.length) {
      const el = document.getElementById(scopedId(scope, 'ctx-before', e.id));
      if (el) { el.innerHTML = data.context_before.map(m => ctxMsgHtml(m)).join(''); wireImagePlaceholders(el); }
    }
    if (data.context_after && data.context_after.length && !e.context_after.length) {
      const el = document.getElementById(scopedId(scope, 'ctx-after', e.id));
      if (el) { el.innerHTML = data.context_after.map(m => ctxMsgHtml(m)).join(''); wireImagePlaceholders(el); }
    }
  } catch (err) { /* best-effort */ }
}

async function loadMoreContext(scope, entryId, direction) {
  const el = document.getElementById(scopedId(scope, direction === 'before' ? 'ctx-before' : 'ctx-after', entryId));
  if (!el) return;
  try {
    const res = await fetch(`/api/entries/${entryId}/context/more`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ direction, count: 2 }),
    });
    const data = await res.json();
    const msgs = data.messages || [];
    if (!msgs.length) return;
    const html = msgs.map(m => ctxMsgHtml(m)).join('');
    if (direction === 'before') el.insertAdjacentHTML('afterbegin', html);
    else el.insertAdjacentHTML('beforeend', html);
    wireImagePlaceholders(el);
  } catch (err) { /* best-effort */ }
}

// ---------- Live Log ----------
const HEADER = ["Date","Time","Sniper","Sniped","Group Snipe","Notable","Kind","Photo","Link","Entry ID",""];
document.getElementById("head").innerHTML = HEADER.map(h => `<th>${h}</th>`).join("");

async function refreshLiveLog() {
  let data;
  try {
    const res = await fetch("/api/snipes");
    data = await res.json();
  } catch (e) {
    document.getElementById("meta").textContent = "⚠️ couldn't reach the server, retrying…";
    return;
  }
  const body = document.getElementById("body");
  const empty = document.getElementById("empty");
  if (!data.rows.length) {
    body.innerHTML = "";
    empty.style.display = "block";
  } else {
    empty.style.display = "none";
    body.innerHTML = data.rows.map(row => {
      const kind = row[6];
      const entryId = row[9];
      let rowClass = "";
      if (kind === "incorrect_snipe") rowClass = "incorrect-snipe";
      else if (row[5] === "Yes") rowClass = "notable";
      else if (kind === "snope") rowClass = "snope";
      else if (kind === "unidentified") rowClass = "unidentified";
      else if (kind === "likely_snipe") rowClass = "likely_snipe";
      else if (kind === "not_a_snipe") rowClass = "not-a-snipe";
      else if (kind === "special") rowClass = "special";
      const cells = row.map((c, i) => {
        if (i === 7 && c) return `<td>${String(c).split("/").pop()}</td>`;
        if (i === 8 && c) return `<td><a href="${c}" target="_blank">open</a></td>`;
        return `<td>${c ?? ""}</td>`;
      }).join("");
      const editCell = entryId ? `<td><button class="btn" data-edit-entry="${entryId}">Edit</button></td>` : `<td></td>`;
      return `<tr class="${rowClass}">${cells}${editCell}</tr>`;
    }).join("");
    body.querySelectorAll('button[data-edit-entry]').forEach(btn => {
      btn.addEventListener('click', () => openEntryModal(btn.dataset.editEntry));
    });
  }
  document.getElementById("meta").textContent =
    `${data.rows.length} entr${data.rows.length === 1 ? "y" : "ies"} • updated ${new Date().toLocaleTimeString()}`;
}
refreshLiveLog();
setInterval(refreshLiveLog, 5000);

// ---------- Entry edit modal (used from Live Log) ----------
async function openEntryModal(entryId) {
  await ensureUsersLoaded();
  const res = await fetch(`/api/entries/${entryId}`);
  if (!res.ok) { alert("Couldn't load that entry."); return; }
  const overlay = document.getElementById('modal-overlay');
  const body = document.getElementById('modal-body');

  function render(entry) {
    body.innerHTML = entryCardHtml('modal', entry);
    wireEntryCard('modal', entry, (id, updated) => {
      render(updated);
      refreshLiveLog();
    });
  }

  render(await res.json());
  overlay.style.display = 'flex';
}
document.getElementById('modal-overlay').addEventListener('click', (ev) => {
  if (ev.target.id === 'modal-overlay') closeEntryModal();
});
document.getElementById('modal-close').addEventListener('click', closeEntryModal);
function closeEntryModal() {
  document.getElementById('modal-overlay').style.display = 'none';
  document.getElementById('modal-body').innerHTML = '';
}

// ---------- Review Queue ----------
let QUEUE = [];
let PAGE_SIZE = 25;
let PAGE_INDEX = 0;

const pageSizeSelect = document.getElementById('review-page-size');
const navEl = document.getElementById('review-nav');
const pagerEl = document.getElementById('review-pager');
const positionEl = document.getElementById('review-position');
const pageInfoEl = document.getElementById('review-page-info');

pageSizeSelect.addEventListener('change', () => {
  PAGE_SIZE = pageSizeSelect.value === 'all' ? Infinity : parseInt(pageSizeSelect.value, 10);
  PAGE_INDEX = 0;
  renderQueue();
});
document.getElementById('review-prev').addEventListener('click', () => {
  PAGE_INDEX = Math.max(0, PAGE_INDEX - 1);
  renderQueue();
});
document.getElementById('review-next').addEventListener('click', () => {
  PAGE_INDEX = Math.min(QUEUE.length - 1, PAGE_INDEX + 1);
  renderQueue();
});
document.getElementById('review-page-prev').addEventListener('click', () => {
  PAGE_INDEX = Math.max(0, PAGE_INDEX - 1);
  renderQueue();
});
document.getElementById('review-page-next').addEventListener('click', () => {
  const maxPage = Math.max(0, Math.ceil(QUEUE.length / PAGE_SIZE) - 1);
  PAGE_INDEX = Math.min(maxPage, PAGE_INDEX + 1);
  renderQueue();
});

async function loadReviewQueue() {
  const list = document.getElementById('review-list');
  list.innerHTML = 'Loading…';
  await ensureUsersLoaded();
  const res = await fetch('/api/review-queue');
  const data = await res.json();
  QUEUE = data.entries || [];
  REPLIES_CACHE = {};
  PAGE_INDEX = 0;
  renderQueue();
}

function renderQueue() {
  const list = document.getElementById('review-list');
  if (!QUEUE.length) {
    list.innerHTML = '<div id="empty">Queue is empty 🎉</div>';
    navEl.style.display = 'none';
    pagerEl.style.display = 'none';
    return;
  }

  const onResolved = (id) => {
    const idx = QUEUE.findIndex(q => q.id === id);
    if (idx !== -1) QUEUE.splice(idx, 1);
    delete REPLIES_CACHE[id];
    renderQueue();
  };

  if (PAGE_SIZE === 1) {
    navEl.style.display = 'flex';
    pagerEl.style.display = 'none';
    PAGE_INDEX = Math.min(Math.max(PAGE_INDEX, 0), QUEUE.length - 1);

    const e = QUEUE[PAGE_INDEX];
    list.innerHTML = entryCardHtml('review', e);
    wireEntryCard('review', e, onResolved);

    for (let d = -2; d <= 2; d++) {
      if (d === 0) continue;
      const neighbor = QUEUE[PAGE_INDEX + d];
      if (neighbor) fetchReplies(neighbor.id);
    }

    positionEl.textContent = `${PAGE_INDEX + 1} / ${QUEUE.length}`;
    document.getElementById('review-prev').disabled = PAGE_INDEX === 0;
    document.getElementById('review-next').disabled = PAGE_INDEX === QUEUE.length - 1;
  } else {
    navEl.style.display = 'none';
    pagerEl.style.display = 'flex';
    const size = PAGE_SIZE === Infinity ? QUEUE.length : PAGE_SIZE;
    const maxPage = Math.max(0, Math.ceil(QUEUE.length / size) - 1);
    PAGE_INDEX = Math.min(Math.max(PAGE_INDEX, 0), maxPage);
    const start = PAGE_INDEX * size;
    const pageEntries = QUEUE.slice(start, start + size);

    list.innerHTML = pageEntries.map(e => entryCardHtml('review', e)).join('');
    pageEntries.forEach(e => wireEntryCard('review', e, onResolved));

    pageInfoEl.textContent = `Page ${PAGE_INDEX + 1} of ${maxPage + 1} (${QUEUE.length} total)`;
    document.getElementById('review-page-prev').disabled = PAGE_INDEX === 0;
    document.getElementById('review-page-next').disabled = PAGE_INDEX === maxPage;
  }
}
document.getElementById('refresh-review').addEventListener('click', loadReviewQueue);

// ---------- Manual Decisions ----------
let MANUAL_LIST = [];
let MANUAL_PAGE_SIZE = 25;
let MANUAL_PAGE_INDEX = 0;

const manualPageSizeSelect = document.getElementById('manual-page-size');
const manualNavEl = document.getElementById('manual-nav');
const manualPagerEl = document.getElementById('manual-pager');
const manualPositionEl = document.getElementById('manual-position');
const manualPageInfoEl = document.getElementById('manual-page-info');

manualPageSizeSelect.addEventListener('change', () => {
  MANUAL_PAGE_SIZE = manualPageSizeSelect.value === 'all' ? Infinity : parseInt(manualPageSizeSelect.value, 10);
  MANUAL_PAGE_INDEX = 0;
  renderManualDecisions();
});
document.getElementById('manual-prev').addEventListener('click', () => {
  MANUAL_PAGE_INDEX = Math.max(0, MANUAL_PAGE_INDEX - 1);
  renderManualDecisions();
});
document.getElementById('manual-next').addEventListener('click', () => {
  MANUAL_PAGE_INDEX = Math.min(MANUAL_LIST.length - 1, MANUAL_PAGE_INDEX + 1);
  renderManualDecisions();
});
document.getElementById('manual-page-prev').addEventListener('click', () => {
  MANUAL_PAGE_INDEX = Math.max(0, MANUAL_PAGE_INDEX - 1);
  renderManualDecisions();
});
document.getElementById('manual-page-next').addEventListener('click', () => {
  const maxPage = Math.max(0, Math.ceil(MANUAL_LIST.length / MANUAL_PAGE_SIZE) - 1);
  MANUAL_PAGE_INDEX = Math.min(maxPage, MANUAL_PAGE_INDEX + 1);
  renderManualDecisions();
});

async function loadManualDecisions() {
  const list = document.getElementById('manual-list');
  list.innerHTML = 'Loading…';
  await ensureUsersLoaded();
  const res = await fetch('/api/manual-decisions');
  const data = await res.json();
  MANUAL_LIST = data.entries || [];
  MANUAL_PAGE_INDEX = 0;
  renderManualDecisions();
}

// Same "Show: N / All / one-at-a-time" paging as the Review Queue -- this
// list can get very long (every manual decision ever made), and rendering
// every card at once (each pulling in context + thread replies) is what
// was hanging the tab.
function renderManualDecisions() {
  const list = document.getElementById('manual-list');
  if (!MANUAL_LIST.length) {
    list.innerHTML = '<div id="empty">No manual decisions yet.</div>';
    manualNavEl.style.display = 'none';
    manualPagerEl.style.display = 'none';
    return;
  }

  const onUpdated = (id, updated) => {
    const idx = MANUAL_LIST.findIndex(m => m.id === id);
    if (idx !== -1) MANUAL_LIST[idx] = updated;
    const card = document.getElementById(scopedId('manual', 'entry', id));
    if (card) {
      card.outerHTML = entryCardHtml('manual', updated);
      wireEntryCard('manual', updated, onUpdated);
    }
  };

  if (MANUAL_PAGE_SIZE === 1) {
    manualNavEl.style.display = 'flex';
    manualPagerEl.style.display = 'none';
    MANUAL_PAGE_INDEX = Math.min(Math.max(MANUAL_PAGE_INDEX, 0), MANUAL_LIST.length - 1);

    const e = MANUAL_LIST[MANUAL_PAGE_INDEX];
    list.innerHTML = entryCardHtml('manual', e);
    wireEntryCard('manual', e, onUpdated);

    manualPositionEl.textContent = `${MANUAL_PAGE_INDEX + 1} / ${MANUAL_LIST.length}`;
    document.getElementById('manual-prev').disabled = MANUAL_PAGE_INDEX === 0;
    document.getElementById('manual-next').disabled = MANUAL_PAGE_INDEX === MANUAL_LIST.length - 1;
  } else {
    manualNavEl.style.display = 'none';
    manualPagerEl.style.display = 'flex';
    const size = MANUAL_PAGE_SIZE === Infinity ? MANUAL_LIST.length : MANUAL_PAGE_SIZE;
    const maxPage = Math.max(0, Math.ceil(MANUAL_LIST.length / size) - 1);
    MANUAL_PAGE_INDEX = Math.min(Math.max(MANUAL_PAGE_INDEX, 0), maxPage);
    const start = MANUAL_PAGE_INDEX * size;
    const pageEntries = MANUAL_LIST.slice(start, start + size);

    list.innerHTML = pageEntries.map(e => entryCardHtml('manual', e)).join('');
    pageEntries.forEach(e => wireEntryCard('manual', e, onUpdated));

    manualPageInfoEl.textContent = `Page ${MANUAL_PAGE_INDEX + 1} of ${maxPage + 1} (${MANUAL_LIST.length} total)`;
    document.getElementById('manual-page-prev').disabled = MANUAL_PAGE_INDEX === 0;
    document.getElementById('manual-page-next').disabled = MANUAL_PAGE_INDEX === maxPage;
  }
}
document.getElementById('refresh-manual').addEventListener('click', loadManualDecisions);

// ---------- Special (too-niche-for-code manual entries) ----------
// Each card is a photo flagged :fingerprint:. Zeke builds any number of
// actual snipe/snope/NAS/missnipe records from it, each with its own
// sniper/sniped -- either a real known user (chip suggestions, same
// ALL_USERS list as everywhere else) or any free-text name typed in and
// added directly (there's no real account for "a deer", and that's fine).
// Both sniper (single) and sniped (multiple) use the same generic chip
// picker below, keyed by an arbitrary string id so one card can host two
// independent pickers -- this (not a native <input list>/<datalist>) is
// what keeps a *picked* real user's actual Slack id attached to the chip,
// rather than just its display text.

function renderSpecialChips(key) {
  const container = document.getElementById(`${key}-chips`);
  if (!container) return;
  container.innerHTML = (SPECIAL_CHIPS[key] || []).map(c =>
    `<span class="chip">${c.display}<button type="button" class="chip-x" data-remove="${c.id}">×</button></span>`
  ).join('');
  container.querySelectorAll('.chip-x').forEach(btn => {
    btn.addEventListener('click', () => {
      SPECIAL_CHIPS[key] = (SPECIAL_CHIPS[key] || []).filter(c => c.id !== btn.dataset.remove);
      renderSpecialChips(key);
    });
  });
}

function wireSpecialChipInput(key, singleSelect) {
  SPECIAL_CHIPS[key] = [];
  renderSpecialChips(key);

  const searchInput = document.getElementById(`${key}-chipsearch`);
  const dropdown = document.getElementById(`${key}-chipdropdown`);
  if (!searchInput) return;

  function closeDropdown() { dropdown.style.display = 'none'; dropdown.innerHTML = ''; }
  function addChip(chipId, display) {
    if (singleSelect) {
      SPECIAL_CHIPS[key] = [{ id: chipId, display }];
    } else {
      SPECIAL_CHIPS[key] = SPECIAL_CHIPS[key] || [];
      if (!SPECIAL_CHIPS[key].some(c => c.id === chipId)) SPECIAL_CHIPS[key].push({ id: chipId, display });
    }
    renderSpecialChips(key);
    searchInput.value = '';
    closeDropdown();
    searchInput.focus();
  }

  searchInput.addEventListener('input', () => {
    const q = searchInput.value.trim();
    if (!q) { closeDropdown(); return; }
    const chosen = new Set((SPECIAL_CHIPS[key] || []).map(c => c.id));
    const matches = ALL_USERS
      .filter(u => !chosen.has(u.id) && (u.name || u.id).toLowerCase().includes(q.toLowerCase()))
      .slice(0, 6);
    const matchHtml = matches.map(u =>
      `<div class="chip-option" data-id="${u.id}" data-display="${u.display.replace(/"/g, '&quot;')}">${u.display}</div>`
    ).join('');
    // The free-text option is always offered, in addition to any real
    // matches -- typing a brand new name (e.g. "deer") is the whole point.
    const customHtml = `<div class="chip-option chip-option-custom" data-custom="${q.replace(/"/g, '&quot;')}">Add "${q}"</div>`;
    dropdown.innerHTML = matchHtml + customHtml;
    dropdown.style.display = 'block';
    dropdown.querySelectorAll('.chip-option[data-id]').forEach(opt => {
      opt.addEventListener('mousedown', (ev) => { ev.preventDefault(); addChip(opt.dataset.id, opt.dataset.display); });
    });
    const customEl = dropdown.querySelector('.chip-option-custom');
    if (customEl) {
      customEl.addEventListener('mousedown', (ev) => { ev.preventDefault(); addChip(customEl.dataset.custom, customEl.dataset.custom); });
    }
  });

  searchInput.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') {
      ev.preventDefault();
      const q = searchInput.value.trim();
      if (q) addChip(q, q);
    }
    if (ev.key === 'Escape') closeDropdown();
  });
  searchInput.addEventListener('blur', () => setTimeout(closeDropdown, 150));
}

function specialDerivedHtml(derived) {
  if (!derived || !derived.length) {
    return '<div class="review-mode-label" style="padding:4px 0">Nothing created from this one yet.</div>';
  }
  return `<div class="special-derived">${derived.map(d => `
    <div class="special-derived-row">
      <span class="badge ${d.kind}">${KIND_LABELS[d.kind] || d.kind}</span>
      <strong>${d.sniper}</strong>${(d.guessed_sniped_display || []).length ? ' → ' + d.guessed_sniped_display.join(', ') : ''}
    </div>`).join('')}</div>`;
}

function specialCardHtml(e) {
  const photo = e.photo_url
    ? (e.is_video
        ? `<div class="photo-wrap"><video src="${e.photo_url}" controls preload="metadata"></video></div>`
        : `<div class="photo-wrap"><img src="${e.photo_url}" loading="lazy"></div>`)
    : `<div class="no-photo">No photo downloaded for this one</div>`;
  const extra = (e.extra_files && e.extra_files.length)
    ? `<div class="extra-files">${extraFilesHtml(e.extra_files)}</div>`
    : '';

  return `
    <div class="card entry-card" id="special-entry-${e.id}">
      <div class="entry-header">${e.sniper}'s photo <span class="badge special">Special</span></div>
      <div class="entry-stack">
        ${photo}
        ${extra}
        <div class="ctx-msg caption-msg">📷 <strong>${e.sniper}:</strong> ${e.photo_text || '<em>(no caption)</em>'}</div>
      </div>
      ${specialDerivedHtml(e.derived)}
      <div class="special-form">
        <select class="special-kind" id="special-kind-${e.id}">
          <option value="snipe">Snipe</option>
          <option value="snope">Snope</option>
          <option value="not_a_snipe">NAS</option>
          <option value="incorrect_snipe">Missnipe</option>
        </select>
        <div class="chip-input single" id="special-sniper-${e.id}-wrap">
          <div class="chips" id="special-sniper-${e.id}-chips"></div>
          <input type="text" class="chip-search" id="special-sniper-${e.id}-chipsearch"
                 placeholder="Sniper — pick, or type + Enter for custom…" autocomplete="off">
          <div class="chip-dropdown" id="special-sniper-${e.id}-chipdropdown"></div>
        </div>
        <div class="chip-input" id="special-sniped-${e.id}-wrap">
          <div class="chips" id="special-sniped-${e.id}-chips"></div>
          <input type="text" class="chip-search" id="special-sniped-${e.id}-chipsearch"
                 placeholder="Sniped name(s) — pick, or type + Enter for custom…" autocomplete="off">
          <div class="chip-dropdown" id="special-sniped-${e.id}-chipdropdown"></div>
        </div>
        <button class="btn primary" id="special-create-${e.id}">Create</button>
      </div>
      <div class="resolve-row">
        <button class="btn" id="special-dismiss-${e.id}">Dismiss — nothing here</button>
      </div>
    </div>`;
}

function wireSpecialCard(e) {
  const card = document.getElementById(`special-entry-${e.id}`);
  if (card) wireImagePlaceholders(card);

  const sniperKey = `special-sniper-${e.id}`;
  const snipedKey = `special-sniped-${e.id}`;
  wireSpecialChipInput(sniperKey, true);
  wireSpecialChipInput(snipedKey, false);

  const kindSelect = document.getElementById(`special-kind-${e.id}`);
  const snipedWrap = document.getElementById(`${snipedKey}-wrap`);
  const createBtn = document.getElementById(`special-create-${e.id}`);
  const dismissBtn = document.getElementById(`special-dismiss-${e.id}`);

  function syncSnipedRequirement() {
    const needsSniped = kindSelect.value === 'snipe' || kindSelect.value === 'snope';
    snipedWrap.style.opacity = needsSniped ? '1' : '0.45';
  }
  kindSelect.addEventListener('change', syncSnipedRequirement);
  syncSnipedRequirement();

  createBtn.addEventListener('click', async () => {
    const kind = kindSelect.value;
    const sniperChip = (SPECIAL_CHIPS[sniperKey] || [])[0];
    const sniper = sniperChip ? sniperChip.id : '';
    const sniped = (SPECIAL_CHIPS[snipedKey] || []).map(c => c.id);
    if (!sniper) { alert('Add a sniper first.'); return; }
    if ((kind === 'snipe' || kind === 'snope') && sniped.length === 0) {
      alert('Add at least one sniped name for a snipe or snope.');
      return;
    }
    createBtn.disabled = true;
    const res = await fetch(`/api/special/${e.id}/create`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind, sniper, sniped }),
    });
    createBtn.disabled = false;
    if (res.ok) {
      loadSpecial();
      refreshLiveLog();
    } else {
      const err = await res.json().catch(() => ({}));
      alert(`Couldn't create that${err.error ? ': ' + err.error : ''}.`);
    }
  });

  dismissBtn.addEventListener('click', async () => {
    if (!confirm('Dismiss this photo as not a snipe at all?')) return;
    dismissBtn.disabled = true;
    const res = await fetch(`/api/special/${e.id}/dismiss`, { method: 'POST' });
    if (res.ok) {
      loadSpecial();
    } else {
      alert("Couldn't dismiss that.");
      dismissBtn.disabled = false;
    }
  });
}

async function loadSpecial() {
  const list = document.getElementById('special-list');
  list.innerHTML = 'Loading…';
  await ensureUsersLoaded();

  const res = await fetch('/api/special');
  const data = await res.json();
  const entries = data.entries || [];

  if (!entries.length) {
    list.innerHTML = '<div id="empty">Nothing flagged special right now.</div>';
    return;
  }

  list.innerHTML = entries.map(specialCardHtml).join('');
  entries.forEach(wireSpecialCard);
}
document.getElementById('refresh-special').addEventListener('click', loadSpecial);

// ---------- Do Not Snipe ----------
async function loadDns() {
  const list = document.getElementById('dns-list');
  list.innerHTML = 'Loading…';
  await ensureUsersLoaded();
  const res = await fetch('/api/dns');
  const data = await res.json();
  const users = data.users || [];
  list.innerHTML = users.length
    ? users.map(u => `
        <div class="card dns-row">
          <span>${u.display}</span>
          <button class="btn danger" data-remove-dns="${u.id}">Remove</button>
        </div>`).join('')
    : '<div id="empty">Nobody is on the do-not-snipe list.</div>';
  list.querySelectorAll('button[data-remove-dns]').forEach(btn => {
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      await fetch(`/api/dns/${btn.dataset.removeDns}`, { method: 'DELETE' });
      loadDns();
    });
  });
}
document.getElementById('refresh-dns').addEventListener('click', loadDns);

function setupDnsAdd() {
  const searchInput = document.getElementById('dns-add-search');
  const dropdown = document.getElementById('dns-add-dropdown');

  function closeDropdown() { dropdown.style.display = 'none'; dropdown.innerHTML = ''; }

  async function addUser(u) {
    searchInput.value = '';
    closeDropdown();
    await fetch('/api/dns', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: u.id }),
    });
    loadDns();
  }

  searchInput.addEventListener('input', async () => {
    await ensureUsersLoaded();
    const q = searchInput.value.trim().toLowerCase();
    if (!q) { closeDropdown(); return; }
    const matches = ALL_USERS.filter(u => (u.name || u.id).toLowerCase().includes(q)).slice(0, 8);
    if (!matches.length) {
      dropdown.innerHTML = '<div class="chip-empty">No matching names yet</div>';
      dropdown.style.display = 'block';
      return;
    }
    dropdown.innerHTML = matches.map(u => `<div class="chip-option" data-id="${u.id}">${u.display}</div>`).join('');
    dropdown.style.display = 'block';
    dropdown.querySelectorAll('.chip-option').forEach(opt => {
      opt.addEventListener('mousedown', (ev) => {
        ev.preventDefault();
        const u = ALL_USERS.find(u => u.id === opt.dataset.id);
        if (u) addUser(u);
      });
    });
  });
  searchInput.addEventListener('blur', () => closeDropdown());
}
setupDnsAdd();

// ---------- Search ----------
// Filters across every entry regardless of kind or pending/manual status --
// everything in snipes.json, in one place, via GET /api/search.
let SEARCH_LIST = [];
let SEARCH_PAGE_SIZE = 25;
let SEARCH_PAGE_INDEX = 0;
let SEARCH_INITIALIZED = false;

const searchPageSizeSelect = document.getElementById('search-page-size');
const searchNavEl = document.getElementById('search-nav');
const searchPagerEl = document.getElementById('search-pager');
const searchPositionEl = document.getElementById('search-position');
const searchPageInfoEl = document.getElementById('search-page-info');
const searchCountEl = document.getElementById('search-count');

searchPageSizeSelect.addEventListener('change', () => {
  SEARCH_PAGE_SIZE = searchPageSizeSelect.value === 'all' ? Infinity : parseInt(searchPageSizeSelect.value, 10);
  SEARCH_PAGE_INDEX = 0;
  renderSearchResults();
});
document.getElementById('search-prev').addEventListener('click', () => {
  SEARCH_PAGE_INDEX = Math.max(0, SEARCH_PAGE_INDEX - 1);
  renderSearchResults();
});
document.getElementById('search-next').addEventListener('click', () => {
  SEARCH_PAGE_INDEX = Math.min(SEARCH_LIST.length - 1, SEARCH_PAGE_INDEX + 1);
  renderSearchResults();
});
document.getElementById('search-page-prev').addEventListener('click', () => {
  SEARCH_PAGE_INDEX = Math.max(0, SEARCH_PAGE_INDEX - 1);
  renderSearchResults();
});
document.getElementById('search-page-next').addEventListener('click', () => {
  const maxPage = Math.max(0, Math.ceil(SEARCH_LIST.length / SEARCH_PAGE_SIZE) - 1);
  SEARCH_PAGE_INDEX = Math.min(maxPage, SEARCH_PAGE_INDEX + 1);
  renderSearchResults();
});

function populateUserSelect(select) {
  const current = select.value;
  select.innerHTML = '<option value="">Any</option>' + ALL_USERS.map(u => `<option value="${u.id}">${u.display}</option>`).join('');
  select.value = current;
}

async function loadSearchTab() {
  await ensureUsersLoaded();
  if (!SEARCH_INITIALIZED) {
    populateUserSelect(document.getElementById('search-sniper'));
    populateUserSelect(document.getElementById('search-sniped'));
    SEARCH_INITIALIZED = true;
    runSearch();
  }
}

function currentSearchParams() {
  const params = new URLSearchParams();
  const kinds = Array.from(document.querySelectorAll('.search-kind:checked')).map(cb => cb.value);
  if (kinds.length && kinds.length < 6) params.set('kind', kinds.join(','));
  const sniper = document.getElementById('search-sniper').value;
  if (sniper) params.set('sniper', sniper);
  const sniped = document.getElementById('search-sniped').value;
  if (sniped) params.set('sniped', sniped);
  const time = document.getElementById('search-time').value;
  if (time) params.set('time', time);
  const day = document.getElementById('search-day').value;
  if (day) params.set('day', day);
  const notable = document.getElementById('search-notable').value;
  if (notable !== '') params.set('notable', notable);
  const manual = document.getElementById('search-manual').value;
  if (manual !== '') params.set('manually_resolved', manual);
  const q = document.getElementById('search-q').value.trim();
  if (q) params.set('q', q);
  return params;
}

async function runSearch() {
  const list = document.getElementById('search-list');
  list.innerHTML = 'Searching…';
  const params = currentSearchParams();
  const res = await fetch(`/api/search?${params.toString()}`);
  const data = await res.json();
  SEARCH_LIST = data.entries || [];
  SEARCH_PAGE_INDEX = 0;
  searchCountEl.textContent = `${data.total ?? SEARCH_LIST.length} match${(data.total ?? SEARCH_LIST.length) === 1 ? '' : 'es'}`;
  renderSearchResults();
}
document.getElementById('search-run').addEventListener('click', runSearch);
document.getElementById('search-clear').addEventListener('click', () => {
  document.querySelectorAll('.search-kind').forEach(cb => cb.checked = true);
  document.getElementById('search-sniper').value = '';
  document.getElementById('search-sniped').value = '';
  document.getElementById('search-time').value = '';
  document.getElementById('search-day').value = '';
  document.getElementById('search-notable').value = '';
  document.getElementById('search-manual').value = '';
  document.getElementById('search-q').value = '';
  runSearch();
});

function renderSearchResults() {
  const list = document.getElementById('search-list');
  if (!SEARCH_LIST.length) {
    list.innerHTML = '<div id="empty">No matches.</div>';
    searchNavEl.style.display = 'none';
    searchPagerEl.style.display = 'none';
    return;
  }

  const onUpdated = (id, updated) => {
    const idx = SEARCH_LIST.findIndex(m => m.id === id);
    if (idx !== -1) SEARCH_LIST[idx] = updated;
    const card = document.getElementById(scopedId('search', 'entry', id));
    if (card) {
      card.outerHTML = entryCardHtml('search', updated);
      wireEntryCard('search', updated, onUpdated);
    }
  };

  if (SEARCH_PAGE_SIZE === 1) {
    searchNavEl.style.display = 'flex';
    searchPagerEl.style.display = 'none';
    SEARCH_PAGE_INDEX = Math.min(Math.max(SEARCH_PAGE_INDEX, 0), SEARCH_LIST.length - 1);

    const e = SEARCH_LIST[SEARCH_PAGE_INDEX];
    list.innerHTML = entryCardHtml('search', e);
    wireEntryCard('search', e, onUpdated);

    searchPositionEl.textContent = `${SEARCH_PAGE_INDEX + 1} / ${SEARCH_LIST.length}`;
    document.getElementById('search-prev').disabled = SEARCH_PAGE_INDEX === 0;
    document.getElementById('search-next').disabled = SEARCH_PAGE_INDEX === SEARCH_LIST.length - 1;
  } else {
    searchNavEl.style.display = 'none';
    searchPagerEl.style.display = 'flex';
    const size = SEARCH_PAGE_SIZE === Infinity ? SEARCH_LIST.length : SEARCH_PAGE_SIZE;
    const maxPage = Math.max(0, Math.ceil(SEARCH_LIST.length / size) - 1);
    SEARCH_PAGE_INDEX = Math.min(Math.max(SEARCH_PAGE_INDEX, 0), maxPage);
    const start = SEARCH_PAGE_INDEX * size;
    const pageEntries = SEARCH_LIST.slice(start, start + size);

    list.innerHTML = pageEntries.map(e => entryCardHtml('search', e)).join('');
    pageEntries.forEach(e => wireEntryCard('search', e, onUpdated));

    searchPageInfoEl.textContent = `Page ${SEARCH_PAGE_INDEX + 1} of ${maxPage + 1} (${SEARCH_LIST.length} total)`;
    document.getElementById('search-page-prev').disabled = SEARCH_PAGE_INDEX === 0;
    document.getElementById('search-page-next').disabled = SEARCH_PAGE_INDEX === maxPage;
  }
}

// ---------- Analysis ----------
async function loadAnalysis() {
  const body = document.getElementById('analysis-body');
  body.innerHTML = 'Loading…';
  const res = await fetch('/api/analysis');
  const d = await res.json();

  const lbRows = d.leaderboard.map(r =>
    `<tr><td>${r.rank}</td><td>${r.user}</td><td>${r.snipes}</td><td>${r.sniped}</td><td>${r.adjustment}</td><td>${r.score}</td></tr>`
  ).join('') || '<tr><td colspan="6">No confirmed snipes yet</td></tr>';

  const dictRows = obj => Object.entries(obj).map(([k,v]) => `<tr><td>${k}</td><td>${typeof v === 'number' ? v.toFixed(2).replace(/\.00$/, '') : v}</td></tr>`).join('') || '<tr><td colspan="2">—</td></tr>';

  const rivalryRows = d.rivalries.map(r => `<tr><td>${r.pair.join(' vs ')}</td><td>${r.count}</td></tr>`).join('') || '<tr><td colspan="2">—</td></tr>';

  body.innerHTML = `
    <div class="stat-block">
      <h3>Leaderboard</h3>
      <table><thead><tr><th>Rank</th><th>User</th><th>Snipes</th><th>Sniped</th><th>Adjustment</th><th>Score</th></tr></thead>
      <tbody>${lbRows}</tbody></table>
    </div>
    <div class="stat-block"><h3>Rivalries</h3><table><tbody>${rivalryRows}</tbody></table></div>
    <div class="stat-block"><h3>Sniping streaks (days)</h3><table><tbody>${dictRows(d.streaks)}</tbody></table></div>
    <div class="stat-block"><h3>Untouchable (days since last sniped)</h3><table><tbody>${dictRows(d.untouchable)}</tbody></table></div>
    <div class="stat-block"><h3>Retaliation speed (avg hrs)</h3><table><tbody>${dictRows(d.retaliation_speed)}</tbody></table></div>
    <div class="stat-block"><h3>Peak days</h3><table><tbody>${dictRows(d.peak_by_day)}</tbody></table></div>
    <div class="stat-block"><h3>Peak time of day</h3><table><tbody>${dictRows(d.peak_by_bucket)}</tbody></table></div>
    <div class="stat-block"><h3>Notable ratio</h3><table><tbody>${dictRows(d.notable_ratio)}</tbody></table></div>
    <div class="stat-block"><h3>Group snipes -- as sniper</h3><table><tbody>${dictRows(d.group_snipe_as_sniper)}</tbody></table></div>
    <div class="stat-block"><h3>Group snipes -- as victim</h3><table><tbody>${dictRows(d.group_snipe_as_victim)}</tbody></table></div>
    <div class="stat-block"><h3>Review queue size</h3>${d.queue_size}</div>
  `;
}
document.getElementById('refresh-analysis').addEventListener('click', loadAnalysis);

// ---------- Matrix (Coded / Manual / True) ----------
let matrixView = 'coded';

function matrixCaption(view) {
  return {
    coded: 'Read-only -- exactly what the bot classified from #snipes. Rows are the sniper, columns are the victim.',
    manual: 'Editable -- your own corrections/additions on top of the coded matrix. Positive = a snipe the bot missed; negative = correcting one it over-counted. Never touched by recount/backfill.',
    'true': 'Editable -- Coded + Manual combined. This is what Snipes/Sniped feed into Score. Editing a cell here saves the difference to the Manual matrix automatically.',
  }[view];
}

async function loadMatrix() {
  document.getElementById('matrix-caption').textContent = matrixCaption(matrixView);
  const wrap = document.getElementById('matrix-wrap');
  wrap.innerHTML = 'Loading…';
  const res = await fetch(`/api/matrix/${matrixView}`);
  const d = await res.json();

  if (!d.users.length) {
    wrap.innerHTML = '<div id="empty">No one on the roster yet.</div>';
    return;
  }

  const headerCells = d.users.map(u => `<th>${u.display}</th>`).join('');
  const bodyRows = d.users.map(row => {
    const cells = d.users.map(col => {
      if (row.id === col.id) return '<td></td>';
      const count = (d.cells[row.id] || {})[col.id] || 0;
      if (!d.editable) return `<td>${count || ''}</td>`;
      return `<td><input type="text" inputmode="numeric" class="cell-input"
                data-sniper="${row.id}" data-victim="${col.id}" value="${count}"></td>`;
    }).join('');
    return `<tr><td>${row.display}</td><td class="row-total">${d.row_totals[row.id] || 0}</td>${cells}</tr>`;
  }).join('');
  const footerCells = d.users.map(u => `<td class="row-total">${d.col_totals[u.id] || 0}</td>`).join('');

  wrap.innerHTML = `
    <table class="matrix-table">
      <thead><tr><th>Sniper \\ Victim</th><th>Snipes</th>${headerCells}</tr></thead>
      <tbody>${bodyRows}</tbody>
      <tfoot><tr><td>Sniped (total)</td><td></td>${footerCells}</tr></tfoot>
    </table>`;

  if (d.editable) {
    wrap.querySelectorAll('.cell-input').forEach(input => {
      const original = input.value;
      input.addEventListener('change', async () => {
        const value = parseInt(input.value, 10);
        if (Number.isNaN(value)) { input.value = original; return; }
        input.disabled = true;
        try {
          await fetch(`/api/matrix/${matrixView}/cell`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sniper: input.dataset.sniper, victim: input.dataset.victim, value }),
          });
          input.value = String(value);
        } finally {
          input.disabled = false;
        }
      });
    });
  }
}

document.querySelectorAll('.matrix-subtab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.matrix-subtab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    matrixView = btn.dataset.matrix;
    loadMatrix();
  });
});

document.getElementById('refresh-matrix').addEventListener('click', loadMatrix);

document.getElementById('matrix-zoom').addEventListener('input', (e) => {
  document.getElementById('matrix-wrap').style.setProperty('--zoom', e.target.value / 100);
});

// ---------- Adjustments ----------
async function loadAdjustments() {
  const list = document.getElementById('adjustments-list');
  list.innerHTML = 'Loading…';
  const res = await fetch('/api/adjustments');
  const d = await res.json();

  list.innerHTML = `
    <table class="matrix-table" style="width:auto">
      <thead><tr><th>User</th><th>Adjustment</th></tr></thead>
      <tbody>${d.users.map(u => `
        <tr>
          <td>${u.display}</td>
          <td><input type="text" inputmode="decimal" class="cell-input adj-input" data-user="${u.id}" value="${u.adjustment}"></td>
        </tr>`).join('') || '<tr><td colspan="2">No users yet</td></tr>'}</tbody>
    </table>`;

  list.querySelectorAll('.adj-input').forEach(input => {
    const original = input.value;
    input.addEventListener('change', async () => {
      const value = parseFloat(input.value);
      if (Number.isNaN(value)) { input.value = original; return; }
      input.disabled = true;
      try {
        await fetch('/api/adjustments', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: input.dataset.user, adjustment: value }),
        });
      } finally {
        input.disabled = false;
      }
    });
  });
}
document.getElementById('refresh-adjustments').addEventListener('click', loadAdjustments);

// ---------- Scripts ----------
async function loadScripts() {
  const list = document.getElementById('scripts-list');
  const res = await fetch('/api/scripts');
  const scripts = await res.json();
  list.innerHTML = Object.entries(scripts).map(([key, s]) => `
    <div class="card">
      <div><strong>${s.label}</strong></div>
      <div class="caption">${s.description}</div>
      <div class="resolve-row">
        ${s.needs_since ? `<input type="date" id="since-${key}">` : ''}
        <button class="btn primary" data-run="${key}">Run</button>
      </div>
    </div>`).join('');
  document.querySelectorAll('button[data-run]').forEach(btn => {
    btn.addEventListener('click', () => runScript(btn.dataset.run, btn));
  });
}

async function runScript(key, btn) {
  const consoleEl = document.getElementById('scripts-console');
  consoleEl.style.display = 'block';
  consoleEl.textContent = `Starting ${key}...\n`;
  btn.disabled = true;

  let body = {};
  const sinceInput = document.getElementById(`since-${key}`);
  if (sinceInput) {
    if (!sinceInput.value) { alert('Pick a date first'); btn.disabled = false; return; }
    body.since = sinceInput.value;
  }

  const startRes = await fetch(`/api/scripts/${key}/run`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  });
  if (!startRes.ok) {
    const err = await startRes.json();
    consoleEl.textContent += `Failed to start: ${err.error}\n`;
    btn.disabled = false;
    return;
  }
  const { run_id } = await startRes.json();

  const poll = setInterval(async () => {
    const statusRes = await fetch(`/api/scripts/runs/${run_id}`);
    const status = await statusRes.json();
    consoleEl.textContent = status.output || '(no output yet)';
    consoleEl.scrollTop = consoleEl.scrollHeight;
    if (status.done) {
      clearInterval(poll);
      consoleEl.textContent += `\n\n[exited with code ${status.returncode}]`;
      btn.disabled = false;
    }
  }, 1500);
}
