import React from 'react';
import { useAwsJobs } from '../../utils/hooks/useAwsJobs';

function statusLabel(job) {
    if (job.status === 'failed' && job.safe_error) return job.safe_error;
    return job.stage || job.status;
}

export default function AwsTaskCenter() {
    const { jobs, error, refresh } = useAwsJobs();
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
            {error ? <p role="alert">{error.message}</p> : null}
            {!jobs.length && !error ? <p>No tasks yet.</p> : null}
            <ul>
                {jobs.map((job) => <li key={job.job_id}>
                    <strong>{job.title || job.source_type}</strong>
                    <span>{job.statusLabel || statusLabel(job)}</span>
                    <progress max="100" value={Number(job.progress) || 0} aria-label={`${job.title || 'Task'} progress`} />
                    <span>{Number(job.progress) || 0}%</span>
                    {['queued', 'running'].includes(job.status) ? <button type="button" onClick={() => action(job, 'cancel')}>Cancel</button> : null}
                    {['failed', 'canceled'].includes(job.status) ? <button type="button" onClick={() => action(job, 'resume')}>Resume</button> : null}
                </li>)}
            </ul>
        </section>
    );
}
