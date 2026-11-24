import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, CheckCircle2, ChevronDown, ChevronRight, Play, Search, ShieldCheck, XCircle, Eye, FileCheck2, Folder, FolderOpen, ImagePlus, Trash2, ZoomIn, ExternalLink } from 'lucide-react';
import PageHeader from '../components/PageHeader.jsx';
import Poster from '../components/Poster.jsx';
import PosterSearchModal from '../components/PosterSearchModal.jsx';
import SeriesArtworkLightbox from '../components/SeriesArtworkLightbox.jsx';
import StatusBadge from '../components/StatusBadge.jsx';
import { api, formatTimestamp, formatEditableTimestamp, parseEditableTimestamp } from '../api/client.js';

const INJECTION_ELIGIBLE_STATES = new Set([
  'AUTO_APPROVED', 'REVIEW_RECOMMENDED', 'MANUALLY_REVIEWED', 'APPROVED',
]);

function isInjectionEligible(episode) {
  return Boolean(
    episode?.id
    && episode.analysis_status === 'COMPLETED'
    && (episode.op || episode.ed)
    && INJECTION_ELIGIBLE_STATES.has(episode.review_status)
    && !['SUCCEEDED', 'IN_PROGRESS', 'RECOVERY_REQUIRED'].includes(episode.injection_status)
  );
}

const EPISODE_STATUS_LABELS = {
  analysis: {
    COMPLETED: 'Completado',
    NO_MATCH: 'Sin coincidencias',
    FAILED: 'Error',
    STALE: 'Desactualizado',
    PENDING: 'Pendiente',
    SKIPPED: 'Omitido',
    CANCELLED: 'Cancelado',
  },
  review: {
    NOT_REQUIRED: 'No requiere revisión',
    AUTO_APPROVED: 'Aprobado automáticamente',
    REVIEW_RECOMMENDED: 'Revisión recomendada',
    PENDING: 'Pendiente de revisión',
    MANUALLY_REVIEWED: 'Revisado manualmente',
    APPROVED: 'Aprobado manualmente',
  },
  injection: {
    NOT_REQUESTED: 'No solicitada',
    IN_PROGRESS: 'En curso',
    RECOVERY_REQUIRED: 'Recuperación necesaria',
    SUCCEEDED: 'Completada y verificada',
    FAILED: 'Fallida',
  },
};

function episodeStatusLabel(group, value) {
  if (!value) return '—';
  return EPISODE_STATUS_LABELS[group]?.[value]
    || String(value).replaceAll('_', ' ').toLowerCase();
}

function confidenceLabel(band) {
  return ({
    HIGH: 'Alta',
    MEDIUM: 'Media',
    LOW: 'Baja',
    UNASSESSED: 'Sin evaluar',
  })[band] || 'Sin evaluar';
}

function ConfidenceSummaryGroup({ label, counts }) {
  return (
    <div className="confidence-summary-group">
      <span className="confidence-summary-label">{label}</span>
      <span className="confidence-count confidence-count-high"><strong>{counts.HIGH}</strong> altas</span>
      <span className="confidence-count confidence-count-medium"><strong>{counts.MEDIUM}</strong> medias</span>
      <span className="confidence-count confidence-count-low"><strong>{counts.LOW + counts.UNASSESSED}</strong> bajas / sin evaluar</span>
    </div>
  );
}

function ConfidenceMetric({ label, score, band, detail }) {
  return (
    <div className={`confidence-metric confidence-${String(band || 'UNASSESSED').toLowerCase()}`}>
      <div className="confidence-metric-heading">
        <span>{label}</span>
        <strong>{score === null ? 'Sin evaluar' : `${score}/100 · ${confidenceLabel(band)}`}</strong>
      </div>
      <div className="confidence-metric-track">
        <span style={{ width: score === null ? '0%' : `${score}%` }} />
      </div>
      {detail && <small>{detail}</small>}
    </div>
  );
}

function ConfidenceDetail({ label, segment, original, anomaly }) {
  if (!segment && !original) return null;
  if (!segment && original) {
    return (
      <div className="confidence-detail confidence-unassessed">
        <div className="confidence-detail-heading">
          <strong>{label}</strong>
          <span>Detección descartada manualmente</span>
        </div>
        <p className="confidence-original-value">
          Resultado original: {formatTimestamp(original.start)}–{formatTimestamp(original.end)}
        </p>
      </div>
    );
  }
  const matchScore = typeof segment.match_confidence_score === 'number'
    ? segment.match_confidence_score : null;
  const temporalScore = typeof segment.temporal_precision_score === 'number'
    ? segment.temporal_precision_score : null;
  const matchBand = segment.match_confidence_band || 'UNASSESSED';
  const temporalBand = segment.temporal_precision_band || 'UNASSESSED';
  const factors = segment.confidence_factors || {};
  const reasons = Array.isArray(factors.reasons) ? factors.reasons : [];
  const changed = original && (
    Number(original.start) !== Number(segment.start)
    || Number(original.end) !== Number(segment.end)
  );
  return (
    <div className="confidence-detail">
      <div className="confidence-detail-heading">
        <strong>{label}</strong>
        {segment.manual_verified && <span className="confidence-manual-tag">Verificado manualmente</span>}
        {anomaly && <span className="confidence-anomaly-tag">Anomalía temporal · revisar contexto</span>}
      </div>
      <div className="confidence-detail-metrics">
        <ConfidenceMetric
          label="Confianza de coincidencia"
          score={matchScore}
          band={matchBand}
          detail="Evalúa si el template detectado corresponde al OP/ED."
        />
        <ConfidenceMetric
          label="Precisión de inicio"
          score={temporalScore}
          band={temporalBand}
          detail="Evalúa qué tan definido está el inicio. El final se calcula usando la duración del template."
        />
      </div>
      <div className="confidence-factor-list">
        {factors.similarity !== null && factors.similarity !== undefined && <span>Similitud {Number(factors.similarity).toFixed(3)}</span>}
        {factors.template_margin !== null && factors.template_margin !== undefined && <span>Separación de templates {Number(factors.template_margin).toFixed(3)}</span>}
        {factors.peak_clarity !== null && factors.peak_clarity !== undefined && <span>Distinción de otras posiciones {Number(factors.peak_clarity).toFixed(3)}</span>}
        {factors.peak_width_seconds !== null && factors.peak_width_seconds !== undefined && <span>Ancho del pico {Number(factors.peak_width_seconds).toFixed(3)} s</span>}
        {factors.temporal_consistency !== null && factors.temporal_consistency !== undefined && <span>Consistencia entre episodios {Number(factors.temporal_consistency).toFixed(3)}</span>}
      </div>
      {reasons.length > 0 && <ul>{reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul>}
      {changed && <p className="confidence-original-value">Detección original: {formatTimestamp(original.start)}–{formatTimestamp(original.end)}</p>}
    </div>
  );
}

function EpisodeRow({
  episode, onOpen, selected = false, onToggleSelection, processingFile = null, showOutliers = true,
}) {
  const canInject = isInjectionEligible(episode);
  const reviewLocked = ['IN_PROGRESS', 'RECOVERY_REQUIRED'].includes(episode.injection_status);
  const injected = episode.injection_status === 'SUCCEEDED';
  const isProcessing = processingFile && episode.file_name === processingFile;
  const badgeStatus = !showOutliers && episode.status === 'Outlier'
    ? (episode.review_status === 'PENDING' ? 'Revisión necesaria'
      : canInject && episode.review_status === 'REVIEW_RECOMMENDED' ? 'Listo · revisar'
        : injected ? 'Listo' : 'Preparado')
    : episode.review_status === 'REVIEW_RECOMMENDED' && canInject
      ? 'Listo · revisar'
      : injected ? 'Listo' : episode.status;
  const bandClass = (band) => `confidence-inline confidence-${String(band || 'unassessed').toLowerCase()}`;

  const renderCompactConfidence = (segment) => {
    if (!segment) {
      return <small className="confidence-inline confidence-unassessed">Sin coincidencia</small>;
    }
    return (
      <div className="episode-confidence-inline" aria-label="Indicadores de confianza">
        <span className="confidence-inline-label">Coincidencia</span>
        <strong className={bandClass(segment.match_confidence_band)}>{segment.match_confidence_score ?? '—'}</strong>
        <span className="confidence-inline-divider">·</span>
        <span className="confidence-inline-label">Inicio</span>
        <strong className={bandClass(segment.temporal_precision_band)}>{segment.temporal_precision_score ?? '—'}</strong>
      </div>
    );
  };

  return (
    <article className={`episode-browser-row ${isProcessing ? 'episode-row-processing' : ''}`}>
      <div className="episode-browser-main">
        <div className="episode-browser-id">
          <input
            type="checkbox"
            className="episode-select-checkbox"
            checked={selected}
            disabled={!canInject || reviewLocked}
            onChange={() => onToggleSelection?.(episode)}
            aria-label={`Seleccionar ${episode.file_name} para inyección masiva`}
            title={canInject ? 'Seleccionar para inyección masiva' : 'Este episodio no está disponible para inyección'}
          />
          <button type="button" className="episode-browser-open" onClick={() => onOpen?.(episode)}>
            <strong>{episode.displayId || episode.file_name}</strong>
            <span className="stage-label">{episode.file_name}</span>
          </button>
        </div>
        <div className="episode-segment">
          <span className="segment-label">Opening</span>
          <strong>{episode.opening}</strong>
          {renderCompactConfidence(episode.op)}
        </div>
        <div className="episode-segment">
          <span className="segment-label">Ending</span>
          <strong>{episode.ending}</strong>
          {renderCompactConfidence(episode.ed)}
        </div>
        <div className="episode-browser-status"><StatusBadge status={badgeStatus} /></div>
        <button type="button" className="icon-button episode-open-button" onClick={() => onOpen?.(episode)}
          aria-label={`Abrir ${episode.displayId || episode.file_name}`} title="Abrir episodio">
          <ChevronRight size={17} />
        </button>
      </div>
    </article>
  );
}

function EpisodeWorkspace({
  episode, siblings, onBack, backLabel = 'Volver a la carpeta', onNavigate, onApprove, onCorrect, onInject, onToast, showOutliers = true,
}) {
  const [opStart, setOpStart] = useState(formatEditableTimestamp(episode.op?.start));
  const [opEnd, setOpEnd] = useState(formatEditableTimestamp(episode.op?.end));
  const [edStart, setEdStart] = useState(formatEditableTimestamp(episode.ed?.start));
  const [edEnd, setEdEnd] = useState(formatEditableTimestamp(episode.ed?.end));
  const [savingCorrections, setSavingCorrections] = useState(false);

  useEffect(() => {
    setOpStart(formatEditableTimestamp(episode.op?.start));
    setOpEnd(formatEditableTimestamp(episode.op?.end));
    setEdStart(formatEditableTimestamp(episode.ed?.start));
    setEdEnd(formatEditableTimestamp(episode.ed?.end));
  }, [episode.id, episode.op?.start, episode.op?.end, episode.ed?.start, episode.ed?.end]);

  const index = siblings.findIndex((item) => item.id === episode.id);
  const previous = index > 0 ? siblings[index - 1] : null;
  const next = index >= 0 && index < siblings.length - 1 ? siblings[index + 1] : null;
  const needsReview = episode.review_status === 'PENDING';
  const reviewRecommended = episode.review_status === 'REVIEW_RECOMMENDED';
  const injected = episode.injection_status === 'SUCCEEDED';
  const injectionInProgress = episode.injection_status === 'IN_PROGRESS';
  const recoveryRequired = episode.injection_status === 'RECOVERY_REQUIRED';
  const reviewLocked = injectionInProgress || recoveryRequired;
  const canInject = isInjectionEligible(episode);
  const hasResult = ['COMPLETED', 'NO_MATCH'].includes(episode.analysis_status);
  const legacyResult = episode.analysis_status === 'STALE' || episode.legacy_result;
  const badgeStatus = reviewRecommended && canInject ? 'Listo · revisar'
    : injected ? 'Listo' : episode.status;
  const segmentDraftChanged = (start, end, segment) => (
    start !== formatEditableTimestamp(segment?.start)
    || end !== formatEditableTimestamp(segment?.end)
  );
  const hasPendingCorrections = segmentDraftChanged(opStart, opEnd, episode.op)
    || segmentDraftChanged(edStart, edEnd, episode.ed);


  const discardSegment = (kind) => {
    const label = kind === 'OP' ? 'Opening' : 'Ending';
    if (!episode[kind.toLowerCase()]) return;
    const accepted = window.confirm(
      `¿Descartar la detección de ${label} de ${episode.file_name}?\n\nEl timestamp original se conservará en el historial y no se incluirá en los capítulos.`
    );
    if (!accepted) return;
    onCorrect(episode, kind === 'OP'
      ? { op: null, ed: episode.ed }
      : { op: episode.op, ed: null });
  };

  const saveCorrections = async () => {
    const build = (start, end, existing, label) => {
      const rawStart = String(start).trim();
      const rawEnd = String(end).trim();
      if (!rawStart && !rawEnd) return null;
      if (!rawStart || !rawEnd) {
        throw new Error(`${label}: completa tanto el inicio como el final.`);
      }
      const numericStart = rawStart === formatEditableTimestamp(existing?.start)
        ? Number(existing.start)
        : parseEditableTimestamp(rawStart);
      const numericEnd = rawEnd === formatEditableTimestamp(existing?.end)
        ? Number(existing.end)
        : parseEditableTimestamp(rawEnd);
      if (!Number.isFinite(numericStart) || !Number.isFinite(numericEnd)) {
        throw new Error(`${label}: utiliza MM:SS.mmm o HH:MM:SS.mmm (por ejemplo, 21:55.642).`);
      }
      if (numericStart < 0 || numericStart >= numericEnd) {
        throw new Error(`${label}: el inicio debe ser menor que el final.`);
      }
      return { ...(existing || {}), name: existing?.name || label, start: numericStart, end: numericEnd };
    };

    try {
      const op = build(opStart, opEnd, episode.op, 'Opening');
      const ed = build(edStart, edEnd, episode.ed, 'Ending');
      if (!op && !ed) throw new Error('Introduce al menos un segmento antes de guardar.');
      setSavingCorrections(true);
      await onCorrect(episode, { op, ed });
    } catch (error) {
      onToast(error.message, 'error');
    } finally {
      setSavingCorrections(false);
    }
  };

  const renderEditor = (label, start, end, setStart, setEnd) => (
    <div className="episode-time-editor">
      <div className="episode-time-editor-heading">
        <strong>{label} · tiempos de capítulo</strong>
        <span>Formato MM:SS.mmm o HH:MM:SS.mmm</span>
      </div>
      <div className="episode-time-editor-fields">
        <label>
          <span>Inicio</span>
          <input aria-label={`${label} inicio`} type="text" inputMode="text" autoComplete="off"
            spellCheck="false" value={start} disabled={reviewLocked}
            onChange={(event) => setStart(event.target.value)} placeholder="21:55.642" />
        </label>
        <label>
          <span>Final</span>
          <input aria-label={`${label} final`} type="text" inputMode="text" autoComplete="off"
            spellCheck="false" value={end} disabled={reviewLocked}
            onChange={(event) => setEnd(event.target.value)} placeholder="23:24.412" />
        </label>
      </div>
      <small className="episode-time-editor-hint">Puedes usar punto o coma decimal. Se guardará internamente en segundos.</small>
    </div>
  );

  return (
    <section className="episode-workspace workspace-view-transition">
      <header className="episode-workspace-header">
        <div className="episode-workspace-title">
          <button type="button" className="workspace-back-link" onClick={onBack}>
            <ArrowLeft size={15} /> {backLabel}
          </button>
          <span className="panel-eyebrow">REVISIÓN DE EPISODIO</span>
          <h2>{episode.displayId || episode.file_name}</h2>
          <p>{episode.file_name}</p>
        </div>
        <div className="episode-workspace-navigation">
          <StatusBadge status={badgeStatus} />
          <div className="episode-previous-next">
            <button className="button button-secondary button-small" type="button" onClick={() => onNavigate(previous)}
              disabled={!previous}>Anterior</button>
            <button className="button button-secondary button-small" type="button" onClick={() => onNavigate(next)}
              disabled={!next}>Siguiente</button>
          </div>
        </div>
      </header>

      <div className="episode-workspace-main">
        <div className="episode-workspace-detections">
          <section className="episode-workspace-segment">
            <div className="episode-workspace-segment-heading">
              <div><span className="panel-eyebrow">DETECCIÓN 01</span><h3>Opening</h3></div>
              <strong>{episode.op ? `${formatTimestamp(episode.op.start)}–${formatTimestamp(episode.op.end)}` : 'Sin coincidencia'}</strong>
            </div>
            <ConfidenceDetail label="Opening" segment={episode.op} original={episode.original_op}
              anomaly={showOutliers && episode.is_anomaly_op} />
            {hasResult && renderEditor('Opening', opStart, opEnd, setOpStart, setOpEnd)}
          </section>

          <section className="episode-workspace-segment">
            <div className="episode-workspace-segment-heading">
              <div><span className="panel-eyebrow">DETECCIÓN 02</span><h3>Ending</h3></div>
              <strong>{episode.ed ? `${formatTimestamp(episode.ed.start)}–${formatTimestamp(episode.ed.end)}` : 'Sin coincidencia'}</strong>
            </div>
            <ConfidenceDetail label="Ending" segment={episode.ed} original={episode.original_ed}
              anomaly={showOutliers && episode.is_anomaly_ed} />
            {hasResult && renderEditor('Ending', edStart, edEnd, setEdStart, setEdEnd)}
          </section>
        </div>

        <aside className="episode-workspace-sidebar">
          <section className="episode-workspace-info">
            <span className="panel-eyebrow">ESTADO DEL PROCESAMIENTO</span>
            <dl>
              <div><dt>Análisis</dt><dd><StatusBadge status={episodeStatusLabel('analysis', episode.analysis_status)} /></dd></div>
              <div><dt>Revisión</dt><dd><StatusBadge status={episodeStatusLabel('review', episode.review_status)} /></dd></div>
              <div><dt>Inyección</dt><dd><StatusBadge status={episodeStatusLabel('injection', episode.injection_status)} /></dd></div>
            </dl>
            {episode.error && <p className="episode-workspace-error">{episode.error}</p>}
            {legacyResult && <p className="episode-workspace-note">Estos timestamps provienen del JSON antiguo. Se conservan como referencia, pero debes repetir el análisis para evaluarlos antes de inyectarlos.</p>}
            {episode.review_history?.length > 0 && (
              <div className="episode-workspace-history">
                <strong>Historial · {episode.review_history.length} cambios</strong>
                {episode.review_history.slice(0, 6).map((entry, itemIndex) => (
                  <small key={itemIndex}>
                    {entry.segment_type} · {entry.action === 'CORRECTED' ? 'corregido' : entry.action === 'REMOVED' ? 'retirado' : 'aprobado'}
                    {' · '}{new Date(entry.created_at).toLocaleString()}
                  </small>
                ))}
              </div>
            )}
          </section>

          <section className="episode-workspace-actions">
            <span className="panel-eyebrow">ACCIONES</span>
            {hasResult && (
              <button className="button button-secondary" onClick={saveCorrections}
                disabled={reviewLocked || savingCorrections || !hasPendingCorrections}>
                {savingCorrections ? 'Guardando…' : hasPendingCorrections ? 'Guardar correcciones' : 'Sin cambios'}
              </button>
            )}
            {episode.op && <button className="button button-danger-ghost" onClick={() => discardSegment('OP')} disabled={reviewLocked}>Descartar Opening</button>}
            {episode.ed && <button className="button button-danger-ghost" onClick={() => discardSegment('ED')} disabled={reviewLocked}>Descartar Ending</button>}
            {legacyResult && <span className="muted-score">Reanaliza el episodio para habilitar la revisión segura.</span>}
            {(needsReview || reviewRecommended) && <button className="button button-secondary button-review-confirm" onClick={() => onApprove(episode)} disabled={reviewLocked}><CheckCircle2 size={14} /> Confirmar manualmente</button>}
            {canInject && !injected && !reviewLocked && <button className="button button-primary" onClick={() => onInject(episode)}><ShieldCheck size={14} /> Inyectar este episodio</button>}
            {injectionInProgress && <span className="episode-workspace-note">Inyección en curso. No edites ni vuelvas a ejecutar esta operación.</span>}
            {injected && <span className="status-badge status-success"><FileCheck2 size={13} /> Capítulos verificados</span>}
            {recoveryRequired && <span className="episode-workspace-note">La aplicación se interrumpió durante una inyección. Inspecciona el MKV y su respaldo antes de continuar.</span>}
            {!hasResult && <span className="muted-score">No hay resultados que revisar.</span>}
          </section>
        </aside>
      </div>
    </section>
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

function findFolderByPath(root, path) {
  if (!path) return root;
  let current = root;
  for (const segment of path.split('/').filter(Boolean)) {
    current = current.folders.get(segment);
    if (!current) return null;
  }
  return current;
}

function collectFolderEpisodes(folder) {
  if (!folder) return [];
  const result = [...folder.episodes];
  for (const child of folder.folders.values()) result.push(...collectFolderEpisodes(child));
  return result;
}

function folderEpisodeSummary(folder, matchingIds) {
  const all = collectFolderEpisodes(folder);
  const visible = matchingIds ? all.filter((episode) => matchingIds.has(episode.id)) : all;
  return {
    count: visible.length,
    review: visible.filter((episode) => ['PENDING', 'REVIEW_RECOMMENDED'].includes(episode.review_status)).length,
    required: visible.filter((episode) => episode.review_status === 'PENDING').length,
    ready: visible.filter(isInjectionEligible).length,
  };
}

function FolderBrowserRow({ folder, matchingIds, onOpen }) {
  const summary = folderEpisodeSummary(folder, matchingIds);
  if (!summary.count) return null;
  return (
    <button type="button" className="folder-browser-row" onClick={() => onOpen(folder)}>
      <span className="folder-browser-icon"><Folder size={19} /></span>
      <span className="folder-browser-copy">
        <strong>{folder.name}</strong>
        <small>{summary.count} {summary.count === 1 ? 'episodio' : 'episodios'}
          {folder.folders.size ? ` · ${folder.folders.size} subcarpetas` : ''}
        </small>
      </span>
      <span className="folder-browser-status">
        <strong>{summary.ready} listos</strong>
        {summary.review > 0 && <small>{summary.required > 0 ? `${summary.required} requieren revisión` : `${summary.review} con avisos`}</small>}
      </span>
      <ChevronRight size={17} />
    </button>
  );
}



export default function SeriesDetail({ series, episodes, onBack, onStartProcessing, onCancelProcessing, activeJob, onRefresh, onPosterUpdated, onToast, showOutliers = true }) {
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('Todos los estados');
  const [posterModalOpen, setPosterModalOpen] = useState(false);
  const [posterLightboxOpen, setPosterLightboxOpen] = useState(false);
  const [posterUrl, setPosterUrl] = useState(series.poster_url || '');
  const [seriesMetadata, setSeriesMetadata] = useState(null);
  const [metadataLoading, setMetadataLoading] = useState(false);
  const [metadataError, setMetadataError] = useState('');
  const [bannerFailed, setBannerFailed] = useState(false);
  const [selectedEpisodeIds, setSelectedEpisodeIds] = useState(() => new Set());
  const [batchSubmitting, setBatchSubmitting] = useState(false);
  const [batchPreviewOpen, setBatchPreviewOpen] = useState(false);
  const [replaceExistingChapters, setReplaceExistingChapters] = useState(false);
  const [activeFolderPath, setActiveFolderPath] = useState(null);
  const [selectedEpisodeId, setSelectedEpisodeId] = useState(null);
  const workspaceScrollRef = useRef(null);
  const folderScrollPositionsRef = useRef(new Map());
  const restoreScrollTopRef = useRef(0);
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
    const matchesStatus = statusFilter === 'Todos los estados'
      || (['Necesita revisión', 'Revisión necesaria'].includes(statusFilter)
        ? (statusFilter === 'Necesita revisión'
          ? ['PENDING', 'REVIEW_RECOMMENDED'].includes(ep.review_status)
          : ep.review_status === 'PENDING')
        : statusFilter === 'Revisión recomendada'
          ? ep.review_status === 'REVIEW_RECOMMENDED'
          : ep.status === statusFilter);
    return matchesText && matchesStatus;
  }), [episodes, search, statusFilter]);
  const folderTree = useMemo(() => buildFolderTree(episodes), [episodes]);
  const matchingEpisodeIds = useMemo(() => new Set(filtered.map((episode) => episode.id)), [filtered]);
  const currentFolder = activeFolderPath === null ? folderTree : findFolderByPath(folderTree, activeFolderPath);
  const selectedEpisode = selectedEpisodeId === null
    ? null
    : episodes.find((episode) => episode.id === selectedEpisodeId) || null;
  const scopeEpisodes = useMemo(() => {
    if (activeFolderPath === null) return filtered;
    return collectFolderEpisodes(currentFolder).filter((episode) => matchingEpisodeIds.has(episode.id));
  }, [activeFolderPath, currentFolder, filtered, matchingEpisodeIds]);
  const directEpisodes = useMemo(() => (
    (currentFolder?.episodes || []).filter((episode) => matchingEpisodeIds.has(episode.id))
  ), [currentFolder, matchingEpisodeIds]);
  const childFolders = useMemo(() => Array.from(currentFolder?.folders?.values() || [])
    .filter((folder) => folderEpisodeSummary(folder, matchingEpisodeIds).count > 0)
    .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' })),
  [currentFolder, matchingEpisodeIds]);
  const folderBreadcrumbs = activeFolderPath ? activeFolderPath.split('/').filter(Boolean).map((name, index, names) => ({
    name,
    path: names.slice(0, index + 1).join('/'),
  })) : [];
  const parentFolderPath = activeFolderPath
    ? activeFolderPath.split('/').slice(0, -1).join('/') || null
    : null;
  const eligibleEpisodes = episodes.filter(isInjectionEligible);
  const scopeEligibleEpisodes = scopeEpisodes.filter(isInjectionEligible);
  const selectedEligibleEpisodes = eligibleEpisodes.filter((episode) => selectedEpisodeIds.has(episode.id));
  useEffect(() => {
    if (activeFolderPath !== null && !findFolderByPath(folderTree, activeFolderPath)) {
      setActiveFolderPath(null);
      setSelectedEpisodeId(null);
    }
  }, [activeFolderPath, folderTree]);
  useLayoutEffect(() => {
    if (!selectedEpisodeId && workspaceScrollRef.current) {
      workspaceScrollRef.current.scrollTop = restoreScrollTopRef.current || 0;
      restoreScrollTopRef.current = 0;
    }
  }, [activeFolderPath, selectedEpisodeId]);
  const rememberWorkspaceScroll = () => {
    if (workspaceScrollRef.current) {
      folderScrollPositionsRef.current.set(activeFolderPath || '__root__', workspaceScrollRef.current.scrollTop);
    }
  };
  const navigateToFolderPath = (path) => {
    rememberWorkspaceScroll();
    restoreScrollTopRef.current = folderScrollPositionsRef.current.get(path || '__root__') || 0;
    setSelectedEpisodeId(null);
    setActiveFolderPath(path);
  };
  const openFolder = (folder) => navigateToFolderPath(folder.path);
  const navigateBackFromEpisode = () => {
    setSelectedEpisodeId(null);
    restoreScrollTopRef.current = folderScrollPositionsRef.current.get(activeFolderPath || '__root__') || 0;
  };
  const openEpisode = (episode) => {
    rememberWorkspaceScroll();
    setSelectedEpisodeId(episode.id);
  };
  const visibleSiblingEpisodes = directEpisodes.length
    ? directEpisodes
    : selectedEpisode ? [selectedEpisode] : [];
  const processingFile = ['analyzing_episodes', 'injecting_chapters'].includes(activeJob?.stage)
    ? activeJob.current_item
    : null;
  const confidenceSummary = useMemo(() => {
    const createCounts = () => ({ HIGH: 0, MEDIUM: 0, LOW: 0, UNASSESSED: 0 });
    const summary = { detections: 0, match: createCounts(), temporal: createCounts() };
    for (const episode of scopeEpisodes) {
      for (const segment of [episode.op, episode.ed]) {
        if (!segment) continue;
        summary.detections += 1;
        const matchBand = summary.match[segment.match_confidence_band] === undefined
          ? 'UNASSESSED' : segment.match_confidence_band;
        const temporalBand = summary.temporal[segment.temporal_precision_band] === undefined
          ? 'UNASSESSED' : segment.temporal_precision_band;
        summary.match[matchBand] += 1;
        summary.temporal[temporalBand] += 1;
      }
    }
    return summary;
  }, [scopeEpisodes]);
  const reviewQueueCount = scopeEpisodes.filter((episode) =>
    ['PENDING', 'REVIEW_RECOMMENDED'].includes(episode.review_status)
  ).length;


  const active = activeJob
    && ['series', 'injection'].includes(activeJob.kind)
    && activeJob.series_name === series.name
    && ['queued', 'running', 'cancellation_requested'].includes(activeJob.status);
  const isInjectionJob = activeJob?.kind === 'injection';
  const activeStageLabel = ({
    queued: 'En cola',
    initializing: 'Iniciando análisis',
    preparing_dependencies: 'Preparando herramientas multimedia',
    searching_templates: 'Consultando AnimeThemes',
    loading_template_metadata: 'Obteniendo información de OP/ED',
    downloading_templates: 'Descargando templates OP/ED',
    converting_templates: 'Convirtiendo vídeos a WAV',
    validating_templates: 'Validando plantillas de audio',
    analyzing_episodes: 'Analizando episodios',
    injecting_chapters: 'Inyectando capítulos en MKV',
    cancellation_requested: 'Solicitando cancelación',
    cancelling: 'Cancelando análisis',
    failed: isInjectionJob ? 'Inyección fallida' : 'Análisis fallido',
    cancelled: isInjectionJob ? 'Inyección cancelada' : 'Análisis cancelado',
  })[activeJob?.stage] || activeJob?.stage || 'Procesamiento';

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

  const toggleEpisodeSelection = (episode) => {
    if (!isInjectionEligible(episode)) return;
    setSelectedEpisodeIds((current) => {
      const next = new Set(current);
      if (next.has(episode.id)) next.delete(episode.id);
      else next.add(episode.id);
      return next;
    });
  };

  const selectAllEligible = () => {
    setSelectedEpisodeIds((current) => new Set([
      ...current,
      ...scopeEligibleEpisodes.map((episode) => episode.id),
    ]));
  };

  const startBatchInjection = () => {
    if (selectedEligibleEpisodes.length && !batchSubmitting && !active) {
      setReplaceExistingChapters(false);
      setBatchPreviewOpen(true);
    }
  };

  const confirmBatchInjection = async () => {
    const selected = [...selectedEligibleEpisodes];
    const ids = selected.map((episode) => episode.id);
    if (!ids.length || batchSubmitting) return;
    setBatchSubmitting(true);
    try {
      const job = await api.startBatchInjection(series.name, ids, replaceExistingChapters);
      setBatchPreviewOpen(false);
      setSelectedEpisodeIds(new Set());
      onToast(`Inyección masiva iniciada para ${ids.length} episodios. Trabajo ${job.id}.`, 'success');
      await onRefresh?.();
    } catch (error) {
      onToast(error.message || 'No se pudo iniciar la inyección masiva.', 'error');
    } finally {
      setBatchSubmitting(false);
    }
  };

  return (
    <div className={`page-content detail-page ${active ? 'is-processing' : ''}`}>
      <div className="detail-fixed-content">
        <div className="breadcrumb">
          <button onClick={onBack}><ArrowLeft size={15} /> Dashboard</button>
          <ChevronRight size={14} />
          <button type="button" onClick={() => navigateToFolderPath(null)}>{series.title}</button>
          {folderBreadcrumbs.map((crumb) => (
            <span className="breadcrumb-segment" key={crumb.path}>
              <ChevronRight size={14} />
              <button type="button" onClick={() => navigateToFolderPath(crumb.path)}>{crumb.name}</button>
            </span>
          ))}
          {selectedEpisode && <><ChevronRight size={14} /><span>{selectedEpisode.displayId || selectedEpisode.file_name}</span></>}
        </div>
        <PageHeader title={selectedEpisode ? (selectedEpisode.displayId || selectedEpisode.file_name)
          : activeFolderPath ? currentFolder?.name || 'Carpeta' : 'Detalle de serie'}
          description={selectedEpisode
            ? 'Revisa las detecciones, consulta la confianza y corrige los timestamps cuando sea necesario.'
            : activeFolderPath
              ? 'Contenido de esta carpeta. Selecciona un episodio para abrir su espacio de revisión.'
              : 'Explora la biblioteca por carpetas y abre cada episodio para consultar o corregir sus detecciones.'}
          eyebrow="BIBLIOTECA / SERIE" actions={active ? <button className="button button-danger-ghost" onClick={() => onCancelProcessing(activeJob?.id)}><XCircle size={15} /> {isInjectionJob ? 'Cancelar inyección' : 'Cancelar análisis'}</button> : <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}><button className="button button-secondary" onClick={openPosterSearch}><ImagePlus size={15} /> {posterUrl ? 'Cambiar carátula' : 'Buscar carátula'}</button><button className="button button-primary" onClick={onStartProcessing}><Play size={15} fill="currentColor" /> Analizar serie</button></div>} />

        {posterModalOpen && <PosterSearchModal
          seriesName={series.name || series.title}
          onClose={() => setPosterModalOpen(false)}
          onApplied={handlePosterApplied}
          onToast={onToast}
        />}

        {batchPreviewOpen && (
          <div className="batch-injection-backdrop" role="presentation" onMouseDown={(event) => {
            if (event.target === event.currentTarget && !batchSubmitting) setBatchPreviewOpen(false);
          }}>
            <section className="panel batch-injection-modal" role="dialog" aria-modal="true" aria-labelledby="batch-injection-title">
              <header className="batch-injection-modal-header">
                <div>
                  <span className="panel-eyebrow">OPERACIÓN MASIVA</span>
                  <h2 id="batch-injection-title">Revisar inyección de capítulos</h2>
                  <p>{selectedEligibleEpisodes.length} episodios seleccionados · se procesarán secuencialmente</p>
                </div>
                <button className="icon-button" type="button" onClick={() => setBatchPreviewOpen(false)} disabled={batchSubmitting} aria-label="Cerrar vista previa"><XCircle size={18} /></button>
              </header>
              <div className="batch-injection-summary">
                <span><strong>{selectedEligibleEpisodes.filter((episode) => episode.op).length}</strong> detecciones Opening</span>
                <span><strong>{selectedEligibleEpisodes.filter((episode) => episode.ed).length}</strong> detecciones Ending</span>
                <span><strong>{selectedEligibleEpisodes.length}</strong> MKV seleccionados</span>
              </div>
              <div className="batch-injection-preview-list" aria-label="Timestamps seleccionados para la inyección">
                {selectedEligibleEpisodes.map((episode) => (
                  <article className="batch-injection-preview-row" key={episode.id}>
                    <strong>{episode.displayId || episode.file_name}</strong>
                    <span className="batch-preview-file-name">{episode.file_name}</span>
                    <div>
                      <span>OP: {episode.op
                        ? `${formatTimestamp(episode.op.start)}–${formatTimestamp(episode.op.end)}`
                        : 'Sin detección'}</span>
                      <span>ED: {episode.ed
                        ? `${formatTimestamp(episode.ed.start)}–${formatTimestamp(episode.ed.end)}`
                        : 'Sin detección'}</span>
                    </div>
                  </article>
                ))}
              </div>
              <div className="batch-injection-options">
                <label>
                  <input
                    type="checkbox"
                    checked={replaceExistingChapters}
                    onChange={(event) => setReplaceExistingChapters(event.target.checked)}
                    disabled={batchSubmitting}
                  />
                  <strong>Reemplazar capítulos existentes</strong> en los MKV que ya contengan capítulos
                </label>
                <p>Se creará una copia de seguridad independiente para cada archivo. Después de escribir, OpEdTagger verificará cantidad, títulos y tiempos con FFprobe. Si no marcas esta opción, los archivos que ya tengan capítulos se registrarán como fallidos y no se modificarán.</p>
              </div>
              <footer className="batch-injection-modal-footer">
                <button className="button button-ghost" type="button" onClick={() => setBatchPreviewOpen(false)} disabled={batchSubmitting}>Volver</button>
                <button className="button button-primary" type="button" onClick={confirmBatchInjection} disabled={batchSubmitting || !selectedEligibleEpisodes.length}>
                  {batchSubmitting ? 'Preparando trabajo…' : `Confirmar e inyectar ${selectedEligibleEpisodes.length} episodios`}
                </button>
              </footer>
            </section>
          </div>
        )}

        {!activeFolderPath && !selectedEpisodeId && (
        <section className={`panel series-hero-panel ${active ? 'series-hero-processing' : ''} ${seriesMetadata?.banner_url && !bannerFailed ? 'series-hero-has-banner' : ''}`}>
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
        )}

        {(activeFolderPath !== null || selectedEpisodeId !== null) && (
          <section className="series-context-bar">
            <div className="series-context-icon"><FolderOpen size={17} /></div>
            <div><strong>{series.title}</strong><span>{series.episodes} archivos locales · Biblioteca de la serie</span></div>
            <button type="button" className="button button-secondary button-small"
              onClick={() => navigateToFolderPath(null)}>Ver carpetas</button>
          </section>
        )}

        {posterLightboxOpen && (
          <SeriesArtworkLightbox
            seriesTitle={series.title || series.name}
            posterUrl={posterUrl}
            metadata={seriesMetadata}
            onClose={() => setPosterLightboxOpen(false)}
          />
        )}

        {active && (() => {
          const percentage = Math.round(activeJob.progress || 0);
          const indeterminate = ['searching_templates', 'loading_template_metadata'].includes(activeJob.stage)
            || (activeJob.stage === 'downloading_templates' && /tamaño desconocido/i.test(activeJob.current_item || ''));
          const items = activeJob.items || [];
          const completedItems = items.filter((item) => item.status === 'completed').length;
          const failedItems = items.filter((item) => item.status === 'failed').length;
          return (
            <section className={`panel series-processing-progress ${isInjectionJob ? 'series-processing-injection' : ''}`} aria-live="polite">
              <div className="progress-heading">
                <span>{activeStageLabel}</span>
                <strong>{indeterminate ? 'En curso' : `${percentage}%`}</strong>
              </div>
              <div
                className={`progress-track ${indeterminate ? 'progress-track-indeterminate' : ''}`}
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={indeterminate ? undefined : percentage}
                aria-valuetext={indeterminate ? activeStageLabel : `${percentage}%`}
              >
                <span style={indeterminate ? undefined : { width: `${Math.min(100, percentage)}%` }} />
              </div>
              <div className="processing-progress-meta">
                <p className="processing-current-item">{activeJob.current_item || 'Preparando…'}</p>
                {isInjectionJob && <span>{completedItems} completados · {failedItems} con error · {activeJob.total_items || items.length} seleccionados</span>}
              </div>
            </section>
          );
        })()}
        {!active && activeJob?.kind === 'series' && activeJob.series_name === series.name && activeJob.status === 'failed' && (
          <section className="panel series-processing-error" role="alert">
            <strong>No se pudo completar el análisis de la serie.</strong>
            <p>{activeJob.error || 'El trabajo terminó con un error sin descripción. Revisa los logs de OpEdTagger.'}</p>
          </section>
        )}
        {!active && activeJob?.kind === 'series' && activeJob.series_name === series.name && activeJob.status === 'cancelled' && (
          <section className="panel series-processing-error" role="status">
            <strong>El análisis fue cancelado.</strong>
            <p>{activeJob.error || 'La cancelación fue solicitada.'}</p>
          </section>
        )}
        {!active && activeJob?.kind === 'injection' && activeJob.series_name === series.name
          && ['completed', 'completed_with_errors', 'failed', 'cancelled'].includes(activeJob.status) && (
          <section className={`panel series-processing-result ${activeJob.status === 'completed' ? 'result-success' : 'result-warning'}`} role="status">
            <strong>
              {activeJob.status === 'completed' ? 'Inyección masiva completada'
                : activeJob.status === 'cancelled' ? 'Inyección masiva cancelada'
                  : activeJob.status === 'failed' ? 'No se pudo completar la inyección masiva'
                    : 'Inyección masiva completada con incidencias'}
            </strong>
            <p>
              {(activeJob.items || []).filter((item) => item.status === 'completed').length} episodios completados ·
              {' '}{(activeJob.items || []).filter((item) => item.status === 'failed').length} con error ·
              {' '}{(activeJob.items || []).filter((item) => item.status === 'cancelled').length} cancelados.
            </p>
            {(activeJob.items || []).filter((item) => item.status === 'failed').slice(0, 4).map((item) => (
              <small key={item.id || item.item_key}>{item.file_name}: {item.error || item.result?.error || 'Error de inyección'}</small>
            ))}
          </section>
        )}

      </div>

      <section className="panel episodes-panel folder-browser-panel">
        {selectedEpisode ? (
          <EpisodeWorkspace
            key={selectedEpisode.id}
            episode={selectedEpisode}
            siblings={visibleSiblingEpisodes}
            onBack={navigateBackFromEpisode}
            backLabel={activeFolderPath ? 'Volver a la carpeta' : 'Volver a la biblioteca'}
            onNavigate={(episode) => episode && setSelectedEpisodeId(episode.id)}
            onApprove={approve}
            onCorrect={correct}
            onInject={inject}
            onToast={onToast}
            showOutliers={showOutliers}
          />
        ) : (
          <>
            <div className="panel-toolbar episode-toolbar folder-browser-toolbar">
              <div className="panel-title-wrap">
                <div className="panel-title-icon"><FolderOpen size={17} /></div>
                <div>
                  <h2>{activeFolderPath ? currentFolder?.name : 'Carpetas de la serie'}</h2>
                  <p>{activeFolderPath
                    ? `${childFolders.length} subcarpetas · ${scopeEpisodes.length} episodios en esta ubicación`
                    : 'Selecciona una carpeta para explorar sus episodios'}
                  </p>
                </div>
              </div>
              <div className="toolbar-controls">
                <label className="search-control"><Search size={17} /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Buscar episodio..." /></label>
                <label className="select-control"><select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
                  <option>Todos los estados</option><option>Preparado</option><option>Revisión necesaria</option><option>Revisión recomendada</option><option>Analizado</option><option>Revisión</option><option>Outlier</option><option>Sin coincidencias</option><option>Desactualizado</option><option>Pendiente</option><option>Error</option><option>Listo</option>
                </select><ChevronDown size={15} /></label>
              </div>
            </div>

            <div className="confidence-summary-bar">
              <div className="confidence-summary-metrics">
                <span className="confidence-summary-detections">{confidenceSummary.detections} detecciones</span>
                <ConfidenceSummaryGroup label="Coincidencia" counts={confidenceSummary.match} />
                <ConfidenceSummaryGroup label="Inicio" counts={confidenceSummary.temporal} />
                <span className="confidence-review-count">{reviewQueueCount} episodios con avisos</span>
              </div>
              <div className="confidence-batch-actions">
                <button className="button button-ghost button-small" onClick={() => setStatusFilter('Necesita revisión')} disabled={!reviewQueueCount}>Ver excepciones</button>
                <button className="button button-secondary button-small" onClick={selectAllEligible} disabled={!scopeEligibleEpisodes.length || active}>
                  {activeFolderPath ? `Seleccionar en esta carpeta (${scopeEligibleEpisodes.length})` : `Seleccionar elegibles (${scopeEligibleEpisodes.length})`}
                </button>
                <button className="button button-primary button-small" onClick={startBatchInjection} disabled={!selectedEligibleEpisodes.length || batchSubmitting || active}>
                  {batchSubmitting ? 'Preparando…' : `Inyectar seleccionados (${selectedEligibleEpisodes.length})`}
                </button>
                {selectedEpisodeIds.size > 0 && <button className="button button-ghost button-small" onClick={() => setSelectedEpisodeIds(new Set())}>Limpiar selección</button>}
              </div>
            </div>

            {directEpisodes.length > 0 && (
              <div className="episode-column-head"><span>EPISODIO</span><span>OPENING</span><span>ENDING</span><span>ESTADO</span><span /></div>
            )}
            <div className="folder-browser-viewport" ref={workspaceScrollRef} aria-label="Carpetas y episodios" tabIndex={0}>
              <div key={activeFolderPath || 'series-root'} className="folder-browser-content workspace-view-transition">
                {childFolders.map((folder) => (
                  <FolderBrowserRow key={folder.path} folder={folder} matchingIds={matchingEpisodeIds} onOpen={openFolder} />
                ))}
                {directEpisodes.map((episode) => (
                  <EpisodeRow
                    key={episode.file_path}
                    episode={episode}
                    onOpen={openEpisode}
                    selected={selectedEpisodeIds.has(episode.id)}
                    onToggleSelection={toggleEpisodeSelection}
                    processingFile={processingFile}
                    showOutliers={showOutliers}
                  />
                ))}
                {!childFolders.length && !directEpisodes.length && (
                  <div className="empty-state">
                    <Search size={23} />
                    <strong>{search || statusFilter !== 'Todos los estados' ? 'No hay elementos que coincidan' : 'Esta carpeta está vacía'}</strong>
                    <span>Modifica la búsqueda o el filtro para encontrar otros episodios.</span>
                  </div>
                )}
              </div>
            </div>
            <div className="table-footer">
              <span>{scopeEpisodes.length} episodios en esta vista · {scopeEligibleEpisodes.length} listos para inyección · {selectedEligibleEpisodes.length} seleccionados</span>
              <span>Los MKV solo se modifican mediante una operación de inyección confirmada.</span>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
