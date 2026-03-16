import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { listJobs } from '../api';

interface Job { jobId: string; repoFullName: string; status: string; prUrl?: string; createdAt: number; }

const statusColor: Record<string, string> = {
  pending: 'var(--amber)', in_progress: 'var(--cyan)', completed: 'var(--green)', failed: 'var(--red)', manual_review: 'var(--amber)',
};

const btn: React.CSSProperties = {
  padding: '8px 20px', background: 'transparent', color: 'var(--green)',
  border: '1px solid var(--green)', fontFamily: 'var(--font)', cursor: 'pointer', fontSize: 13,
};

const th: React.CSSProperties = {
  textAlign: 'left', padding: '10px 12px', borderBottom: '1px solid var(--green-muted)',
  color: 'var(--green)', fontWeight: 700, fontSize: 12, letterSpacing: 1, textTransform: 'uppercase',
};

const td: React.CSSProperties = {
  padding: '10px 12px', borderBottom: '1px solid var(--border)', color: 'var(--green-dim)', fontSize: 13,
};

export function Jobs() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    listJobs().then(data => setJobs(Array.isArray(data) ? data : [])).finally(() => setLoading(false));
    const interval = setInterval(() => listJobs().then(data => setJobs(Array.isArray(data) ? data : [])), 3000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div style={{ maxWidth: 800, margin: '0 auto', padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--border)', paddingBottom: 16, marginBottom: 24 }}>
        <h1 style={{ fontSize: 18, fontWeight: 700, letterSpacing: 2 }}>{'>'} MIGRATION JOBS</h1>
        <button style={btn} onClick={() => navigate('/repos')}>[ ← SELECT REPO ]</button>
      </div>

      {loading ? <p style={{ color: 'var(--green-dim)' }}>LOADING...</p> : jobs.length === 0 ? (
        <p style={{ color: 'var(--text-dim)' }}>NO JOBS FOUND.</p>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={th}>REPOSITORY</th>
              <th style={th}>STATUS</th>
              <th style={th}>PR</th>
              <th style={th}>CREATED</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.jobId}>
                <td style={td}><Link to={`/jobs/${j.jobId}`}>{j.repoFullName}</Link></td>
                <td style={{ ...td, color: statusColor[j.status] || 'var(--green-dim)' }}>{j.status.toUpperCase()}</td>
                <td style={td}>{j.prUrl ? <a href={j.prUrl} target="_blank" rel="noreferrer">VIEW PR</a> : '—'}</td>
                <td style={td}>{new Date(j.createdAt * 1000).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
