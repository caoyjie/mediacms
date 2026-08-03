import React, { useState } from 'react';
import { useYouTubeJob } from '../../utils/hooks/useYouTubeJob';
import YouTubeMetadataCard from './YouTubeMetadataCard';

function csrfToken() {
    const token = document.cookie.split(';').map((item) => item.trim()).find((item) => item.startsWith('csrftoken='));
    return token ? decodeURIComponent(token.slice(10)) : '';
}

export default function YouTubeImportPanel() {
    const [url, setUrl] = useState('');
    const [title, setTitle] = useState('');
    const [jobId, setJobId] = useState(null);
    const [message, setMessage] = useState('');
    const { job, error } = useYouTubeJob(jobId);

    async function submit(event) {
        event.preventDefault();
        setMessage('');
        const response = await fetch('/api/v1/aws/youtube/jobs/', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
            body: JSON.stringify({ url, title: title || 'YouTube video', idempotency_key: `youtube-${Date.now()}` }),
        });
        const data = await response.json();
        if (!response.ok) {
            setMessage(data.detail || 'Unable to create YouTube job.');
            return;
        }
        setJobId(data.job_id);
        setMessage('Metadata discovery started.');
    }

    return (
        <section className="youtube-import-panel" aria-label="YouTube video import">
            <h2>YouTube video</h2>
            <form onSubmit={submit}>
                <label htmlFor="youtube-url">YouTube URL</label>
                <input id="youtube-url" type="url" value={url} onChange={(event) => setUrl(event.target.value)} required />
                <label htmlFor="youtube-title">Title (optional)</label>
                <input id="youtube-title" type="text" value={title} onChange={(event) => setTitle(event.target.value)} />
                <button type="submit">Discover metadata</button>
            </form>
            {message ? <p role="status">{message}</p> : null}
            {error ? <p role="alert">{error.message}</p> : null}
            {job ? <YouTubeMetadataCard metadata={job.metadata} title={job.title} onTitleChange={setTitle} disabled={job.status === 'completed'} /> : null}
        </section>
    );
}
