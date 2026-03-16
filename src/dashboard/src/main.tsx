import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import './console.css';
import { Login } from './pages/Login';
import { Callback } from './pages/Callback';
import { Repos } from './pages/Repos';
import { Jobs } from './pages/Jobs';
import { JobDetail } from './pages/JobDetail';

function AuthGuard({ children }: { children: React.ReactNode }) {
  const token = localStorage.getItem('sessionToken');
  if (!token) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/callback" element={<Callback />} />
        <Route path="/repos" element={<AuthGuard><Repos /></AuthGuard>} />
        <Route path="/jobs" element={<AuthGuard><Jobs /></AuthGuard>} />
        <Route path="/jobs/:jobId" element={<AuthGuard><JobDetail /></AuthGuard>} />
        <Route path="*" element={<Navigate to="/repos" replace />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>
);
