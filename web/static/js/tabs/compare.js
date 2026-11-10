import { api } from '../api.js';
import { $, $$, el, empty, html, render } from '../dom.js';

const SUBS = [['diff', 'Discrepancies'], ['people', 'By person'], ['human', 'Human matrix (edit)'], ['import', 'Paste / notes']];
const sign = (n) => (n > 0 ? `+${n}` : String(n));
const cls = (n) => (n > 0 ? 'diff-pos' : n < 0 ? 'diff-neg' : 'diff-zero');
const r2 = (n) => Math.round(n * 100) / 100;
const find = (root, sel, attr, id) => $$(sel, root).find((n) => n.dataset[attr] === id);

export function mount(panel) {
  let sub = 'diff', data = null, showResolved = false;
  const btns = SUBS.map(([k, label]) => el('button', {
    type: 'button', class: 'matrix-subtab-btn' + (k === sub ? ' active' : ''), text: label,
    onclick: () => { sub = k; btns.forEach((b, i) => b.classList.toggle('active', SUBS[i][0] === k)); paint(); },
  }));
  const refresh = el('button', { type: 'button', class: 'btn', text: 'Refresh', onclick: () => load() });
  const applyAll = el('button', { type: 'button', class: 'btn primary', text: 'Make True match Human (all)', onclick: async () => {
    if (!confirm('Write Manual-matrix corrections and Adjustments so the True numbers equal the Human numbers everywhere? (Cells aimed at do-not-snipe people are skipped.)')) return;
    const r = await api('/compare/remedy/all', { method: 'POST', body: {} });
    alert(`Fixed ${r.cells_fixed} cell(s) and ${r.adjustments_fixed} adjustment(s).` + (r.dns_skipped ? ` Skipped ${r.dns_skipped} do-not-snipe cell(s) — decide those by hand.` : ''));
    load();
  } });
  const summary = el('div');
  const body = el('div', { style: 'margin-top:10px' });
  panel.append(el('div', { class: 'review-controls' }, el('div', { class: 'matrix-subtabs' }, ...btns), refresh, applyAll), summary, body);

  async function load() { render(body, 'Loading…'); data = await api('/compare'); paint(); }

  function paint() {
    const s = data.summary;
    const pill = (label, v, kind = '') => html`<div class="cmp-pill ${kind}">${label}<b>${v}</b></div>`;
    render(summary, html`<div class="cmp-summary">
      ${pill('Human total', s.human_total)}${pill('Bot total', s.bot_total)}${pill('True total (bot + manual)', s.true_total)}
      ${pill('Bot missed', s.bot_missed, s.bot_missed ? 'bad' : 'good')}${pill('Bot over-counted', s.bot_overcounted, s.bot_overcounted ? 'bad' : 'good')}
      ${pill('Cells still wrong in True', s.cells_still_wrong, s.cells_still_wrong ? 'bad' : 'good')}
      ${pill('People with a score gap', s.people_score_gap, s.people_score_gap ? 'bad' : 'good')}</div>
      ${data.meta.as_of || data.meta.note ? html`<div class="cmp-note">Human truth as of <b>${data.meta.as_of || '—'}</b>. ${data.meta.note}</div>` : ''}`);
    ({ diff: paintDiff, people: paintPeople, human: paintHuman, import: paintImport })[sub]();
  }

  function paintDiff() {
    const rows = data.discrepancies.filter((d) => showResolved || !d.resolved);
    if (!Object.keys(data.human_cells).length) {
      render(body, empty('No human matrix yet. Enter it in “Human matrix (edit)” or paste it in “Paste / notes”, and every difference from the bot appears here.')); return;
    }
    const toggle = el('label', { class: 'cmp-note' }, el('input', { type: 'checkbox', ...(showResolved ? { checked: '' } : {}), onchange: (e) => { showResolved = e.target.checked; paint(); } }), ' show cells already fixed');
    const table = html`<table class="cmp-table"><thead><tr><th>Sniper → Victim</th><th class="num">Human</th><th class="num">Bot</th><th class="num">Diff</th>
        <th class="num">Manual now</th><th class="num">True now</th><th>What to check</th><th></th></tr></thead><tbody>
      ${rows.length ? rows.map((d, i) => html`<tr class="${d.resolved ? 'resolved' : ''}">
        <td><b>${d.sniper_name}</b> → ${d.victim_name}${d.dns ? html` <span title="Victim is on the do-not-snipe list: the bot scores nothing for this pair">🛡️ DNS</span>` : ''}</td>
        <td class="num">${d.human}</td><td class="num">${d.bot}</td><td class="num ${cls(d.diff)}">${sign(d.diff)}</td>
        <td class="num">${d.manual}</td><td class="num ${cls(d.gap)}">${d.true}${d.gap ? ` (${sign(d.gap)} off)` : ' ✓'}</td>
        <td>${d.resolved ? 'Fixed by a manual correction' : d.diff < 0 ? 'Bot over-counted — one of its snipes for this pair is probably wrong:' : 'Bot missed — these photos weren’t counted:'}
          ${d.suspects.map((e) => html`<div class="cmp-suspect"><span class="k">${e.kind}</span> ${e.date} · ${e.sniper}${e.sniped.length ? ' → ' + e.sniped.join(', ') : ''} — ${e.caption || '(no caption)'}</div>`)}
          ${!d.resolved && !d.suspects.length ? html`<div class="cmp-suspect">No matching photos found — the human may have counted something that isn’t in the log.</div>` : ''}</td>
        <td>${d.resolved ? '' : html`<button type="button" class="btn" data-fix="${i}" title="Set Manual to ${d.remedy}">Apply ${sign(d.remedy)}</button>`}</td></tr>`)
        : html`<tr><td colspan="8">Nothing to fix — True matches the human everywhere. 🎉</td></tr>`}
      </tbody></table>`;
    render(body, html`<div class="cmp-note">Diff = Human − Bot: positive means the bot missed snipes, negative means it over-counted. “Apply” stores the Manual correction (Human − Bot) so True equals Human.</div>`);
    body.append(toggle, el('div', { class: 'matrix-scroll' }));
    render(body.lastChild, table);
    $$('[data-fix]', body).forEach((b) => b.addEventListener('click', async () => {
      const d = rows[Number(b.dataset.fix)];
      await api('/compare/remedy/cell', { method: 'POST', body: { sniper: d.sniper, victim: d.victim } });
      load();
    }));
  }

  function paintPeople() {
    const fix = (p) => p.gap.adjustment !== 0;
    const cell = (o) => html`<td class="num">${o.snipes}</td><td class="num">${o.sniped}</td><td class="num">${r2(o.adjustment)}</td><td class="num"><b>${r2(o.score)}</b></td>`;
    render(body, html`<div class="cmp-note">Score = Snipes − Sniped + Adjustment. “Bot” is the untouched bot count (no adjustments). “True” is what is scoring today.</div>
      <div class="matrix-scroll"><table class="cmp-table"><thead><tr><th rowspan="2">Person</th><th colspan="4">Human</th><th colspan="4">Bot</th><th colspan="4">True (today)</th><th colspan="2">Human − Bot / True</th><th rowspan="2"></th></tr>
        <tr>${['Snipes', 'Sniped', 'Adj', 'Score'].map((h) => html`<th class="num">${h}</th>`)}${['Snipes', 'Sniped', 'Adj', 'Score'].map((h) => html`<th class="num">${h}</th>`)}${['Snipes', 'Sniped', 'Adj', 'Score'].map((h) => html`<th class="num">${h}</th>`)}<th class="num">vs Bot</th><th class="num">vs True</th></tr></thead>
      <tbody>${data.persons.map((p) => html`<tr><td><b>${p.name}</b></td>${cell(p.human)}${cell(p.bot)}${cell(p.true)}
        <td class="num ${cls(p.diff.score)}">${sign(r2(p.diff.score))}</td><td class="num ${cls(p.gap.score)}">${sign(r2(p.gap.score))}</td>
        <td>${fix(p) ? html`<button type="button" class="btn" data-adj="${p.id}">Apply adj ${r2(p.human.adjustment)}</button>` : ''}</td></tr>`)}</tbody></table></div>`);
    $$('[data-adj]', body).forEach((b) => b.addEventListener('click', async () => {
      await api('/compare/remedy/adjustment', { method: 'POST', body: { user_id: b.dataset.adj } }); load();
    }));
  }

  function paintHuman() {
    const users = data.users, cells = data.human_cells, adj = data.human_adjustments;
    const tot = {};
    render(body, html`<div class="cmp-note">The human’s matrix: type the numbers from the full read-through. Saves as you go; Discrepancies updates when you switch tabs. Adjustment is the human’s per-person adjustment.</div>
      <div class="matrix-scroll"><table class="matrix-table"><thead><tr><th>Sniper \\ Victim</th><th>Snipes</th>${users.map((u) => html`<th>${u.display}</th>`)}<th>Sniped</th><th>Adjustment</th><th>Score</th></tr></thead>
      <tbody>${users.map((r) => html`<tr><td>${r.display}</td><td class="row-total" data-rt="${r.id}">0</td>
        ${users.map((c) => r.id === c.id ? html`<td></td>` : html`<td><input type="text" inputmode="numeric" class="cell-input" data-s="${r.id}" data-v="${c.id}" value="${(cells[r.id] || {})[c.id] || 0}"></td>`)}
        <td class="row-total" data-ct="${r.id}">0</td>
        <td><input type="text" inputmode="decimal" class="cell-input adj-input" data-u="${r.id}" value="${adj[r.id] || 0}"></td>
        <td class="row-total" data-sc="${r.id}">0</td></tr>`)}</tbody></table></div>`);
    const recalc = () => {
      const rows = {}, cols = {};
      $$('.cell-input[data-s]', body).forEach((i) => { const n = Number(i.value) || 0; rows[i.dataset.s] = (rows[i.dataset.s] || 0) + n; cols[i.dataset.v] = (cols[i.dataset.v] || 0) + n; });
      users.forEach((u) => {
        const a = Number(find(body, '.adj-input', 'u', u.id).value) || 0;
        find(body, '[data-rt]', 'rt', u.id).textContent = rows[u.id] || 0;
        find(body, '[data-ct]', 'ct', u.id).textContent = cols[u.id] || 0;
        find(body, '[data-sc]', 'sc', u.id).textContent = r2((rows[u.id] || 0) - (cols[u.id] || 0) + a);
      });
    };
    recalc();
    $$('.cell-input[data-s]', body).forEach((input) => {
      let last = input.value;
      input.addEventListener('input', recalc);
      input.addEventListener('change', async () => {
        const value = parseInt(input.value, 10);
        if (Number.isNaN(value)) { input.value = last; recalc(); return; }
        input.disabled = true;
        try { await api('/compare/human/cell', { method: 'POST', body: { sniper: input.dataset.s, victim: input.dataset.v, value } }); last = String(value);
          (cells[input.dataset.s] ||= {})[input.dataset.v] = value; }
        catch (err) { alert(`Couldn't save: ${err.message}`); }
        input.value = last; input.disabled = false; recalc();
      });
    });
    $$('.adj-input', body).forEach((input) => {
      let last = input.value;
      input.addEventListener('input', recalc);
      input.addEventListener('change', async () => {
        const value = parseFloat(input.value);
        if (Number.isNaN(value)) { input.value = last; recalc(); return; }
        input.disabled = true;
        try { await api('/compare/human/adjustment', { method: 'POST', body: { user_id: input.dataset.u, adjustment: value } }); last = String(value); adj[input.dataset.u] = value; }
        catch (err) { alert(`Couldn't save: ${err.message}`); }
        input.value = last; input.disabled = false; recalc();
      });
    });
  }

  function paintImport() {
    const text = el('textarea', { class: 'cmp-textarea', placeholder: 'Paste the human’s matrix from a spreadsheet:\n(blank)\tAlice\tBob\nCarol\t4\t1\nAlice\t0\t2' });
    const go = el('button', { type: 'button', class: 'btn primary', text: 'Replace human matrix with this', onclick: async () => {
      if (!text.value.trim()) return;
      if (!confirm('Replace the whole human matrix with the pasted one?')) return;
      try { const r = await api('/compare/human/import', { method: 'POST', body: { text: text.value } }); alert(`Imported ${r.cells} cell(s).`); text.value = ''; load(); }
      catch (err) { alert(err.message); }
    } });
    const asOf = el('input', { type: 'text', class: 'cell-input', style: 'width:12em', placeholder: 'e.g. 2026-11-01', value: data.meta.as_of || '' });
    const note = el('textarea', { class: 'cmp-textarea', style: 'min-height:60px', placeholder: 'Notes about this human pass (who did it, what range of the chat it covers)…' });
    note.value = data.meta.note || '';
    const save = el('button', { type: 'button', class: 'btn', text: 'Save notes', onclick: async () => {
      await api('/compare/human/meta', { method: 'POST', body: { as_of: asOf.value, note: note.value } }); load();
    } });
    body.replaceChildren(
      el('div', { class: 'cmp-note', text: 'First row = victim names, first column = sniper names; tab-, comma- or semicolon-separated. Names must match people on the roster. Nothing is saved if any name doesn’t match.' }),
      text, go,
      el('h3', { text: 'When was this human pass made?', style: 'margin-top:20px' }), asOf, note, save);
  }

  return { activate: load };
}
