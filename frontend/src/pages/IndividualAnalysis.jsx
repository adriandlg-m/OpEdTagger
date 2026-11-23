import { useEffect, useRef, useState } from 'react';
import { CheckCircle2, Database, FileVideo2, FolderOpen, Info, Search, UploadCloud, Sparkles, X, RefreshCw } from 'lucide-react';
import PageHeader from '../components/PageHeader.jsx';
import StatusBadge from '../components/StatusBadge.jsx';
import { api, formatTimestamp } from '../api/client.js';
import '../styles/individual-analysis.css';

export default function IndividualAnalysis({ onToast }) {
  const fileRef = useRef(null);
  const [file, setFile] = useState(null);
  const [anime, setAnime] = useState('');
  const [isDragging, setIsDragging] = useState(false);
  const [candidates, setCandidates] = useState([]);
  const [selectedSlug, setSelectedSlug] = useState('');
  const [searching, setSearching] = useState(false);
  const [job, setJob] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleFile = (nextFile) => {
    if (!nextFile) return;
    if (!nextFile.name.toLowerCase().endsWith('.mkv')) {
      onToast('Selecciona un archivo con extensión .mkv.', 'warning');
      return;
    }
    setFile(nextFile); setResult(null); setJob(null); setError('');
  };

  const searchAnimeTemplates = async () => {
    if (!anime.trim()) { onToast('Escribe el nombre del anime para buscar templates.', 'warning'); return; }
    setSearching(true); setCandidates([]); setSelectedSlug(''); setError('');
    try {
      const response = await api.searchTemplates(anime.trim());
      setCandidates(response.candidates || []);
      if (response.candidates?.length === 1) setSelectedSlug(response.candidates[0].slug);
      if (!response.candidates?.length) onToast('AnimeThemes no devolvió resultados para esa búsqueda.', 'warning');
    } catch (err) { setError(err.message); onToast(err.message, 'error'); }
    finally { setSearching(false); }
  };

  const runAnalysis = async () => {
    if (!file) { onToast('Selecciona un archivo MKV para continuar.', 'warning'); return; }
    if (!anime.trim()) { onToast('Escribe el nombre del anime para continuar.', 'warning'); return; }
    setSubmitting(true); setResult(null); setError('');
    try {
      const created = await api.uploadAnalysis(file, anime.trim(), selectedSlug);
      setJob(created);
      onToast('Archivo recibido. El análisis se ejecuta en segundo plano.', 'success');
    } catch (err) { setError(err.message); onToast(err.message, 'error'); }
    finally { setSubmitting(false); }
  };

  useEffect(() => {
    if (!job?.id || !['queued', 'running'].includes(job.status)) return undefined;
    let stopped = false;
    const poll = async () => {
      try {
        const latest = await api.job(job.id);
        if (stopped) return;
        setJob(latest);
        if (['completed', 'completed_with_errors', 'failed', 'cancelled', 'interrupted'].includes(latest.status)) {
          const itemResult = latest.items?.[0]?.result;
          if (itemResult) setResult(itemResult);
          if (latest.status === 'failed') setError(latest.error || 'El análisis falló.');
        }
      } catch (err) { if (!stopped) setError(err.message); }
    };
    poll();
    const timer = window.setInterval(poll, 1400);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [job?.id, job?.status]);

  const clearFile = (event) => {
    event.stopPropagation(); setFile(null); setResult(null); setJob(null); setError('');
    if (fileRef.current) fileRef.current.value = '';
  };
  const finished = job && ['completed', 'completed_with_errors', 'failed', 'cancelled', 'interrupted'].includes(job.status);
  const busy = submitting || (job && ['queued', 'running'].includes(job.status));

  return (
    <div className="page-content secondary-page ia-page">
      <PageHeader title="Análisis Individual" description="Evalúa un capítulo MKV de forma independiente, sin incorporarlo al catálogo ni modificar el archivo original." eyebrow="HERRAMIENTAS / ANÁLISIS" />
      <div className="ia-layout">
        <section className="panel ia-form-panel">
          <div className="ia-panel-heading"><div className="ia-heading-icon"><Sparkles size={19} /></div><div><h2>Nuevo análisis</h2><p>Configura el capítulo y la fuente de templates.</p></div></div>
          <div className="ia-section">
            <div className="ia-section-heading"><span className="ia-step-number">01</span><div><h3>Archivo de vídeo</h3><p>Selecciona el capítulo que deseas analizar.</p></div></div>
            <button type="button" className={`ia-dropzone ${isDragging ? 'ia-dropzone-dragging' : ''} ${file ? 'ia-dropzone-selected' : ''}`} onClick={() => fileRef.current?.click()}
              onDragOver={(event) => { event.preventDefault(); setIsDragging(true); }} onDragLeave={() => setIsDragging(false)}
              onDrop={(event) => { event.preventDefault(); setIsDragging(false); handleFile(event.dataTransfer.files?.[0]); }}>
              {file ? <><div className="ia-dropzone-icon ia-file-icon"><FileVideo2 size={26} /></div><strong className="ia-selected-filename">{file.name}</strong><span>{(file.size / (1024 * 1024)).toFixed(1)} MB · Archivo seleccionado</span><em><FolderOpen size={14} /> Cambiar archivo</em></> : <><div className="ia-dropzone-icon"><UploadCloud size={27} /></div><strong>Arrastra tu archivo MKV aquí</strong><span>El archivo se enviará al backend local para su análisis.</span><em><FolderOpen size={14} /> Explorar archivos</em></>}
            </button>
            <input ref={fileRef} className="ia-visually-hidden" type="file" accept=".mkv,video/x-matroska" onChange={(event) => { handleFile(event.target.files?.[0]); event.target.value = ''; }} tabIndex={-1} />
            {file && <div className="ia-file-info"><div className="ia-file-info-left"><CheckCircle2 size={15} /><span>Archivo listo para analizar</span></div><button type="button" className="ia-clear-file" onClick={clearFile} title="Quitar archivo" aria-label="Quitar archivo"><X size={15} /></button></div>}
          </div>

          <div className="ia-section">
            <div className="ia-section-heading"><span className="ia-step-number">02</span><div><h3>Identificación del anime</h3><p>Indica la serie a la que pertenece el capítulo.</p></div></div>
            <label className="ia-field-label" htmlFor="anime-title">Nombre del anime</label>
            <div className="ia-input-wrapper"><Search size={16} /><input id="anime-title" type="text" className="ia-input" value={anime} onChange={(event) => { setAnime(event.target.value); setCandidates([]); setSelectedSlug(''); }} placeholder="Ej. DAN DA DAN" autoComplete="off" /></div>
            <div className="ia-field-hint"><Info size={13} /> Utiliza el nombre con el que se identifica el anime en AnimeThemes.</div>
          </div>

          <div className="ia-section">
            <div className="ia-section-heading"><span className="ia-step-number">03</span><div><h3>Fuente de templates</h3><p>Consulta la API y selecciona la coincidencia correcta.</p></div></div>
            <div className="ia-source-card"><div className="ia-source-icon"><Database size={19} /></div><div className="ia-source-copy"><strong>AnimeThemes API</strong><span>Consulta real; el slug seleccionado evita coincidencias ambiguas.</span></div><StatusBadge status="Listo" compact /></div>
            <button type="button" className="button button-secondary button-wide" style={{ marginTop: 12 }} onClick={searchAnimeTemplates} disabled={searching || !anime.trim()}><Search size={15} /> {searching ? 'Buscando…' : 'Buscar coincidencias en AnimeThemes'}</button>
            {candidates.length > 0 && <div className="ia-input-wrapper" style={{ marginTop: 12 }}><select className="ia-input" value={selectedSlug} onChange={(event) => setSelectedSlug(event.target.value)}><option value="">Selecciona el anime…</option>{candidates.map((candidate) => <option key={candidate.slug} value={candidate.slug}>{candidate.title_english || candidate.title_romaji || candidate.slug} · {candidate.slug} (OP {candidate.opening_count} / ED {candidate.ending_count})</option>)}</select></div>}
            {candidates.length > 0 && <div className="ia-field-hint"><Info size={13} /> Confirma el título correcto antes de descargar templates.</div>}
          </div>

          <div className="ia-form-footer">
            <button type="button" className="button button-primary button-wide ia-submit-button" onClick={runAnalysis} disabled={busy || (candidates.length > 0 && !selectedSlug)}><Search size={16} /> {busy ? 'Analizando…' : 'Iniciar análisis'}</button>
            <div className="ia-disclaimer"><Info size={14} /><span>El archivo se analiza en el backend local. No se modifica ni se añade a la biblioteca.</span></div>
          </div>
        </section>

        <section className="panel ia-results-panel">
          <div className="ia-results-header"><div><div className="ia-results-title"><h2>Resultado del análisis</h2>{finished && <StatusBadge status={job.status === 'completed' ? 'Listo' : job.status === 'completed_with_errors' ? 'Parcial' : job.status === 'failed' ? 'Error' : job.status === 'interrupted' ? 'Interrumpido' : job.status === 'cancelled' ? 'Cancelado' : 'Pendiente'} />}</div><p>Coincidencias reales y timestamps detectados.</p></div><div className="ia-results-header-icon"><FileVideo2 size={20} /></div></div>
          {!job && !error ? <div className="ia-empty-state"><div className="ia-empty-icon"><Search size={29} /></div><h3>Esperando un análisis</h3><p>Selecciona un archivo MKV, identifica el anime y consulta sus templates.</p><div className="ia-empty-steps"><div><span>01</span> Seleccionar MKV</div><div><span>02</span> Identificar anime</div><div><span>03</span> Evaluar templates</div></div></div> : (
            <div className="ia-live-results">
              {job && <div className="ia-result-summary"><div className="ia-result-summary-icon">{busy ? <RefreshCw size={19} /> : <CheckCircle2 size={19} />}</div><div className="ia-result-summary-copy"><strong>{busy ? 'Análisis en curso' : job.status === 'completed' ? 'Análisis completado' : job.status === 'completed_with_errors' ? 'Análisis completado con errores' : job.status === 'failed' ? 'Análisis fallido' : job.status === 'interrupted' ? 'Análisis interrumpido' : job.status === 'cancelled' ? 'Análisis cancelado' : 'Trabajo finalizado'}</strong><span>{anime} · Trabajo {job.id}</span></div></div>}
              {job && busy && <div className="episode-progress-block"><div className="progress-heading"><span>{job.stage || 'Preparando'}</span><strong>{Math.round(job.progress || 0)}%</strong></div><div className="progress-track"><span style={{ width: `${Math.min(100, job.progress || 0)}%` }} /></div></div>}
              {(error || job?.error) && <div className="ia-review-callout"><Info size={17} /><div><strong>No se pudo completar el análisis</strong><span>{error || job.error}</span></div></div>}
              {result && <>
                <div className="ia-result-file"><FileVideo2 size={15} /><span title={file?.name}>{result.file_name || file?.name}</span></div>
                <div className="ia-detection-heading"><div><h3>Segmentos detectados</h3><p>Score original del algoritmo; no es porcentaje ni probabilidad.</p></div><span className="ia-count-chip">{Number(Boolean(result.op)) + Number(Boolean(result.ed))} segmentos</span></div>
                {['op', 'ed'].map((key) => {
                  const segment = result[key];
                  return <div className="ia-result-segment" key={key}><div className="ia-segment-top"><div className="ia-segment-type"><span className={`ia-segment-indicator ${key === 'op' ? 'ia-op-indicator' : 'ia-ed-indicator'}`} />{key === 'op' ? 'OPENING' : 'ENDING'}</div><StatusBadge status={segment ? 'Analizado' : 'Sin coincidencias'} compact /></div><div className="ia-segment-data"><strong>{segment ? `${formatTimestamp(segment.start)} – ${formatTimestamp(segment.end)}` : 'No detectado'}</strong><span className={segment ? 'score-positive' : 'muted-score'}>{segment ? `Score: ${Number(segment.score).toFixed(4)}` : 'Sin coincidencia superior al umbral'}</span></div></div>;
                })}
                <div className="ia-review-callout"><Info size={17} /><div><strong>El MKV original permanece intacto</strong><span>Este análisis individual no se incorpora a la biblioteca y no habilita inyección directa en esta versión. El MKV original permanece intacto.</span></div></div>
              </>}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
