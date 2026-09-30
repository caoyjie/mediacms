import { ProgressService } from '../../src/static/js/utils/playback/ProgressService';
import { attachPlaybackProgress } from '../../src/static/js/utils/playback/PlaybackController';

const media = { token: 'video', revision: 'revision-1' };
const record = { position: 480, duration: 1200, completed: false, content_revision: 'revision-1' };
const tick = async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); };
const response = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });

beforeEach(() => { localStorage.clear(); jest.useFakeTimers(); });
afterEach(() => { jest.useRealTimers(); });

function guest() { return new ProgressService({ siteId: 'site', userKey: null, storage: localStorage }); }

test('guest progress survives a new service and never leaks into an account or another site', async () => {
  const service = guest();
  const writer = service.writer(media);
  await writer.start();
  await writer.save(record);
  expect(await guest().read(media)).toMatchObject({ position: 480 });
  const account = new ProgressService({ siteId: 'site', userKey: 'alice', storage: localStorage, fetch: async () => response({}) });
  const read = account.read(media);
  jest.runOnlyPendingTimers();
  expect(await read).toBeNull();
  expect(await new ProgressService({ siteId: 'other', storage: localStorage }).read(media)).toBeNull();
  expect(await guest().read({ ...media, revision: 'replacement' })).toBeNull();
});

test('account reads are batched and deduplicated, and local saves beat a late read', async () => {
  let finishRead;
  const urls = [];
  const service = new ProgressService({ userKey: 'alice', fetch: async (url, options) => {
    urls.push(url);
    if (url.includes('?')) return new Promise(resolve => { finishRead = resolve; });
    if (url.endsWith('playback-session')) return response({ generation: 1 });
    return response({ ...record, ...JSON.parse(options.body) });
  } });
  const first = service.read(media);
  const duplicate = service.read(media);
  const second = service.read({ token: 'audio', revision: 'revision-1' });
  jest.runOnlyPendingTimers();
  await tick();
  expect(urls).toHaveLength(1);
  expect(urls[0]).toContain('video,audio');
  const writer = service.writer(media);
  await writer.start();
  await writer.save({ ...record, position: 60 });
  finishRead(response({ video: record }));
  await Promise.all([first, duplicate, second]);
  expect(await service.read(media)).toMatchObject({ position: 60 });
});

test('a superseded account session stops writing and failures never write guest storage', async () => {
  const requests = [];
  const service = new ProgressService({ userKey: 'alice', storage: localStorage, fetch: async (url, options) => {
    requests.push({ url, options });
    return url.endsWith('playback-session') ? response({ generation: 2 }) : response({}, 409);
  } });
  const writer = service.writer(media);
  await writer.start();
  await writer.save(record);
  await writer.save({ ...record, position: 20 });
  expect(requests).toHaveLength(2);
  expect(JSON.parse(requests[1].options.body)).toMatchObject({ generation: 2, sequence: 1 });
  expect(localStorage.length).toBe(0);
});

test('guest storage errors are harmless', async () => {
  const service = new ProgressService({ storage: { getItem() { throw Error('blocked'); }, setItem() { throw Error('quota'); } } });
  expect(await service.read(media)).toBeNull();
  const writer = service.writer(media);
  await writer.start();
  await expect(writer.save(record)).resolves.toBeUndefined();
});

// The fake supplies the Video.js event boundary; the controller and persistence are real.
class Player {
  constructor() { this.events = {}; this.time = 0; this.isPaused = true; this.length = 1200; this.metadata = 1; this.seekableLength = 1; }
  on(name, fn) { (this.events[name] ||= new Set()).add(fn); }
  off(name, fn) { this.events[name]?.delete(fn); }
  emit(name) { [...(this.events[name] || [])].forEach(fn => fn()); }
  currentTime(value) { if (value !== undefined) { this.time = value; this.emit('seeking'); this.emit('seeked'); } return this.time; }
  duration() { return this.length; }
  readyState() { return this.metadata; }
  seekable() { return { length: this.seekableLength, start: () => 0, end: () => this.length }; }
  paused() { return this.isPaused; }
  seeking() { return false; }
  play() { this.isPaused = false; this.emit('play'); this.emit('playing'); }
  pause() { this.isPaused = true; this.emit('pause'); }
}

async function controller({ saved = record, timestamp = null, load, player = new Player() } = {}) {
  const service = guest();
  if (saved) { const previous = service.writer(media); await previous.start(); await previous.save(saved); }
  const writer = service.writer(media);
  if (load) writer.load = load;
  const cleanup = attachPlaybackProgress(player, writer, timestamp, () => player.play());
  await tick();
  return { player, service, cleanup };
}

test.each([null, 0, 240])('restores before autoplay with timestamp precedence (%s)', async timestamp => {
  const { player, cleanup } = await controller({ timestamp });
  expect(player.currentTime()).toBe(timestamp === null ? 480 : timestamp);
  expect(player.paused()).toBe(false);
  cleanup();
});

test('saves pause and backward seek, and persists periodic playback within five seconds', async () => {
  const { player, service, cleanup } = await controller();
  player.time = 485;
  player.emit('timeupdate');
  jest.advanceTimersByTime(5000);
  await tick();
  expect(await service.read(media)).toMatchObject({ position: 485 });
  player.currentTime(120);
  await tick();
  expect(await service.read(media)).toMatchObject({ position: 120 });
  player.time = 123;
  player.pause();
  await tick();
  expect(await service.read(media)).toMatchObject({ position: 123 });
  cleanup();
});

test('opening completed media retains the full bar until replay advances', async () => {
  const { player, service, cleanup } = await controller({ saved: { ...record, completed: true } });
  expect(player.currentTime()).toBe(0);
  player.pause();
  await tick();
  expect(await service.read(media)).toMatchObject({ completed: true });
  player.play();
  player.time = 1;
  player.emit('timeupdate');
  player.pause();
  await tick();
  expect(await service.read(media)).toMatchObject({ position: 1, completed: false });
  cleanup();
});

test('slow reads time out without later interrupting playback', async () => {
  let resolve;
  const { player, cleanup } = await controller({ load: () => new Promise(r => { resolve = r; }) });
  expect(player.paused()).toBe(true);
  jest.advanceTimersByTime(2000);
  await tick();
  expect(player.paused()).toBe(false);
  player.time = 15;
  resolve(record);
  await tick();
  expect(player.currentTime()).toBe(15);
  cleanup();
});

test('user play during a lookup prevents a later automatic seek', async () => {
  let resolve;
  const { player, cleanup } = await controller({ load: () => new Promise(r => { resolve = r; }) });
  player.play();
  player.currentTime(42);
  resolve(record);
  await tick();
  expect(player.currentTime()).toBe(42);
  cleanup();
});

test('metadata gates restore; page hide saves and cleanup removes listeners', async () => {
  const player = new Player();
  player.metadata = 0;
  const { service, cleanup } = await controller({ player });
  expect(player.paused()).toBe(true);
  player.metadata = 1;
  player.emit('loadedmetadata');
  await tick();
  expect(player.currentTime()).toBe(480);
  player.time = 500;
  player.emit('timeupdate');
  window.dispatchEvent(new Event('pagehide'));
  await tick();
  expect(await service.read(media)).toMatchObject({ position: 500 });
  cleanup();
  expect(Object.values(player.events).every(set => set.size === 0)).toBe(true);
});

test('non-seekable and live media do not resume or save', async () => {
  const player = new Player();
  player.seekableLength = 0;
  player.length = Infinity;
  const { service, cleanup } = await controller({ player, saved: null });
  expect(player.time).toBe(0);
  player.time = 50;
  player.emit('timeupdate');
  player.pause();
  await tick();
  expect(await service.read(media)).toBeNull();
  cleanup();
});

test('overlapping card reads share an in-flight request', async () => {
  let complete;
  const fetcher = jest.fn(() => new Promise(resolve => { complete = resolve; }));
  const service = new ProgressService({ userKey: 'alice', fetch: fetcher });
  const first = service.read(media);
  jest.runOnlyPendingTimers();
  await tick();
  const second = service.read(media);
  jest.runOnlyPendingTimers();
  await tick();
  expect(fetcher).toHaveBeenCalledTimes(1);
  complete(response({ video: record }));
  expect(await first).toMatchObject({ position: 480 });
  expect(await second).toMatchObject({ position: 480 });
});

test('account writes include CSRF and the original account guard, and retry after network failures', async () => {
  document.cookie = 'csrftoken=test-csrf';
  const requests = [];
  let fail = true;
  const service = new ProgressService({ userKey: 'alice', fetch: async (url, options) => {
    requests.push(options);
    if (url.endsWith('playback-session')) return response({ generation: 1 });
    if (fail) { fail = false; throw Error('offline'); }
    return response({ ...record, position: JSON.parse(options.body).position });
  } });
  const writer = service.writer(media);
  await writer.start();
  await writer.save(record);
  await writer.save({ ...record, position: 120 }, { keepalive: true });
  expect(requests[2]).toMatchObject({ keepalive: true, credentials: 'same-origin', headers: { 'X-CSRFToken': 'test-csrf', 'X-Playback-User': 'alice' } });
  expect(await service.read(media)).toMatchObject({ position: 120 });
});

test('waits for seekable ranges that become available after metadata', async () => {
  const player = new Player();
  player.seekableLength = 0;
  const { cleanup } = await controller({ player });
  expect(player.paused()).toBe(true);
  player.seekableLength = 1;
  player.emit('canplay');
  await tick();
  expect(player.currentTime()).toBe(480);
  cleanup();
});

test('a stalled account claim times out so the next save can retry', async () => {
  const service = new ProgressService({ userKey: 'alice', fetch: (url, options) => new Promise((resolve, reject) => {
    options.signal?.addEventListener('abort', () => reject(new Error('timeout')));
  }) });
  let settled = false;
  service.writer(media).start().then(() => { settled = true; });
  jest.advanceTimersByTime(5000);
  await tick();
  expect(settled).toBe(true);
});

test('a final save overtaking a periodic save does not disable later playback saves', async () => {
  const updates = [];
  const service = new ProgressService({ userKey: 'alice', fetch: (url, options) => {
    if (url.endsWith('playback-session')) return Promise.resolve(response({ generation: 1 }));
    return new Promise(resolve => updates.push({ resolve, body: JSON.parse(options.body) }));
  } });
  const writer = service.writer(media);
  await writer.start();
  const periodic = writer.save(record);
  await tick();
  const final = writer.save({ ...record, position: 490 }, { keepalive: true });
  await tick();
  updates[0].resolve(response({}, 409));
  await periodic;
  updates[1].resolve(response({ ...record, position: 490 }));
  await final;
  const resumed = writer.save({ ...record, position: 495 });
  await tick();
  expect(updates).toHaveLength(3);
  updates[2].resolve(response({ ...record, position: 495 }));
  await resumed;
  expect(await service.read(media)).toMatchObject({ position: 495 });
});

test('zero-width seek ranges are not seekable playback', async () => {
  const player = new Player();
  player.seekable = () => ({ length: 1, start: () => 0, end: () => 0 });
  const { service, cleanup } = await controller({ player, saved: null });
  jest.advanceTimersByTime(2000);
  await tick();
  player.time = 2;
  player.emit('timeupdate');
  player.pause();
  await tick();
  const progress = await service.read(media);
  cleanup();
  expect(progress).toBeNull();
});
