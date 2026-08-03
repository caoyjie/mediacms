import { useEffect, useRef, useState } from 'react';

const TERMINAL = new Set(['completed', 'failed', 'canceled']);

export function useYouTubeJob(jobId, { enabled = true } = {}) {
    const [job, setJob] = useState(null);
    const [error, setError] = useState(null);
    const delay = useRef(1000);

    useEffect(() => {
        if (!enabled || !jobId) return undefined;
        let active = true;
        let timer;
        const poll = async () => {
            try {
                const response = await fetch(`/api/v1/aws/youtube/jobs/${jobId}/`, { credentials: 'same-origin' });
                if (!response.ok) throw new Error(`Unable to load YouTube job (${response.status})`);
                const next = await response.json();
                if (!active) return;
                setJob(next);
                setError(null);
                if (!TERMINAL.has(next.status)) {
                    delay.current = next.stage === 'metadata_pending' ? Math.min(delay.current * 1.5, 8000) : 3000;
                    timer = window.setTimeout(poll, delay.current);
                }
            } catch (caught) {
                if (!active) return;
                setError(caught);
                delay.current = Math.min(delay.current * 2, 15000);
                timer = window.setTimeout(poll, delay.current);
            }
        };
        delay.current = 1000;
        poll();
        return () => {
            active = false;
            if (timer) window.clearTimeout(timer);
        };
    }, [enabled, jobId]);

    return { job, error, isTerminal: Boolean(job && TERMINAL.has(job.status)) };
}
