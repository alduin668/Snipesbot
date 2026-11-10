import { api } from '../api.js';
import { el, fromHtml, html } from '../dom.js';

const clock = (seconds) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;

export function mount(panel) {
  const banner = el('div', { class: 'run-banner', role: 'status', 'aria-live': 'polite', hidden: '' });
  const list = el('div');
  const output = el('div', { class: 'console', style: 'display:none' });
  panel.append(banner, list, output);

  const buttons = [];                 // { key, btn }
  let poll = null;                    // polling the run we started (or resumed)
  let watching = null;                // run id being watched
  let ticker = null;                  // smooth mm:ss between polls
  let external = null;                // polling for a sync someone else is running

  // ---- banner ---------------------------------------------------------------------------------------
  function stopTicker() { clearInterval(ticker); ticker = null; }

  function showRunning({ label, elapsed = 0, last_line: last = '' }, who = '') {
    stopTicker();
    banner.hidden = false;
    banner.className = 'run-banner running';
    banner.replaceChildren(fromHtml(html`
      <div class="run-banner-main">
        <span class="spinner" aria-hidden="true"></span>
        <strong>${who ? who : `Running: ${label}`}</strong>
        <span class="run-timer">${clock(elapsed)}</span>
        <span class="run-last">${last}</span>
      </div>`), el('div', { class: 'progress-bar indeterminate' }));
    const started = Date.now() - elapsed * 1000;
    ticker = setInterval(() => {
      const t = banner.querySelector('.run-timer');
      if (t) t.textContent = clock((Date.now() - started) / 1000);
    }, 1000);
  }

  function showFinished(run) {
    stopTicker();
    banner.hidden = false;
    banner.className = `run-banner ${run.ok ? 'done' : 'failed'}`;
    banner.replaceChildren(fromHtml(html`
      <div class="run-banner-main">
        <span aria-hidden="true">${run.ok ? '✓' : '✗'}</span>
        <strong>${run.ok ? 'Finished' : 'Failed'}: ${run.label}</strong>
        <span class="run-timer">took ${clock(run.elapsed)}</span>
        <span class="run-last">${run.ok ? '' : run.error || ''}</span>
      </div>`));
  }

  function lock(activeKey, why) {
    buttons.forEach(({ key, btn }) => {
      btn.disabled = true;
      btn.textContent = key === activeKey ? 'Running…' : 'Busy';
      btn.title = why;
    });
  }

  function unlock() {
    buttons.forEach(({ btn }) => { btn.disabled = false; btn.textContent = 'Run'; btn.title = ''; });
  }

  const announce = () => {
    try { window.dispatchEvent(new CustomEvent('snipesbot:activity')); } catch { /* purely cosmetic: never let it break a run */ }
  };

  // ---- watching a run -----------------------------------------------------------------------------------
  function watch(runId, key) {
    if (watching === runId) return;
    clearInterval(poll);
    watching = runId;
    const tick = async () => {
      try {
        const run = await api(`/maintenance/runs/${runId}`);
        output.style.display = 'block';
        output.textContent = run.output || '(no output yet)';
        output.scrollTop = output.scrollHeight;
        if (run.done) {
          clearInterval(poll);
          watching = null;
          showFinished(run);
          unlock();
          announce();
        } else {
          showRunning(run);
          lock(key || run.task, `Waiting for "${run.label}" to finish`);
        }
      } catch (err) {
        clearInterval(poll);
        watching = null;
        unlock();
        banner.className = 'run-banner failed';
        banner.textContent = `Lost track of the run: ${err.message}`;
      }
    };
    poll = setInterval(tick, 1000);
    tick();
  }

  function watchExternal() {
    if (external) return;
    showRunning({ label: 'sync', elapsed: 0 }, 'A sync is already running in another process (the bot’s scheduled pass or the CLI)');
    banner.querySelector('.run-timer').textContent = '';
    lock(null, 'A sync is running elsewhere');
    external = setInterval(async () => {
      try {
        const act = await api('/maintenance/status');
        if (act.running) { clearInterval(external); external = null; watch(act.running.id); }
        else if (!act.external_sync) {
          clearInterval(external); external = null; stopTicker();
          banner.hidden = true; unlock(); announce();
        }
      } catch { /* try again next tick */ }
    }, 3000);
  }

  async function resume() {
    try {
      const act = await api('/maintenance/status');
      if (act.running) watch(act.running.id, act.running.task);
      else if (act.external_sync) watchExternal();
    } catch { /* the header shows connectivity problems */ }
  }

  // ---- starting a task ----------------------------------------------------------------------------------------
  function paramInput(p) {
    const input = el('input', { type: p.kind === 'bool' ? 'checkbox' : 'date', 'data-param': p.name });
    const label = el('label', { class: 'review-mode-label', title: p.help || '' }, input, ' ' + p.label);
    return { input, label };
  }

  function taskCard(key, t) {
    const inputs = t.params.map(paramInput);
    const run = el('button', { type: 'button', class: `btn ${t.dangerous ? 'danger' : 'primary'}`, text: 'Run' });
    buttons.push({ key, btn: run });
    const card = fromHtml(html`<div class="card"><div><strong>${t.label}</strong></div><div class="caption">${t.description}</div><div class="resolve-row"></div></div>`);
    card.querySelector('.resolve-row').append(...inputs.map((i) => i.label), run);
    run.addEventListener('click', () => start(key, t, inputs));
    return card;
  }

  async function start(key, t, inputs) {
    if (t.dangerous && !confirm(`${t.label}\n\n${t.description}\n\nRun it?`)) return;
    const body = {};
    inputs.forEach(({ input }) => {
      const v = input.type === 'checkbox' ? input.checked : input.value;
      if (v) body[input.dataset.param] = v;
    });
    output.style.display = 'block';
    output.textContent = `Starting ${t.label}…\n`;
    showRunning({ label: t.label, elapsed: 0 });          // show it immediately, before the server answers
    lock(key, `Waiting for "${t.label}" to finish`);
    try {
      const { run_id: runId } = await api(`/maintenance/${key}/run`, { method: 'POST', body });
      announce();
      watch(runId, key);
    } catch (err) {
      stopTicker();
      banner.className = 'run-banner failed';
      banner.textContent = `Not started: ${err.message}`;
      unlock();
      resume();                                            // maybe something else is running; show that instead
    }
  }

  return {
    async activate() {
      if (!list.firstChild) {
        const tasks = await api('/maintenance/tasks');
        list.replaceChildren(...Object.entries(tasks).map(([k, t]) => taskCard(k, t)));
      }
      await resume();
    },
  };
}
