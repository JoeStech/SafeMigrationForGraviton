import { useEffect, useState } from 'react';
import { getLoginUrl } from '../api';

export function Login() {
  const [url, setUrl] = useState('');

  useEffect(() => {
    getLoginUrl().then((data) => setUrl(data.url));
  }, []);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: '100vh' }}>
      <pre style={{ color: 'var(--green)', fontSize: 11, lineHeight: 1.2, marginBottom: 24, textShadow: 'var(--green-glow)' }}>{`
  ___        __     __  __ _                 _   _
 / __| __ _ / _|___|  \\/  (_)__ _ _ _ __ _| |_(_)___ _ _
 \\__ \\/ _\` |  _/ -_) |\\/| | / _\` | '_/ _\` |  _| / _ \\ ' \\
 |___/\\__,_|_| \\___|_|  |_|_\\__, |_| \\__,_|\\__|_\\___/_||_|
                             |___/
      `}</pre>
      <p style={{ color: 'var(--green-dim)', marginBottom: 32, letterSpacing: 2 }}>
        {'>'} ELIMINATE THE FEAR OF GRAVITON MIGRATION_
      </p>
      <a
        href={url}
        role="button"
        style={{
          padding: '12px 32px',
          fontSize: 14,
          background: 'transparent',
          color: 'var(--green)',
          border: '1px solid var(--green)',
          fontFamily: 'var(--font)',
          letterSpacing: 1,
          cursor: 'pointer',
          textDecoration: 'none',
          transition: 'all 0.2s',
        }}
        onMouseEnter={e => { e.currentTarget.style.background = 'var(--green)'; e.currentTarget.style.color = 'var(--bg)'; }}
        onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--green)'; }}
      >
        [ LOGIN WITH GITHUB ]
      </a>
      <p style={{ color: 'var(--text-dim)', marginTop: 48, fontSize: 12 }}>
        SYSTEM READY ■
      </p>
    </div>
  );
}
