import { useEffect, useState } from 'react';

export function useAwsJobs({ enabled = true, interval = 5000 } = {}) {
    const [jobs, setJobs] = useState([]);
    const [error, setError] = useState(null);
    useEffect(() => {
        if (!enabled) return undefined;
        let active = true;
        let timer;
        const poll = async () => {
            try {
                const response = await fetch('/api/v1/aws/jobs/?limit=50', { credentials: 'same-origin' });
                if (!response.ok) throw new Error(`Unable to load jobs (${response.status})`);
                const data = await response.json();
                if (active) {
                    setJobs(data.results || []);
                    setError(null);
                }
            } catch (caught) {
                if (active) setError(caught);
            } finally {
                if (active) timer = window.setTimeout(poll, interval);
            }
        };
        poll();
        return () => {
            active = false;
            if (timer) window.clearTimeout(timer);
        };
    }, [enabled, interval]);
    return { jobs, error };
}
