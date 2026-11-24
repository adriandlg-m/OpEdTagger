const API_BASE = import.meta.env.VITE_API_BASE || '/api';

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(options.headers || {}),
    },
  });
  const contentType = response.headers.get('content-type') || '';
  const payload = contentType.includes('application/json')
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    const message = typeof payload === 'object' ? payload.detail || payload.message : payload;
    throw new Error(message || `Request failed (${response.status})`);
  }
  return payload;
}

export const api = {
  health: () => request('/system/health'),
  selectFolder: (initialPath = '', title = 'Seleccionar carpeta') => request('/system/select-folder', {
    method: 'POST', body: JSON.stringify({ initial_path: initialPath, title }),
  }),
  dashboard: (scan = false) => request(`/dashboard?scan=${scan ? 'true' : 'false'}`),
  library: () => request('/library'),
  scanLibrary: () => request('/library/scan', { method: 'POST' }),
  series: (name) => request(`/series/${encodeURIComponent(name)}`),
  jobs: () => request('/jobs'),
  job: (id) => request(`/jobs/${encodeURIComponent(id)}`),
  startSeriesJob: (name, payload = {}) => request(`/jobs/series/${encodeURIComponent(name)}`, { method: 'POST', body: JSON.stringify(payload) }),
  cancelJob: (id) => request(`/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
  logs: (params = {}) => {
    const query = new URLSearchParams();
    if (params.level && params.level !== 'Todos') query.set('level', params.level);
    if (params.q) query.set('q', params.q);
    query.set('limit', String(params.limit || 500));
    return request(`/logs?${query}`);
  },
  settings: () => request('/settings'),
  saveSettings: (payload) => request('/settings', { method: 'PUT', body: JSON.stringify(payload) }),
  preferences: () => request('/settings/preferences'),
  savePreferences: (payload) => request('/settings/preferences', { method: 'PUT', body: JSON.stringify(payload) }),
  searchAnimeArtwork: (seriesName, query = '') => request('/artwork/search', {
    method: 'POST', body: JSON.stringify({ series_name: seriesName, query: query || undefined }),
  }),
  assignAnimeArtwork: (seriesName, anilistId) => request(`/artwork/series/${encodeURIComponent(seriesName)}/poster`, {
    method: 'PUT', body: JSON.stringify({ anilist_id: anilistId }),
  }),
  seriesArtworkMetadata: (seriesName) => request(`/artwork/series/${encodeURIComponent(seriesName)}/metadata`),
  unlinkSeriesArtworkMetadata: (seriesName) => request(`/artwork/series/${encodeURIComponent(seriesName)}/metadata`, { method: 'DELETE' }),
  uploadCustomArtwork: (seriesName, file) => {
    const form = new FormData();
    form.append('file', file);
    return request(`/artwork/series/${encodeURIComponent(seriesName)}/poster/upload`, {
      method: 'POST', body: form,
    });
  },
  removeSeriesArtwork: (seriesName) => request(`/artwork/series/${encodeURIComponent(seriesName)}/poster`, { method: 'DELETE' }),
  searchTemplates: (query) => request('/templates/search', { method: 'POST', body: JSON.stringify({ query }) }),
  downloadTemplates: (seriesName, animeSlug) => request('/templates/download', { method: 'POST', body: JSON.stringify({ series_name: seriesName, anime_slug: animeSlug }) }),
  uploadAnalysis: (file, seriesName, animeSlug = '') => {
    const form = new FormData();
    form.append('file', file);
    form.append('series_name', seriesName);
    if (animeSlug) form.append('anime_slug', animeSlug);
    return request('/analysis', { method: 'POST', body: form });
  },
  approveEpisode: (id, payload) => request(`/episodes/${id}/review`, { method: 'PUT', body: JSON.stringify(payload) }),
  chapterPreview: (id) => request(`/episodes/${id}/chapter-preview`),
  injectChapters: (id, replaceExisting) => request(`/episodes/${id}/inject`, {
    method: 'POST', body: JSON.stringify({ confirm: true, replace_existing_chapters: replaceExisting }),
  }),
  startBatchInjection: (seriesName, episodeIds, replaceExisting) => request(
    `/jobs/series/${encodeURIComponent(seriesName)}/inject`,
    { method: 'POST', body: JSON.stringify({
      episode_ids: episodeIds,
      replace_existing_chapters: Boolean(replaceExisting),
    }) },
  ),
  exportLogsUrl: () => `${API_BASE}/logs/export`,
};

export function formatEditableTimestamp(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))) return '';
  const totalMilliseconds = Math.max(0, Math.round(Number(seconds) * 1000));
  const hours = Math.floor(totalMilliseconds / 3600000);
  const totalMinutes = Math.floor(totalMilliseconds / 60000);
  const minutes = hours > 0 ? Math.floor((totalMilliseconds % 3600000) / 60000) : totalMinutes;
  const wholeSeconds = Math.floor((totalMilliseconds % 60000) / 1000);
  const milliseconds = totalMilliseconds % 1000;
  const pad = (value, size = 2) => String(value).padStart(size, '0');

  return hours > 0
    ? `${pad(hours)}:${pad(minutes)}:${pad(wholeSeconds)}.${pad(milliseconds, 3)}`
    : `${pad(minutes)}:${pad(wholeSeconds)}.${pad(milliseconds, 3)}`;
}

export function parseEditableTimestamp(value) {
  const text = String(value ?? '').trim().replace(',', '.');
  if (!text) return null;

  const mmss = text.match(/^(\d+):([0-5]?\d)(?:\.(\d{1,3}))?$/);
  const hhmmss = text.match(/^(\d+):([0-5]\d):([0-5]\d)(?:\.(\d{1,3}))?$/);
  const toMilliseconds = (fraction = '') => Number((fraction + '000').slice(0, 3));

  if (mmss) {
    const minutes = Number(mmss[1]);
    const seconds = Number(mmss[2]);
    const milliseconds = toMilliseconds(mmss[3]);
    return minutes * 60 + seconds + milliseconds / 1000;
  }

  if (hhmmss) {
    const hours = Number(hhmmss[1]);
    const minutes = Number(hhmmss[2]);
    const seconds = Number(hhmmss[3]);
    const milliseconds = toMilliseconds(hhmmss[4]);
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000;
  }

  return null;
}

export function formatTimestamp(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))) return '—';
  const value = Math.max(0, Number(seconds));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const secs = Math.floor(value % 60);
  return hours > 0
    ? `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
    : `${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

export function mapSeriesStatus(status) {
  if (status === 'Listo') return 'Listo';
  if (status === 'Outliers') return 'Outliers';
  if (status === 'En curso') return 'En curso';
  return 'Pendiente';
}

export function mapEpisode(episode) {
  const hasOutlier = episode.is_anomaly_op || episode.is_anomaly_ed;
  let status = 'Pendiente';
  if (episode.analysis_status === 'FAILED') status = 'Error';
  else if (episode.analysis_status === 'STALE') status = 'Desactualizado';
  else if (episode.analysis_status === 'NO_MATCH') status = 'Sin coincidencias';
  else if (episode.analysis_status === 'COMPLETED') {
    if (hasOutlier && episode.review_status === 'PENDING') status = 'Revisión necesaria';
    else if (episode.review_status === 'PENDING') status = 'Revisión necesaria';
    else if (episode.review_status === 'REVIEW_RECOMMENDED') status = 'Revisión recomendada';
    else if (episode.injection_status === 'SUCCEEDED') status = 'Listo';
    else if (['AUTO_APPROVED', 'MANUALLY_REVIEWED', 'APPROVED'].includes(episode.review_status)) status = 'Preparado';
    else if (hasOutlier) status = 'Outlier';
    else status = 'Analizado';
  }
  const segment = (value) => value ? `${formatTimestamp(value.start)}–${formatTimestamp(value.end)}` : '—';
  const confidence = (value) => value && Number.isFinite(Number(value.confidence_score))
    ? Number(value.confidence_score)
    : null;
  return {
    ...episode,
    id: episode.id,
    displayId: episode.display_name || episode.file_name,
    opening: segment(episode.op),
    ending: segment(episode.ed),
    openingScore: confidence(episode.op),
    endingScore: confidence(episode.ed),
    status,
  };
}
