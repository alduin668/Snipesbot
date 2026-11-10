import { api } from '../api.js';
import { $, $$, el, empty, html, render } from '../dom.js';

const CAPTIONS = {
  coded: 'Read-only — exactly what the bot classified from #snipes. Rows are the sniper, columns are the victim.',
  manual: 'Editable — your own corrections on top of the coded matrix. Positive = a snipe the bot missed; negative = correcting an over-count. Never touched by sync.',
  true: 'Editable live — Coded + Manual combined. Edit any cell, a person’s Adjustment, or their Score (the big number): totals and scores update as you go. Editing a cell saves the difference to the Manual matrix; editing a Score saves the Adjustment needed to reach it.',
};
const VIEWS = ['coded', 'manual', 'true'];
const num = (v) => { const n = Number(v); return Number.isFinite(n) ? n : 0; };
const round2 = (n) => Math.round(n * 100) / 100;
/** Find an element by a data attribute without building a CSS selector from user data. */
const find = (root, sel, attr, id) => $$(sel, root).find((n) => n.dataset[attr] === id);

export function mount(panel) {
  let view = 'coded';
  const tabs = VIEWS.map((v) => el('button', {
    type: 'button', class: 'matrix-subtab-btn' + (v === view ? ' active' : ''), text: v[0].toUpperCase() + v.slice(1),
    onclick: () => { view = v; tabs.forEach((t, i) => t.classList.toggle('active', VIEWS[i] === v)); load(); },
  }));
  const refresh = el('button', { type: 'button', class: 'btn', text: 'Refresh', onclick: () => load() });
  const zoom = el('input', { type: 'range', min: '60', max: '160', value: '100', step: '10' });
  const caption = el('div', { class: 'review-mode-label', style: 'margin-top:8px' });
  const wrap = el('div', { class: 'matrix-scroll', style: 'margin-top:8px' });
  zoom.addEventListener('input', () => wrap.style.setProperty('--zoom', zoom.value / 100));
  panel.append(el('div', { class: 'review-controls' }, el('div', { class: 'matrix-subtabs' }, ...tabs), refresh,
    el('label', { class: 'review-mode-label' }, 'Zoom ', zoom)), caption, wrap);

  /** Recompute every total and score from what is currently typed in the cells. */
  function recalc() {
    const rows = {}, cols = {};
    $$('.cell-input', wrap).forEach((i) => {
      const n = num(i.value);
      rows[i.dataset.sniper] = (rows[i.dataset.sniper] || 0) + n;
      cols[i.dataset.victim] = (cols[i.dataset.victim] || 0) + n;
    });
    $$('[data-rt]', wrap).forEach((c) => { c.textContent = rows[c.dataset.rt] || 0; });
    $$('[data-ct]', wrap).forEach((c) => { c.textContent = cols[c.dataset.ct] || 0; });
    $$('.score-input', wrap).forEach((s) => {
      if (document.activeElement === s) return;
      const id = s.dataset.user, adj = find(wrap, '.adj-input', 'user', id);
      s.value = round2((rows[id] || 0) - (cols[id] || 0) + num(adj?.value));
    });
  }

  async function save(input, path, body, revert) {
    input.disabled = true;
    try { await api(path, { method: 'POST', body }); return true; }
    catch (err) { alert(`Couldn't save: ${err.message}`); revert(); return false; }
    finally { input.disabled = false; }
  }

  async function load() {
    caption.textContent = CAPTIONS[view];
    render(wrap, 'Loading…');
    const d = await api(`/matrix/${view}`);
    if (!d.users.length) { render(wrap, empty('No one on the roster yet.')); return; }
    const full = view === 'true';
    render(wrap, html`
      <table class="matrix-table">
        <thead><tr><th>Sniper \\ Victim</th><th>Snipes</th>${d.users.map((u) => html`<th>${u.display}</th>`)}
          ${full ? html`<th>Sniped</th><th>Adjustment</th><th>Score</th>` : ''}</tr></thead>
        <tbody>${d.users.map((row) => html`<tr><td>${row.display}</td><td class="row-total" data-rt="${row.id}">${d.row_totals[row.id] || 0}</td>
          ${d.users.map((col) => {
            if (row.id === col.id) return html`<td></td>`;
            const n = (d.cells[row.id] || {})[col.id] || 0;
            return d.editable
              ? html`<td><input type="text" inputmode="numeric" class="cell-input" data-sniper="${row.id}" data-victim="${col.id}" value="${n}"></td>`
              : html`<td>${n || ''}</td>`;
          })}
          ${full ? html`<td class="row-total" data-ct="${row.id}">${d.col_totals[row.id] || 0}</td>
            <td><input type="text" inputmode="decimal" class="cell-input adj-input" data-user="${row.id}" value="${d.adjustments[row.id] || 0}"></td>
            <td><input type="text" inputmode="decimal" class="cell-input score-input big-number" data-user="${row.id}"
              value="${round2((d.row_totals[row.id] || 0) - (d.col_totals[row.id] || 0) + (d.adjustments[row.id] || 0))}"></td>` : ''}
        </tr>`)}</tbody>
        <tfoot><tr><td>Sniped (total)</td><td></td>${d.users.map((u) => html`<td class="row-total" data-ct="${u.id}">${d.col_totals[u.id] || 0}</td>`)}
          ${full ? html`<td></td><td></td><td></td>` : ''}</tr></tfoot>
      </table>`);

    $$('.cell-input[data-sniper]', wrap).forEach((input) => {
      let last = input.value;
      input.addEventListener('input', recalc);                           // live, before saving
      input.addEventListener('change', async () => {
        const value = parseInt(input.value, 10);
        if (Number.isNaN(value)) { input.value = last; recalc(); return; }
        const ok = await save(input, `/matrix/${view}/cell`,
          { sniper: input.dataset.sniper, victim: input.dataset.victim, value }, () => { input.value = last; });
        if (ok) last = String(value);
        input.value = last;
        recalc();
      });
    });

    $$('.adj-input', wrap).forEach((input) => {
      let last = input.value;
      input.addEventListener('input', recalc);
      input.addEventListener('change', async () => {
        const value = parseFloat(input.value);
        if (Number.isNaN(value)) { input.value = last; recalc(); return; }
        const ok = await save(input, '/adjustments', { user_id: input.dataset.user, adjustment: value }, () => { input.value = last; });
        if (ok) last = String(value);
        input.value = last;
        recalc();
      });
    });

    // The big number: typing a Score stores the Adjustment that makes Snipes − Sniped + Adjustment equal it.
    $$('.score-input', wrap).forEach((input) => {
      let last = input.value;
      input.addEventListener('change', async () => {
        const want = parseFloat(input.value);
        if (Number.isNaN(want)) { input.value = last; return; }
        const id = input.dataset.user;
        const rt = num(find(wrap, '[data-rt]', 'rt', id)?.textContent);
        const ct = num(find(wrap, '[data-ct]', 'ct', id)?.textContent);
        const adj = round2(want - (rt - ct));
        const adjInput = find(wrap, '.adj-input', 'user', id);
        const ok = await save(input, '/adjustments', { user_id: id, adjustment: adj }, () => { input.value = last; });
        if (ok) { adjInput.value = adj; }
        recalc();
        last = input.value;
      });
    });
  }
  return { activate: load };
}
