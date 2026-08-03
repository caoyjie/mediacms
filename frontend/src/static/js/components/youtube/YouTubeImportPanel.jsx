import React, { useEffect, useState } from 'react';
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
    const [cookieStatus, setCookieStatus] = useState(null);
    const [cookieVersionId, setCookieVersionId] = useState(null);
    const [selectedSubtitles, setSelectedSubtitles] = useState([]);
    const { job, error } = useYouTubeJob(jobId);

    useEffect(() => {
        if (job && Array.isArray(job.subtitle_options) && !selectedSubtitles.length) {
            setSelectedSubtitles(job.subtitle_options.map((option) => option.language));
        }
    }, [job, selectedSubtitles.length]);

    useEffect(() => {
        fetch('/api/v1/aws/youtube/cookies/status/', { credentials: 'same-origin' })
            .then((response) => response.ok ? response.json() : null)
            .then((data) => data && setCookieStatus(data))
            .catch(() => {});
    }, []);

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

    async function uploadCookies(event) {
        const file = event.target.files && event.target.files[0];
        if (!file) return;
        const body = new FormData();
        body.append('cookies', file);
        const response = await fetch('/api/v1/aws/youtube/cookies/', { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': csrfToken() }, body });
        const data = await response.json();
        if (!response.ok) {
            setMessage(data.detail || 'Cookie upload failed.');
            return;
        }
        setCookieVersionId(data.cookie_version_id);
        setCookieStatus({ available: true, uploaded_at: data.uploaded_at, status: 'active' });
        setMessage('Cookies uploaded.');
    }

    async function resume() {
        const response = await fetch(`/api/v1/aws/youtube/jobs/${jobId}/resume/`, {
            method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
            body: JSON.stringify(cookieVersionId ? { cookie_version_id: cookieVersionId } : {}),
        });
        const data = await response.json();
        setMessage(response.ok ? 'Job resumed.' : (data.detail || 'Unable to resume job.'));
    }

    async function startImport() {
        const response = await fetch(`/api/v1/aws/youtube/jobs/${jobId}/start/`, {
            method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
            body: JSON.stringify({ subtitle_languages: selectedSubtitles }),
        });
        const data = await response.json();
        setMessage(response.ok ? 'Import started.' : (data.detail || 'Unable to start import.'));
    }

    function toggleSubtitle(language) {
        setSelectedSubtitles((current) => current.includes(language) ? current.filter((item) => item !== language) : [...current, language]);
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
            <div className="youtube-cookie-status">
                <strong>Cookies</strong>
                {cookieStatus && cookieStatus.available ? <span>Last uploaded: {new Date(cookieStatus.uploaded_at).toLocaleString()}</span> : <span>No cookies have been uploaded. Restricted videos may fail.</span>}
                <label htmlFor="youtube-cookies">Upload cookies.txt</label>
                <input id="youtube-cookies" type="file" accept="text/plain,.txt" onChange={uploadCookies} />
            </div>
            {message ? <p role="status">{message}</p> : null}
            {error ? <p role="alert">{error.message}</p> : null}
            {job ? <YouTubeMetadataCard metadata={job.metadata} title={job.title} onTitleChange={setTitle} subtitleOptions={job.subtitle_options} selectedSubtitles={selectedSubtitles} onSubtitleChange={toggleSubtitle} disabled={job.status === 'completed'} /> : null}
            {job && job.stage === 'metadata_ready' && !job.import_requested ? <button type="button" onClick={startImport}>Start import</button> : null}
            {job && job.stage === 'action_required' && /cookie/i.test(job.safe_error || '') ? <button type="button" onClick={resume}>Resume with cookies</button> : null}
        </section>
    );
}
