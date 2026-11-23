import { useEffect } from 'react';
import { ExternalLink, Image as ImageIcon, X } from 'lucide-react';
import Poster from './Poster.jsx';

function formatMetadata(metadata) {
  if (!metadata) return '';
  return [metadata.year, metadata.format_label || metadata.format, metadata.country_label || metadata.country_of_origin]
    .filter(Boolean)
    .join(' · ');
}

export default function SeriesArtworkLightbox({ seriesTitle, posterUrl, metadata, onClose }) {
  useEffect(() => {
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  return (
    <div
      className="series-artwork-lightbox-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="series-artwork-lightbox"
        role="dialog"
        aria-modal="true"
        aria-labelledby="series-artwork-lightbox-title"
      >
        <header className="series-artwork-lightbox-header">
          <div className="series-artwork-lightbox-heading-icon"><ImageIcon size={17} /></div>
          <div className="series-artwork-lightbox-heading-copy">
            <h2 id="series-artwork-lightbox-title">Ficha del anime</h2>
            <p>Carátula y metadatos de la serie seleccionada.</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Cerrar ficha">
            <X size={17} />
          </button>
        </header>

        <div className="series-artwork-lightbox-body">
          <div className="series-artwork-lightbox-poster-column">
            <Poster
              imageUrl={posterUrl || ''}
              alt={`Carátula de ${metadata?.title || seriesTitle}`}
              className="series-artwork-lightbox-poster"
            />
            {metadata?.site_url && (
              <a className="series-artwork-source-link" href={metadata.site_url} target="_blank" rel="noreferrer">
                Abrir en AniList <ExternalLink size={12} />
              </a>
            )}
          </div>

          <div className="series-artwork-lightbox-copy">
            <div>
              <h3>{metadata?.title || seriesTitle}</h3>
              {metadata && seriesTitle && metadata.title !== seriesTitle && (
                <p className="series-artwork-local-title">Carpeta local: {seriesTitle}</p>
              )}
              {metadata?.title_native && metadata.title_native !== metadata.title && (
                <p className="series-artwork-native-title">{metadata.title_native}</p>
              )}
              {metadata?.title_romaji && metadata.title_romaji !== metadata.title && metadata.title_romaji !== metadata.title_native && (
                <p className="series-artwork-native-title">{metadata.title_romaji}</p>
              )}
              {formatMetadata(metadata) && <p className="series-artwork-detail-line">{formatMetadata(metadata)}</p>}
            </div>

            {metadata?.genres?.length > 0 && (
              <div className="series-artwork-genres" aria-label="Géneros">
                {metadata.genres.map((genre) => <span key={genre}>{genre}</span>)}
              </div>
            )}

            <div className="series-artwork-external-stats">
              {metadata?.episodes != null && <span><strong>{metadata.episodes}</strong> episodios según AniList</span>}
              {metadata?.duration != null && <span><strong>{metadata.duration} min</strong> por episodio</span>}
            </div>

            <div className="series-artwork-synopsis-full">
              <h4>Sinopsis</h4>
              {metadata?.description
                ? <p>{metadata.description}</p>
                : <p className="series-artwork-no-description">AniList no proporciona una sinopsis para esta entrada.</p>}
            </div>

            <p className="series-artwork-attribution">
              {metadata
                ? <>Metadatos consultados a AniList al abrir esta ficha. OpEdTagger es un proyecto independiente y no está afiliado con AniList. {metadata.site_url && <>Consulta la <a href={metadata.site_url} target="_blank" rel="noreferrer">ficha de origen <ExternalLink size={10} /></a>.</>}</>
                : 'Carátula guardada en la biblioteca local de OpEdTagger.'}
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}
