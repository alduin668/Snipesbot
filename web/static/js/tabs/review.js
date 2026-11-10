import { api, query } from '../api.js';
import { createEntryCard } from '../entryCard.js';
import { el } from '../dom.js';
import { createPagedList } from '../pagedList.js';

export function mount(panel) {
  const refreshBtn = el('button', { type: 'button', class: 'btn', text: 'Refresh queue' });
  const list = createPagedList({
    emptyText: 'Queue is empty 🎉',
    storageKey: 'review',
    header: [refreshBtn, el('span', { class: 'review-mode-label',
      text: 'Needs a decision: unidentified photos, likely snipes, and snipes marked in Slack with nobody recorded as sniped yet.' })],
    fetchPage: (page) => api('/entries' + query({ review: 1, ...page })),
    // Resolving removes the card: reload this page so the next entry slides in.
    renderItem: (entry, ctx) => createEntryCard(entry, { layout: ctx.focus ? 'focus' : 'stack', onChange: () => ctx.reload() }),
  });
  refreshBtn.addEventListener('click', () => list.reload());
  panel.append(list.el);
  return { activate: () => list.reset() };
}
