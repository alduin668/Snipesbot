// Safe HTML building. Every interpolated value is escaped unless it was
// itself produced by html`` (or wrapped in raw()), so text that came from
// Slack -- captions, names, file names -- can never become markup.
const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

export const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ESC[c]);

class Safe {
  constructor(s) { this.s = s; }
  toString() { return this.s; }
}

export const raw = (s) => new Safe(s);

const part = (v) => (v instanceof Safe ? v.s : Array.isArray(v) ? v.map(part).join('') : esc(v));

export const html = (strings, ...vals) =>
  new Safe(strings.reduce((out, s, i) => out + s + (i < vals.length ? part(vals[i]) : ''), ''));

/** Replace an element's contents with safe html (or with escaped text). */
export function render(el, content) {
  el.innerHTML = content instanceof Safe ? content.s : esc(content);
  return el;
}

/** Build a single element from safe html. */
export function fromHtml(safe) {
  const t = document.createElement('template');
  t.innerHTML = String(safe).trim();
  return t.content.firstElementChild;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  node.append(...children);
  return node;
}

export const empty = (text) => html`<div class="empty">${text}</div>`;
