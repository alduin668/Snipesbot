import { api } from '../api.js';
import { el, html, render } from '../dom.js';

const fmt = (v) => (typeof v === 'number' ? v.toFixed(2).replace(/\.00$/, '') : v);

/** Highest value first; ties by name so the order never jumps around. */
const desc = (obj) => Object.entries(obj).sort((a, b) => (b[1] - a[1]) || String(a[0]).localeCompare(String(b[0])));

export function mount(panel) {
  const run = el('button', { type: 'button', class: 'btn', text: 'Run fresh analysis' });
  const body = el('div', { style: 'margin-top:14px' });
  panel.append(run, body);

  async function load() {
    render(body, 'Loading…');
    const [d, logic] = await Promise.all([api('/analysis'), api('/analysis/logic')]);
    const order = Object.fromEntries(logic.analyses.map((a) => [a.key, a.order]));
    const dictTable = (title, key, obj) => html`
      <div class="stat-block"><h3>${title}</h3><div class="caption">${order[key] || ''}</div><table><tbody>
        ${Object.keys(obj).length
          ? desc(obj).map(([k, v], i) => html`<tr><td>${i + 1}</td><td>${k}</td><td>${fmt(v)}</td></tr>`)
          : html`<tr><td colspan="3">—</td></tr>`}
      </tbody></table></div>`;

    render(body, html`
      <details class="logic" open><summary>How every analysis works (current logic)</summary>
        <h4>How a photo is judged</h4><ul>${logic.classification.map((t) => html`<li>${t}</li>`)}</ul>
        <h4>How scoring works</h4><ul>${logic.scoring.map((t) => html`<li>${t}</li>`)}</ul>
        <h4>Each analysis</h4>
        <ul>${logic.analyses.map((a) => html`<li><b>${a.title}</b> <span class="order">· ${a.order}</span><br>${a.logic}</li>`)}</ul>
      </details>
      <div class="stat-block"><h3>Leaderboard</h3><div class="caption">${order.leaderboard}</div>
        <table><thead><tr><th>Rank</th><th>User</th><th>Snipes</th><th>Sniped</th><th>Adjustment</th><th>Score</th></tr></thead>
        <tbody>${d.leaderboard.length
          ? d.leaderboard.map((r) => html`<tr><td>${r.rank}</td><td>${r.user}</td><td>${r.snipes}</td><td>${r.sniped}</td><td>${fmt(r.adjustment)}</td><td>${fmt(r.score)}</td></tr>`)
          : html`<tr><td colspan="6">No confirmed snipes yet</td></tr>`}</tbody></table></div>
      <div class="stat-block"><h3>Rivalries</h3><div class="caption">${order.rivalries}</div><table><tbody>
        ${d.rivalries.length ? d.rivalries.map((r, i) => html`<tr><td>${i + 1}</td><td>${r.pair.join(' vs ')}</td><td>${r.count}</td></tr>`) : html`<tr><td colspan="3">—</td></tr>`}
      </tbody></table></div>
      <div class="stat-block"><h3>No-points snipes (target is on the do-not-snipe list)</h3><div class="caption">${order.no_points}</div>
        <p class="caption">${d.no_points.total} snipe(s) logged but not scored — neither the sniper nor the target earns points.</p>
        <table><tbody>${d.no_points.recent.length
          ? d.no_points.recent.map((r) => html`<tr><td>${r.date}</td><td>${r.sniper}</td><td>→ ${r.victims.join(', ')}</td></tr>`)
          : html`<tr><td colspan="3">None</td></tr>`}</tbody></table></div>
      ${dictTable('No-points snipes by sniper', 'no_points', d.no_points.by_sniper)}
      ${dictTable('Sniping streaks (days)', 'streaks', d.streaks)}
      ${dictTable('Untouchable (days since last sniped)', 'untouchable', d.untouchable)}
      ${dictTable('Retaliation speed (avg hrs)', 'retaliation_speed', d.retaliation_speed)}
      ${dictTable('Peak days', 'peak_by_day', d.peak_by_day)}
      ${dictTable('Peak time of day', 'peak_by_bucket', d.peak_by_bucket)}
      ${dictTable('Notable ratio', 'notable_ratio', d.notable_ratio)}
      ${dictTable('Group snipes — as sniper', 'group_snipe_as_sniper', d.group_snipe_as_sniper)}
      ${dictTable('Group snipes — as victim', 'group_snipe_as_victim', d.group_snipe_as_victim)}
      <div class="stat-block"><h3>Review queue size (needs a decision)</h3>${d.queue_size}</div>`);
  }
  run.addEventListener('click', load);
  return { activate: load };
}
