import React, { useMemo, useState } from 'react';
import { useAwsJobs } from '../../utils/hooks/useAwsJobs';
import '../../../css/task-center.scss';

function statusLabel(job) {
    if (job.status === 'failed' && job.safe_error) return job.safe_error;
    return job.stage || job.status;
}

export default function AwsTaskCenter() {
    const { jobs, error, nextOffset, loadMore, refresh } = useAwsJobs();
    const [sourceFilter, setSourceFilter] = useState('all');
    const [statusFilter, setStatusFilter] = useState('all');
    const [expanded, setExpanded] = useState(null);
    const visibleJobs = useMemo(() => jobs.filter((job) => (
        (sourceFilter === 'all' || job.source_type === sourceFilter)
        && (statusFilter === 'all' || job.status === statusFilter)
    )), [jobs, sourceFilter, statusFilter]);
    const summary = {
        active: jobs.filter((job) => ['queued', 'running'].includes(job.status)).length,
        queued: jobs.filter((job) => job.status === 'queued').length,
        failed: jobs.filter((job) => job.status === 'failed').length,
        completed: jobs.filter((job) => job.status === 'completed').length,
    };
    async function action(job, actionName) {
        const response = await fetch(`/api/v1/aws/jobs/${job.job_id}/${actionName}/`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'X-CSRFToken': document.cookie.split(';').map((item) => item.trim()).find((item) => item.startsWith('csrftoken='))?.slice(10) || '' },
        });
        if (response.ok) refresh();
    }
    return (
        <section className="aws-task-center" aria-label="AWS task center">
            <h2>Task Center</h2>
            <div className="aws-task-summary" aria-label="Task summary">
                <span>Active: {summary.active}</span><span>Queued: {summary.queued}</span><span>Failed: {summary.failed}</span><span>Completed: {summary.completed}</span>
            </div>
            <div className="aws-task-filters">
                <label>Source <select value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}><option value="all">All</option><option value="upload">Upload</option><option value="hls_zip">HLS</option><option value="youtube">YouTube</option></select></label>
                <label>Status <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="all">All</option><option value="queued">Queued</option><option value="running">Running</option><option value="failed">Failed</option><option value="completed">Completed</option><option value="canceled">Canceled</option></select></label>
            </div>
            {error ? <p role="alert">{error.message}</p> : null}
            {!visibleJobs.length && !error ? <p>No tasks match the selected filters.</p> : null}
            <ul>
                {visibleJobs.map((job) => <li key={job.job_id}>
                    <strong>{job.title || job.source_type}</strong>
                    <span>{job.statusLabel || statusLabel(job)}</span>
                    <progress max="100" value={Number(job.progress) || 0} aria-label={`${job.title || 'Task'} progress`} />
                    <span>{Number(job.progress) || 0}%</span>
                    {['queued', 'running'].includes(job.status) ? <button type="button" onClick={() => action(job, 'cancel')}>Cancel</button> : null}
                    {['failed', 'canceled'].includes(job.status) ? <button type="button" onClick={() => action(job, 'resume')}>Resume</button> : null}
                    <button type="button" onClick={() => setExpanded(expanded === job.job_id ? null : job.job_id)}>{expanded === job.job_id ? 'Hide details' : 'Details'}</button>
                    {expanded === job.job_id ? <ol aria-label="Checkpoint timeline">{(job.checkpoints || []).map((checkpoint) => <li key={`${checkpoint.name}-${checkpoint.completed_at || 'pending'}`}><span>{checkpoint.name}</span><span>{checkpoint.status}</span></li>)}</ol> : null}
                </li>)}
            </ul>
            {nextOffset !== null ? <button type="button" onClick={loadMore}>Load more history</button> : null}
        </section>
    );
}
