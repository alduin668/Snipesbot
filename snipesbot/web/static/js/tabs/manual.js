import { api, query } from '../api.js';
import { createEntryCard } from '../entryCard.js';
import { el } from '../dom.js';
import { createPagedList } from '../pagedList.js';

export function mount(panel) {
  const refreshBtn = el('button', { type: 'button', class: 'btn', text: 'Refresh' });
  const list = createPagedList({
    emptyText: 'No manual decisions yet.',
    storageKey: 'manual',
    header: [refreshBtn, el('span', { class: 'review-mode-label',
      text: 'Every entry decided by hand — re-classify or correct any of them here.' })],
    fetchPage: (page) => api('/entries' + query({ manual: 1, ...page })),
    // An edited decision stays in the list; just redraw that card.
    renderItem: (entry, ctx) => createEntryCard(entry, { layout: ctx.focus ? 'focus' : 'stack', onChange: (u) => ctx.replace(u) }),
  });
  refreshBtn.addEventListener('click', () => list.reload());
  panel.append(list.el);
  return { activate: () => list.reset() };
}
