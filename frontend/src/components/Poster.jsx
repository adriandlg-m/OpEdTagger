import { useEffect, useState } from 'react';

const posterMeta = {
  fmab: { initials: '鋼', label: 'FULLMETAL', tone: 'poster-fmab' },
  dandadan: { initials: 'D²', label: 'DAN DA DAN', tone: 'poster-dandadan' },
  drstone: { initials: '石', label: 'DR. STONE', tone: 'poster-drstone' },
  frieren: { initials: '葬', label: 'FRIEREN', tone: 'poster-frieren' },
  gachiakuta: { initials: '餓', label: 'GACHIAKUTA', tone: 'poster-gachiakuta' },
  mushoku: { initials: '無', label: 'MUSHOKU', tone: 'poster-mushoku' },
  default: { initials: 'OP', label: 'ANIME', tone: 'poster-default' },
};

export default function Poster({ variant = 'default', imageUrl = '', alt, className = '' }) {
  const item = posterMeta[variant] || posterMeta.default;
  const [imgFailed, setImgFailed] = useState(false);

  useEffect(() => {
    setImgFailed(false);
  }, [imageUrl]);

  const showImage = Boolean(imageUrl) && !imgFailed;

  return (
    <div
      className={`poster ${item.tone} ${showImage ? 'poster-has-image' : ''} ${className}`}
      aria-label={alt || `Carátula de ${item.label}`}
    >
      {showImage ? (
        <img
          src={imageUrl}
          alt={alt || item.label}
          className="poster-img"
          loading="lazy"
          onError={() => setImgFailed(true)}
        />
      ) : (
        <>
          <span className="poster-orbit" />
          <span className="poster-mark">{item.initials}</span>
          <span className="poster-caption">{item.label}</span>
        </>
      )}
    </div>
  );
}