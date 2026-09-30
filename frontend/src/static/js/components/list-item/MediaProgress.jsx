import React, { useEffect, useState } from 'react';
import { getProgressService } from '../../utils/playback/ProgressService';

export function useMediaProgress({ mediaId, contentRevision, link }) {
  let token = mediaId;
  if (!token && link) {
    try { token = new URL(link, window.location.href).searchParams.get('m'); } catch { /* Invalid link. */ }
  }
  const [state, setState] = useState(null);
  useEffect(() => {
    if (!token || !contentRevision) return;
    return getProgressService().subscribe({ token, revision: contentRevision }, progress => {
      setState({ token, revision: contentRevision, progress });
    });
  }, [token, contentRevision]);
  return state?.token === token && state?.revision === contentRevision ? state.progress : null;
}

function percent(progress) {
  return progress.completed ? 100 : Math.max(0, Math.min(100, progress.position / progress.duration * 100));
}

export function MediaProgressBar({ progress }) {
  return progress ? <span className="media-progress-bar" aria-hidden="true" style={{ width: `${percent(progress)}%` }} /> : null;
}

export function MediaProgressDescription({ progress }) {
  return progress ? <span className="media-progress-description">
    {progress.completed ? 'Completed' : `Playback progress: ${Math.round(percent(progress))}%`}
  </span> : null;
}
