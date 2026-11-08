import { api } from '../api.js';
import { createEntryCard } from '../entryCard.js';
import { el, empty, html, render } from '../dom.js';
import { closeModal, openModal } from '../modal.js';

const COLUMNS = ['Date', 'Time', 'Sniper', 'Sniped', 'Group', 'Notable', 'Kind', 'Link', ''];
const ROW_CLASS = {
  incorrect_snipe: 'incorrect-snipe', snope: 'snope', unidentified: 'unidentified',
  likely_snipe: 'likely_snipe', not_a_snipe: 'not-a-snipe', special: 'special',
};

export function mount(panel) {
  const bar = el('div', { class: 'review-controls' },
    el('a', { class: 'btn-link', href: '/api/export.xlsx', text: '⬇ Download .xlsx' }),
    el('span', { class: 'review-mode-label', id: 'livelog-count' }));
  const table = el('table', {}, el('thead'), el('tbody'));
  table.tHead.innerHTML = '<tr>' + COLUMNS.map((c) => `<th>${c}</th>`).join('') + '</tr>';
  const note = el('div');
  panel.append(bar, table, note);

  async function refresh() {
    let data;
    try { data = await api('/log'); } catch { return false; }
    const body = table.tBodies[0];
    if (!data.rows.length) {
      body.replaceChildren();
      render(note, empty('No snipes recorded yet.'));
    } else {
      note.replaceChildren();
      body.replaceChildren(...data.rows.map(row));
    }
    bar.querySelector('#livelog-count').textContent = `${data.total} entries`;
    return true;
  }

  function row(e) {
    const tr = el('tr', { class: e.notable && e.kind !== 'incorrect_snipe' ? 'notable' : ROW_CLASS[e.kind] || '' });
    const cells = [e.date, e.time, e.sniper, e.sniped_display.join(', '), e.sniped_ids.length > 1 ? 'Yes' : 'No',
      e.notable ? 'Yes' : 'No', e.kind_label];
    cells.forEach((c) => tr.append(el('td', { text: c })));
    const link = el('td');
    if (e.permalink) link.append(el('a', { href: e.permalink, target: '_blank', rel: 'noopener', text: 'open' }));
    const edit = el('td', {}, el('button', { type: 'button', class: 'btn', text: 'Edit', onclick: () => openEditor(e.id) }));
    tr.append(link, edit);
    return tr;
  }

  async function openEditor(id) {
    try {
      const entry = await api(`/entries/${id}`);
      const show = async (e) => openModal(await createEntryCard(e, { onChange: (u) => { show(u); refresh(); }, onNotable: refresh }));
      await show(entry);
    } catch (err) {
      alert(`Couldn't load that entry: ${err.message}`);
      closeModal();
    }
  }

  // Poll only while this tab is showing and the page is visible.
  let timer = null;
  const tick = async () => {
    if (!panel.classList.contains('active') || document.hidden) return;
    await refresh();
  };
  timer = setInterval(tick, 10000);

  return { activate: refresh, refresh };
}
