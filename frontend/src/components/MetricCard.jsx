import { AlertTriangle, CheckCircle2, Layers3 } from 'lucide-react';

const icons = { layers: Layers3, check: CheckCircle2, alert: AlertTriangle };

export default function MetricCard({ label, value, icon = 'layers', tone = 'violet', note }) {
  const Icon = icons[icon] || Layers3;

  return (
    <article className={`metric-card metric-${tone}`}>
      <div className="metric-icon">
        <Icon size={21} strokeWidth={2.3} />
      </div>
      <div className="metric-content">
        <span className="metric-label">{label}</span>
        <strong className="metric-value">{value}</strong>
        {note && <span className="metric-note">{note}</span>}
      </div>
      <svg
        className="metric-sparkline"
        viewBox="0 0 140 52"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <defs>
          <linearGradient id={`area-${tone}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="currentColor" stopOpacity="0.28" />
            <stop offset="100%" stopColor="currentColor" stopOpacity="0.01" />
          </linearGradient>
        </defs>
        <path
          d="M0,44 C22,42 36,28 56,31 C76,34 92,14 114,18 C126,20 134,10 140,6 L140,52 L0,52 Z"
          fill={`url(#area-${tone})`}
        />
        <path
          d="M0,44 C22,42 36,28 56,31 C76,34 92,14 114,18 C126,20 134,10 140,6"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.2"
          strokeLinecap="round"
        />
      </svg>
    </article>
  );
}