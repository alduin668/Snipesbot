import { api } from '../api.js';
import { $$, el, html, render } from '../dom.js';

export function mount(panel) {
  const refresh = el('button', { type: 'button', class: 'btn', text: 'Refresh' });
  const list = el('div', { style: 'margin-top:14px' });
  panel.append(el('div', { class: 'review-controls' }, refresh,
    el('span', { class: 'review-mode-label', text: 'Manual per-person score adjustments, added to Score alongside Snipes − Sniped. Edit and click away to save.' })), list);

  async function load() {
    render(list, 'Loading…');
    const { users } = await api('/adjustments');
    render(list, html`<table class="matrix-table" style="width:auto">
      <thead><tr><th>User</th><th>Adjustment</th></tr></thead>
      <tbody>${users.length ? users.map((u) => html`<tr><td>${u.display}</td>
        <td><input type="text" inputmode="decimal" class="cell-input adj-input" data-user="${u.id}" value="${u.adjustment}"></td></tr>`)
        : html`<tr><td colspan="2">No users yet</td></tr>`}</tbody></table>`);
    $$('.adj-input', list).forEach((input) => {
      let last = input.value;
      input.addEventListener('change', async () => {
        const value = parseFloat(input.value);
        if (Number.isNaN(value)) { input.value = last; return; }
        input.disabled = true;
        try { await api('/adjustments', { method: 'POST', body: { user_id: input.dataset.user, adjustment: value } }); last = String(value); }
        catch (err) { alert(`Couldn't save: ${err.message}`); input.value = last; }
        input.disabled = false;
      });
    });
  }
  refresh.addEventListener('click', load);
  return { activate: load };
}
