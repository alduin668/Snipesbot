import { $ } from './dom.js';

const overlay = () => $('#modal-overlay');
const body = () => $('#modal-body');

export function openModal(node) {
  body().replaceChildren(node);
  overlay().style.display = 'flex';
}

export function closeModal() {
  overlay().style.display = 'none';
  body().replaceChildren();
}

export function initModal() {
  overlay().addEventListener('click', (e) => { if (e.target === overlay()) closeModal(); });
  $('#modal-close').addEventListener('click', closeModal);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });
}
