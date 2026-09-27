import { NavLink, Outlet } from 'react-router-dom';
import { useApi } from '../hooks/useApi';
import { getHealth } from '../api/endpoints';

export default function Layout() {
  const health = useApi(() => getHealth(), []);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">◆</span>
          <span className="brand-name">Procurement Verifier</span>
          <span className="brand-sub">Validation Intelligence</span>
        </div>
        <div className="topbar-right">
          <nav className="nav">
            <NavLink to="/" end className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}>
              Dashboard
            </NavLink>
            <NavLink to="/cases" className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}>
              Cases
            </NavLink>
          </nav>
          <div className="sys-status">
            {health.loading ? (
              <span className="sys-dot muted" />
            ) : health.error ? (
              <span className="sys-dot bad" title={health.error} />
            ) : (
              <span className="sys-dot ok" />
            )}
            <span className="sys-label">
              {health.data ? `API ${health.data.version} · ${health.data.database}` : 'API offline'}
            </span>
          </div>
        </div>
      </header>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}