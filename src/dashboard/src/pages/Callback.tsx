import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { authCallback } from '../api';

export function Callback() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const calledRef = useRef(false);

  useEffect(() => {
    if (calledRef.current) return;
    calledRef.current = true;

    const code = params.get('code');
    if (!code) { navigate('/login'); return; }

    authCallback(code)
      .then((data) => {
        if (data.error) { setError(data.error); return; }
        localStorage.setItem('sessionToken', data.sessionToken);
        navigate('/repos');
      })
      .catch((err) => setError(err.message || 'Authentication failed'));
  }, [params, navigate]);

  if (error) {
    return (
      <div style={{ textAlign: 'center', marginTop: '20vh' }}>
        <p style={{ color: 'var(--red)', marginBottom: 16 }}>ERROR: AUTHENTICATION FAILED</p>
        <p style={{ color: 'var(--red)', fontSize: 13 }}>{error}</p>
        <button
          onClick={() => navigate('/login')}
          style={{
            marginTop: 24, padding: '8px 24px', background: 'transparent',
            color: 'var(--green)', border: '1px solid var(--green)',
            fontFamily: 'var(--font)', cursor: 'pointer',
          }}
        >
          [ RETRY ]
        </button>
      </div>
    );
  }

  return (
    <p style={{ textAlign: 'center', marginTop: '20vh', color: 'var(--green-dim)' }}>
      AUTHENTICATING<span className="blink">_</span>
    </p>
  );
}
