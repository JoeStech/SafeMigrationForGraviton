import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { getJob } from '../api';

const STAGES = ['fork', 'analyze', 'generate', 'stub', 'create_pr'];

interface LogEntry { ts: number; msg: string; }
interface StageInfo { status: string; startedAt?: number; completedAt?: number; error?: string; }
interface JobData {
  jobId: string; repoFullName: string; status: string; currentStage?: string;
  stages: Record<string, StageInfo>; stageLogs?: Record<string, LogEntry[]>;
  prUrl?: string; prNumber?: number; errorMessage?: string;
  migrationSummary?: { modified_files: number; stubbed_secrets: number; stubbed_databases: number; stubbed_services: number; flagged_for_review: number; };
}

function stageStyle(info?: StageInfo, selected?: boolean): React.CSSProperties {
  const base: React.CSSProperties = { cursor: 'pointer', transition: 'all 0.15s' };
  if (!info) return { ...base, background: 'transparent', color: 'var(--text-dim)', border: '1px solid var(--border)' };
  if (info.status === 'completed') return { ...base, background: selected ? 'var(--green)' : 'transparent', color: selected ? 'var(--bg)' : 'var(--green)', border: '1px solid var(--green)' };
  if (info.status === 'in_progress') return { ...base, background: selected ? 'rgba(0,204,204,0.15)' : 'transparent', color: 'var(--cyan)', border: '1px solid var(--cyan)', boxShadow: '0 0 8px rgba(0,204,204,0.3)' };
  if (info.status === 'failed') return { ...base, background: selected ? 'rgba(255,51,51,0.1)' : 'transparent', color: 'var(--red)', border: '1px solid var(--red)' };
  return { ...base, background: 'transparent', color: 'var(--text-dim)', border: '1px solid var(--border)' };
}

export function JobDetail() {
  const { jobId } = useParams<{ jobId: string }>();
  const [job, setJob] = useState<JobData | null>(null);
  const [selectedStage, setSelectedStage] = useState<string | null>(null);
  const [userSelected, setUserSelected] = useState(false);

  useEffect(() => {
    if (!jobId) return;
    getJob(jobId).then(setJob);
    const interval = setInterval(() => getJob(jobId).then(setJob), 3000);
    return () => clearInterval(interval);
  }, [jobId]);

  // Auto-follow the active stage unless the user has manually clicked one
  useEffect(() => {
    if (!job || userSelected) return;
    // Prefer in_progress stage, then fall back to last stage with any data
    const inProgress = STAGES.find(s => job.stages?.[s]?.status === 'in_progress');
    const active = inProgress || job.currentStage || STAGES.slice().reverse().find(s => job.stages?.[s]);
    if (active) setSelectedStage(active);
  }, [job]);

  if (!job) return <p style={{ textAlign: 'center', marginTop: '20vh', color: 'var(--green-dim)' }}>LOADING...</p>;

  const logs = selectedStage ? (job.stageLogs?.[selectedStage] || []) : [];
  const selectedInfo = selectedStage ? job.stages?.[selectedStage] : undefined;

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: 24 }}>
      <Link to="/jobs" style={{ color: 'var(--green-dim)', fontSize: 13 }}>{'<'} BACK TO JOBS</Link>
      <h1 style={{ fontSize: 18, fontWeight: 700, letterSpacing: 2, marginTop: 16, marginBottom: 8 }}>
        {'>'} {job.repoFullName}
      </h1>
      <p style={{ color: 'var(--green-dim)', marginBottom: 24 }}>
        STATUS: <span style={{ color: job.status === 'failed' ? 'var(--red)' : job.status === 'completed' ? 'var(--green)' : 'var(--amber)' }}>
          {job.status.toUpperCase()}
        </span>
      </p>

      {/* Pipeline stage graph — clickable */}
      <div style={{ display: 'flex', gap: 4, margin: '24px 0', flexWrap: 'wrap', alignItems: 'center' }}>
        {STAGES.map((stage, i) => (
          <div key={stage} style={{ display: 'flex', alignItems: 'center' }}>
            <div
              onClick={() => { setUserSelected(true); setSelectedStage(stage); }}
              style={{
                padding: '6px 14px', fontSize: 12, fontWeight: 700, fontFamily: 'var(--font)',
                letterSpacing: 1, ...stageStyle(job.stages?.[stage], selectedStage === stage),
              }}
              role="button" tabIndex={0}
              onKeyDown={e => e.key === 'Enter' && (setUserSelected(true), setSelectedStage(stage))}
              aria-label={`View logs for ${stage}`}
            >
              {stage.replace('_', ' ').toUpperCase()}
            </div>
            {i < STAGES.length - 1 && <span style={{ margin: '0 2px', color: 'var(--text-dim)' }}>→</span>}
          </div>
        ))}
      </div>

      {/* Live logs panel */}
      <div style={{
        border: '1px solid var(--border)', background: 'rgba(0,0,0,0.3)',
        padding: 0, marginBottom: 24, minHeight: 200,
      }}>
        <div style={{
          padding: '8px 12px', borderBottom: '1px solid var(--border)',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <span style={{ color: 'var(--green)', fontSize: 12, fontWeight: 700, letterSpacing: 1 }}>
            {selectedStage ? `▸ ${selectedStage.replace('_', ' ').toUpperCase()} LOGS` : 'SELECT A STAGE'}
          </span>
          {selectedInfo && (
            <span style={{
              fontSize: 11, padding: '2px 8px',
              color: selectedInfo.status === 'completed' ? 'var(--green)' : selectedInfo.status === 'failed' ? 'var(--red)' : selectedInfo.status === 'in_progress' ? 'var(--cyan)' : 'var(--text-dim)',
              border: `1px solid ${selectedInfo.status === 'completed' ? 'var(--green)' : selectedInfo.status === 'failed' ? 'var(--red)' : selectedInfo.status === 'in_progress' ? 'var(--cyan)' : 'var(--border)'}`,
            }}>
              {selectedInfo.status.toUpperCase()}
            </span>
          )}
        </div>
        <div style={{
          padding: '8px 12px', maxHeight: 300, overflowY: 'auto',
          fontFamily: 'var(--font)', fontSize: 12, lineHeight: 1.8,
        }}>
          {logs.length === 0 ? (
            <p style={{ color: 'var(--text-dim)', fontStyle: 'italic' }}>
              {selectedInfo?.status === 'in_progress' ? 'Waiting for logs...' : selectedInfo ? 'No logs recorded for this stage.' : 'Click a stage above to view its logs.'}
            </p>
          ) : (
            logs.map((entry, i) => (
              <div key={i} style={{ display: 'flex', gap: 8 }}>
                <span style={{ color: 'var(--text-dim)', flexShrink: 0 }}>
                  {new Date(entry.ts * 1000).toLocaleTimeString()}
                </span>
                <span style={{
                  color: entry.msg.startsWith('ERROR') ? 'var(--red)' : 'var(--green-dim)',
                  wordBreak: 'break-word',
                }}>
                  {entry.msg}
                </span>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Error details */}
      {(job.status === 'failed') && (
        <div style={{ marginTop: 16, padding: 16, border: '1px solid var(--red)', background: 'rgba(255,51,51,0.05)' }}>
          <p style={{ color: 'var(--red)', fontWeight: 700, marginBottom: 8, letterSpacing: 1 }}>
            ✗ PIPELINE FAILED
          </p>
          {job.currentStage && (
            <p style={{ color: 'var(--red)', fontSize: 13, marginBottom: 8 }}>
              Failed at stage: {job.currentStage.replace('_', ' ').toUpperCase()}
            </p>
          )}
          {Object.entries(job.stages || {}).map(([name, info]) =>
            info.error ? (
              <div key={name} style={{ marginBottom: 8 }}>
                <p style={{ color: 'var(--amber)', fontSize: 12, marginBottom: 4 }}>{name.toUpperCase()}:</p>
                <pre style={{
                  color: 'var(--red)', fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                  background: 'var(--bg)', padding: 8, border: '1px solid var(--border)', margin: 0,
                  fontFamily: 'var(--font)', maxHeight: 200, overflow: 'auto',
                }}>{info.error}</pre>
              </div>
            ) : null
          )}
          {job.errorMessage && !Object.values(job.stages || {}).some(s => s.error) && (
            <pre style={{
              color: 'var(--red)', fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              background: 'var(--bg)', padding: 8, border: '1px solid var(--border)', margin: 0,
              fontFamily: 'var(--font)', maxHeight: 200, overflow: 'auto',
            }}>{job.errorMessage}</pre>
          )}
        </div>
      )}

      {/* PR link */}
      {job.prUrl && (
        <p style={{ marginTop: 16 }}>
          PULL REQUEST: <a href={job.prUrl} target="_blank" rel="noreferrer">
            #{job.prNumber} — VIEW ON GITHUB
          </a>
        </p>
      )}

      {/* Migration summary */}
      {job.migrationSummary && (
        <div style={{ marginTop: 24, padding: 16, border: '1px solid var(--border)', background: 'var(--bg-panel)' }}>
          <p style={{ color: 'var(--green)', fontWeight: 700, marginBottom: 12, letterSpacing: 1 }}>MIGRATION SUMMARY</p>
          <table style={{ width: '100%', fontSize: 13 }}>
            <tbody>
              {[
                ['Modified files', job.migrationSummary.modified_files],
                ['Stubbed secrets', job.migrationSummary.stubbed_secrets],
                ['Stubbed databases', job.migrationSummary.stubbed_databases],
                ['Stubbed services', job.migrationSummary.stubbed_services],
                ['Flagged for review', job.migrationSummary.flagged_for_review],
              ].map(([label, val]) => (
                <tr key={String(label)}>
                  <td style={{ padding: '4px 0', color: 'var(--green-dim)' }}>{label}</td>
                  <td style={{ padding: '4px 0', color: 'var(--green)', textAlign: 'right' }}>{val}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
