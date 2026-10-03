import { getProgressService } from './ProgressService';
import { attachPlaybackProgress } from './PlaybackController';
import { tracePlayback } from './ProgressDiagnostics';

export function createProgressAdapter({ data, inEmbed, isWatchPage }) {
  if (inEmbed || !isWatchPage || !['video', 'audio'].includes(data?.media_type) || !data.content_revision) return null;
  let token = data.friendly_token;
  if (!token) {
    try { token = new URL(data.url, window.location.href).searchParams.get('m'); } catch { return null; }
  }
  if (!token) return null;
  const writer = getProgressService().writer({ token, revision: data.content_revision });
  tracePlayback('adapter-created', { token, revision: data.content_revision });
  let cleanup;
  return {
    attach(player, timestamp, autoplay) {
      cleanup?.();
      cleanup = attachPlaybackProgress(player, writer, timestamp, autoplay);
      return () => { cleanup?.(); cleanup = null; };
    },
    dispose() { cleanup?.(); cleanup = null; },
  };
}
