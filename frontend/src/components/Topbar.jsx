import { Bell, Moon, Sun } from 'lucide-react';

export default function Topbar({ theme, onThemeChange }) {
  return (
    <header className="topbar">
      <div className="topbar-context" />
      <div className="topbar-actions">
        <div className="theme-switch" role="group" aria-label="Tema de interfaz">
          <button
            className={theme === 'mate' ? 'selected' : ''}
            onClick={() => onThemeChange('mate')}
            aria-pressed={theme === 'mate'}
          >
            <Moon size={15} /> <span>Dark</span>
          </button>
          <button
            className={theme === 'light' ? 'selected' : ''}
            onClick={() => onThemeChange('light')}
            aria-pressed={theme === 'light'}
          >
            <Sun size={15} /> <span>Light</span>
          </button>
        </div>
        <button className="icon-button notification-button" aria-label="Notificaciones">
          <Bell size={18} />
          <i />
        </button>
      </div>
    </header>
  );
}