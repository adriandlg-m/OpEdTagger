from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
import uuid
import wave
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

try:
    from curl_cffi import requests as http
    _CURL_CFFI_AVAILABLE = True
except ImportError:  # Fallback keeps the API usable until Python dependencies are refreshed.
    import requests as http
    _CURL_CFFI_AVAILABLE = False

from backend.infrastructure.config import load_config
from core.analyzer import prepare_song_templates

logger = logging.getLogger(__name__)
_TEMPLATE_LOCK = threading.RLock()
GRAPHQL_URL = "https://graphql.animethemes.moe/"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}
SEARCH_QUERY = """
query ($q: String!, $first: Int!) {
  search(search: $q, first: $first) {
    anime { id slug year season title { romaji english native } }
  }
}
"""
THEMES_QUERY = """
query ($slug: String!) {
  anime(slug: $slug) {
    id
    slug
    title { romaji english native }
    animethemes {
      id
      slug
      type
      sequence
      song { title { romaji native } }
      animethemeentries {
        episodes
        videos { nodes { basename link resolution nc } }
      }
    }
  }
}
"""


def _request_options() -> dict:
    # AnimeThemes is behind Cloudflare; keep the same browser impersonation used
    # by the CLI, but target the current public GraphQL host.
    return {"impersonate": "chrome120"} if _CURL_CFFI_AVAILABLE else {}


def _report_progress(
    callback: Callable[[str, float, str], None] | None,
    stage: str,
    percentage: float,
    item: str,
) -> None:
    if callback:
        callback(stage, max(0.0, min(100.0, float(percentage))), item)


def _graphql_request(
    query: str,
    variables: dict,
    *,
    attempts: int = 2,
    progress: Callable[[str, float, str], None] | None = None,
    stage: str = "searching_templates",
    item: str = "AnimeThemes",
) -> dict:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        _report_progress(progress, stage, 1.0, f"{item} · consulta {attempt}/{attempts}")
        try:
            response = http.post(
                GRAPHQL_URL,
                json={"query": query, "variables": variables},
                headers=HEADERS,
                timeout=(10, 20),
                **_request_options(),
            )
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "")
                try:
                    delay = min(max(float(retry_after), 1.0), 5.0)
                except (TypeError, ValueError):
                    delay = min(float(attempt), 3.0)
                raise RuntimeError(f"AnimeThemes limitó temporalmente la consulta (HTTP 429). Reintento en {delay:g}s.")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("AnimeThemes devolvió una respuesta GraphQL con formato inesperado.")
            if payload.get("errors"):
                details = "; ".join(
                    str(error.get("message") or "Error GraphQL desconocido")
                    for error in payload["errors"]
                )
                raise RuntimeError(f"AnimeThemes rechazó la consulta GraphQL: {details}")
            data = payload.get("data")
            if not isinstance(data, dict):
                raise RuntimeError("AnimeThemes respondió sin el objeto 'data' de GraphQL.")
            return payload
        except Exception as exc:
            last_error = exc
            logger.warning(
                "AnimeThemes GraphQL attempt %s/%s failed (%s): %s",
                attempt,
                attempts,
                item,
                exc,
            )
            if attempt < attempts:
                time.sleep(min(float(attempt), 2.0))

    raise RuntimeError(
        f"No se pudo consultar AnimeThemes ({item}) después de {attempts} intentos: {last_error}"
    ) from last_error


def _post_graphql(
    search_term: str,
    *,
    attempts: int = 2,
    progress: Callable[[str, float, str], None] | None = None,
) -> dict:
    """Search AnimeThemes using the current GraphQL schema."""
    return _graphql_request(
        SEARCH_QUERY,
        {"q": search_term, "first": 20},
        attempts=attempts,
        progress=progress,
        stage="searching_templates",
        item=f"Buscando «{search_term}» en AnimeThemes",
    )


def _fetch_anime_details(
    slug: str,
    *,
    attempts: int = 2,
    progress: Callable[[str, float, str], None] | None = None,
    percentage: float = 3.0,
) -> dict:
    return _graphql_request(
        THEMES_QUERY,
        {"slug": slug},
        attempts=attempts,
        progress=progress,
        stage="loading_template_metadata",
        item=f"Obteniendo OP/ED de {slug}",
    )


def sanitize_query_from_folder(folder_name: str) -> str:
    """Mirror the CLI's AnimeThemes query cleanup for local folder names."""
    clean = re.sub(r"\(\d{4}\)", "", folder_name)
    clean = re.sub(r"\[.*?\]", "", clean)
    return clean.strip()


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", re.sub(r"\(\d{4}\)|\[.*?\]", "", value.lower()))


def _select_cli_matches(series_name: str, query_term: str, anime_rows: list[dict]) -> list[dict]:
    """Match the original CLI: keep every slug/romaji partial match, or all API results as fallback."""
    query_clean = query_term.replace(" ", "").lower()
    matched = [
        anime for anime in anime_rows
        if query_clean and (
            query_clean in str(anime.get("slug") or "").lower()
            or query_clean in str((anime.get("title") or {}).get("romaji") or "").replace(" ", "").lower()
        )
    ]
    return matched or anime_rows


def _valid_wav_files(directory: Path) -> list[Path]:
    valid = []
    if not directory.is_dir():
        return valid
    for path in sorted(directory.glob("*.wav"), key=lambda item: item.name.lower()):
        try:
            if path.stat().st_size <= 44:
                continue
            with wave.open(str(path), "rb") as audio:
                if audio.getnframes() > 0 and audio.getframerate() > 0 and audio.getnchannels() > 0:
                    valid.append(path)
        except (OSError, EOFError, wave.Error):
            continue
    return valid


def _safe_series_template_dir(series_name: str) -> Path:
    name = (series_name or "").strip()
    if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError("Invalid series name for template storage.")
    root = Path(load_config()["template_path"]).expanduser().resolve()
    target = (root / name).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("Template directory must remain inside template_path.") from exc
    return target


def _recover_template_swap(target_dir: Path) -> None:
    """Recover a previous template folder if the process stopped mid-swap."""
    parent = target_dir.parent
    if not parent.exists():
        return
    previous_dirs = sorted(
        parent.glob(f".{target_dir.name}.previous-*"),
        key=lambda item: item.stat().st_mtime if item.exists() else 0,
        reverse=True,
    )
    staging_dirs = list(parent.glob(f".{target_dir.name}.staging-*"))
    if not target_dir.exists() and previous_dirs:
        previous_dirs[0].replace(target_dir)
        previous_dirs = previous_dirs[1:]
    if target_dir.exists():
        for path in previous_dirs:
            shutil.rmtree(path, ignore_errors=True)
    for path in staging_dirs:
        shutil.rmtree(path, ignore_errors=True)


def search_candidates(query: str) -> list[dict]:
    payload = _post_graphql(query)
    data = payload.get("data", {}).get("search", {}).get("anime", []) or []
    result = []
    for anime in data:
        title = anime.get("title") or {}
        result.append({
            "slug": anime.get("slug"),
            "title_romaji": title.get("romaji"),
            "title_english": title.get("english"),
            "opening_count": None,
            "ending_count": None,
        })
    return result


def _choose_exact_candidate(series_name: str, candidates: list[dict]) -> dict | None:
    wanted = normalize(series_name)
    exact = [
        candidate for candidate in candidates
        if wanted and wanted in {
            normalize(candidate.get("slug") or ""),
            normalize(candidate.get("title_romaji") or ""),
            normalize(candidate.get("title_english") or ""),
        }
    ]
    return exact[0] if len(exact) == 1 else None


def _format_bytes(value: int | None) -> str:
    if value is None or value < 0:
        return "tamaño desconocido"
    units = ("B", "KB", "MB", "GB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _download(
    url: str,
    target: Path,
    timeout: int = 30,
    attempts: int = 2,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("AnimeThemes returned a non-HTTPS media URL; refusing download.")
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            current_size = part.stat().st_size if part.exists() else 0
            headers = dict(HEADERS)
            if current_size:
                headers["Range"] = f"bytes={current_size}-"

            with http.get(
                url,
                headers=headers,
                stream=True,
                timeout=(10, timeout),
                **_request_options(),
            ) as response:
                if response.status_code == 206 and current_size:
                    content_range = response.headers.get("Content-Range", "")
                    if content_range and not content_range.startswith(f"bytes {current_size}-"):
                        part.unlink(missing_ok=True)
                        raise RuntimeError("AnimeThemes returned an inconsistent resume offset.")
                    mode = "ab"
                    downloaded = current_size
                    total = None
                    if "/" in content_range and content_range.rsplit("/", 1)[-1].isdigit():
                        total = int(content_range.rsplit("/", 1)[-1])
                elif response.status_code == 200:
                    mode = "wb"
                    downloaded = 0
                    current_size = 0
                    raw_length = response.headers.get("Content-Length")
                    total = int(raw_length) if raw_length and raw_length.isdigit() else None
                elif response.status_code == 416 and part.exists():
                    content_range = response.headers.get("Content-Range", "")
                    total_text = content_range.rsplit("/", 1)[-1] if "/" in content_range else ""
                    if total_text.isdigit() and int(total_text) == part.stat().st_size:
                        part.replace(target)
                        if on_progress:
                            on_progress(target.stat().st_size, target.stat().st_size)
                        return
                    part.unlink(missing_ok=True)
                    raise RuntimeError("The partial download could not be resumed; restarting it.")
                else:
                    response.raise_for_status()
                    mode = "wb"
                    downloaded = 0
                    current_size = 0
                    raw_length = response.headers.get("Content-Length")
                    total = int(raw_length) if raw_length and raw_length.isdigit() else None

                if total is None:
                    raw_length = response.headers.get("Content-Length")
                    if raw_length and raw_length.isdigit():
                        total = current_size + int(raw_length)

                if on_progress:
                    on_progress(downloaded, total)

                last_reported = downloaded
                last_report_time = time.monotonic()
                with part.open(mode) as stream:
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if not chunk:
                            continue
                        stream.write(chunk)
                        downloaded += len(chunk)
                        now = time.monotonic()
                        # Avoid writing dozens of SQLite updates per second.
                        if on_progress and (
                            downloaded - last_reported >= 512 * 1024
                            or now - last_report_time >= 0.4
                        ):
                            on_progress(downloaded, total)
                            last_reported = downloaded
                            last_report_time = now

            if not part.exists() or part.stat().st_size == 0:
                raise RuntimeError(f"Downloaded template is empty: {target.name}")
            part.replace(target)
            if on_progress:
                final_size = target.stat().st_size
                on_progress(final_size, total or final_size)
            return
        except InterruptedError:
            # Keep the partial file so a user-cancelled operation can resume later.
            raise
        except Exception as exc:
            last_error = exc
            logger.warning(
                "AnimeThemes download attempt %s/%s failed for %s: %s",
                attempt,
                attempts,
                target.name,
                exc,
            )
            if attempt < attempts:
                time.sleep(min(float(attempt), 2.0))

    raise RuntimeError(
        f"Could not download template '{target.name}' after {attempts} attempts: {last_error}"
    ) from last_error


def _download_anime_rows_templates_impl(
    series_name: str,
    anime_rows: list[dict],
    progress: Callable[[str, float, str], None] | None = None,
) -> dict:
    """Synchronize OP/ED videos returned by the current AnimeThemes GraphQL API."""
    if not anime_rows:
        raise RuntimeError(f"AnimeThemes no devolvió series para '{series_name}'.")

    primary = anime_rows[0]
    slugs = [str(item.get("slug")) for item in anime_rows if item.get("slug")]
    tasks_by_kind: dict[str, list[tuple[str, str]]] = {"OP": [], "ED": []}
    for anime in anime_rows:
        # Current GraphQL schema calls these relationships animethemes and
        # animethemeentries (the old rs endpoint used themes and entries).
        for theme in anime.get("animethemes", []) or []:
            kind = theme.get("type")
            if kind not in {"OP", "ED"}:
                continue
            best = None
            for entry in theme.get("animethemeentries", []) or []:
                for video in (entry.get("videos") or {}).get("nodes", []) or []:
                    link = video.get("link")
                    if not link:
                        continue
                    basename = video.get("basename") or Path(urlparse(link).path).name
                    try:
                        resolution = int(video.get("resolution") or 0)
                    except (TypeError, ValueError):
                        resolution = 0
                    is_nc = bool(video.get("nc")) or "NC" in basename.upper()
                    score = (10000 if is_nc else 0) + resolution
                    if best is None or score > best[0]:
                        best = (score, str(link), str(basename))
            if best:
                tasks_by_kind[kind].append((best[1], best[2]))

    # Preserve CLI ordering: all OPs first, followed by all EDs, numbered
    # sequentially across every AnimeThemes result that matched the folder.
    tasks = [
        (kind, sequence, link, basename)
        for kind in ("OP", "ED")
        for sequence, (link, basename) in enumerate(tasks_by_kind[kind], start=1)
    ]
    if not tasks:
        raise RuntimeError(
            f"AnimeThemes encontró el anime, pero no devolvió vídeos OP/ED utilizables para '{series_name}'."
        )

    target_dir = _safe_series_template_dir(series_name)
    template_root = target_dir.parent
    template_root.mkdir(parents=True, exist_ok=True)
    old_manifest: dict = {}
    manifest_path = target_dir / "template_manifest.json"
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        old_manifest = value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        old_manifest = {}
    old_sources = old_manifest.get("video_sources")
    old_sources = old_sources if isinstance(old_sources, dict) else {}

    staging = template_root / f".{target_dir.name}.staging-{uuid.uuid4().hex[:10]}"
    video_dir = staging / "video_files"
    songs_dir = staging / "songs_files"
    video_dir.mkdir(parents=True, exist_ok=True)
    songs_dir.mkdir(parents=True, exist_ok=True)

    # Keep manual WAVs and the existing source fingerprints for derived WAVs.
    old_songs_dir = target_dir / "songs_files"
    if old_songs_dir.is_dir():
        shutil.copytree(old_songs_dir, songs_dir, dirs_exist_ok=True)

    successes: list[str] = []
    video_sources: dict[str, str] = {}
    total_tasks = len(tasks)
    try:
        safe_name = re.sub(r"[^A-Za-z0-9._ -]+", "_", series_name).strip(" .") or "anime"
        for index, (kind, sequence, url, basename) in enumerate(tasks, 1):
            suffix = Path(basename).suffix.lower() if basename else Path(urlparse(url).path).suffix.lower()
            if not suffix or len(suffix) > 8 or not re.fullmatch(r"\.[a-z0-9]{1,7}", suffix):
                suffix = ".webm"
            target = video_dir / f"{safe_name} - {kind}{sequence}{suffix}"
            previous_video = target_dir / "video_files" / target.name
            label = f"{'Opening' if kind == 'OP' else 'Ending'} {kind}{sequence} · {index}/{total_tasks}"

            if (
                old_sources.get(target.name) == url
                and previous_video.is_file()
                and previous_video.stat().st_size > 0
            ):
                shutil.copy2(previous_video, target)
                copied_size = target.stat().st_size
                _report_progress(
                    progress,
                    "downloading_templates",
                    5.0 + (index / total_tasks) * 65.0,
                    f"{label} · caché local ({_format_bytes(copied_size)})",
                )
            else:
                _report_progress(
                    progress,
                    "downloading_templates",
                    5.0 + ((index - 1) / total_tasks) * 65.0,
                    f"Descargando {label}",
                )

                def byte_progress(downloaded: int, total: int | None) -> None:
                    fraction = min(1.0, downloaded / total) if total and total > 0 else 0.0
                    overall = 5.0 + ((index - 1 + fraction) / total_tasks) * 65.0
                    size_label = _format_bytes(downloaded)
                    if total and total > 0:
                        size_label += f" / {_format_bytes(total)}"
                    _report_progress(
                        progress,
                        "downloading_templates",
                        overall,
                        f"Descargando {label} · {size_label}",
                    )

                _download(url, target, on_progress=byte_progress)

            successes.append(str(target))
            video_sources[target.name] = url
            _report_progress(
                progress,
                "downloading_templates",
                5.0 + (index / total_tasks) * 65.0,
                f"{label} · descarga lista",
            )

        # Downloaded OP/ED videos are converted before the series detector runs.
        song_templates: list[Path] = []
        audio_preparation_error = None
        audio_preparation_warning = None
        try:
            cfg = load_config()

            def conversion_progress(index: int, total: int, name: str) -> None:
                percentage = 72.0 + ((index + 1) / max(total, 1)) * 18.0
                _report_progress(progress, "converting_templates", percentage, f"Convirtiendo a WAV · {name}")

            _report_progress(progress, "converting_templates", 72.0, "Preparando referencias WAV con FFmpeg")
            song_templates = prepare_song_templates(
                staging,
                Path(cfg["plugins_path"]).expanduser().resolve(),
                on_template_progress=conversion_progress,
            )
            _report_progress(progress, "converting_templates", 90.0, "Conversión de WAV finalizada")
        except InterruptedError:
            raise
        except Exception as exc:
            audio_preparation_error = str(exc)
            logger.exception("Could not prepare WAV templates for %s.", series_name)

        conversion_manifest = staging / "songs_files" / ".template_wav_manifest.json"
        try:
            converted_sources = json.loads(conversion_manifest.read_text(encoding="utf-8"))
            converted_count = len(converted_sources) if isinstance(converted_sources, dict) else 0
        except (OSError, ValueError):
            converted_count = 0
        if audio_preparation_error is None and converted_count < len(successes):
            audio_preparation_warning = (
                f"Solo se convirtieron {converted_count} de {len(successes)} vídeos OP/ED a WAV. "
                "Comprueba FFmpeg y los logs de la aplicación."
            )
            logger.warning("%s (%s)", audio_preparation_warning, series_name)

        manifest = {
            "provider": "AnimeThemes",
            "anime_slug": primary.get("slug"),
            "anime_slugs": slugs,
            "title_romaji": (primary.get("title") or {}).get("romaji"),
            "title_english": (primary.get("title") or {}).get("english"),
            "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "video_files": [Path(path).name for path in successes],
            "video_sources": video_sources,
        }
        (staging / "template_manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        previous = None
        if target_dir.exists():
            previous = template_root / f".{target_dir.name}.previous-{uuid.uuid4().hex[:10]}"
            target_dir.replace(previous)
        try:
            staging.replace(target_dir)
        except Exception:
            if previous and previous.exists() and not target_dir.exists():
                previous.replace(target_dir)
            raise
        if previous and previous.exists():
            shutil.rmtree(previous, ignore_errors=True)

        return {
            "series_name": series_name,
            "anime_slug": primary.get("slug"),
            "anime_slugs": slugs,
            "downloaded": len(successes),
            "files": [str(target_dir / "video_files" / Path(path).name) for path in successes],
            "video_files_dir": str(target_dir / "video_files"),
            "songs_files_dir": str(target_dir / "songs_files"),
            "song_templates": len(song_templates),
            "audio_preparation_error": audio_preparation_error,
            "audio_preparation_warning": audio_preparation_warning,
            "manifest": str(target_dir / "template_manifest.json"),
        }
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise



def _download_selected_templates_impl(
    series_name: str,
    slug: str,
    progress: Callable[[str, float, str], None] | None = None,
) -> dict:
    payload = _fetch_anime_details(slug, progress=progress)
    anime = payload.get("data", {}).get("anime")
    if not isinstance(anime, dict) or anime.get("slug") != slug:
        raise ValueError(f"AnimeThemes no devolvió el anime solicitado para el slug '{slug}'.")
    return _download_anime_rows_templates_impl(series_name, [anime], progress)



def download_selected_templates(
    series_name: str,
    slug: str,
    progress: Callable[[str, float, str], None] | None = None,
) -> dict:
    with _TEMPLATE_LOCK:
        target_dir = _safe_series_template_dir(series_name)
        _recover_template_swap(target_dir)
        return _download_selected_templates_impl(series_name, slug, progress)


def _ensure_templates_impl(
    series_name: str,
    selected_slug: str | None = None,
    progress: Callable[[str, float, str], None] | None = None,
) -> Path:
    series_dir = _safe_series_template_dir(series_name)
    video_dir = series_dir / "video_files"
    wav_dir = series_dir / "songs_files"
    manifest_path = series_dir / "template_manifest.json"
    manifest_provider = None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        manifest_provider = manifest.get("provider") if isinstance(manifest, dict) else None
    except (OSError, ValueError, AttributeError):
        manifest = {}

    video_files = [
        item for item in video_dir.glob("*")
        if item.is_file() and item.suffix.lower() in {".mkv", ".mp4", ".ts", ".avi", ".mov", ".webm"}
        and item.stat().st_size > 0
    ] if video_dir.exists() else []
    wav_files = _valid_wav_files(wav_dir)
    has_templates = bool(video_files or wav_files)

    if selected_slug:
        result = download_selected_templates(series_name, selected_slug, progress)
        if result.get("audio_preparation_error"):
            raise RuntimeError(f"No se pudieron preparar los WAV: {result['audio_preparation_error']}")
        if result.get("audio_preparation_warning"):
            raise RuntimeError(result["audio_preparation_warning"])
        return series_dir

    # Preserve manually supplied WAV-only template sets. Legacy CLI folders
    # with videos and no manifest still require automatic AnimeThemes sync.
    user_managed_wavs_only = bool(wav_files) and not video_files and manifest_provider != "AnimeThemes"
    if user_managed_wavs_only:
        _report_progress(progress, "validating_templates", 100.0, f"Usando {len(wav_files)} WAV manuales")
        return series_dir
    if manifest_provider not in {None, "AnimeThemes"} and has_templates:
        _report_progress(progress, "validating_templates", 100.0, "Usando templates locales")
        return series_dir

    query = sanitize_query_from_folder(series_name)
    if not query:
        raise RuntimeError(f"The series folder name '{series_name}' produces an empty AnimeThemes search term.")

    try:
        _report_progress(progress, "searching_templates", 1.0, f"Buscando «{query}» en AnimeThemes")
        payload = _post_graphql(query, progress=progress)
        anime_rows = payload.get("data", {}).get("search", {}).get("anime", []) or []
        if not isinstance(anime_rows, list):
            raise RuntimeError("AnimeThemes devolvió un listado de animes con formato inesperado.")
        if not anime_rows:
            if has_templates:
                logger.warning(
                    "AnimeThemes returned no results for %s; reusing existing local templates.",
                    series_name,
                )
                _report_progress(progress, "validating_templates", 100.0, "Reutilizando templates locales")
                return series_dir
            raise RuntimeError(
                f"AnimeThemes no encontró resultados para «{query}» (serie local: '{series_name}')."
            )

        # Preserve the former CLI matching semantics: all partial slug/Romaji
        # matches are used; if none match, fall back to all search results.
        matched_animes = _select_cli_matches(series_name, query, anime_rows)
        matched_animes = [anime for anime in matched_animes if anime.get("slug")]
        if not matched_animes:
            raise RuntimeError(f"AnimeThemes devolvió resultados sin slugs utilizables para '{series_name}'.")

        detailed_rows = []
        total_matches = len(matched_animes)
        for index, anime in enumerate(matched_animes, 1):
            slug = str(anime["slug"])
            percentage = 2.0 + (index - 1) / max(total_matches, 1) * 3.0
            _report_progress(
                progress,
                "loading_template_metadata",
                percentage,
                f"Obteniendo metadatos OP/ED · {anime.get('title', {}).get('romaji') or slug} ({index}/{total_matches})",
            )
            details = _fetch_anime_details(slug, progress=progress)
            detailed = details.get("data", {}).get("anime")
            if not isinstance(detailed, dict):
                raise RuntimeError(f"AnimeThemes no devolvió metadatos OP/ED para '{slug}'.")
            detailed_rows.append(detailed)

        result = _download_anime_rows_templates_impl(series_name, detailed_rows, progress)
    except InterruptedError:
        raise
    except Exception as exc:
        if has_templates:
            logger.warning(
                "AnimeThemes synchronization failed for %s; reusing existing local templates: %s",
                series_name,
                exc,
            )
            _report_progress(progress, "validating_templates", 100.0, "Reutilizando templates locales por fallo de conexión")
            return series_dir
        raise

    if result.get("audio_preparation_error"):
        raise RuntimeError(f"Los vídeos se descargaron, pero falló la conversión WAV: {result['audio_preparation_error']}")
    if result.get("audio_preparation_warning"):
        raise RuntimeError(result["audio_preparation_warning"])
    _report_progress(progress, "validating_templates", 95.0, "Comprobando que los WAV sean legibles")
    valid_wavs = _valid_wav_files(series_dir / "songs_files")
    if not valid_wavs:
        raise RuntimeError(
            f"AnimeThemes descargó los vídeos de '{series_name}', pero no quedaron plantillas WAV válidas."
        )
    _report_progress(
        progress,
        "validating_templates",
        100.0,
        f"Templates listos: {len(result.get('files') or [])} vídeos y {len(valid_wavs)} WAV",
    )
    return series_dir


def ensure_templates(
    series_name: str,
    selected_slug: str | None = None,
    progress: Callable[[str, float, str], None] | None = None,
) -> Path:
    with _TEMPLATE_LOCK:
        target_dir = _safe_series_template_dir(series_name)
        _recover_template_swap(target_dir)
        # Do not create empty video_files/songs_files folders before the API
        # response has yielded usable OP/ED media. Failures must remain visible.
        return _ensure_templates_impl(series_name, selected_slug, progress)
