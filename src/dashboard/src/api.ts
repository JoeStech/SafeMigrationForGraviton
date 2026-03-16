const API_BASE = import.meta.env.VITE_API_URL || '';

function getToken(): string | null {
  return localStorage.getItem('sessionToken');
}

async function apiFetch(path: string, options: RequestInit = {}) {
  const token = getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (res.status === 401) {
    localStorage.removeItem('sessionToken');
    window.location.href = '/login';
    throw new Error('Unauthorized');
  }
  return res.json();
}

export function getLoginUrl() { return apiFetch('/auth/login'); }
export function authCallback(code: string) {
  return apiFetch('/auth/callback', { method: 'POST', body: JSON.stringify({ code }) });
}
export function logout() {
  return apiFetch('/auth/logout', { method: 'POST' });
}
export function getSession() { return apiFetch('/auth/session'); }
export function listRepos() { return apiFetch('/repos'); }
export function validateRepo(owner: string, repo: string) {
  return apiFetch(`/repos/${owner}/${repo}/validate`);
}
export function createJob(repository: object) {
  return apiFetch('/jobs', { method: 'POST', body: JSON.stringify({ repository }) });
}
export function listJobs() { return apiFetch('/jobs'); }
export function getJob(jobId: string) { return apiFetch(`/jobs/${jobId}`); }
export function getJobStatus(jobId: string) { return apiFetch(`/jobs/${jobId}/status`); }
