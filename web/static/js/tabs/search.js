import { api, query } from '../api.js';
import { createEntryCard } from '../entryCard.js';
import { $, $$, el, fromHtml, html } from '../dom.js';
import { createPagedList } from '../pagedList.js';
import { loadUsers } from '../users.js';

const KINDS = [['snipe', 'Snipe'], ['snope', 'Snope'], ['likely_snipe', 'Likely snipe'], ['unidentified', 'Unidentified'],
  ['not_a_snipe', 'NAS'], ['incorrect_snipe', 'Missnipe'], ['special', 'Special']];
const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

export function mount(panel) {
  const form = fromHtml(html`
    <div class="search-filters">
      <div class="filter-group"><label>Kind</label>
        <div class="kind-checks">${KINDS.map(([v, l]) => html`<label><input type="checkbox" class="kind" value="${v}" checked> ${l}</label>`)}</div></div>
      <div class="filter-group"><label for="s-sniper">Sniper</label><select id="s-sniper"><option value="">Any</option></select></div>
      <div class="filter-group"><label for="s-sniped">Sniped (victim)</label><select id="s-sniped"><option value="">Any</option></select></div>
      <div class="filter-group"><label for="s-time">Time of day</label><select id="s-time">
        <option value="">Any</option><option value="morning">Morning (5am–12pm)</option><option value="afternoon">Afternoon (12–5pm)</option>
        <option value="evening">Evening (5–9pm)</option><option value="night">Night (9pm–5am)</option></select></div>
      <div class="filter-group"><label for="s-day">Day of week</label><select id="s-day">
        <option value="">Any</option>${DAYS.map((d) => html`<option>${d}</option>`)}</select></div>
      <div class="filter-group"><label for="s-notable">Starred</label><select id="s-notable">
        <option value="">Any</option><option value="1">Starred only</option><option value="0">Not starred</option></select></div>
      <div class="filter-group"><label for="s-manual">Decision</label><select id="s-manual">
        <option value="">Any</option><option value="1">Manual only</option><option value="0">Automatic only</option></select></div>
      <div class="filter-group"><label for="s-q">Caption contains</label><input type="text" id="s-q" placeholder="text in caption…"></div>
    </div>`);

  const run = el('button', { type: 'button', class: 'btn primary', text: 'Search' });
  const clear = el('button', { type: 'button', class: 'btn', text: 'Clear filters' });
  const count = el('span', { class: 'review-mode-label' });

  const params = () => {
    const kinds = $$('.kind:checked', form).map((c) => c.value);
    return {
      kind: kinds.length && kinds.length < KINDS.length ? kinds.join(',') : undefined,
      sniper: $('#s-sniper', form).value, sniped: $('#s-sniped', form).value, time: $('#s-time', form).value,
      day: $('#s-day', form).value, notable: $('#s-notable', form).value, manual: $('#s-manual', form).value,
      q: $('#s-q', form).value.trim(),
    };
  };

  const list = createPagedList({
    emptyText: 'No matches.',
    storageKey: 'search',
    header: [run, clear, count],
    fetchPage: async (page) => {
      const data = await api('/entries' + query({ ...params(), ...page }));
      count.textContent = `${data.total} match${data.total === 1 ? '' : 'es'}`;
      return data;
    },
    renderItem: (entry, ctx) => createEntryCard(entry, { layout: ctx.focus ? 'focus' : 'stack', onChange: (u) => ctx.replace(u) }),
  });

  run.addEventListener('click', () => list.reset());
  $('#s-q', form).addEventListener('keydown', (e) => { if (e.key === 'Enter') list.reset(); });
  clear.addEventListener('click', () => {
    $$('.kind', form).forEach((c) => { c.checked = true; });
    $$('select', form).forEach((s) => { s.value = ''; });
    $('#s-q', form).value = '';
    list.reset();
  });
  panel.append(form, list.el);

  let ready = false;
  return {
    async activate() {
      if (ready) return;
      ready = true;
      const users = await loadUsers();
      for (const id of ['#s-sniper', '#s-sniped']) {
        $(id, form).append(...users.map((u) => el('option', { value: u.id, text: u.display })));
      }
      list.reset();
    },
  };
}
