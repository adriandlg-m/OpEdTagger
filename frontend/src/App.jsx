import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

import Sidebar from './components/Sidebar.jsx';
import Topbar from './components/Topbar.jsx';
import Toast from './components/Toast.jsx';
import Dashboard from './pages/Dashboard.jsx';
import SeriesDetail from './pages/SeriesDetail.jsx';
import IndividualAnalysis from './pages/IndividualAnalysis.jsx';
import Logs from './pages/Logs.jsx';
import Settings from './pages/Settings.jsx';
import { api, mapEpisode } from './api/client.js';
import { createRequestSequence } from './api/requestSequence.js';

export default function App() {
  const [page, setPage] = useState('dashboard');
  const [theme, setTheme] = useState('mate');
  const [series, setSeries] = useState([]);
  const [summary, setSummary] = useState(null);
  const [selectedSeries, setSelectedSeries] = useState(null);
  const [seriesDetail, setSeriesDetail] = useState(null);
  const [seriesDetailFor, setSeriesDetailFor] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailLoadError, setDetailLoadError] = useState('');
  const [jobs, setJobs] = useState([]);
  const [activeJob, setActiveJob] = useState(null);
  const [logs, setLogs] = useState([]);
  const [settings, setSettings] = useState(null);
  const [preferences, setPreferences] = useState({});
  const [backendOnline, setBackendOnline] = useState(false);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState(null);
  const toastSequence = useRef(0);
  const [error, setError] = useState('');
  const scrollRef = useRef(null);
  // Prevent out-of-order responses from a previously selected series from
  // replacing the detail currently being viewed.
  const seriesDetailRequestRef = useRef(null);
  const selectedSeriesNameRef = useRef(null);
  if (seriesDetailRequestRef.current === null) {
    seriesDetailRequestRef.current = createRequestSequence();
  }

  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToast(null), 3600);
    return () => window.clearTimeout(timer);
  }, [toast?.id]);

  const notify = useCallback((message, type = 'info') => {
    toastSequence.current += 1;
    setToast({ id: toastSequence.current, message: String(message), type });
  }, []);

  const refreshDashboard = useCallback(async (scan = false) => {
    const data = await api.dashboard(scan);
    setSummary(data);
    setSeries((data.series || []).map((item) => ({
      ...item,
      title: item.name,
      year: '—',
      poster: 'default',
      status: item.status,
    })));
    return data;
  }, []);

  const refreshJobs = useCallback(async () => {
    const data = await api.jobs();
    setJobs(data.jobs || []);
    const current = (data.jobs || []).find((job) => ['queued', 'running', 'cancellation_requested'].includes(job.status));
    setActiveJob(current || null);
  }, []);

  const refreshLogs = useCallback(async () => {
    const data = await api.logs({ limit: 500 });
    setLogs(data.logs || []);
  }, []);

  const refreshSettings = useCallback(async () => {
    const [config, prefs] = await Promise.all([api.settings(), api.preferences()]);
    setSettings(config);
    setPreferences(prefs);
    if (prefs.theme) setTheme(prefs.theme);
  }, []);

  const refreshSeriesDetail = useCallback(async (name, requestId = null) => {
    // Refreshes for the current series share its selection token. Only a new
    // selection/reopen invalidates requests, so periodic refreshes cannot
    // repeatedly supersede one another while a slow local API call is active.
    const activeRequestId = requestId ?? seriesDetailRequestRef.current.current();
    const data = await api.series(name);

    // A late response is allowed to finish, but it must never replace the
    // detail belonging to a newer selection.
    if (seriesDetailRequestRef.current.isCurrent(activeRequestId)
      && selectedSeriesNameRef.current === name) {
      setSeriesDetail(data);
      setSeriesDetailFor(name);
      setDetailLoading(false);
      setDetailLoadError('');
    }

    return data;
  }, []);

  const selectedSeriesJob = jobs.find((job) => ['series', 'injection'].includes(job.kind)
    && job.series_name === selectedSeries?.name
    && ['queued', 'running', 'cancellation_requested'].includes(job.status));
  const latestSelectedSeriesJob = jobs.find((job) => ['series', 'injection'].includes(job.kind)
    && job.series_name === selectedSeries?.name);

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        await api.health();
        if (!mounted) return;
        setBackendOnline(true);
        await Promise.all([refreshDashboard(true), refreshJobs(), refreshSettings()]);
      } catch (err) {
        if (mounted) {
          setBackendOnline(false);
          setError(err.message || 'No se pudo conectar con el backend.');
        }
      } finally {
        if (mounted) setLoading(false);
      }
    })();
    return () => { mounted = false; };
  }, [refreshDashboard, refreshJobs, refreshSettings]);

  useEffect(() => {
    if (!backendOnline) return undefined;
    const timer = window.setInterval(async () => {
      try {
        await refreshJobs();
        if (page === 'dashboard') await refreshDashboard();
        // Detail pages only rescan their episode files while a series job is active.
        // Manual review/injection handlers refresh explicitly after each operation.
        if (page === 'detail' && selectedSeries?.name && selectedSeriesJob) {
          await refreshSeriesDetail(selectedSeries.name);
        }
      } catch (err) {
        console.error('Background refresh failed:', err);
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [backendOnline, page, selectedSeries, selectedSeriesJob?.id, refreshDashboard, refreshJobs, refreshSeriesDetail]);

  useEffect(() => {
    if (page === 'logs' && backendOnline) refreshLogs().catch((err) => setError(err.message));
    if (page === 'settings' && backendOnline) refreshSettings().catch((err) => setError(err.message));
  }, [page, backendOnline, refreshLogs, refreshSettings]);

  const viewKey = page === 'detail' ? `detail-${selectedSeries?.name || 'unknown'}` : page;
  useLayoutEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [viewKey]);

  const openSeries = async (item) => {
    const name = item.name || item.title;
    if (!name) return;

    // Clear the previous series before React paints the detail view. This
    // prevents its poster, banner, synopsis and episode rows flashing while
    // the next series is loading.
    const requestId = seriesDetailRequestRef.current.begin();
    selectedSeriesNameRef.current = name;
    setSelectedSeries({ ...item, name, title: item.title || name });
    setSeriesDetail(null);
    setSeriesDetailFor(null);
    setDetailLoading(true);
    setDetailLoadError('');
    setPage('detail');

    try {
      const detail = await refreshSeriesDetail(name, requestId);
      if (!seriesDetailRequestRef.current.isCurrent(requestId)) return;
      setSelectedSeries((current) => (current?.name === name
        ? { ...current, episodes: detail.total_episodes, analyzed: detail.analyzed_episodes, outliers: detail.outliers }
        : current));
    } catch (err) {
      if (!seriesDetailRequestRef.current.isCurrent(requestId)) return;
      setDetailLoading(false);
      setDetailLoadError(err.message || 'No se pudo cargar el detalle de la serie.');
      notify(err.message, 'error');
    }
  };

  const startProcessing = async () => {
    if (!selectedSeries?.name) return;
    try {
      const job = await api.startSeriesJob(selectedSeries.name);
      setActiveJob(job);
      notify('Análisis de serie iniciado. Los MKV no se modificarán.', 'success');
      await refreshJobs();
    } catch (err) {
      notify(err.message, 'error');
    }
  };

  const cancelProcessing = async (requestedJobId = null) => {
    const jobId = requestedJobId || activeJob?.id;
    if (!jobId) return;
    try {
      await api.cancelJob(jobId);
      notify('Solicitud de cancelación enviada.', 'success');
    } catch (err) {
      notify(err.message, 'error');
    }
  };

  const saveSettings = async (payload) => {
    const saved = await api.saveSettings(payload);
    setSettings((current) => ({ ...current, ...saved }));
    await refreshDashboard();
    notify('Configuración guardada.', 'success');
  };

  const savePreferences = async (payload) => {
    const saved = await api.savePreferences(payload);
    setPreferences((current) => ({ ...current, ...saved }));
    if (saved.theme) setTheme(saved.theme);
    notify('Preferencias guardadas.', 'success');
  };

  // Theme is a presentation choice: apply immediately and persist silently.
  // Only failures are surfaced; switching between Dark and Light is not an
  // operation that needs a success notification.
  const persistTheme = async (value) => {
    setTheme(value);
    try {
      const saved = await api.savePreferences({ theme: value });
      setPreferences((current) => ({ ...current, ...saved, theme: value }));
    } catch (err) {
      notify(err.message || 'No se pudo guardar el tema seleccionado.', 'error');
    }
  };

  const scanLibrary = async () => {
    try {
      const result = await api.scanLibrary();
      await refreshDashboard();
      notify(`Biblioteca actualizada: ${result.total_series} series y ${result.total_episodes} episodios.`, 'success');
    } catch (err) {
      notify(err.message, 'error');
    }
  };

  return (
    <div className="app-shell">
      <Sidebar page={page} onNavigate={setPage} backendOnline={backendOnline} />
      <main className="main-shell">
        <Topbar theme={theme} onThemeChange={persistTheme} />
        <div className={`page-scroll ${['dashboard', 'detail', 'logs'].includes(page) ? 'page-scroll-contained' : ''}`} ref={scrollRef}>
          <div key={viewKey} className={`page-transition ${page === 'dashboard' ? 'page-transition-dashboard' : ''} ${page === 'detail' ? 'page-transition-detail' : ''} ${page === 'logs' ? 'page-transition-logs' : ''}`}>
            {!backendOnline && !loading && (
              <div className="page-content"><div className="panel" style={{ padding: 24 }}><h2>Backend no disponible</h2><p>{error || 'Inicia OpEdTagger con run_windows.bat o python -m backend.'}</p></div></div>
            )}
            {backendOnline && loading && <div className="page-content"><div className="panel" style={{ padding: 24 }}>Conectando con OpEdTagger…</div></div>}
            {backendOnline && !loading && page === 'dashboard' && (
              <Dashboard series={series} summary={summary} onOpenSeries={openSeries} onToast={notify}
                onGoIndividual={() => setPage('individual')} onScanLibrary={scanLibrary}
                onPosterUpdated={() => refreshDashboard(false)} />
            )}
            {backendOnline && !loading && page === 'detail' && selectedSeries
              && seriesDetail && seriesDetailFor === selectedSeries.name && (
              <SeriesDetail key={`series-detail-${selectedSeries.name}`}
                series={{ ...selectedSeries, ...seriesDetail, episodes: seriesDetail.total_episodes,
                  analyzed: seriesDetail.analyzed_episodes, outliers: seriesDetail.outliers }}
                episodes={(seriesDetail.episodes || []).map(mapEpisode)} showOutliers={preferences.show_outliers !== false} onBack={() => setPage('dashboard')}
                onStartProcessing={startProcessing} onCancelProcessing={cancelProcessing}
                activeJob={selectedSeriesJob || latestSelectedSeriesJob || activeJob} onRefresh={() => refreshSeriesDetail(selectedSeries.name)}
                onPosterUpdated={() => refreshDashboard(false)} onToast={notify} />
            )}
            {backendOnline && !loading && page === 'detail' && selectedSeries
              && (!seriesDetail || seriesDetailFor !== selectedSeries.name) && (
              <div className="page-content detail-load-state" role="status" aria-live="polite" aria-busy={detailLoading}>
                <div className="panel detail-load-panel">
                  {detailLoadError ? (
                    <>
                      <h2>No se pudo cargar {selectedSeries.name}</h2>
                      <p>{detailLoadError}</p>
                      <div className="inline-actions">
                        <button className="button button-secondary" onClick={() => openSeries(selectedSeries)}>Reintentar</button>
                        <button className="button button-ghost" onClick={() => setPage('dashboard')}>Volver al Dashboard</button>
                      </div>
                    </>
                  ) : (
                    <>
                      <span className="detail-load-spinner" aria-hidden="true" />
                      <h2>Cargando {selectedSeries.name}…</h2>
                      <p>Actualizando episodios y metadatos de esta serie.</p>
                    </>
                  )}
                </div>
              </div>
            )}
            {backendOnline && !loading && page === 'individual' && <IndividualAnalysis onToast={notify} />}
            {backendOnline && !loading && page === 'logs' && <Logs logs={logs} onToast={notify} autoRefresh={preferences.log_refresh !== false} />}
            {backendOnline && !loading && page === 'settings' && (
              <Settings theme={theme} onThemeChange={persistTheme}
                settings={settings} preferences={preferences} onSaveSettings={saveSettings}
                onSavePreferences={savePreferences} onToast={notify} onScanLibrary={scanLibrary} />
            )}
          </div>
        </div>
      </main>
      <Toast message={toast?.message} type={toast?.type} onClose={() => setToast(null)} />
    </div>
  );
}
