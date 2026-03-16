import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { listRepos, validateRepo, createJob, logout } from '../api';

interface Repo { owner: string; name: string; full_name: string; default_branch: string; is_private: boolean; }
interface Validation { is_valid: boolean; workflow_files: string[]; dockerfiles: string[]; message?: string; }

const btn: React.CSSProperties = {
  padding: '8px 20px', background: 'transparent', color: 'var(--green)',
  border: '1px solid var(--green)', fontFamily: 'var(--font)', cursor: 'pointer', fontSize: 13,
};

export function Repos() {
  const [repos, setRepos] = useState<Repo[]>([]);
  const [selected, setSelected] = useState<Repo | null>(null);
  const [validation, setValidation] = useState<Validation | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    listRepos()
      .then(setRepos)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  async function handleSelect(repo: Repo) {
    setSelected(repo);
    setValidation(null);
    const result = await validateRepo(repo.owner, repo.name);
    setValidation(result);
  }

  async function handleMigrate() {
    if (!selected) return;
    await createJob(selected);
    navigate('/jobs');
  }

  return (
    <div style={{ maxWidth: 800, margin: '0 auto', padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--border)', paddingBottom: 16, marginBottom: 24 }}>
        <h1 style={{ fontSize: 18, fontWeight: 700, letterSpacing: 2 }}>{'>'} SELECT REPOSITORY</h1>
        <button style={btn} onClick={() => { logout(); localStorage.removeItem('sessionToken'); navigate('/login'); }}>
          [ LOGOUT ]
        </button>
      </div>

      {loading ? <p style={{ color: 'var(--green-dim)' }}>SCANNING REPOSITORIES...</p> : error ? (
        <p style={{ color: 'var(--amber)' }}>⚠ {error}</p>
      ) : (
        <ul style={{ listStyle: 'none', padding: 0 }}>
          {repos.map((r) => (
            <li
              key={r.full_name}
              onClick={() => handleSelect(r)}
              style={{
                padding: '10px 14px',
                border: '1px solid var(--border)',
                marginBottom: 2,
                cursor: 'pointer',
                background: selected?.full_name === r.full_name ? 'var(--bg-selected)' : 'transparent',
                color: selected?.full_name === r.full_name ? 'var(--green)' : 'var(--green-dim)',
                fontFamily: 'var(--font)',
                transition: 'all 0.15s',
              }}
              onMouseEnter={e => { if (selected?.full_name !== r.full_name) e.currentTarget.style.background = 'var(--bg-hover)'; }}
              onMouseLeave={e => { if (selected?.full_name !== r.full_name) e.currentTarget.style.background = 'transparent'; }}
            >
              {selected?.full_name === r.full_name ? '▸ ' : '  '}{r.full_name} {r.is_private && '🔒'}
            </li>
          ))}
        </ul>
      )}

      {validation && (
        <div style={{ marginTop: 16, padding: 16, border: '1px solid var(--border)', background: 'var(--bg-panel)' }}>
          {validation.is_valid ? (
            <>
              <p style={{ color: 'var(--green)', marginBottom: 8 }}>✓ MIGRATABLE ARTIFACTS DETECTED:</p>
              <ul style={{ paddingLeft: 20, color: 'var(--green-dim)', marginBottom: 12 }}>
                {validation.workflow_files.map((f) => <li key={f}>workflow: {f}</li>)}
                {validation.dockerfiles.map((f) => <li key={f}>dockerfile: {f}</li>)}
              </ul>
              <button
                onClick={handleMigrate}
                style={{ ...btn, background: 'var(--green)', color: 'var(--bg)', fontWeight: 700 }}
              >
                [ START MIGRATION ]
              </button>
            </>
          ) : (
            <p style={{ color: 'var(--amber)' }}>⚠ {validation.message}</p>
          )}
        </div>
      )}

      <div style={{ marginTop: 24 }}>
        <button style={btn} onClick={() => navigate('/jobs')}>[ VIEW JOBS → ]</button>
      </div>
    </div>
  );
}
