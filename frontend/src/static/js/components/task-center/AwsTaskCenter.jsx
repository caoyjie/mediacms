import React from 'react';
import { useAwsJobs } from '../../utils/hooks/useAwsJobs';

function statusLabel(job) {
    if (job.status === 'failed' && job.safe_error) return job.safe_error;
    return job.stage || job.status;
}

export default function AwsTaskCenter() {
    const { jobs, error } = useAwsJobs();
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
                </li>)}
            </ul>
        </section>
    );
}
