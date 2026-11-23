import { AudioLines, LayoutDashboard, Logs, Settings, Zap } from 'lucide-react';

const navItems = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { id: 'individual', label: 'Análisis Individual', icon: AudioLines },
  { id: 'logs', label: 'Logs', icon: Logs },
  { id: 'settings', label: 'Settings', icon: Settings },
];

export default function Sidebar({ page, onNavigate, backendOnline = false }) {
  return (
    <aside className="sidebar">
      <div className="brand-block">
        <div className="brand-mark"><Zap size={24} fill="currentColor" /></div>
        <div className="brand-copy">
          <strong>OpEdTagger</strong>
        </div>
      </div>

      <nav className="side-nav" aria-label="Navegación principal">
        {navItems.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            className={`nav-item ${page === id || (page === 'detail' && id === 'dashboard') ? 'active' : ''}`}
            onClick={() => onNavigate(id)}
          >
            <Icon size={18} strokeWidth={2.1} />
            <span>{label}</span>
          </button>
        ))}
      </nav>

      <div className="sidebar-spacer" />
      <div className="server-card">
        <div className="server-line"><span className={`online-dot ${backendOnline ? "" : "server-offline-dot"}`} /><strong>{backendOnline ? "Servidor en línea" : "Servidor desconectado"}</strong></div>
        <span className="server-caption">Última actualización</span>
        <strong className="server-time">{backendOnline ? "API local activa" : "Inicia el backend"}</strong>
      </div>
      <div className="sidebar-decoration" aria-hidden="true"><span /><span /><span /></div>
    </aside>
  );
}