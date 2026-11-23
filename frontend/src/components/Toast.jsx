import { AlertCircle, CheckCircle2, Info, X, AlertTriangle } from 'lucide-react';

const ICONS = {
  success: CheckCircle2,
  error: AlertCircle,
  warning: AlertTriangle,
  info: Info,
};

export default function Toast({ message, type = 'info', onClose }) {
  if (!message) return null;
  const Icon = ICONS[type] || Info;
  const role = type === 'error' || type === 'warning' ? 'alert' : 'status';

  return (
    <div className={`toast toast-${type}`} role={role} aria-live={role === 'alert' ? 'assertive' : 'polite'}>
      <Icon className="toast-icon" size={18} aria-hidden="true" />
      <span className="toast-message">{message}</span>
      <button type="button" className="toast-close" onClick={onClose} aria-label="Cerrar notificación">
        <X size={15} />
      </button>
    </div>
  );
}
