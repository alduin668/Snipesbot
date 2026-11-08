import { api, query } from '../api.js';
import { createEntryCard } from '../entryCard.js';
import { el } from '../dom.js';
import { createPagedList } from '../pagedList.js';

export function mount(panel) {
  const refreshBtn = el('button', { type: 'button', class: 'btn', text: 'Refresh queue' });
  const list = createPagedList({
    emptyText: 'Queue is empty 🎉',
    header: [refreshBtn],
    fetchPage: (page) => api('/entries' + query({ pending: 1, ...page })),
    // Resolving removes the card: reload this page so the next entry slides in.
    renderItem: (entry, ctx) => createEntryCard(entry, { onChange: () => ctx.reload() }),
  });
  refreshBtn.addEventListener('click', () => list.reload());
  panel.append(list.el);
  return { activate: () => list.reset() };
}
