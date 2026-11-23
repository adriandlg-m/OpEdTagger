import { useEffect, useMemo, useState } from 'react';

import {
  Activity,
  ArrowDownUp,
  ChevronDown,
  Copy,
  Download,
  Pause,
  Play,
  Search
} from 'lucide-react';

import PageHeader from '../components/PageHeader.jsx';
import { api } from '../api/client.js';

import '../styles/logs.css';

const LEVELS = ['Todos', 'INFO', 'WARNING', 'ERROR'];

const getLogKey = (log) =>
  log.id ??
  `${log.time}-${log.level}-${log.source}-${log.message}`;

const getTimeValue = (value) => {
  const time = String(value ?? '');

  const match = time.match(/(\d{1,2}):(\d{2})(?::(\d{2}))?/);

  if (match) {
    return (
      Number(match[1]) * 3600 +
      Number(match[2]) * 60 +
      Number(match[3] || 0)
    );
  }

  const parsed = Date.parse(time);

  return Number.isNaN(parsed) ? 0 : parsed;
};

export default function Logs({ logs = [], onToast, autoRefresh = true }) {
  const [level, setLevel] = useState('Todos');
  const [search, setSearch] = useState('');
  const [paused, setPaused] = useState(false);
  const [expanded, setExpanded] = useState(null);
  const [sort, setSort] = useState('recentes');
  const [liveLogs, setLiveLogs] = useState(logs);

  useEffect(() => setLiveLogs(logs), [logs]);
  useEffect(() => {
    if (paused || !autoRefresh) return undefined;
    let active = true;
    const refresh = async () => {
      try {
        const response = await api.logs({ limit: 500 });
        if (active) setLiveLogs(response.logs || []);
      } catch (error) {
        console.error('Could not refresh logs:', error);
      }
    };
    const timer = window.setInterval(refresh, 5000);
    return () => { active = false; window.clearInterval(timer); };
  }, [paused, autoRefresh]);


  // ==========================================
  // Contadores
  // ==========================================

  const counts = useMemo(() => ({
    INFO: liveLogs.filter((log) => log.level === 'INFO').length,
    WARNING: liveLogs.filter((log) => log.level === 'WARNING').length,
    ERROR: liveLogs.filter((log) => log.level === 'ERROR').length
  }), [liveLogs]);

  // ==========================================
  // Filtrado y ordenación
  // ==========================================

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();

    const result = liveLogs.filter((log) => {
      const matchesLevel =
        level === 'Todos' || log.level === level;

      const searchableText = [
        log.time,
        log.level,
        log.source,
        log.message,
        log.detail
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();

      const matchesSearch =
        !query || searchableText.includes(query);

      return matchesLevel && matchesSearch;
    });

    return [...result].sort((a, b) => {
      const difference =
        getTimeValue(a.time) - getTimeValue(b.time);

      return sort === 'recientes'
        ? -difference
        : difference;
    });
  }, [liveLogs, level, search, sort]);

  // ==========================================
  // Copiar registros visibles
  // ==========================================

  const copyAll = async () => {
    if (filtered.length === 0) {
      onToast('No hay registros para copiar.', 'warning');
      return;
    }

    const content = filtered
      .map((log) => {
        return [
          `${log.time} [${log.level}] ${log.source}`,
          log.message,
          log.detail || ''
        ].filter(Boolean).join('\n');
      })
      .join('\n\n');

    try {
      if (!navigator.clipboard?.writeText) {
        throw new Error('Clipboard unavailable');
      }

      await navigator.clipboard.writeText(content);

      onToast(`${filtered.length} registros copiados.`, 'success');
    } catch {
      onToast(
        'No se pudo acceder al portapapeles. Comprueba los permisos del navegador.', 'error'
      );
    }
  };

  // ==========================================
  // Expandir detalles
  // ==========================================

  const toggleExpanded = (log) => {
    const key = getLogKey(log);

    setExpanded((current) =>
      current === key ? null : key
    );
  };

  // ==========================================
  // Render principal
  // ==========================================

  return (
    <div className="page-content secondary-page logs-page">

      <PageHeader
        title="Logs"
        description="Supervisa la actividad, las advertencias y los errores de OpEdTagger."
        eyebrow="SISTEMA / REGISTROS"
        actions={
          <>
            <button
              type="button"
              className="button button-quiet logs-action-button"
              onClick={() => setPaused((current) => !current)}
              title={paused ? 'Reanudar vista' : 'Pausar vista'}
            >
              {paused ? (
                <Play size={15} />
              ) : (
                <Pause size={15} />
              )}

              {paused ? 'Reanudar' : 'Pausar'}
            </button>

            <button
              type="button"
              className="button button-secondary logs-action-button"
              onClick={copyAll}
              title="Copiar registros filtrados"
            >
              <Copy size={15} />
              Copiar
            </button>

            <button
              type="button"
              className="button button-secondary logs-action-button"
              onClick={() => { window.location.assign(api.exportLogsUrl()); }}
              title="Exportar registros"
            >
              <Download size={15} />
              Exportar
            </button>
          </>
        }
      />

      {/* =====================================
          MÉTRICAS
      ===================================== */}

      <section className="logs-metrics">

        <article className="logs-metric logs-metric-info">

          <div className="logs-metric-icon">
            <Activity size={19} />
          </div>

          <div className="logs-metric-content">
            <span className="logs-metric-label">
              Información
            </span>

            <strong>{counts.INFO}</strong>

            <small>Registros informativos</small>
          </div>

          <span className="logs-metric-dot" />

        </article>

        <article className="logs-metric logs-metric-warning">

          <div className="logs-metric-icon">
            <Activity size={19} />
          </div>

          <div className="logs-metric-content">
            <span className="logs-metric-label">
              Advertencias
            </span>

            <strong>{counts.WARNING}</strong>

            <small>Registros de advertencia</small>
          </div>

          <span className="logs-metric-dot" />

        </article>

        <article className="logs-metric logs-metric-error">

          <div className="logs-metric-icon">
            <Activity size={19} />
          </div>

          <div className="logs-metric-content">
            <span className="logs-metric-label">
              Errores
            </span>

            <strong>{counts.ERROR}</strong>

            <small>Registros de error</small>
          </div>

          <span className="logs-metric-dot" />

        </article>

      </section>

      {/* =====================================
          VISOR DE REGISTROS
      ===================================== */}

      <section className="panel logs-viewer-panel">

        {/* Toolbar */}

        <div className="logs-toolbar">

          <div className="logs-toolbar-heading">

            <div className="logs-toolbar-title">
              <div className="logs-toolbar-icon">
                <Activity size={17} />
              </div>

              <div>
                <h2>Registro de actividad</h2>

                <p>
                  Eventos generados por los módulos de la aplicación.
                </p>
              </div>
            </div>

            <span
              className={`logs-live-indicator ${
                paused ? 'logs-live-paused' : ''
              }`}
            >
              <span />
              {paused ? 'Vista pausada' : 'Vista activa'}
            </span>

          </div>

          <div className="logs-toolbar-controls">

            {/* Filtros */}

            <div
              className="logs-filter-tabs"
              role="group"
              aria-label="Filtrar por nivel"
            >
              {LEVELS.map((item) => (
                <button
                  type="button"
                  key={item}
                  className={
                    level === item
                      ? 'logs-filter-active'
                      : ''
                  }
                  onClick={() => {
                    setLevel(item);
                    setExpanded(null);
                  }}
                >
                  {item}
                </button>
              ))}
            </div>

            {/* Búsqueda */}

            <label className="logs-search">
              <Search size={16} />

              <input
                type="search"
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setExpanded(null);
                }}
                placeholder="Buscar registros..."
                aria-label="Buscar registros"
              />
            </label>

            {/* Ordenación */}

            <label className="logs-sort">
              <ArrowDownUp size={15} />

              <select
                value={sort}
                onChange={(event) => {
                  setSort(event.target.value);
                }}
                aria-label="Ordenar registros"
              >
                <option value="recientes">
                  Más recientes
                </option>

                <option value="antiguos">
                  Más antiguos
                </option>
              </select>

              <ChevronDown size={14} />
            </label>

          </div>

        </div>

        {/* Encabezado de columnas */}

        <div className="logs-list-header">
          <span>HORA</span>
          <span>NIVEL</span>
          <span>ORIGEN / MENSAJE</span>
          <span>DETALLE</span>
        </div>

        {/* Registros */}

        <div className="logs-list">

          {filtered.map((log) => {
            const key = getLogKey(log);
            const isExpanded = expanded === key;

            return (
              <article
                className={`logs-entry ${
                  isExpanded ? 'logs-entry-expanded' : ''
                }`}
                key={key}
              >

                <span className="logs-time">
                  {log.time}
                </span>

                <div className="logs-level-cell">
                  <span
                    className={`logs-level logs-level-${String(
                      log.level
                    ).toLowerCase()}`}
                  >
                    <span className="logs-level-dot" />
                    {log.level}
                  </span>
                </div>

                <div className="logs-message">

                  <strong>
                    {log.message}
                  </strong>

                  <span className="logs-source">
                    {log.source}
                  </span>

                  {isExpanded && (
                    <div className="logs-detail">

                      <span className="logs-detail-label">
                        DETALLE DEL REGISTRO
                      </span>

                      <p>
                        {log.detail || 'No hay información adicional para este registro.'}
                      </p>

                    </div>
                  )}

                </div>

                <div className="logs-entry-action">

                  <button
                    type="button"
                    className={`logs-expand-button ${
                      isExpanded ? 'logs-expand-active' : ''
                    }`}
                    onClick={() => toggleExpanded(log)}
                    aria-label={
                      isExpanded
                        ? 'Ocultar detalle'
                        : 'Mostrar detalle'
                    }
                    aria-expanded={isExpanded}
                    title={
                      isExpanded
                        ? 'Ocultar detalle'
                        : 'Mostrar detalle'
                    }
                  >
                    <ChevronDown size={17} />
                  </button>

                </div>

              </article>
            );
          })}

          {/* Estado vacío */}

          {filtered.length === 0 && (
            <div className="logs-empty-state">

              <div className="logs-empty-icon">
                <Search size={24} />
              </div>

              <strong>Sin resultados</strong>

              <span>
                No hay registros que coincidan con los filtros actuales.
              </span>

            </div>
          )}

        </div>

        {/* Footer */}

        <div className="logs-footer">

          <span>
            Mostrando <strong>{filtered.length}</strong> de{' '}
            <strong>{liveLogs.length}</strong> registros
          </span>

          <span className="logs-footer-status">
            <span
              className={`logs-footer-dot ${
                paused ? 'logs-footer-dot-paused' : ''
              }`}
            />

            {paused
              ? 'Vista pausada'
              : 'Registros reales del backend local'}
          </span>

        </div>

      </section>

    </div>
  );
}