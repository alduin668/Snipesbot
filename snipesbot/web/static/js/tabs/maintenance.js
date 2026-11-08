import { api } from '../api.js';
import { el, fromHtml, html, render } from '../dom.js';

export function mount(panel) {
  const list = el('div');
  const output = el('div', { class: 'console', style: 'display:none' });
  panel.append(list, output);
  let poll = null;

  function paramInput(p) {
    const input = el('input', { type: p.kind === 'bool' ? 'checkbox' : 'date', 'data-param': p.name });
    const label = el('label', { class: 'review-mode-label', title: p.help || '' }, input, ' ' + p.label);
    return { input, label };
  }

  function taskCard(key, t) {
    const inputs = t.params.map(paramInput);
    const run = el('button', { type: 'button', class: `btn ${t.dangerous ? 'danger' : 'primary'}`, text: 'Run' });
    const card = fromHtml(html`<div class="card"><div><strong>${t.label}</strong></div><div class="caption">${t.description}</div><div class="resolve-row"></div></div>`);
    card.querySelector('.resolve-row').append(...inputs.map((i) => i.label), run);
    run.addEventListener('click', () => start(key, t, inputs, run));
    return card;
  }

  async function start(key, t, inputs, btn) {
    if (t.dangerous && !confirm(`${t.label}\n\n${t.description}\n\nRun it?`)) return;
    const body = {};
    inputs.forEach(({ input }) => {
      const v = input.type === 'checkbox' ? input.checked : input.value;
      if (v) body[input.dataset.param] = v;
    });
    output.style.display = 'block';
    output.textContent = `Starting ${t.label}…\n`;
    btn.disabled = true;
    clearInterval(poll);
    let runId;
    try { ({ run_id: runId } = await api(`/maintenance/${key}/run`, { method: 'POST', body })); }
    catch (err) { output.textContent += `Not started: ${err.message}\n`; btn.disabled = false; return; }

    poll = setInterval(async () => {
      try {
        const s = await api(`/maintenance/runs/${runId}`);
        output.textContent = s.output || '(no output yet)';
        output.scrollTop = output.scrollHeight;
        if (s.done) {
          clearInterval(poll);
          output.textContent += `\n\n[${s.ok ? 'finished' : 'failed'}]`;
          btn.disabled = false;
        }
      } catch (err) { clearInterval(poll); output.textContent += `\nLost track of the run: ${err.message}`; btn.disabled = false; }
    }, 1500);
  }

  return {
    async activate() {
      if (list.firstChild) return;
      const tasks = await api('/maintenance/tasks');
      list.replaceChildren(...Object.entries(tasks).map(([k, t]) => taskCard(k, t)));
    },
  };
}
