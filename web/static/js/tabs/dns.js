import { api } from '../api.js';
import { createChipInput } from '../chipInput.js';
import { el, empty, html, render } from '../dom.js';
import { loadUsers } from '../users.js';

export function mount(panel) {
  const refresh = el('button', { type: 'button', class: 'btn', text: 'Refresh' });
  const list = el('div', { style: 'margin-top:14px' });
  const slot = el('div', { style: 'flex:1;min-width:260px' });
  panel.append(el('div', { class: 'review-controls' }, refresh, slot), list);

  async function load() {
    render(list, 'Loading…');
    const { users } = await api('/dns');
    if (!users.length) { render(list, empty('Nobody is on the do-not-snipe list.')); return; }
    list.replaceChildren(...users.map((u) => el('div', { class: 'card dns-row' },
      el('span', { text: u.display }),
      el('button', { type: 'button', class: 'btn danger', text: 'Remove', onclick: async (ev) => {
        ev.target.disabled = true;
        await api(`/dns/${encodeURIComponent(u.id)}`, { method: 'DELETE' });
        load();
      } }))));
  }

  refresh.addEventListener('click', load);
  return {
    async activate() {
      const users = await loadUsers();
      if (!slot.firstChild) {
        slot.append(createChipInput({
          users: () => users, placeholder: 'Add someone to the do-not-snipe list…',
          onPick: async (u) => { await api('/dns', { method: 'POST', body: { user_id: u.id } }); load(); },
        }).el);
      }
      load();
    },
  };
}
