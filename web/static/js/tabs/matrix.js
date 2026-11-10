import { api } from '../api.js';
import { $$, el, empty, html, render } from '../dom.js';

const CAPTIONS = {
  coded: 'Read-only — exactly what the bot classified from #snipes. Rows are the sniper, columns are the victim.',
  manual: 'Editable — your own corrections on top of the coded matrix. Positive = a snipe the bot missed; negative = correcting an over-count. Never touched by sync.',
  true: 'Editable — Coded + Manual combined. This is what Snipes/Sniped feed into Score. Editing a cell saves the difference to the Manual matrix.',
};

export function mount(panel) {
  let view = 'coded';
  const tabs = ['coded', 'manual', 'true'].map((v) => el('button', {
    type: 'button', class: 'matrix-subtab-btn' + (v === view ? ' active' : ''), text: v[0].toUpperCase() + v.slice(1),
    onclick: () => { view = v; tabs.forEach((t) => t.classList.toggle('active', t === tabs[['coded', 'manual', 'true'].indexOf(v)])); load(); },
  }));
  const refresh = el('button', { type: 'button', class: 'btn', text: 'Refresh', onclick: () => load() });
  const zoom = el('input', { type: 'range', min: '60', max: '160', value: '100', step: '10' });
  const caption = el('div', { class: 'review-mode-label', style: 'margin-top:8px' });
  const wrap = el('div', { class: 'matrix-scroll', style: 'margin-top:8px' });
  zoom.addEventListener('input', () => wrap.style.setProperty('--zoom', zoom.value / 100));
  panel.append(el('div', { class: 'review-controls' }, el('div', { class: 'matrix-subtabs' }, ...tabs), refresh,
    el('label', { class: 'review-mode-label' }, 'Zoom ', zoom)), caption, wrap);

  async function load() {
    caption.textContent = CAPTIONS[view];
    render(wrap, 'Loading…');
    const d = await api(`/matrix/${view}`);
    if (!d.users.length) { render(wrap, empty('No one on the roster yet.')); return; }
    render(wrap, html`
      <table class="matrix-table">
        <thead><tr><th>Sniper \\ Victim</th><th>Snipes</th>${d.users.map((u) => html`<th>${u.display}</th>`)}</tr></thead>
        <tbody>${d.users.map((row) => html`<tr><td>${row.display}</td><td class="row-total">${d.row_totals[row.id] || 0}</td>
          ${d.users.map((col) => {
            if (row.id === col.id) return html`<td></td>`;
            const n = (d.cells[row.id] || {})[col.id] || 0;
            return d.editable
              ? html`<td><input type="text" inputmode="numeric" class="cell-input" data-sniper="${row.id}" data-victim="${col.id}" value="${n}"></td>`
              : html`<td>${n || ''}</td>`;
          })}</tr>`)}</tbody>
        <tfoot><tr><td>Sniped (total)</td><td></td>${d.users.map((u) => html`<td class="row-total">${d.col_totals[u.id] || 0}</td>`)}</tr></tfoot>
      </table>`);

    $$('.cell-input', wrap).forEach((input) => {
      let last = input.value;
      input.addEventListener('change', async () => {
        const value = parseInt(input.value, 10);
        if (Number.isNaN(value)) { input.value = last; return; }
        input.disabled = true;
        try {
          await api(`/matrix/${view}/cell`, { method: 'POST', body: { sniper: input.dataset.sniper, victim: input.dataset.victim, value } });
          last = String(value);
        } catch (err) { alert(`Couldn't save: ${err.message}`); }
        input.value = last;
        input.disabled = false;
      });
    });
  }
  return { activate: load };
}
