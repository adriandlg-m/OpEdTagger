import { useMemo, useState } from 'react';
import { ArrowUpRight, ChevronDown, ImagePlus, ListFilter, Search, RefreshCw, Play } from 'lucide-react';
import PageHeader from '../components/PageHeader.jsx';
import MetricCard from '../components/MetricCard.jsx';
import Poster from '../components/Poster.jsx';
import StatusBadge from '../components/StatusBadge.jsx';
import PosterSearchModal from '../components/PosterSearchModal.jsx';

export default function Dashboard({ series = [], summary, onOpenSeries, onToast, onGoIndividual, onScanLibrary, onPosterUpdated }) {
  const [search, setSearch] = useState('');
  const [posterTarget, setPosterTarget] = useState(null);
  const [statusFilter, setStatusFilter] = useState('Todos los estados');
  const filtered = useMemo(() => series.filter((item) => {
    const matchesText = item.title.toLowerCase().includes(search.toLowerCase());
    const matchesStatus = statusFilter === 'Todos los estados' || item.status === statusFilter;
    return matchesText && matchesStatus;
  }), [series, search, statusFilter]);
  const latest = summary?.recent_jobs?.[0];
  const format = (value) => Number(value || 0).toLocaleString();

  return (
    <div className="page-content dashboard-page">
      {posterTarget && <PosterSearchModal
        seriesName={posterTarget.name || posterTarget.title}
        onClose={() => setPosterTarget(null)}
        onApplied={async () => { await onPosterUpdated?.(); }}
        onToast={onToast}
      />}
      <PageHeader title="Dashboard" description="Resumen real de tu biblioteca" actions={(
        <button className="button button-secondary" onClick={onScanLibrary}><RefreshCw size={15} /> Analizar biblioteca</button>
      )} />

      <section className="dashboard-overview">
        <MetricCard label="Series en biblioteca" value={format(summary?.total_series)} icon="layers" tone="violet" />
        <MetricCard label="Episodios analizados" value={format(summary?.analyzed_episodes)} icon="check" tone="blue" />
        <MetricCard label="Outliers detectados" value={format(summary?.outliers)} icon="alert" tone="amber" />
        <article className="activity-card">
          <Poster variant="default" imageUrl={series.find((item) => item.name === latest?.series_name)?.poster_url || ''} alt="Carátula de actividad reciente" className="activity-poster" />
          <div className="activity-copy">
            <div className="activity-heading"><span className="online-dot" /> <span>Actividad reciente</span></div>
            <h3>{latest?.series_name || 'Sin actividad'}</h3>
            <p>{latest?.created_at ? new Date(latest.created_at).toLocaleString() : 'Todavía no hay trabajos registrados'}</p>
            <p className="activity-result">{latest ? `Trabajo ${latest.id}` : `${format(summary?.pending_episodes)} episodios pendientes`}</p>
            {latest && <StatusBadge status={latest.status === 'completed' ? 'Listo' : latest.status === 'completed_with_errors' ? 'Parcial' : latest.status === 'running' || latest.status === 'cancellation_requested' ? 'En curso' : latest.status === 'failed' ? 'Error' : latest.status === 'interrupted' ? 'Interrumpido' : latest.status === 'cancelled' ? 'Cancelado' : 'Pendiente'} />}
          </div>
          {latest && <button className="activity-open" onClick={() => onToast(`Trabajo ${latest.id}: ${latest.status}`)} aria-label="Ver último trabajo"><ArrowUpRight size={18} /></button>}
        </article>
      </section>

      <section className="panel series-panel">
        <div className="panel-toolbar">
          <div className="panel-title-wrap">
            <div className="panel-title-icon"><ListFilter size={18} /></div>
            <div><h2>Biblioteca</h2><p>{summary?.library_path || 'Configura una ruta de biblioteca en Settings.'}</p></div>
          </div>
          <div className="toolbar-controls">
            <label className="search-control"><Search size={17} /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Buscar serie..." /></label>
            <label className="select-control"><select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} aria-label="Filtrar por estado">
              <option>Todos los estados</option><option>Listo</option><option>Outliers</option><option>En curso</option><option>Pendiente</option><option>Sin archivos</option>
            </select><ChevronDown size={15} /></label>
            <button className="button button-primary" onClick={onGoIndividual}><Play size={15} fill="currentColor" /> Análisis individual</button>
          </div>
        </div>

        <div className="series-table-wrap">
          <table className="data-table series-table">
            <thead><tr><th>SERIE</th><th>EPISODIOS</th><th>ANALIZADOS</th><th>ESTADO</th><th className="actions-heading">ACCIONES</th></tr></thead>
            <tbody>
              {filtered.map((item) => (
                <tr key={item.id} onDoubleClick={() => onOpenSeries(item)}>
                  <td><button className="series-cell" onClick={() => onOpenSeries(item)}><Poster variant="default" imageUrl={item.poster_url || ''} alt={`Carátula de ${item.title}`} className="table-poster" /><span className="series-name"><strong>{item.title}</strong></span></button></td>
                  <td><span className="episode-count">{format(item.episodes)}</span></td>
                  <td><span className="episode-count">{format(item.analyzed)} / {format(item.episodes)}</span></td>
                  <td><StatusBadge status={item.status} /></td>
                  <td><div className="row-actions">
                    <button className="icon-button" onClick={() => setPosterTarget(item)} title="Buscar o cambiar carátula" aria-label={`Buscar carátula para ${item.title}`}><ImagePlus size={15} /></button>
                    <button className="icon-button row-open" onClick={() => onOpenSeries(item)} title="Abrir serie" aria-label={`Abrir ${item.title}`}><ArrowUpRight size={16} /></button>
                  </div></td>
                </tr>
              ))}
              {filtered.length === 0 && <tr><td colSpan="5"><div className="empty-state"><Search size={24} /><strong>{series.length ? 'No se encontraron series' : 'Biblioteca vacía'}</strong><span>{series.length ? 'Prueba con otro nombre o filtro.' : 'Configura la ruta de biblioteca y pulsa Analizar biblioteca.'}</span></div></td></tr>}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
