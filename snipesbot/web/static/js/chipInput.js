import { el } from './dom.js';

/**
 * Name picker with chips. Used for "who was sniped" (multi), the Special
 * tab's sniper (single, free text allowed) and the do-not-snipe add box
 * (onPick: pick-and-forget). Chips remember a real user's Slack id apart
 * from the text shown, which a plain <datalist> cannot do.
 *
 * Everything is built with textContent, so names are never parsed as HTML.
 */
export function createChipInput({ users, single = false, allowCustom = false, initial = [], placeholder = '', onPick = null }) {
  let chips = initial.map((c) => ({ ...c }));
  const root = el('div', { class: 'chip-input' + (single ? ' single' : '') });
  const chipsEl = el('div', { class: 'chips' });
  const input = el('input', { type: 'text', class: 'chip-search', placeholder, autocomplete: 'off' });
  const dropdown = el('div', { class: 'chip-dropdown' });
  root.append(chipsEl, input, dropdown);

  function renderChips() {
    chipsEl.replaceChildren(...chips.map((c) => el('span', { class: 'chip' },
      c.display,
      el('button', { type: 'button', class: 'chip-x', 'aria-label': `Remove ${c.display}`, onclick: () => {
        chips = chips.filter((x) => x.id !== c.id);
        renderChips();
      } }, '×'))));
  }

  const closeDropdown = () => { dropdown.style.display = 'none'; dropdown.replaceChildren(); };

  function add(id, display) {
    if (onPick) { onPick({ id, display }); }
    else if (single) chips = [{ id, display }];
    else if (!chips.some((c) => c.id === id)) chips.push({ id, display });
    renderChips();
    input.value = '';
    closeDropdown();
    input.focus();
  }

  function option(text, cls, handler) {
    const o = el('div', { class: 'chip-option ' + cls }, text);
    o.addEventListener('mousedown', (ev) => { ev.preventDefault(); handler(); });
    return o;
  }

  input.addEventListener('input', () => {
    const q = input.value.trim();
    if (!q) { closeDropdown(); return; }
    const chosen = new Set(chips.map((c) => c.id));
    const matches = users()
      .filter((u) => !chosen.has(u.id) && (u.name || u.id).toLowerCase().includes(q.toLowerCase()))
      .slice(0, 8)
      .map((u) => option(u.display, '', () => add(u.id, u.display)));
    const rows = [...matches];
    if (allowCustom) rows.push(option(`Add "${q}"`, 'chip-option-custom', () => add(q, q)));
    if (!rows.length) rows.push(el('div', { class: 'chip-empty' }, 'No matching names yet'));
    dropdown.replaceChildren(...rows);
    dropdown.style.display = 'block';
  });

  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') {
      ev.preventDefault();
      const q = input.value.trim();
      if (q && allowCustom) add(q, q);
    }
    if (ev.key === 'Escape') closeDropdown();
  });
  input.addEventListener('blur', () => setTimeout(closeDropdown, 150));

  renderChips();
  return {
    el: root,
    ids: () => chips.map((c) => c.id),
    clear: () => { chips = []; renderChips(); },
  };
}
