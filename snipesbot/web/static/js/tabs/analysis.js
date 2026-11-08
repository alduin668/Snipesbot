import { api } from '../api.js';
import { el, html, render } from '../dom.js';

const fmt = (v) => (typeof v === 'number' ? v.toFixed(2).replace(/\.00$/, '') : v);

const dictTable = (title, obj) => html`
  <div class="stat-block"><h3>${title}</h3><table><tbody>
    ${Object.keys(obj).length
      ? Object.entries(obj).map(([k, v]) => html`<tr><td>${k}</td><td>${fmt(v)}</td></tr>`)
      : html`<tr><td colspan="2">—</td></tr>`}
  </tbody></table></div>`;

export function mount(panel) {
  const run = el('button', { type: 'button', class: 'btn', text: 'Run fresh analysis' });
  const body = el('div', { style: 'margin-top:14px' });
  panel.append(run, body);

  async function load() {
    render(body, 'Loading…');
    const d = await api('/analysis');
    render(body, html`
      <div class="stat-block"><h3>Leaderboard</h3>
        <table><thead><tr><th>Rank</th><th>User</th><th>Snipes</th><th>Sniped</th><th>Adjustment</th><th>Score</th></tr></thead>
        <tbody>${d.leaderboard.length
          ? d.leaderboard.map((r) => html`<tr><td>${r.rank}</td><td>${r.user}</td><td>${r.snipes}</td><td>${r.sniped}</td><td>${fmt(r.adjustment)}</td><td>${fmt(r.score)}</td></tr>`)
          : html`<tr><td colspan="6">No confirmed snipes yet</td></tr>`}</tbody></table></div>
      <div class="stat-block"><h3>Rivalries</h3><table><tbody>
        ${d.rivalries.length ? d.rivalries.map((r) => html`<tr><td>${r.pair.join(' vs ')}</td><td>${r.count}</td></tr>`) : html`<tr><td colspan="2">—</td></tr>`}
      </tbody></table></div>
      ${dictTable('Sniping streaks (days)', d.streaks)}
      ${dictTable('Untouchable (days since last sniped)', d.untouchable)}
      ${dictTable('Retaliation speed (avg hrs)', d.retaliation_speed)}
      ${dictTable('Peak days', d.peak_by_day)}
      ${dictTable('Peak time of day', d.peak_by_bucket)}
      ${dictTable('Notable ratio', d.notable_ratio)}
      ${dictTable('Group snipes — as sniper', d.group_snipe_as_sniper)}
      ${dictTable('Group snipes — as victim', d.group_snipe_as_victim)}
      <div class="stat-block"><h3>Review queue size</h3>${d.queue_size}</div>`);
  }
  run.addEventListener('click', load);
  return { activate: load };
}
