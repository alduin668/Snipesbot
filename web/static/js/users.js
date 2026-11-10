import { api } from './api.js';

let cache = null;

/** [{id, name, display}] for every user with a cached name. */
export async function loadUsers(force = false) {
  if (!cache || force) cache = (await api('/users')).users || [];
  return cache;
}
