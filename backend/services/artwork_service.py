from __future__ import annotations

import hashlib
import re
import threading
import time
import uuid
from copy import deepcopy
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import requests

from backend.infrastructure.config import app_data_dir
from backend.infrastructure.database import db_session, utc_now

ANILIST_GRAPHQL = "https://graphql.anilist.co"
REQUEST_TIMEOUT = (6, 25)
MAX_POSTER_BYTES = 12 * 1024 * 1024
METADATA_CACHE_TTL_SECONDS = 15 * 60
METADATA_CACHE_MAX_ENTRIES = 40
_METADATA_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
_METADATA_CACHE_LOCK = threading.RLock()
JPEG_SIGNATURE = bytes.fromhex("ffd8ff")
PNG_SIGNATURE = bytes.fromhex("89504e470d0a1a0a")
SUPPORTED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
SUPPORTED_IMAGE_SUFFIXES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

SEARCH_QUERY = """
query SearchAnime($search: String!, $page: Int!) {
  Page(page: $page, perPage: 15) {
    pageInfo { hasNextPage }
    media(
      search: $search,
      type: ANIME,
      isAdult: false,
      sort: [SEARCH_MATCH, POPULARITY_DESC]
    ) {
      id
      title { romaji english native userPreferred }
      format
      seasonYear
      startDate { year }
      episodes
      averageScore
      coverImage { large extraLarge }
      siteUrl
    }
  }
}
"""

# Anime libraries commonly append the release year to the folder title, e.g.
# "Dan Dan Dan (2019)". AniList search expects the title, not this local
# disambiguation suffix, so remove only a final year token in brackets.
_TRAILING_YEAR_SUFFIX = re.compile(
    r"\s*(?:\((?:18|19|20|21)\d{2}\)|\[(?:18|19|20|21)\d{2}\]|\{(?:18|19|20|21)\d{2}\})\s*$"
)


def normalize_anilist_search_title(value: str) -> str:
    """Remove a trailing folder-name release year without altering real title text."""
    original = " ".join(str(value or "").split())
    normalized = _TRAILING_YEAR_SUFFIX.sub("", original).strip()
    return " ".join(normalized.split())


DETAIL_QUERY = """
query AnimeById($id: Int!) {
  Media(id: $id, type: ANIME) {
    id
    title { romaji english native userPreferred }
    description(asHtml: false)
    format
    season
    seasonYear
    startDate { year month day }
    endDate { year month day }
    episodes
    duration
    countryOfOrigin
    genres
    coverImage { large extraLarge }
    bannerImage
    siteUrl
  }
}
"""


class ArtworkError(RuntimeError):
    pass


def _ensure_series_exists(series_name: str) -> None:
    with db_session() as conn:
        row = conn.execute("SELECT 1 FROM series WHERE name=?", (series_name,)).fetchone()
    if not row:
        raise ArtworkError("La serie no existe en la biblioteca actual. Vuelve a analizar la biblioteca.")


def _graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    try:
        response = requests.post(
            ANILIST_GRAPHQL,
            json={"query": query, "variables": variables},
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "OpEdTagger/1.5.0 (local anime media utility)",
            },
            timeout=REQUEST_TIMEOUT,
        )
    except requests.Timeout as exc:
        raise ArtworkError("AniList no respondió dentro del tiempo permitido.") from exc
    except requests.RequestException as exc:
        raise ArtworkError(f"No se pudo conectar con AniList: {exc}") from exc

    if response.status_code == 429:
        retry = response.headers.get("Retry-After")
        suffix = f" Espera {retry} segundos antes de volver a buscar." if retry else " Espera un momento antes de volver a buscar."
        raise ArtworkError("AniList ha limitado temporalmente las consultas." + suffix)
    if response.status_code in (401, 403):
        raise ArtworkError("AniList rechazó la consulta pública. Comprueba su disponibilidad e inténtalo más tarde.")
    if not response.ok:
        raise ArtworkError(f"AniList devolvió un error HTTP {response.status_code}.")

    try:
        payload = response.json()
    except ValueError as exc:
        raise ArtworkError("AniList devolvió una respuesta que no es JSON válido.") from exc
    if not isinstance(payload, dict):
        raise ArtworkError("AniList devolvió una respuesta inesperada.")

    errors = payload.get("errors") or []
    if errors:
        messages = [str(item.get("message") or "Error GraphQL") for item in errors if isinstance(item, dict)]
        combined = " ".join(messages)
        if any("too many requests" in message.casefold() for message in messages):
            raise ArtworkError("AniList ha limitado temporalmente las consultas. Espera un momento antes de volver a buscar.")
        raise ArtworkError(combined[:500] or "AniList no pudo completar la consulta.")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise ArtworkError("AniList no devolvió datos para la consulta.")
    return data


def _valid_anilist_image_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(str(value))
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.username or parsed.password or port not in (None, 443):
        return None
    if not (host == "anilist.co" or host.endswith(".anilist.co")):
        return None
    if not parsed.path.startswith("/file/anilistcdn/"):
        return None
    return str(value)


def _map_candidate(media: dict[str, Any]) -> dict[str, Any] | None:
    try:
        media_id = int(media.get("id"))
    except (TypeError, ValueError):
        return None
    title = media.get("title") or {}
    start_date = media.get("startDate") or {}
    cover = media.get("coverImage") or {}
    image_url = _valid_anilist_image_url(cover.get("extraLarge") or cover.get("large"))
    return {
        "id": media_id,
        "title": title.get("userPreferred") or title.get("english") or title.get("romaji") or title.get("native") or "Título desconocido",
        "title_english": title.get("english") or "",
        "title_romaji": title.get("romaji") or "",
        "title_native": title.get("native") or "",
        "format": media.get("format") or "UNKNOWN",
        "year": media.get("seasonYear") or start_date.get("year"),
        "episodes": media.get("episodes"),
        "average_score": media.get("averageScore"),
        "poster_preview_url": image_url,
        "has_poster": bool(image_url),
        "site_url": f"https://anilist.co/anime/{media_id}",
    }


def search_anime(series_name: str, query: str | None = None) -> dict[str, Any]:
    _ensure_series_exists(series_name)
    original_query = " ".join(str(query if query is not None else series_name).split())
    if not original_query:
        raise ArtworkError("Introduce un título para buscar en AniList.")
    if len(original_query) > 180:
        raise ArtworkError("El título de búsqueda no puede superar los 180 caracteres.")

    search_query = normalize_anilist_search_title(original_query)
    if not search_query:
        raise ArtworkError("Introduce el título del anime; el año entre paréntesis no es suficiente para buscar.")

    data = _graphql(SEARCH_QUERY, {"search": search_query, "page": 1})
    page = data.get("Page") or {}
    media = page.get("media") or []
    candidates = [candidate for item in media if isinstance(item, dict)
                  if (candidate := _map_candidate(item)) is not None]
    return {
        # "query" remains the exact string sent to AniList. Keep the folder/user
        # text separately so the UI can retain its original value unchanged.
        "query": search_query,
        "original_query": original_query,
        "year_suffix_removed": search_query != original_query,
        "total": len(candidates),
        "has_next_page": bool((page.get("pageInfo") or {}).get("hasNextPage")),
        "candidates": candidates,
        "source": "AniList",
    }


def _get_anime_details(anilist_id: int) -> dict[str, Any]:
    if anilist_id <= 0:
        raise ArtworkError("El identificador de AniList debe ser positivo.")
    data = _graphql(DETAIL_QUERY, {"id": int(anilist_id)})
    media = data.get("Media")
    if not isinstance(media, dict) or int(media.get("id") or 0) != int(anilist_id):
        raise ArtworkError("El anime seleccionado ya no está disponible en AniList.")
    return media




class _DescriptionTextParser(HTMLParser):
    """Convert AniList's HTML/markup-like synopsis to safe plain text."""

    _BLOCK_TAGS = {
        "br", "p", "div", "li", "ul", "ol", "blockquote",
        "h1", "h2", "h3", "h4", "h5", "h6", "section",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.casefold()
        if normalized in {"script", "style", "iframe", "object"}:
            self._skip_depth += 1
            return
        if not self._skip_depth and normalized in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in {"script", "style", "iframe", "object"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if not self._skip_depth and normalized in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def _plain_description(value: str | None) -> str:
    raw = str(value or "")[:20000]
    parser = _DescriptionTextParser()
    try:
        parser.feed(raw)
        parser.close()
        text = "".join(parser.parts)
    except Exception:
        text = re.sub(r"<[^>]*>", " ", raw)
    text = unescape(text).replace("\r", "")
    # Keep meaningful paragraph/list breaks while removing formatting syntax.
    text = re.sub(r"\[([^\]]+)\]\((?:https?://[^)]+)\)", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"\*\*(.*?)\*\*|__(.*?)__", lambda m: m.group(1) or m.group(2), text)
    text = re.sub(r"(?<!\w)\*(.*?)\*(?!\w)", r"\1", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:12000]


def _map_anime_metadata(media: dict[str, Any]) -> dict[str, Any]:
    try:
        media_id = int(media.get("id"))
    except (TypeError, ValueError):
        raise ArtworkError("AniList devolvió un identificador de anime no válido.")
    title = media.get("title") or {}
    start_date = media.get("startDate") or {}
    cover = media.get("coverImage") or {}
    banner_url = _valid_anilist_image_url(media.get("bannerImage"))
    cover_url = _valid_anilist_image_url(cover.get("extraLarge") or cover.get("large"))
    site_url = f"https://anilist.co/anime/{media_id}"
    genres = [str(item)[:60] for item in (media.get("genres") or []) if isinstance(item, str)][:20]
    media_format = media.get("format") or "UNKNOWN"
    format_labels = {
        "TV": "Serie de TV",
        "TV_SHORT": "Serie de TV corta",
        "MOVIE": "Película",
        "SPECIAL": "Especial",
        "OVA": "OVA",
        "ONA": "ONA",
        "MUSIC": "Musical",
    }
    country_code = media.get("countryOfOrigin") or None
    country_labels = {
        "JP": "Japón",
        "KR": "Corea del Sur",
        "CN": "China",
        "TW": "Taiwán",
        "US": "Estados Unidos",
    }
    return {
        "anilist_id": media_id,
        "title": title.get("userPreferred") or title.get("english") or title.get("romaji") or title.get("native") or "Título desconocido",
        "title_english": title.get("english") or "",
        "title_romaji": title.get("romaji") or "",
        "title_native": title.get("native") or "",
        "format": media_format,
        "format_label": format_labels.get(media_format, media_format.replace("_", " ").title()),
        "season": media.get("season") or None,
        "year": media.get("seasonYear") or start_date.get("year"),
        "episodes": media.get("episodes"),
        "duration": media.get("duration"),
        "country_of_origin": country_code,
        "country_label": country_labels.get(country_code, country_code),
        "genres": genres,
        "description": _plain_description(media.get("description")),
        "banner_url": banner_url,
        "cover_url": cover_url,
        "site_url": site_url,
        "source": "AniList",
    }


def _remember_anime_metadata(anilist_id: int, metadata: dict[str, Any]) -> None:
    now = time.monotonic()
    with _METADATA_CACHE_LOCK:
        expired = [key for key, (expires, _) in _METADATA_CACHE.items() if expires <= now]
        for key in expired:
            _METADATA_CACHE.pop(key, None)
        _METADATA_CACHE.pop(anilist_id, None)
        while len(_METADATA_CACHE) >= METADATA_CACHE_MAX_ENTRIES:
            oldest_key = min(_METADATA_CACHE, key=lambda key: _METADATA_CACHE[key][0])
            _METADATA_CACHE.pop(oldest_key, None)
        _METADATA_CACHE[anilist_id] = (now + METADATA_CACHE_TTL_SECONDS, deepcopy(metadata))


def _get_cached_anime_metadata(anilist_id: int) -> dict[str, Any]:
    now = time.monotonic()
    with _METADATA_CACHE_LOCK:
        cached = _METADATA_CACHE.get(anilist_id)
        if cached and cached[0] > now:
            return deepcopy(cached[1])
        if cached:
            _METADATA_CACHE.pop(anilist_id, None)

    media = _get_anime_details(anilist_id)
    metadata = _map_anime_metadata(media)
    _remember_anime_metadata(anilist_id, metadata)
    return metadata


def get_series_metadata(series_name: str) -> dict[str, Any]:
    """Fetch the selected AniList profile on demand; never persist catalogue metadata."""
    _ensure_series_exists(series_name)
    with db_session() as conn:
        row = conn.execute(
            "SELECT anilist_id FROM series_anilist_links WHERE series_name=?",
            (series_name,),
        ).fetchone()
    if not row:
        return {"linked": False, "metadata": None, "source": None}
    metadata = _get_cached_anime_metadata(int(row["anilist_id"]))
    return {"linked": True, "metadata": metadata, "source": "AniList"}


def _image_type_and_extension(content_type: str, content: bytes) -> tuple[str, str]:
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized == "image/jpeg" and content.startswith(JPEG_SIGNATURE):
        return normalized, ".jpg"
    if normalized == "image/png" and content.startswith(PNG_SIGNATURE):
        return normalized, ".png"
    if normalized == "image/webp" and len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return normalized, ".webp"
    raise ArtworkError("El archivo no es una imagen JPEG, PNG o WebP válida.")


def _detect_image_type(content: bytes) -> str:
    if content.startswith(JPEG_SIGNATURE):
        return "image/jpeg"
    if content.startswith(PNG_SIGNATURE):
        return "image/png"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    raise ArtworkError("El archivo no es una imagen JPEG, PNG o WebP válida.")


def _download_anilist_image(image_url: str) -> tuple[bytes, str]:
    safe_url = _valid_anilist_image_url(image_url)
    if not safe_url:
        raise ArtworkError("AniList devolvió una URL de imagen no permitida.")
    try:
        response = requests.get(
            safe_url,
            headers={"Accept": "image/jpeg,image/png,image/webp", "User-Agent": "OpEdTagger/1.5.0"},
            timeout=REQUEST_TIMEOUT,
            stream=True,
            allow_redirects=False,
        )
    except requests.Timeout as exc:
        raise ArtworkError("La descarga de la carátula agotó el tiempo de espera.") from exc
    except requests.RequestException as exc:
        raise ArtworkError(f"No se pudo descargar la carátula desde AniList: {exc}") from exc

    with response:
        if 300 <= response.status_code < 400:
            raise ArtworkError("El servidor de imágenes intentó redirigir a una dirección no verificada.")
        if not response.ok:
            raise ArtworkError(f"El servidor de imágenes devolvió HTTP {response.status_code}.")
        try:
            declared_length = int(response.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            declared_length = 0
        if declared_length > MAX_POSTER_BYTES:
            raise ArtworkError("La carátula supera el límite de descarga de 12 MiB.")
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            size += len(chunk)
            if size > MAX_POSTER_BYTES:
                raise ArtworkError("La carátula supera el límite de descarga de 12 MiB.")
            chunks.append(chunk)
        content = b"".join(chunks)
        content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    _image_type_and_extension(content_type, content)
    return content, content_type


def _store_selected_artwork(
    series_name: str,
    content: bytes,
    *,
    provider: str,
    external_id: str | None,
    selected_title: str,
    original_title: str | None = None,
    source_image_url: str | None = None,
) -> dict[str, Any]:
    content_type = _detect_image_type(content)
    content_type, extension = _image_type_and_extension(content_type, content)
    series_digest = hashlib.sha256(series_name.encode("utf-8")).hexdigest()[:24]
    image_digest = hashlib.sha256(content).hexdigest()[:16]
    provider_slug = "anilist" if provider == "anilist" else "custom"
    filename_id = str(external_id) if external_id else "local"
    unique_suffix = uuid.uuid4().hex[:10]
    relative = Path("posters") / provider_slug / f"series_{series_digest}_{filename_id}_{image_digest}_{unique_suffix}{extension}"
    data_root = app_data_dir().resolve()
    poster_root = (data_root / "posters").resolve()
    destination = (data_root / relative).resolve()
    try:
        destination.relative_to(poster_root)
    except ValueError as exc:
        raise ArtworkError("La ruta de almacenamiento de la carátula no es válida.") from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + f".{uuid.uuid4().hex}.tmp")
    old_file = None
    updated_at = utc_now()
    promoted = False
    try:
        temporary.write_bytes(content)
        # Promote the uniquely named image before committing its DB reference.
        # The previous image remains intact until the metadata update succeeds.
        temporary.replace(destination)
        promoted = True
        with db_session() as conn:
            exists = conn.execute("SELECT 1 FROM series WHERE name=?", (series_name,)).fetchone()
            if not exists:
                raise ArtworkError("La serie ya no existe en la biblioteca. Actualiza el catálogo e inténtalo de nuevo.")
            previous = conn.execute(
                "SELECT poster_file FROM series_artwork WHERE series_name=?", (series_name,)
            ).fetchone()
            old_file = previous["poster_file"] if previous else None
            conn.execute(
                "INSERT INTO series_artwork(series_name,provider,external_id,selected_title,original_title,poster_file,source_image_url,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(series_name) DO UPDATE SET "
                "provider=excluded.provider,external_id=excluded.external_id,selected_title=excluded.selected_title,"
                "original_title=excluded.original_title,poster_file=excluded.poster_file,"
                "source_image_url=excluded.source_image_url,updated_at=excluded.updated_at",
                (series_name, provider, external_id, selected_title, original_title or "", relative.as_posix(), source_image_url, updated_at),
            )
            if provider == "anilist" and external_id:
                try:
                    selected_anilist_id = int(external_id)
                except (TypeError, ValueError):
                    raise ArtworkError("El identificador de AniList no es válido.")
                if selected_anilist_id <= 0:
                    raise ArtworkError("El identificador de AniList debe ser positivo.")
                conn.execute(
                    "INSERT INTO series_anilist_links(series_name,anilist_id,linked_at) VALUES(?,?,?) "
                    "ON CONFLICT(series_name) DO UPDATE SET anilist_id=excluded.anilist_id,linked_at=excluded.linked_at",
                    (series_name, selected_anilist_id, updated_at),
                )
    except Exception:
        temporary.unlink(missing_ok=True)
        if promoted:
            destination.unlink(missing_ok=True)
        raise
    else:
        # The DB now points at the new image. Only then remove the superseded
        # app-managed image; never remove files outside the posters directory.
        if old_file and old_file != relative.as_posix():
            old_path = (data_root / old_file).resolve()
            try:
                old_path.relative_to(poster_root)
                old_path.unlink(missing_ok=True)
            except (ValueError, OSError):
                pass

    return {
        "series_name": series_name,
        "provider": provider,
        "external_id": external_id,
        "selected_title": selected_title,
        "poster_url": poster_endpoint_url(series_name, relative.as_posix(), updated_at),
    }


def assign_anilist_poster(series_name: str, anilist_id: int) -> dict[str, Any]:
    _ensure_series_exists(series_name)
    media = _get_anime_details(int(anilist_id))
    title = media.get("title") or {}
    cover = media.get("coverImage") or {}
    image_url = _valid_anilist_image_url(cover.get("extraLarge") or cover.get("large"))
    if not image_url:
        raise ArtworkError("El anime seleccionado no tiene una carátula válida disponible.")
    content, _ = _download_anilist_image(image_url)
    selected_title = title.get("userPreferred") or title.get("english") or title.get("romaji") or title.get("native") or series_name
    original_title = title.get("native") or title.get("romaji") or ""
    result = _store_selected_artwork(
        series_name, content, provider="anilist", external_id=str(anilist_id),
        selected_title=selected_title, original_title=original_title,
        source_image_url=image_url,
    )
    # The details request above already retrieved the selected media profile.
    # Reuse it for the first Series Detail view instead of making an immediate
    # second GraphQL request for the same AniList ID.
    _remember_anime_metadata(int(anilist_id), _map_anime_metadata(media))
    return result


def assign_custom_poster(series_name: str, content: bytes) -> dict[str, Any]:
    _ensure_series_exists(series_name)
    if not content or len(content) > MAX_POSTER_BYTES:
        raise ArtworkError("La imagen debe ocupar entre 1 byte y 12 MiB.")
    content_type = _detect_image_type(content)
    _image_type_and_extension(content_type, content)
    return _store_selected_artwork(
        series_name, content, provider="local", external_id=None,
        selected_title="Carátula personalizada", original_title="",
        source_image_url=None,
    )


def poster_endpoint_url(series_name: str, poster_file: str | None, updated_at: str | None = None) -> str | None:
    if not poster_file:
        return None
    data_root = app_data_dir().resolve()
    poster_root = (data_root / "posters").resolve()
    file_path = (data_root / poster_file).resolve()
    try:
        file_path.relative_to(poster_root)
    except ValueError:
        return None
    if not file_path.is_file():
        return None
    endpoint = f"/api/artwork/series/{quote(series_name, safe='')}/poster"
    # The selected cache file has a unique digest/suffix, so it is a reliable
    # cache-buster even when two poster changes happen in the same second.
    endpoint += f"?v={quote(file_path.name, safe='')}"
    return endpoint


def get_poster_file(series_name: str) -> tuple[Path, str] | None:
    with db_session() as conn:
        row = conn.execute(
            "SELECT poster_file FROM series_artwork WHERE series_name=?", (series_name,)
        ).fetchone()
    if not row or not row["poster_file"]:
        return None
    data_root = app_data_dir().resolve()
    poster_root = (data_root / "posters").resolve()
    file_path = (data_root / row["poster_file"]).resolve()
    try:
        file_path.relative_to(poster_root)
    except ValueError:
        return None
    if not file_path.is_file():
        return None
    media_type = SUPPORTED_IMAGE_SUFFIXES.get(file_path.suffix.lower())
    if not media_type:
        return None
    return file_path, media_type




def remove_series_anilist_link(series_name: str) -> None:
    _ensure_series_exists(series_name)
    with db_session() as conn:
        conn.execute("DELETE FROM series_anilist_links WHERE series_name=?", (series_name,))
        # The locally selected image remains in place; only its external metadata
        # association is removed.
        conn.execute(
            "UPDATE series_artwork SET external_id=NULL WHERE series_name=? AND provider='anilist'",
            (series_name,),
        )


def remove_series_poster(series_name: str) -> None:
    with db_session() as conn:
        row = conn.execute(
            "SELECT poster_file FROM series_artwork WHERE series_name=?", (series_name,)
        ).fetchone()
        conn.execute("DELETE FROM series_artwork WHERE series_name=?", (series_name,))
    if row and row["poster_file"]:
        data_root = app_data_dir().resolve()
        poster_root = (data_root / "posters").resolve()
        file_path = (data_root / row["poster_file"]).resolve()
        try:
            file_path.relative_to(poster_root)
            file_path.unlink(missing_ok=True)
        except (ValueError, OSError):
            pass
