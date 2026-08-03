import { useCallback, useEffect, useRef, useState } from 'react';

export function usePlaybackProgress(mediaId, assetVersionId) {
    const [progress, setProgress] = useState(null);
    const lastSent = useRef(0);
    useEffect(() => {
        if (!mediaId) return undefined;
        let active = true;
        fetch(`/api/v1/media/${mediaId}/playback-progress/`, { credentials: 'same-origin' })
            .then((response) => response.ok ? response.json() : null)
            .then((data) => { if (active && data) setProgress(data); })
            .catch(() => {});
        return () => { active = false; };
    }, [mediaId]);
    const save = useCallback((positionSeconds, durationSeconds, completed = false) => {
        if (!mediaId || positionSeconds - lastSent.current < 5 && !completed) return;
        lastSent.current = positionSeconds;
        return fetch(`/api/v1/media/${mediaId}/playback-progress/`, {
            method: 'PUT', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ position_seconds: positionSeconds, duration_seconds: durationSeconds, completed, asset_version_id: assetVersionId || null }),
        }).then((response) => response.ok ? response.json() : null).then((data) => { if (data) setProgress(data); });
    }, [assetVersionId, mediaId]);
    return { progress, save };
}
