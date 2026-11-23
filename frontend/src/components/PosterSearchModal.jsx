import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, ExternalLink, ImagePlus, LoaderCircle, Search, Upload, X } from 'lucide-react';
import { api } from '../api/client.js';

function CandidatePoster({ candidate }) {
  if (candidate.poster_preview_url) {
    return <img src={candidate.poster_preview_url} alt={`Carátula de ${candidate.title}`} loading="lazy" />;
  }
  return <div className="artwork-candidate-placeholder"><ImagePlus size={22} /><span>Sin carátula</span></div>;
}

export default function PosterSearchModal({ seriesName, onClose, onApplied, onToast }) {
  const [query, setQuery] = useState(seriesName || '');
  const [candidates, setCandidates] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [assigningId, setAssigningId] = useState(null);
  const [uploading, setUploading] = useState(false);
  const inputRef = useRef(null);
  const requestSequence = useRef(0);

  const runSearch = useCallback(async (searchText = query) => {
    const clean = String(searchText || '').trim();
    if (!clean) {
      setError('Introduce el título del anime que quieres buscar.');
      setCandidates([]);
      return;
    }
    const requestId = ++requestSequence.current;
    setLoading(true);
    setError('');
    setCandidates([]);
    try {
      const result = await api.searchAnimeArtwork(seriesName, clean);
      if (requestId !== requestSequence.current) return;
      setCandidates(result.candidates || []);
      if (!(result.candidates || []).length) {
        setError('AniList no encontró coincidencias. Prueba con el título romanizado, inglés o japonés.');
      }
    } catch (err) {
      if (requestId === requestSequence.current) {
        setError(err.message || 'No se pudo consultar AniList. Comprueba tu conexión e inténtalo de nuevo.');
      }
    } finally {
      if (requestId === requestSequence.current) setLoading(false);
    }
  }, [query, seriesName]);

  useEffect(() => {
    runSearch(seriesName || '');
    const frame = window.requestAnimationFrame(() => inputRef.current?.focus());
    return () => {
      requestSequence.current += 1;
      window.cancelAnimationFrame(frame);
    };
    // The modal performs exactly one automatic initial search per opening.
    // Manual changes are submitted explicitly by the user.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === 'Escape' && !assigningId && !uploading) onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose, assigningId, uploading]);

  const chooseCandidate = async (candidate) => {
    if (!candidate.has_poster || assigningId !== null) return;
    setAssigningId(candidate.id);
    setError('');
    try {
      const result = await api.assignAnimeArtwork(seriesName, candidate.id);
      await onApplied?.(result);
      onToast?.(`Carátula asignada: ${candidate.title}.`, 'success');
      onClose();
    } catch (err) {
      setError(err.message || 'No se pudo descargar la carátula seleccionada.');
    } finally {
      setAssigningId(null);
    }
  };

  const chooseLocalFile = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    const allowedMime = ['image/jpeg', 'image/png', 'image/webp'];
    const extension = file.name.toLowerCase().split('.').pop();
    const allowedExtension = ['jpg', 'jpeg', 'png', 'webp'];
    if (!allowedMime.includes(file.type) && !allowedExtension.includes(extension)) {
      setError('Selecciona una imagen JPEG, PNG o WebP.');
      return;
    }
    if (file.size > 12 * 1024 * 1024) {
      setError('La imagen supera el límite de 12 MiB.');
      return;
    }
    setUploading(true);
    setError('');
    try {
      const result = await api.uploadCustomArtwork(seriesName, file);
      await onApplied?.(result);
      onToast?.('Carátula personalizada guardada.', 'success');
      onClose();
    } catch (err) {
      setError(err.message || 'No se pudo guardar la imagen seleccionada.');
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="artwork-modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !assigningId && !uploading) onClose(); }}>
      <section className="artwork-modal" role="dialog" aria-modal="true" aria-labelledby="artwork-modal-title">
        <header className="artwork-modal-header">
          <div className="artwork-modal-heading-icon"><ImagePlus size={18} /></div>
          <div className="artwork-modal-heading-copy">
            <h2 id="artwork-modal-title">Buscar carátula</h2>
            <p>Busca una portada para <strong>{seriesName}</strong>. La estructura de carpetas no se modifica.</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} disabled={assigningId !== null || uploading} aria-label="Cerrar ventana">
            <X size={17} />
          </button>
        </header>

        <form className="artwork-search-form" onSubmit={(event) => { event.preventDefault(); runSearch(query); }}>
          <label className="search-control artwork-search-input">
            <Search size={16} />
            <input ref={inputRef} value={query} onChange={(event) => setQuery(event.target.value)} maxLength={180}
              placeholder="Título en inglés, romanizado o japonés…" aria-label="Título para buscar en AniList" />
          </label>
          <button type="submit" className="button button-primary" disabled={loading || !query.trim() || assigningId !== null || uploading}>
            {loading ? <LoaderCircle size={15} className="artwork-spinner" /> : <Search size={15} />}
            {loading ? 'Buscando…' : 'Buscar'}
          </button>
        </form>
        <p className="artwork-search-year-hint">
          Si el nombre termina con un año entre paréntesis, como (2019), OpEdTagger lo omite automáticamente al consultar AniList. El nombre de la carpeta no cambia.
        </p>

        {error && <div className="artwork-search-message" role="status">{error}</div>}
        {loading && <div className="artwork-loading"><LoaderCircle size={19} className="artwork-spinner" /> Consultando el catálogo público de AniList…</div>}

        {!loading && candidates.length > 0 && (
          <div className="artwork-results" aria-label="Resultados de búsqueda">
            {candidates.map((candidate) => (
              <article className="artwork-result-card" key={candidate.id}>
                <CandidatePoster candidate={candidate} />
                <div className="artwork-result-copy">
                  <strong>{candidate.title}</strong>
                  {candidate.title_native && candidate.title_native !== candidate.title && <span className="artwork-native-title">{candidate.title_native}</span>}
                  {candidate.title_romaji && candidate.title_romaji !== candidate.title && <span>{candidate.title_romaji}</span>}
                  <small>{candidate.year || 'Año desconocido'} · {candidate.format || 'Formato desconocido'}{candidate.episodes ? ` · ${candidate.episodes} episodios` : ''}</small>
                  {candidate.average_score != null && <small>Puntuación AniList: {candidate.average_score}/100</small>}
                  <a className="artwork-source-link" href={candidate.site_url} target="_blank" rel="noreferrer">
                    Ver en AniList <ExternalLink size={11} />
                  </a>
                  <button type="button" className="button button-secondary button-small artwork-use-button"
                    disabled={!candidate.has_poster || assigningId !== null || uploading}
                    onClick={() => chooseCandidate(candidate)}>
                    {assigningId === candidate.id ? <LoaderCircle size={13} className="artwork-spinner" /> : <Check size={13} />}
                    {assigningId === candidate.id ? 'Guardando…' : 'Usar esta carátula'}
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}

        {!loading && candidates.length === 0 && !error && (
          <div className="artwork-empty-state"><ImagePlus size={25} /><strong>Busca el anime por su título</strong><span>Se mostrarán varias coincidencias para que elijas la correcta.</span></div>
        )}

        <footer className="artwork-modal-footer">
          <div className="artwork-local-upload-copy">
            <strong>¿No encuentras la portada?</strong>
            <span>También puedes usar una imagen de tu equipo (JPEG, PNG o WebP; máximo 12 MiB).</span>
          </div>
          <label className={`button button-secondary artwork-upload-button ${(uploading || assigningId !== null) ? 'is-disabled' : ''}`}>
            {uploading ? <LoaderCircle size={14} className="artwork-spinner" /> : <Upload size={14} />}
            {uploading ? 'Guardando…' : 'Elegir imagen local'}
            <input type="file" accept="image/jpeg,image/png,image/webp" hidden disabled={uploading || assigningId !== null} onChange={chooseLocalFile} />
          </label>
        </footer>
        <div className="artwork-attribution">
          El título de búsqueda se envía a AniList; no se comparten rutas completas ni archivos multimedia.
          <br />
          Búsqueda pública e información proporcionada por <a href="https://anilist.co" target="_blank" rel="noreferrer">AniList <ExternalLink size={11} /></a>. OpEdTagger es independiente y no está afiliado con AniList.
        </div>
      </section>
    </div>
  );
}
