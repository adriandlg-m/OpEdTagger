# Contratos REST principales

Base local: `http://127.0.0.1:8000/api`. FastAPI publica OpenAPI interactivo en `/docs`.

| Método | Ruta | Función |
|---|---|---|
| GET | `/system/health` | Estado local y detección de herramientas |
| GET | `/dashboard?scan=false` | Métricas agregadas; `scan=true` fuerza indexación |
| GET | `/library` | Lista de series, indexando biblioteca |
| POST | `/library/scan` | Fuerza un escaneo |
| GET | `/series/{series_name}` | Episodios y estados de una serie |
| GET | `/jobs?limit=100` | Historial y trabajos |
| POST | `/jobs/series/{series_name}` | Crea análisis de serie; body opcional: `skip_indices`, `selected_anime_slug` |
| POST | `/jobs/series/{series_name}/inject` | Crea un trabajo de inyección masiva; body: `episode_ids` (1–500), `replace_existing_chapters` |
| GET | `/jobs/{job_id}` | Estado y items del job |
| POST | `/jobs/{job_id}/cancel` | Solicita cancelación cooperativa |
| POST | `/templates/search` | Body: `{ "query": "..." }`; la pantalla de serie permite elegir la entrada correcta |
| POST | `/templates/download` | Body: `{ "series_name": "...", "anime_slug": "..." }` |
| POST | `/analysis` | Multipart: `file`, `series_name`, `anime_slug` opcional |
| PUT | `/episodes/{id}/review` | Body: `op`, `ed`, `approved`; cada segmento puede corregirse o descartarse (`null`); se conserva el original y se registra el historial |
| GET | `/episodes/{id}/chapter-preview` | Duración real y plan de capítulos |
| POST | `/episodes/{id}/inject` | Body: `{ "confirm": true, "replace_existing_chapters": false }` |
| GET | `/logs?level=&q=&limit=300` | Consulta de logs estructurados |
| GET | `/logs/export` | Exportación TXT |
| GET/PUT | `/settings` | Rutas y ajustes de ejecución |
| GET/PUT | `/settings/preferences` | Preferencias de interfaz |

Los segmentos incluyen `confidence_score` (0–100), `confidence_band` (`HIGH`, `MEDIUM`, `LOW`, `UNASSESSED`) y `confidence_factors`. El estado `review_status` del episodio puede ser `AUTO_APPROVED`, `REVIEW_RECOMMENDED`, `PENDING`, `MANUALLY_REVIEWED`, `APPROVED` o `NOT_REQUIRED`. El índice es heurístico, no una probabilidad calibrada. `GET /jobs/{job_id}` devuelve el progreso y el resultado/error de cada elemento de un lote de inyección.

Los errores utilizan respuestas HTTP con `detail` comprensible. La validación detallada de cada request está declarada en `backend/api/schemas/models.py` y en el esquema OpenAPI.

## User folder hierarchy

`GET /api/series/{series_name}` returns each episode with `relative_path`, `folder_path`, and `folder_segments`. These are relative to the series directory and preserve the exact folder names and nesting chosen by the user. Folder names are not inferred from AniList, AnimeThemes, season numbers, or cours. Files directly inside the series root use an empty `folder_path` and an empty `folder_segments` array.

`POST /api/system/select-folder` opens the native folder picker on the local machine. Request body: `{ "initial_path": "...", "title": "..." }`. A selected folder returns `{ "selected": true, "path": "..." }`; cancelling returns `{ "selected": false, "path": null }`. This endpoint is intended only for the loopback desktop application.


## AniList artwork

La búsqueda pública de AniList no requiere token, API key ni cuenta. Las consultas se realizan desde el backend mediante GraphQL.

| Método | Ruta | Propósito |
|---|---|---|
| POST | `/api/artwork/search` | Busca hasta 15 candidatos por el nombre de carpeta o un texto introducido manualmente. |
| PUT | `/api/artwork/series/{series_name}/poster` | Revalida el ID seleccionado en AniList, descarga y asigna esa carátula. |
| POST | `/api/artwork/series/{series_name}/poster/upload` | Asigna una imagen local JPEG, PNG o WebP de hasta 12 MiB. |
| GET | `/api/artwork/series/{series_name}/poster` | Sirve la carátula guardada en la caché local. |
| DELETE | `/api/artwork/series/{series_name}/poster` | Elimina la asociación y el archivo administrado por OpEdTagger. |
| GET | `/api/artwork/series/{series_name}/metadata` | Recupera bajo demanda la ficha del AniList ID asociado (sinopsis, títulos, año, formato, géneros, episodios, carátula y banner). |
| DELETE | `/api/artwork/series/{series_name}/metadata` | Desvincula la ficha AniList sin eliminar una carátula local o personalizada. |

Las búsquedas solo se ejecutan cuando el usuario abre el buscador o envía una consulta manual. El escaneo de biblioteca no consulta AniList. Los resultados son candidatos, no identificadores inequívocos: el usuario debe seleccionar la coincidencia correcta. Véase `docs/ANILIST_ARTWORK.md` para el flujo, la atribución y las condiciones de uso.


## AniList series metadata

The selected AniList media ID is stored separately from poster selection in `series_anilist_links`. The API fetches the media details from AniList when a series detail view requests them. The response includes a plain-text description, title variants, season/year, format, country of origin, genres, external episode count/duration, cover URL, banner URL and AniList page URL. The synopsis is sanitized on the backend and rendered as text by React.

Catalogue metadata is held only in a bounded in-process cache for 15 minutes (maximum 40 entries); description, genres, episode count and banner URL are not written to SQLite or local catalogue files. The user-selected poster remains cached locally as artwork. A banner URL is accepted only from an approved HTTPS AniList CDN host and is used as a subtle background in the series hero, not behind episode rows.
