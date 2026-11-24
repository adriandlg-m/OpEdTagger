import { AlertTriangle, Check, Clock3, LoaderCircle, CircleX, SkipForward } from 'lucide-react';

const config = {
  Listo: { tone: 'success', icon: Check },
  'Listo · revisar': { tone: 'warning', icon: AlertTriangle },
  'Listo · revisar': { tone: 'warning', icon: AlertTriangle },
  Conectado: { tone: 'success', icon: Check },
  Configurado: { tone: 'success', icon: Check },
  Outliers: { tone: 'warning', icon: AlertTriangle },
  Outlier: { tone: 'warning', icon: AlertTriangle },
  'En curso': { tone: 'info', icon: LoaderCircle },
  'Analizado': { tone: 'info', icon: Check },
  'Revisión': { tone: 'warning', icon: AlertTriangle },
  'Revisión necesaria': { tone: 'danger', icon: AlertTriangle },
  'Revisión recomendada': { tone: 'warning', icon: AlertTriangle },
  'Preparado': { tone: 'info', icon: Check },
  'Sin coincidencias': { tone: 'neutral', icon: Clock3 },
  'Desactualizado': { tone: 'warning', icon: AlertTriangle },
  'Interrumpido': { tone: 'danger', icon: CircleX },
  Parcial: { tone: 'warning', icon: AlertTriangle },
  Procesando: { tone: 'info', icon: LoaderCircle },
  Pendiente: { tone: 'neutral', icon: Clock3 },
  Omitido: { tone: 'neutral', icon: SkipForward },
  Cancelado: { tone: 'danger', icon: CircleX },
  Error: { tone: 'danger', icon: CircleX },
};

export default function StatusBadge({ status, compact = false }) {
  const item = config[status] || config.Pendiente;
  const Icon = item.icon;
  return (
    <span className={`status-badge status-${item.tone} ${compact ? 'status-compact' : ''}`}>
      <Icon size={compact ? 11 : 13} strokeWidth={2.4} />
      <span>{status}</span>
    </span>
  );
}
