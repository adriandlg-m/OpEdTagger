# AniList artwork search and series metadata

OpEdTagger searches the public AniList GraphQL API for anime artwork. Public anime metadata and search do not require OAuth, an API key, or a user token. Requests are POSTed to `https://graphql.anilist.co`.

## User flow

1. From the Dashboard series list, choose the **Buscar/cambiar carátula** action, or open a series and choose **Buscar carátula**.
2. The modal performs one initial search using the local series folder name.
3. The user can edit the search title and submit another search. A trailing release year in parentheses (for example, `Dan Dan Dan (2019)`) is automatically removed from the AniList query; the displayed search text and local folder name remain unchanged. Trailing years in square or curly brackets are handled the same way. Other title text, including season labels, is preserved.
4. The user explicitly selects a matching result, or chooses a local image.
5. Only the selected cover is saved to the app's local artwork cache.

The folder hierarchy, season names, episode numbering, and file metadata are never modified by AniList. Search results are suggestions only; titles are not unique identifiers. Each selected AniList ID is re-validated by querying the public Media endpoint before downloading its image.

## API routes

- `POST /api/artwork/search` — search anime by a user-provided title or the local series name. Returns at most 15 candidates.
- `PUT /api/artwork/series/{series_name}/poster` — assign an explicitly selected AniList media ID and download its cover image.
- `POST /api/artwork/series/{series_name}/poster/upload` — assign a user-selected local JPEG, PNG, or WebP image (maximum 12 MiB).
- `GET /api/artwork/series/{series_name}/poster` — serve the locally selected cover.
- `DELETE /api/artwork/series/{series_name}/poster` — remove the selected artwork and its managed local cache file.

## Rate limiting and acceptable use

Search is user-initiated; library scans do not search AniList. The application does not bulk query the catalog or scrape AniList. It limits each search to 15 results, handles HTTP/GraphQL rate-limit responses, and caches only an image explicitly selected by the user. Search again only when needed; AniList enforces request rate limits and may temporarily lower them; the service handles 429 responses and Retry-After headers.

AniList's terms prohibit mass collection/hoarding, using the API as a backup/data-storage service, and use in competing non-complementary anime/manga tracker services. OpEdTagger uses the API as a complementary, user-triggered artwork lookup for a local MKV chapter utility, not as an anime list or tracker. Reassess these terms before any commercial distribution or significant change in product scope.

AniList is identified as the source in the artwork dialog. OpEdTagger is an independent application and is not affiliated with AniList. The application makes no claim of endorsement by AniList.

## Local storage and compatibility

Selected AniList images are cached under `%LOCALAPPDATA%\OpEdTagger\posters\anilist\`. User-provided images are stored under `posters/custom`. The backend validates HTTPS image URLs against AniList-owned hostnames, disallows redirects, limits download size, and checks JPEG/PNG/WebP signatures.

Existing posters from the previous TMDb-enabled build are migrated once by reference into the generic `series_artwork` table as `tmdb_legacy`; their image files are not moved during migration. The old TMDb credential is removed because the new implementation does not use it. The migration marker prevents removed legacy artwork from reappearing on later application starts. New AniList or local selections replace the old association through the provider-neutral table.


## Series detail metadata and banner

After the user explicitly selects an AniList result for a series, OpEdTagger stores the selected AniList media ID in `series_anilist_links`. The ID is the local association needed to retrieve that one entry again; AniList catalogue fields are not copied into persistent SQLite rows.

When the user opens the corresponding Series Detail view, the backend queries the AniList `Media(id, type: ANIME)` fields needed for the detail page: title variants, description, format, season, year, start/end date, episode count, duration, country of origin, genres, cover image, banner image and source URL. The description is converted to bounded plain text on the backend and rendered as text in React; it is never injected as HTML.

The fetched metadata is cached only in process memory for up to 15 minutes, with a maximum of 40 entries. This temporary cache reduces repeat requests while navigating the same library; it is cleared when the backend process exits. The synopsis, genres, banner URL and external episode count are not persisted to SQLite or exported as a local catalogue.

The series detail hero displays the selected local poster, the AniList title and synopsis, year, format, country and genres. The local folder name remains visible when it differs from the AniList title. Local episode/file counts remain separate from AniList's external episode count. Clicking the poster opens a lightbox with the enlarged image and full synopsis.

If AniList provides a banner, its validated HTTPS URL is rendered at low opacity behind the series hero only, with a gradient wash to preserve text contrast. It is not placed behind the folder tree or episode rows. A missing or inaccessible banner does not affect the rest of the series view.

### Metadata routes

- `GET /api/artwork/series/{series_name}/metadata` — return `{ linked, metadata, source }`; if the series is not linked, return `linked: false` and `metadata: null`. If linked, fetch its current public AniList details (or use the short-lived in-memory cache).
- `DELETE /api/artwork/series/{series_name}/metadata` — remove the AniList ID association while preserving the selected poster image and any local/custom poster.

The AniList link is migrated from existing explicitly selected AniList poster associations once through SQLite schema migration 3. Existing poster files are not modified. If a user uploads a custom poster after linking AniList, the AniList metadata association remains, allowing a local poster to coexist with the remote series description.

AniList is the source of the external synopsis and catalogue metadata. OpEdTagger's on-disk folder hierarchy, local episode count, analysis state and injection state remain the source of truth for the user's library. No AniList season/cour data is used to rename or group local folders.
