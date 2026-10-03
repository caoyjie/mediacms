import { tracePlayback } from './ProgressDiagnostics';

/** Connect the separate Video.js bundle through a small, storage-free boundary. */
export function attachPlaybackProgress(player, writer, timestamp, autoplay) {
  let disposed = false;
  let interacted = false;
  let restoring = false;
  let started = false;
  let advanced = false;
  let baseline = 0;
  let initialized = false;
  const trace = (event, details = {}) => {
    try {
      const ranges = player.seekable();
      tracePlayback(event, {
        currentTime: player.currentTime(), duration: player.duration(), readyState: player.readyState(),
        paused: player.paused(), seeking: player.seeking(),
        ranges: Array.from({ length: ranges.length }, (_, i) => [ranges.start(i), ranges.end(i)]),
        interacted, restoring, initialized, ...details,
      });
    } catch { /* Diagnostics must not affect playback. */ }
  };
  trace('controller-attached', { timestamp });
  const handlers = [];
  const on = (name, handler) => { player.on(name, handler); handlers.push([name, handler]); };
  const seekable = () => Number.isFinite(player.duration()) && player.duration() > 0 &&
    player.seekable().length > 0 && player.seekable().end(player.seekable().length - 1) > player.seekable().start(0) &&
    !player.liveTracker?.isLive?.();
  const save = (ended = false, keepalive = false) => {
    if (disposed || !started || !advanced || restoring || !seekable()) return;
    const position = player.currentTime();
    if (!Number.isFinite(position) || position < 0) return;
    Promise.resolve(writer.save({ position, duration: player.duration(), ended }, { keepalive })).catch(() => {});
  };
  on('play', () => { if (!restoring) interacted = true; trace('play'); });
  const start = () => {
    if (!seekable() || started) return;
    started = true;
    baseline = player.currentTime();
    Promise.resolve(writer.start()).catch(() => {});
  };
  on('playing', start);
  on('seeking', () => { if (!restoring) interacted = true; trace('seeking'); });
  on('seeked', () => {
    if (restoring) return;
    if (started) { advanced = true; save(); }
  });
  on('timeupdate', () => {
    if (!player.paused()) start();
    if (started && !restoring && player.currentTime() !== baseline) {
      const firstAdvance = !advanced;
      advanced = true;
      // Clear a completed indicator as soon as replay actually advances.
      if (firstAdvance) save();
    }
  });
  on('pause', () => save());
  on('ended', () => { if (started) advanced = true; save(true); });
  const pageHide = () => save(false, true);
  const visibility = () => { if (document.hidden) pageHide(); };
  window.addEventListener('pagehide', pageHide);
  document.addEventListener('visibilitychange', visibility);
  const interval = setInterval(() => { if (!player.paused() && !player.seeking()) save(); }, 5000);

  let metadataReady;
  const metadata = new Promise(resolve => { metadataReady = resolve; });
  if (player.readyState() >= 1) metadataReady();
  else on('loadedmetadata', metadataReady);
  let rangeTimeout;
  const readyToSeek = metadata.then(() => {
    trace('metadata-ready');
    if (seekable() || !Number.isFinite(player.duration()) || player.duration() <= 0) return;
    // Native media/HLS can report metadata before a seekable range exists.
    return new Promise(resolve => {
      const ready = () => { if (seekable()) { trace('range-ready'); clearTimeout(rangeTimeout); resolve(); } };
      on('canplay', ready);
      on('progress', ready);
      on('loadeddata', ready);
      rangeTimeout = setTimeout(() => { trace('range-timeout'); resolve(); }, 2000);
    });
  });
  let timeout;
  const hasTimestamp = typeof timestamp === 'number' && Number.isFinite(timestamp) && timestamp >= 0;
  const lookup = hasTimestamp ? Promise.resolve(null) : Promise.race([
    Promise.resolve().then(() => writer.load()).then(saved => {
      trace('lookup-result', { saved });
      return saved;
    }).catch(error => { trace('lookup-error', { error: error?.name }); return null; }),
    new Promise(resolve => { timeout = setTimeout(() => { trace('lookup-timeout'); resolve(null); }, 2000); }),
  ]);
  const beginAutoplay = () => {
    if (disposed || initialized) return;
    initialized = true;
    trace('autoplay-decision');
    if (!interacted) Promise.resolve(autoplay()).catch(() => {});
  };
  Promise.all([readyToSeek, lookup]).then(([, saved]) => {
    clearTimeout(timeout);
    trace('restore-decision', { saved, disposed });
    if (disposed) return;
    if (interacted) { trace('restore-skipped-interaction'); initialized = true; return; }
    const position = hasTimestamp ? timestamp : saved && !saved.completed ? saved.position : 0;
    if (seekable() && Number.isFinite(position) && position >= 0 && position !== player.currentTime()) {
      restoring = true;
      const restored = () => {
        if (!restoring) return;
        restoring = false;
        trace('restore-finished');
        player.off('seeked', restored);
        beginAutoplay();
      };
      on('seeked', restored);
      const ranges = player.seekable();
      trace('restore-seek', { target: position });
      // Metadata may advertise slightly more than the currently available range.
      player.currentTime(Math.max(ranges.start(0), Math.min(position, player.duration(), ranges.end(ranges.length - 1))));
      if (!player.seeking()) restored();
    } else { trace('restore-no-seek', { target: position }); beginAutoplay(); }
  }).catch(error => { trace('restore-error', { error: error?.name }); beginAutoplay(); });

  const cleanup = () => {
    if (disposed) return;
    save(false, true);
    disposed = true;
    trace('controller-disposed');
    clearTimeout(timeout);
    clearInterval(interval);
    clearTimeout(rangeTimeout);
    handlers.forEach(([name, handler]) => player.off(name, handler));
    window.removeEventListener('pagehide', pageHide);
    document.removeEventListener('visibilitychange', visibility);
  };
  on('dispose', cleanup);
  return cleanup;
}
