import { csrfToken } from '../helpers/csrfToken';

function sessionId() {
  const bytes = new Uint8Array(16);
  window.crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function validProgress(value, revision) {
  return !!value && value.content_revision === revision &&
    Number.isFinite(value.position) && value.position >= 0 &&
    Number.isFinite(value.duration) && value.duration > 0 && typeof value.completed === 'boolean';
}

export class ProgressService {
  constructor({ siteId = 'media-cms', userKey = null, storage, fetch: fetcher, apiBase = '/api/v1' } = {}) {
    this.userKey = userKey;
    this.prefix = `MediaCMS[${siteId}][guest-playback-v1]`;
    try { this.storage = storage === undefined ? window.localStorage : storage; } catch { this.storage = null; }
    this.fetch = fetcher || ((...args) => window.fetch(...args));
    this.apiBase = apiBase.replace(/\/$/, '');
    this.cache = new Map();
    this.versions = new Map();
    this.pending = new Map();
    this.reads = new Map();
    this.listeners = new Map();
  }

  localRead(token) {
    try { return JSON.parse(this.storage?.getItem(`${this.prefix}[${token}]`) || 'null'); } catch { return null; }
  }

  cached(media) {
    const value = this.cache.get(media.token);
    return validProgress(value, media.revision) ? value : null;
  }

  publish(token, value) {
    this.cache.set(token, value);
    this.versions.set(token, (this.versions.get(token) || 0) + 1);
    this.listeners.get(token)?.forEach(({ media, callback }) => callback(this.cached(media)));
  }

  read(media) {
    if (!media.token || !media.revision) return Promise.resolve(null);
    if (!this.userKey) {
      // A fresh read also observes other tabs and browser-restored pages.
      this.cache.set(media.token, this.localRead(media.token));
      return Promise.resolve(this.cached(media));
    }
    if (this.cache.has(media.token)) return Promise.resolve(this.cached(media));
    if (this.reads.has(media.token)) return this.reads.get(media.token).then(() => this.cached(media));
    const reading = new Promise(resolve => {
      if (!this.pending.has(media.token)) this.pending.set(media.token, []);
      this.pending.get(media.token).push({ media, resolve });
      if (!this.batchTimer) this.batchTimer = setTimeout(() => this.flushReads(), 0);
    });
    this.reads.set(media.token, reading);
    return reading;
  }

  async flushReads() {
    this.batchTimer = null;
    const pending = this.pending;
    this.pending = new Map();
    const tokens = [...pending.keys()];
    for (let offset = 0; offset < tokens.length; offset += 100) {
      const batch = tokens.slice(offset, offset + 100);
      const versions = batch.map(token => this.versions.get(token) || 0);
      let data = {};
      try { data = await this.request(`/playback-progress?media=${batch.map(encodeURIComponent).join(',')}`); } catch { /* Playback can proceed. */ }
      batch.forEach((token, index) => {
        // Never let a read started before a save replace that newer position.
        if ((this.versions.get(token) || 0) === versions[index]) this.publish(token, data[token] || null);
        this.reads.delete(token);
        pending.get(token).forEach(({ media, resolve }) => resolve(this.cached(media)));
      });
    }
  }

  subscribe(media, callback) {
    const entry = { media, callback };
    if (!this.listeners.has(media.token)) this.listeners.set(media.token, new Set());
    this.listeners.get(media.token).add(entry);
    this.read(media).then(value => { if (this.listeners.get(media.token)?.has(entry)) callback(value); });
    return () => {
      this.listeners.get(media.token)?.delete(entry);
      if (!this.listeners.get(media.token)?.size) this.listeners.delete(media.token);
    };
  }

  refresh() {
    this.listeners.forEach((entries, token) => {
      this.cache.delete(token);
      this.versions.set(token, (this.versions.get(token) || 0) + 1);
      const media = entries.values().next().value.media;
      this.read(media).then(value => {
        if (!this.userKey) this.publish(token, value);
      });
    });
  }

  async request(path, body, method = 'GET', keepalive = false) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
      const result = await this.fetch(this.apiBase + path, {
        signal: controller.signal,
        method, credentials: 'same-origin', cache: 'no-store', keepalive,
        headers: {
          ...(this.userKey ? { 'X-Playback-User': encodeURIComponent(this.userKey) } : {}),
          ...(body ? { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() || '' } : {}),
        },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      if (!result.ok) { const error = new Error('Progress request failed'); error.status = result.status; throw error; }
      return await result.json();
    } finally { clearTimeout(timeout); }
  }

  writer(media) {
    const id = sessionId();
    let generation = null;
    let claiming = null;
    let stopped = false;
    let sequence = 0;
    let accepted = 0;
    let pending = null;
    let saving = null;
    const path = `/media/${encodeURIComponent(media.token)}`;
    const terminal = error => {
      if ([401, 403, 404, 409].includes(error.status)) stopped = true;
    };
    const start = async () => {
      if (stopped || !this.userKey || generation !== null) return;
      if (!claiming) {
        claiming = this.request(path + '/playback-session', {
          session_id: id, content_revision: media.revision,
        }, 'POST', true).then(data => { generation = data.generation; })
          .catch(terminal).finally(() => { claiming = null; });
      }
      await claiming;
    };
    const send = async ({ value, keepalive }) => {
      if (stopped) return;
      await start();
      if (stopped || generation === null) return;
      const current = ++sequence;
      try {
        const saved = await this.request(path + '/playback-progress', {
          ...value, content_revision: media.revision, session_id: id, generation, sequence: current,
        }, 'PUT', keepalive);
        if (current > accepted) { accepted = current; this.publish(media.token, saved); }
      } catch (error) {
        // An older request can lose to a keepalive save from this same writer.
        if (current === sequence) terminal(error);
      }
    };
    const save = async (value, { keepalive = false } = {}) => {
      if (stopped || !Number.isFinite(value.position) || !Number.isFinite(value.duration) || value.duration <= 0) return;
      if (!this.userKey) {
        const position = Math.max(0, Math.min(value.position, value.duration));
        const saved = {
          position, duration: value.duration, content_revision: media.revision,
          completed: value.ended === true || value.completed === true || position >= Math.max(0, value.duration - 5),
          updated_at: new Date().toISOString(),
        };
        try { this.storage?.setItem(`${this.prefix}[${media.token}]`, JSON.stringify(saved)); } catch { /* Storage may be blocked/full. */ }
        this.publish(media.token, saved);
        return;
      }
      // A final request must not wait behind an ordinary in-flight save.
      if (keepalive && generation !== null) return send({ value, keepalive });
      pending = { value, keepalive };
      if (!saving) {
        saving = (async () => {
          while (pending && !stopped) {
            const next = pending;
            pending = null;
            await send(next);
          }
        })().finally(() => { saving = null; });
      }
      await saving;
    };
    return { load: () => this.read(media), start, save };
  }
}

let singleton;
let identity;
export function getProgressService() {
  const config = window.MediaCMS || {};
  const userKey = config.user?.is?.anonymous === false ? config.user.username : null;
  const key = `${config.site?.id || 'media-cms'}:${userKey || 'guest'}`;
  if (!singleton || identity !== key) {
    identity = key;
    singleton = new ProgressService({ siteId: config.site?.id, userKey, apiBase: config.site?.api || '/api/v1' });
  }
  return singleton;
}

if (typeof window !== 'undefined') {
  const refresh = () => singleton?.refresh();
  window.addEventListener('pageshow', refresh);
  window.addEventListener('focus', refresh);
  window.addEventListener('storage', event => {
    if (!singleton?.userKey && (!event.key || event.key.startsWith(singleton?.prefix))) refresh();
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
}
