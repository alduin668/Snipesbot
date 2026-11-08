import { el, empty, render } from './dom.js';

const SIZES = [['10', '10'], ['25', '25'], ['50', '50'], ['100', '100'], ['all', 'All'], ['1', 'One at a time']];

/**
 * One paging component for every list of entries (Review Queue, Manual
 * Decisions, Search). Paging happens on the server, so a tab with thousands
 * of entries only ever builds the cards on screen.
 *
 *   fetchPage({limit, offset}) -> {entries, total}
 *   renderItem(entry, {replace(updated), reload()}) -> Promise<Element>|Element
 */
export function createPagedList({ emptyText, fetchPage, renderItem, header = [] }) {
  const state = { size: 25, index: 0, total: 0 };
  const sizeSelect = el('select');
  SIZES.forEach(([v, label]) => sizeSelect.append(el('option', { value: v, text: label, ...(v === '25' ? { selected: '' } : {}) })));
  const prev = el('button', { type: 'button', class: 'btn', text: '← Prev' });
  const next = el('button', { type: 'button', class: 'btn', text: 'Next →' });
  const info = el('span');
  const pager = el('div', { class: 'review-pager' }, prev, info, next);
  const controls = el('div', { class: 'review-controls' }, ...header,
    el('label', { class: 'review-mode-label' }, 'Show: ', sizeSelect), pager);
  const list = el('div', { class: 'entry-list' });
  const root = el('div', {}, controls, list);

  const pageSize = () => (sizeSelect.value === 'all' ? Infinity : parseInt(sizeSelect.value, 10));
  const lastIndex = () => (state.size === Infinity ? 0 : Math.max(0, Math.ceil(state.total / state.size) - 1));

  async function load() {
    state.size = pageSize();
    render(list, 'Loading…');
    let data;
    try {
      data = await fetchPage({ limit: state.size === Infinity ? undefined : state.size, offset: state.index * (state.size === Infinity ? 0 : state.size) });
    } catch (err) {
      render(list, `Couldn't load: ${err.message}`);
      return;
    }
    state.total = data.total;
    if (state.index > lastIndex()) { state.index = lastIndex(); return load(); }   // e.g. the last card on a page was resolved
    if (!data.entries.length) {
      render(list, empty(emptyText));
      pager.style.display = 'none';
      return;
    }
    const cards = [];
    for (const entry of data.entries) {
      const holder = { node: null };
      holder.node = await renderItem(entry, ctxFor(holder));
      cards.push(holder.node);
    }
    list.replaceChildren(...cards);
    pager.style.display = 'flex';
    info.textContent = state.size === 1 ? `${state.index + 1} / ${state.total}` : `Page ${state.index + 1} of ${lastIndex() + 1} (${state.total} total)`;
    prev.disabled = state.index === 0;
    next.disabled = state.index >= lastIndex();
  }

  // Helpers handed to renderItem: refresh this page, or swap one card in place.
  function ctxFor(holder) {
    return {
      reload: load,
      replace: async (updated) => { const fresh = await renderItem(updated, ctxFor(holder)); holder.node.replaceWith(fresh); holder.node = fresh; },
    };
  }

  sizeSelect.addEventListener('change', () => { state.index = 0; load(); });
  prev.addEventListener('click', () => { state.index = Math.max(0, state.index - 1); load(); });
  next.addEventListener('click', () => { state.index = Math.min(lastIndex(), state.index + 1); load(); });

  return { el: root, reload: load, reset: () => { state.index = 0; return load(); } };
}
