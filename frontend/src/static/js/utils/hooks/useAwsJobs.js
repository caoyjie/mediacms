import { useEffect, useState } from 'react';

export function useAwsJobs({ enabled = true, interval = 5000 } = {}) {
    const [jobs, setJobs] = useState([]);
    const [nextOffset, setNextOffset] = useState(null);
    const [error, setError] = useState(null);
    const [refreshKey, setRefreshKey] = useState(0);
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
                    setNextOffset(data.next_offset);
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
    }, [enabled, interval, refreshKey]);
    async function loadMore() {
        if (nextOffset === null) return;
        const response = await fetch(`/api/v1/aws/jobs/?limit=50&offset=${nextOffset}`, { credentials: 'same-origin' });
        if (!response.ok) return;
        const data = await response.json();
        setJobs((current) => [...current, ...(data.results || [])]);
        setNextOffset(data.next_offset);
    }
    return { jobs, error, nextOffset, loadMore, refresh: () => setRefreshKey((value) => value + 1) };
}
