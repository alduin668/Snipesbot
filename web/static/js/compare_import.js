import { api } from '../api.js';
import { el } from '../dom.js';

const NONE = '__none';
const STATUS = { auto: 'matched', alias: 'remembered', ambiguous: 'several people fit', unmatched: 'no match', skip: 'skipped' };

/** The "Paste / notes" sub-tab: paste the human's sheet, names are matched automatically,
 *  only the few it can't resolve need a click, and every choice is remembered. */
export function paintImportTab(body, data, reload) {
  const text = el('textarea', { class: 'cmp-textarea', placeholder: 'Paste the whole sheet here, corner cell and totals included.\nTotal columns/rows ("Sniped", "Sniper", "Total") are skipped automatically.' });
  const out = el('div', { style: 'margin-top:12px' });
  const matchBtn = el('button', { type: 'button', class: 'btn primary', text: 'Match names' });
  matchBtn.addEventListener('click', runPreview);

  async function runPreview() {
    if (!text.value.trim()) return;
    out.replaceChildren('Matching…');
    try { show(await api('/compare/human/import/preview', { method: 'POST', body: { text: text.value } })); }
    catch (err) { out.replaceChildren(el('div', { class: 'cmp-note', text: err.message })); }
  }

  function show(d) {
    const roster = [...d.roster].sort((a, b) => a.name.localeCompare(b.name));
    const picks = [];                                           // { key, select }
    const opt = (parent, value, label) => { const o = document.createElement('option'); o.value = value; o.textContent = label; parent.append(o); };
    const group = (s, label) => { const g = document.createElement('optgroup'); g.label = label; s.append(g); return g; };

    const makeSelect = (n) => {
      const s = document.createElement('select');
      opt(s, NONE, '— choose —'); opt(s, '', 'Ignore this name');
      if (n.candidates.length) { const g = group(s, 'Closest matches'); n.candidates.forEach((c) => opt(g, c.id, c.name + (c.on_roster ? '' : ' (not on roster)'))); }
      const g2 = group(s, 'Everyone on the roster'); roster.forEach((r) => opt(g2, r.id, r.name));
      const want = n.status === 'auto' || n.status === 'alias' ? n.user_id : NONE;
      if (![...s.options].some((o) => o.value === want)) opt(s, want, n.user_name || want);
      s.value = want;
      s.addEventListener('change', refreshCount);
      picks.push({ key: n.key, select: s });
      return s;
    };
    const row = (n) => el('tr', {}, el('td', {}, el('b', { text: n.raw })), el('td', { class: 'num', text: `${n.snipes} / ${n.sniped}` }),
      el('td', {}, makeSelect(n)), el('td', { class: 'cmp-note', text: STATUS[n.status] || n.status }));
    const table = (list) => el('table', { class: 'cmp-table' }, el('thead', {}, el('tr', {}, el('th', { text: 'Name in the sheet' }),
      el('th', { class: 'num', text: 'snipes / sniped' }), el('th', { text: 'Is really' }), el('th', { text: '' }))), el('tbody', {}, ...list.map(row)));

    const needs = d.names.filter((n) => n.status === 'unmatched' || n.status === 'ambiguous');
    const rest = d.names.filter((n) => !needs.includes(n));
    const importBtn = el('button', { type: 'button', class: 'btn primary', text: 'Replace human matrix with this' });
    function refreshCount() {
      const pending = picks.filter((p) => p.select.value === NONE).length;
      importBtn.textContent = pending ? `Import (${pending} name${pending === 1 ? '' : 's'} still need a choice)` : 'Replace human matrix with this';
    }
    importBtn.addEventListener('click', async () => {
      const pending = picks.filter((p) => p.select.value === NONE).length;
      if (pending) { alert(`${pending} name(s) still say “— choose —”. Pick a person, or “Ignore this name”.`); return; }
      if (!confirm('Replace the whole human matrix with this one?')) return;
      const mapping = {}; picks.forEach((p) => { mapping[p.key] = p.select.value; });
      try {
        const r = await api('/compare/human/import/apply', { method: 'POST', body: { text: text.value, mapping } });
        alert(`Imported ${r.cells} cell(s).` + (r.aliases_saved ? ` Remembered ${r.aliases_saved} name choice(s).` : '') + (r.ignored_snipes ? ` ${r.ignored_snipes} snipe(s) belonged to ignored names.` : ''));
        reload();
      } catch (err) { alert(err.message); }
    });

    const kids = [el('div', { class: 'cmp-note', text: `${d.total} snipes in ${d.cells} cells. ${d.names.length} names have numbers; ${d.empty_ignored} with no numbers were skipped. ${needs.length ? needs.length + ' need a quick look below.' : 'Every name matched.'}` })];
    if (d.warnings.length) kids.push(el('div', { class: 'cmp-note' }, el('b', { text: 'The sheet’s own totals disagree in places (a typo in the sheet?): ' }), d.warnings.join(' · ')));
    if (needs.length) kids.push(el('h3', { text: 'Needs you', style: 'margin-top:14px' }), table(needs));
    if (rest.length) { const det = el('details', { style: 'margin-top:12px' }, el('summary', { text: `Matched automatically (${rest.length}) — change any that are wrong` }), table(rest)); kids.push(det); }
    kids.push(el('div', { style: 'margin-top:12px' }, importBtn));
    out.replaceChildren(...kids);
    refreshCount();
  }

  // --- when was this human pass made? (unchanged from before) ---
  const asOf = el('input', { type: 'text', class: 'cell-input', style: 'width:12em', placeholder: 'e.g. 2026-11-08', value: data.meta.as_of || '' });
  const note = el('textarea', { class: 'cmp-textarea', style: 'min-height:60px', placeholder: 'Notes about this human pass (who did it, what range of the chat it covers)…' });
  note.value = data.meta.note || '';
  const save = el('button', { type: 'button', class: 'btn', text: 'Save notes' });
  save.addEventListener('click', async () => { await api('/compare/human/meta', { method: 'POST', body: { as_of: asOf.value, note: note.value } }); reload(); });

  body.replaceChildren(
    el('div', { class: 'cmp-note', text: 'Paste the human’s sheet exactly as it is. Names are matched to Slack people automatically (first names work); you only resolve the ones it can’t, once, and the choice is remembered.' }),
    text, matchBtn, out,
    el('h3', { text: 'When was this human pass made?', style: 'margin-top:20px' }), asOf, note, save);
}
