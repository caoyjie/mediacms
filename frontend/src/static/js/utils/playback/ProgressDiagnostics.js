// Opt-in, local-only startup diagnostics. Never sends logs to a server.
export function tracePlayback(event, details = {}) {
  try {
    if (new URLSearchParams(window.location.search).get('playbackDebug') !== '1') return;
    const events = window.__playbackDebug ||= [];
    events.push({ ms: Math.round(performance.now()), event, ...details });
    if (events.length > 100) events.shift();
  } catch { /* Diagnostics must not affect playback. */ }
}
