import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowLeft, CheckCircle2, ChevronDown, ChevronRight, CircleAlert, Play, Search, ShieldCheck, XCircle, Eye, FileCheck2, Folder, FolderOpen, ImagePlus, Trash2, ZoomIn, ExternalLink } from 'lucide-react';
import PageHeader from '../components/PageHeader.jsx';
import Poster from '../components/Poster.jsx';
import PosterSearchModal from '../components/PosterSearchModal.jsx';
import SeriesArtworkLightbox from '../components/SeriesArtworkLightbox.jsx';
import StatusBadge from '../components/StatusBadge.jsx';
import { api, formatTimestamp } from '../api/client.js';

function EpisodeRow({ episode, onApprove, onCorrect, onInject, onToast, showOutliers = true }) {
  const [expanded, setExpanded] = useState(false);
  const [opStart, setOpStart] = useState(episode.op?.start ?? '');
  const [opEnd, setOpEnd] = useState(episode.op?.end ?? '');
  const [edStart, setEdStart] = useState(episode.ed?.start ?? '');
  const [edEnd, setEdEnd] = useState(episode.ed?.end ?? '');
  useEffect(() => {
    setOpStart(episode.op?.start ?? ''); setOpEnd(episode.op?.end ?? '');
    setEdStart(episode.ed?.start ?? ''); setEdEnd(episode.ed?.end ?? '');
  }, [episode.op?.start, episode.op?.end, episode.ed?.start, episode.ed?.end]);
  const isOutlier = showOutliers && episode.status === 'Outlier';
  const badgeStatus = !showOutliers && episode.status === 'Outlier'
    ? (episode.review_status === 'PENDING' ? 'Revisión' : episode.injection_status === 'SUCCEEDED' ? 'Listo' : 'Analizado')
    : episode.status;
  const needsReview = episode.review_status === 'PENDING';
  const approved = episode.review_status === 'APPROVED';
  const injected = episode.injection_status === 'SUCCEEDED';
  const injectionInProgress = episode.injection_status === 'IN_PROGRESS';
  const recoveryRequired = episode.injection_status === 'RECOVERY_REQUIRED';
  const reviewLocked = injectionInProgress || recoveryRequired;
  const hasResult = ['COMPLETED', 'NO_MATCH'].includes(episode.analysis_status);
  const legacyResult = episode.analysis_status === 'STALE' || episode.legacy_result;
  const saveCorrections = () => {
    const build = (start, end, existing, label) => {
      if (String(start).trim() === '' && String(end).trim() === '') return null;
      const numericStart = Number(start); const numericEnd = Number(end);
      if (!Number.isFinite(numericStart) || !Number.isFinite(numericEnd) || numericStart < 0 || numericStart >= numericEnd) {
        throw new Error(`${label}: introduce valores numéricos donde el inicio sea menor que el final.`);
      }
      return { ...(existing || {}), name: existing?.name || label, start: numericStart, end: numericEnd };
    };
    try {
      const op = build(opStart, opEnd, episode.op, 'OP');
      const ed = build(edStart, edEnd, episode.ed, 'ED');
      if (!op && !ed) throw new Error('Introduce al menos un segmento antes de guardar.');
      onCorrect(episode, { op, ed });
    } catch (error) { onToast(error.message, 'error'); }
  };

  return (
    <article className={`episode-row ${isOutlier ? 'episode-outlier' : ''}`}>
      <div className="episode-main-row">
        <div className="episode-id-block"><strong>{episode.displayId || episode.file_name}</strong><span className="stage-label">{episode.file_name}</span></div>
        <div className="episode-segment"><span className="segment-label">Opening</span><strong>{episode.opening}</strong><small className={episode.openingScore === '—' ? 'muted-score' : 'score-positive'}>{episode.openingScore === '—' ? 'Sin coincidencia' : `Score ${episode.openingScore}`}</small></div>
        <div className="episode-segment"><span className="segment-label">Ending</span><strong>{episode.ending}</strong><small className={episode.endingScore === '—' ? 'muted-score' : 'score-positive'}>{episode.endingScore === '—' ? 'Sin coincidencia' : `Score ${episode.endingScore}`}</small></div>
        <div className="episode-status"><StatusBadge status={badgeStatus} /></div>
        <div className="episode-row-actions"><button className="icon-button expand-button" onClick={() => setExpanded((value) => !value)} aria-label="Mostrar detalles"><ChevronDown size={17} /></button></div>
      </div>
      {expanded && (
        <div className="outlier-detail">
          <div className="outlier-detail-icon"><CircleAlert size={17} /></div>
          <div className="outlier-detail-copy">
            <strong>Estado del episodio</strong>
            <p>Análisis: {episode.analysis_status} · Revisión: {episode.review_status} · Inyección: {episode.injection_status}</p>
            <div className="outlier-meta">
              {episode.op && <span>OP: <b>{formatTimestamp(episode.op.start)}–{formatTimestamp(episode.op.end)}</b></span>}
              {episode.ed && <span>ED: <b>{formatTimestamp(episode.ed.start)}–{formatTimestamp(episode.ed.end)}</b></span>}
              {showOutliers && episode.is_anomaly_op && <span><b>OP marcada como anomalía</b></span>}
              {showOutliers && episode.is_anomaly_ed && <span><b>ED marcada como anomalía</b></span>}
              {episode.error && <span>Error: <b>{episode.error}</b></span>}
              {legacyResult && <span>Estos timestamps provienen del JSON antiguo. Se conservan como referencia, pero debes repetir el análisis para validarlos antes de revisarlos o inyectarlos.</span>}
            </div>
            {hasResult && <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 12, marginTop: 14, maxWidth: 620 }}>
              {[{ label: 'Opening', start: opStart, end: opEnd, setStart: setOpStart, setEnd: setOpEnd },
                { label: 'Ending', start: edStart, end: edEnd, setStart: setEdStart, setEnd: setEdEnd }].map((item) => (
                <div key={item.label} style={{ display: 'grid', gap: 6 }}>
                  <strong style={{ fontSize: 12, color: 'var(--text-secondary, #9ca3af)' }}>{item.label} · segundos</strong>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
                    <input aria-label={`${item.label} inicio en segundos`} type="number" min="0" step="0.01" value={item.start} disabled={reviewLocked} onChange={(event) => item.setStart(event.target.value)} placeholder="Inicio" style={{ minWidth: 0, width: '100%', padding: '8px 9px', borderRadius: 8, border: '1px solid var(--border-color, #333)', background: 'var(--surface-2, #151a27)', color: 'var(--text-primary, #fff)' }} />
                    <input aria-label={`${item.label} final en segundos`} type="number" min="0" step="0.01" value={item.end} disabled={reviewLocked} onChange={(event) => item.setEnd(event.target.value)} placeholder="Final" style={{ minWidth: 0, width: '100%', padding: '8px 9px', borderRadius: 8, border: '1px solid var(--border-color, #333)', background: 'var(--surface-2, #151a27)', color: 'var(--text-primary, #fff)' }} />
                  </div>
                </div>
              ))}
            </div>}
            <div className="inline-actions" style={{ marginTop: 12, flexWrap: 'wrap' }}>
              {hasResult && <button className="button button-secondary button-small" onClick={saveCorrections} disabled={reviewLocked}>Guardar timestamps</button>}
              {legacyResult && <span className="muted-score">Reanaliza el episodio para habilitar la revisión segura.</span>}
              {needsReview && <button className="button button-primary button-small" onClick={() => onApprove(episode)}><CheckCircle2 size={13} /> Aprobar detecciones</button>}
              {approved && !injected && !reviewLocked && <button className="button button-secondary button-small" onClick={() => onInject(episode)}><ShieldCheck size={13} /> Revisar e inyectar</button>}
              {injectionInProgress && <span className="muted-score">Inyección en curso. No edites ni vuelvas a ejecutar esta operación.</span>}
              {injected && <span className="status-badge status-success"><FileCheck2 size={13} /> Capítulos verificados</span>}
              {recoveryRequired && <span className="muted-score">La aplicación se interrumpió durante una inyección. Inspecciona el MKV y su respaldo antes de continuar.</span>}
              {!hasResult && <span className="muted-score">No hay resultados que aprobar.</span>}
            </div>
          </div>
          <span className="outlier-detail-label">Datos reales</span>
        </div>
      )}
    </article>
  );
}

function buildFolderTree(episodes) {
  const root = { name: '', path: '', folders: new Map(), episodes: [] };

  for (const episode of episodes) {
    const rawSegments = Array.isArray(episode.folder_segments)
      ? episode.folder_segments
      : String(episode.folder_path || '').split(/[\\/]+/).filter(Boolean);

    let current = root;
    const accumulated = [];

    for (const segment of rawSegments) {
      if (!current.folders.has(segment)) {
        accumulated.push(segment);
        current.folders.set(segment, {
          name: segment,
          path: accumulated.join('/'),
          folders: new Map(),
          episodes: [],
        });
      } else {
        accumulated.push(segment);
      }
      current = current.folders.get(segment);
    }

    current.episodes.push(episode);
  }

  return root;
}

function countFolderEpisodes(folder) {
  let count = folder.episodes.length;
  for (const child of folder.folders.values()) count += countFolderEpisodes(child);
  return count;
}

function FolderNode({ folder, depth, onApprove, onCorrect, onInject, onToast, showOutliers, expandAll = false }) {
  // Every real folder starts collapsed. Active search/status filters expand
  // the matching tree temporarily so filtered episodes remain discoverable.
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    setExpanded(Boolean(expandAll));
  }, [expandAll]);
  const childFolders = Array.from(folder.folders.values()).sort((a, b) =>
    a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' })
  );
  const total = countFolderEpisodes(folder);
  const isExpanded = expanded || expandAll;

  return (
    <section className={`folder-node ${depth > 0 ? 'folder-node-nested' : ''}`} data-folder-path={folder.path} style={{ '--folder-depth': depth }}>
      <button
        type="button"
        className="folder-node-heading"
        onClick={() => { if (!expandAll) setExpanded((value) => !value); }}
        aria-expanded={isExpanded}
        title={folder.path}
      >
        <span className="folder-node-leading">
          {isExpanded ? <FolderOpen size={17} /> : <Folder size={17} />}
        </span>
        <span className="folder-node-name">{folder.name}</span>
        <span className="folder-node-count">{total} {total === 1 ? 'archivo' : 'archivos'}</span>
        <span className="folder-node-chevron">{isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</span>
      </button>

      {isExpanded && (
        <div className="folder-node-content">
          {folder.episodes.length > 0 && (
            <div className="episode-list folder-episode-list">
              {folder.episodes.map((episode) => (
                <EpisodeRow key={episode.file_path} episode={episode} onApprove={onApprove}
                  onCorrect={onCorrect} onInject={onInject} onToast={onToast} showOutliers={showOutliers} />
              ))}
            </div>
          )}
          {childFolders.map((child) => (
            <FolderNode key={child.path} folder={child} depth={depth + 1}
              onApprove={onApprove} onCorrect={onCorrect} onInject={onInject}
              onToast={onToast} showOutliers={showOutliers} expandAll={expandAll} />
          ))}
        </div>
      )}
    </section>
  );
}

export default function SeriesDetail({ series, episodes, onBack, onStartProcessing, onCancelProcessing, activeJob, onRefresh, onPosterUpdated, onToast, showOutliers = true }) {
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('Todos los estados');
  const [templatePanel, setTemplatePanel] = useState(false);
  const [templateCandidates, setTemplateCandidates] = useState([]);
  const [templateLoading, setTemplateLoading] = useState(false);
  const [templateError, setTemplateError] = useState('');
  const [downloadingSlug, setDownloadingSlug] = useState('');
  const [posterModalOpen, setPosterModalOpen] = useState(false);
  const [posterLightboxOpen, setPosterLightboxOpen] = useState(false);
  const [posterUrl, setPosterUrl] = useState(series.poster_url || '');
  const [seriesMetadata, setSeriesMetadata] = useState(null);
  const [metadataLoading, setMetadataLoading] = useState(false);
  const [metadataError, setMetadataError] = useState('');
  const [bannerFailed, setBannerFailed] = useState(false);
  useEffect(() => setPosterUrl(series.poster_url || ''), [series.poster_url]);

  const refreshSeriesMetadata = useCallback(async () => {
    const seriesName = series.name || series.title;
    if (!seriesName) return;
    setMetadataLoading(true);
    setMetadataError('');
    try {
      const result = await api.seriesArtworkMetadata(seriesName);
      setSeriesMetadata(result?.metadata || null);
    } catch (error) {
      setSeriesMetadata(null);
      setMetadataError(error.message || 'No se pudieron cargar los metadatos de AniList.');
    } finally {
      setMetadataLoading(false);
    }
  }, [series.name, series.title]);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const result = await api.seriesArtworkMetadata(series.name || series.title);
        if (mounted) {
          setSeriesMetadata(result?.metadata || null);
          setMetadataError('');
        }
      } catch (error) {
        if (mounted) setMetadataError(error.message || 'No se pudieron cargar los metadatos de AniList.');
      } finally {
        if (mounted) setMetadataLoading(false);
      }
    };
    setMetadataLoading(true);
    setSeriesMetadata(null);
    setBannerFailed(false);
    load();
    return () => { mounted = false; };
  }, [series.name, series.title]);

  useEffect(() => setBannerFailed(false), [seriesMetadata?.banner_url]);
  const filtered = useMemo(() => episodes.filter((ep) => {
    const query = search.trim().toLowerCase();
    const matchesText = !query
      || String(ep.displayId || ep.file_name).toLowerCase().includes(query)
      || ep.file_name.toLowerCase().includes(query)
      || String(ep.folder_path || '').toLowerCase().includes(query);
    const matchesStatus = statusFilter === 'Todos los estados' || ep.status === statusFilter;
    return matchesText && matchesStatus;
  }), [episodes, search, statusFilter]);
  const folderTree = useMemo(() => buildFolderTree(filtered), [filtered]);
  const expandAllFolders = search.trim().length > 0 || statusFilter !== 'Todos los estados';
  const orderedFolders = useMemo(() => Array.from(folderTree.folders.values()).sort((a, b) =>
    a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' })
  ), [folderTree]);
  const active = activeJob && activeJob.kind === 'series' && activeJob.series_name === series.name && ['queued', 'running', 'cancellation_requested'].includes(activeJob.status);

  const openTemplateSearch = async () => {
    setTemplatePanel(true);
    setTemplateLoading(true);
    setTemplateError('');
    try {
      const result = await api.searchTemplates(series.name);
      setTemplateCandidates(result.candidates || []);
      if (!(result.candidates || []).length) setTemplateError('AnimeThemes no devolvió resultados para esta búsqueda.');
    } catch (error) {
      setTemplateError(error.message || 'No se pudo consultar AnimeThemes.');
    } finally {
      setTemplateLoading(false);
    }
  };

  const useTemplateCandidate = async (candidate) => {
    setDownloadingSlug(candidate.slug);
    setTemplateError('');
    try {
      const result = await api.downloadTemplates(series.name, candidate.slug);
      setTemplatePanel(false);
      await onRefresh?.();
      onToast(`Templates descargados: ${result.downloaded} archivo(s) para ${candidate.title_english || candidate.title_romaji || candidate.slug}.`, 'success');
    } catch (error) {
      setTemplateError(error.message || 'No se pudieron descargar los templates.');
    } finally {
      setDownloadingSlug('');
    }
  };

  const openPosterSearch = () => setPosterModalOpen(true);

  const handlePosterApplied = async (result) => {
    setPosterUrl(result.poster_url || '');
    await refreshSeriesMetadata();
    await onRefresh?.();
    await onPosterUpdated?.();
  };

  const removeMetadataLink = async () => {
    try {
      await api.unlinkSeriesArtworkMetadata(series.name || series.title);
      setSeriesMetadata(null);
      setBannerFailed(false);
      onToast('Se desvinculó la ficha de AniList. La carátula local se conservó.', 'success');
    } catch (error) {
      onToast(error.message || 'No se pudo desvincular la ficha de AniList.', 'error');
    }
  };

  const removePoster = async () => {
    try {
      await api.removeSeriesArtwork(series.name || series.title);
      setPosterUrl('');
      await onRefresh?.();
      await onPosterUpdated?.();
      onToast('Se quitó la carátula de esta serie.', 'success');
    } catch (error) {
      onToast(error.message || 'No se pudo quitar la carátula.', 'error');
    }
  };

  const correct = async (episode, payload) => {
    try {
      await api.approveEpisode(episode.id, { ...payload, approved: false });
      await onRefresh();
      onToast(`${episode.file_name}: timestamps guardados. Revisa y aprueba antes de inyectar.`, 'success');
    } catch (error) { onToast(error.message, 'error'); }
  };

  const approve = async (episode) => {
    try {
      await api.approveEpisode(episode.id, { approved: true });
      await onRefresh();
      onToast(`${episode.file_name}: detecciones aprobadas.`, 'success');
    } catch (error) { onToast(error.message, 'error'); }
  };

  const inject = async (episode) => {
    try {
      const preview = await api.chapterPreview(episode.id);
      const chapterSummary = preview.chapters.map((chapter) => `${formatTimestamp(chapter.start)}–${formatTimestamp(chapter.end)}  ${chapter.title}`).join('\n');
      const accepted = window.confirm(`Se aplicarán estos capítulos a ${episode.file_name}.\n\n${chapterSummary}\n\nSe creará una copia de seguridad antes de modificar el MKV. ¿Continuar?`);
      if (!accepted) return;
      try {
        await api.injectChapters(episode.id, false);
      } catch (error) {
        if (!error.message.toLowerCase().includes('already contains chapters')) throw error;
        const replace = window.confirm('El MKV ya contiene capítulos. ¿Confirmas reemplazarlos? La copia de seguridad anterior se conservará.');
        if (!replace) return;
        await api.injectChapters(episode.id, true);
      }
      await onRefresh();
      onToast('Capítulos inyectados y verificados. Se creó una copia de seguridad.', 'success');
    } catch (error) { onToast(error.message, 'error'); }
  };

  return (
    <div className="page-content detail-page">
      <div className="detail-fixed-content">
        <div className="breadcrumb"><button onClick={onBack}><ArrowLeft size={15} /> Dashboard</button><ChevronRight size={14} /><span>{series.title}</span></div>
        <PageHeader title="Detalle de serie" description="Revisa los segmentos detectados y el estado de cada episodio. El análisis no modifica archivos MKV."
          eyebrow="BIBLIOTECA / SERIE" actions={active ? <button className="button button-danger-ghost" onClick={onCancelProcessing}><XCircle size={15} /> Cancelar análisis</button> : <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}><button className="button button-secondary" onClick={openPosterSearch}><ImagePlus size={15} /> {posterUrl ? 'Cambiar carátula' : 'Buscar carátula'}</button><button className="button button-secondary" onClick={openTemplateSearch}><Search size={15} /> Vincular AnimeThemes</button><button className="button button-primary" onClick={onStartProcessing}><Play size={15} fill="currentColor" /> Analizar serie</button></div>} />

        {posterModalOpen && <PosterSearchModal
          seriesName={series.name || series.title}
          onClose={() => setPosterModalOpen(false)}
          onApplied={handlePosterApplied}
          onToast={onToast}
        />}

        {templatePanel && <section className="panel" style={{ padding: 18, marginBottom: 16 }}>
          <div className="panel-toolbar" style={{ marginBottom: 12 }}>
            <div className="panel-title-wrap"><div className="panel-title-icon"><Search size={16} /></div><div><h2>Seleccionar anime en AnimeThemes</h2><p>Elige la entrada correcta para descargar sus templates OP/ED en la caché de esta serie.</p></div></div>
            <button className="icon-button" onClick={() => setTemplatePanel(false)} aria-label="Cerrar búsqueda"><XCircle size={16} /></button>
          </div>
          {templateLoading && <p className="muted-score">Consultando AnimeThemes…</p>}
          {templateError && <p className="muted-score" role="status">{templateError}</p>}
          <div style={{ display: 'grid', gap: 8 }}>
            {templateCandidates.map((candidate) => <div key={candidate.slug} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', padding: 12, borderRadius: 10, background: 'var(--surface-2, #151a27)' }}>
              <div style={{ minWidth: 0 }}><strong>{candidate.title_english || candidate.title_romaji || candidate.slug}</strong><div className="muted-score">{candidate.title_romaji || ''} · OP {candidate.opening_count} · ED {candidate.ending_count} · {candidate.slug}</div></div>
              <button className="button button-secondary button-small" disabled={Boolean(downloadingSlug)} onClick={() => useTemplateCandidate(candidate)}>{downloadingSlug === candidate.slug ? 'Descargando…' : 'Usar y descargar'}</button>
            </div>)}
          </div>
        </section>}

        <section className={`panel series-hero-panel ${seriesMetadata?.banner_url && !bannerFailed ? 'series-hero-has-banner' : ''}`}>
          {seriesMetadata?.banner_url && !bannerFailed && (
            <img
              className="series-hero-banner"
              src={seriesMetadata.banner_url}
              alt=""
              aria-hidden="true"
              loading="lazy"
              referrerPolicy="no-referrer"
              onError={() => setBannerFailed(true)}
            />
          )}
          {seriesMetadata?.banner_url && !bannerFailed && <div className="series-hero-banner-wash" aria-hidden="true" />}
          <div className="series-hero-main">
            <button
              type="button"
              className={`series-poster-trigger ${posterUrl ? 'is-zoomable' : ''}`}
              onClick={() => { if (posterUrl || seriesMetadata) setPosterLightboxOpen(true); }}
              disabled={!posterUrl && !seriesMetadata}
              aria-label="Ampliar carátula y consultar ficha del anime"
              title={posterUrl || seriesMetadata ? 'Ampliar carátula y consultar ficha' : 'Asigna una carátula para ampliarla'}
            >
              <Poster variant="default" imageUrl={posterUrl} alt={`Carátula de ${seriesMetadata?.title || series.title}`} className="detail-poster" />
              {(posterUrl || seriesMetadata) && <span className="series-poster-zoom"><ZoomIn size={15} /></span>}
            </button>
            <div className="series-hero-copy">
              <div className="series-hero-title-row">
                <div className="series-hero-title-copy">
                  <h2>{seriesMetadata?.title || series.title}</h2>
                  {seriesMetadata && series.title && seriesMetadata.title !== series.title && (
                    <p className="series-hero-local-title">Carpeta local: {series.title}</p>
                  )}
                  {seriesMetadata?.title_native && seriesMetadata.title_native !== seriesMetadata.title && (
                    <p className="series-hero-native-title">{seriesMetadata.title_native}</p>
                  )}
                </div>
                <StatusBadge status={series.status} />
              </div>
              {seriesMetadata ? (
                <>
                  <div className="series-metadata-line">
                    {[seriesMetadata.year, seriesMetadata.format_label || seriesMetadata.format, seriesMetadata.country_label || seriesMetadata.country_of_origin].filter(Boolean).join(' · ')}
                    <span>·</span> {series.episodes} archivos locales
                    <span className="series-metadata-source">ANILIST</span>
                  </div>
                  {seriesMetadata.genres?.length > 0 && (
                    <div className="series-genre-chips">
                      {seriesMetadata.genres.slice(0, 6).map((genre) => <span key={genre}>{genre}</span>)}
                    </div>
                  )}
                  {seriesMetadata.description ? (
                    <div className="series-hero-synopsis-wrap">
                      <p className="series-hero-synopsis">{seriesMetadata.description}</p>
                      <button type="button" className="series-read-synopsis" onClick={() => setPosterLightboxOpen(true)}>Leer sinopsis completa</button>
                    </div>
                  ) : (
                    <p className="series-hero-metadata-muted">AniList no proporciona una sinopsis para esta entrada.</p>
                  )}
                  {seriesMetadata.site_url && (
                    <a className="series-anilist-credit" href={seriesMetadata.site_url} target="_blank" rel="noreferrer">
                      Metadatos de AniList <ExternalLink size={11} />
                    </a>
                  )}
                </>
              ) : (
                <>
                  <p className="series-hero-local-count">{series.episodes} archivos locales · Biblioteca local</p>
                  {metadataLoading && <p className="series-hero-metadata-muted">Cargando ficha de AniList…</p>}
                  {!metadataLoading && metadataError && <p className="series-hero-metadata-muted">{metadataError}</p>}
                  {!metadataLoading && !metadataError && <p className="series-hero-metadata-muted">Vincula una ficha de AniList para mostrar sinopsis, géneros y banner.</p>}
                </>
              )}
              <div className="series-hero-artwork-actions">
                {posterUrl && <button type="button" className="artwork-remove-poster" onClick={removePoster}><Trash2 size={12} /> Quitar carátula</button>}
                {seriesMetadata && <button type="button" className="artwork-remove-poster" onClick={removeMetadataLink}>Desvincular ficha</button>}
                {seriesMetadata && <span className="series-hero-metadata-source">Ficha vinculada · AniList</span>}
              </div>
            </div>
          </div>
          <div className="series-hero-stats" aria-label="Estadísticas de la biblioteca local">
            <div><strong className="stat-success">{series.analyzed}</strong><span>Analizados</span></div>
            <div><strong className="stat-warning">{series.outliers}</strong><span>Outliers</span></div>
            <div><strong>{series.episodes}</strong><span>Archivos locales</span></div>
          </div>
        </section>

        {posterLightboxOpen && (
          <SeriesArtworkLightbox
            seriesTitle={series.title || series.name}
            posterUrl={posterUrl}
            metadata={seriesMetadata}
            onClose={() => setPosterLightboxOpen(false)}
          />
        )}

        {active && <section className="panel" style={{ padding: 16, marginBottom: 16 }}>
          <div className="progress-heading"><span>{activeJob.stage || 'Procesamiento'} · {activeJob.current_item || 'Preparando…'}</span><strong>{Math.round(activeJob.progress || 0)}%</strong></div>
          <div className="progress-track"><span style={{ width: `${Math.min(100, activeJob.progress || 0)}%` }} /></div>
        </section>}      </div>

      <section className="panel episodes-panel">
        <div className="panel-toolbar episode-toolbar">
          <div className="panel-title-wrap"><div className="panel-title-icon"><FolderOpen size={17} /></div><div><h2>Contenido de la serie</h2><p>Organizado según la estructura de carpetas de tu biblioteca</p></div></div>
          <div className="toolbar-controls"><label className="search-control"><Search size={17} /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Buscar episodio..." /></label>
            <label className="select-control"><select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}><option>Todos los estados</option><option>Analizado</option><option>Revisión</option><option>Outlier</option><option>Sin coincidencias</option><option>Desactualizado</option><option>Pendiente</option><option>Error</option><option>Listo</option></select><ChevronDown size={15} /></label>
          </div>
        </div>
        <div className="episode-column-head"><span>EPISODIO</span><span>OPENING</span><span>ENDING</span><span>ESTADO</span><span /></div>
        <div className="folder-tree-viewport" aria-label="Carpetas y episodios de la serie" tabIndex={0}>
          <div className="folder-tree">
            {folderTree.episodes.length > 0 && (
              <section className="folder-root-group">
                <div className="folder-root-heading"><FolderOpen size={16} /><strong>Raíz de la serie</strong><span>{folderTree.episodes.length} {folderTree.episodes.length === 1 ? 'archivo' : 'archivos'}</span></div>
                <div className="episode-list folder-episode-list">
                  {folderTree.episodes.map((episode) => <EpisodeRow key={episode.file_path} episode={episode} onApprove={approve} onCorrect={correct} onInject={inject} onToast={onToast} showOutliers={showOutliers} />)}
                </div>
              </section>
            )}
            {orderedFolders.map((folder) => <FolderNode key={folder.path} folder={folder} depth={0}
              onApprove={approve} onCorrect={correct} onInject={inject} onToast={onToast}
              showOutliers={showOutliers} expandAll={expandAllFolders} />)}
            {filtered.length === 0 && <div className="empty-state"><Search size={23} /><strong>No hay episodios para mostrar</strong><span>Modifica la búsqueda o el filtro.</span></div>}
          </div>
        </div>
        <div className="table-footer"><span>{filtered.length} de {episodes.length} episodios</span><span>Los capítulos solo se escriben tras revisión y confirmación.</span></div>
      </section>
    </div>
  );
}
