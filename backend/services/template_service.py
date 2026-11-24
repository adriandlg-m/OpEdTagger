from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
import uuid
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
GRAPHQL_URL = "https://rs.animethemes.moe/graphql"
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Origin": "https://animethemes.moe",
    "Referer": "https://animethemes.moe/",
}
SEARCH_QUERY = """
query ($searchTerm: String!) {
  search(search: $searchTerm, first: 20, page: 1) {
    anime { data { slug title { romaji english } themes { type sequence slug entries { videos { nodes { link resolution filename } } } } } }
  }
}
"""


def _request_options() -> dict:
    return {"impersonate": "chrome120"} if _CURL_CFFI_AVAILABLE else {}


def _post_graphql(search_term: str, *, attempts: int = 3) -> dict:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = http.post(
                GRAPHQL_URL,
                json={"query": SEARCH_QUERY, "variables": {"searchTerm": search_term}},
                headers=HEADERS,
                timeout=45,
                **_request_options(),
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("errors"):
                raise RuntimeError(
                    "AnimeThemes returned GraphQL errors: "
                    + "; ".join(error.get("message", "Unknown error") for error in payload["errors"])
                )
            return payload
        except Exception as exc:
            last_error = exc
            logger.warning("AnimeThemes request attempt %s/%s failed: %s", attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"AnimeThemes could not be reached after {attempts} attempts: {last_error}") from last_error


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
    data = payload.get("data", {}).get("search", {}).get("anime", {}).get("data", []) or []
    result = []
    for anime in data:
        title = anime.get("title") or {}
        themes = anime.get("themes") or []
        result.append({
            "slug": anime.get("slug"),
            "title_romaji": title.get("romaji"),
            "title_english": title.get("english"),
            "opening_count": sum(1 for theme in themes if theme.get("type") == "OP"),
            "ending_count": sum(1 for theme in themes if theme.get("type") == "ED"),
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


def _download(url: str, target: Path, timeout: int = 90, attempts: int = 3) -> None:
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
                timeout=timeout,
                **_request_options(),
            ) as response:
                if response.status_code == 206 and current_size:
                    content_range = response.headers.get("Content-Range", "")
                    if content_range and not content_range.startswith(f"bytes {current_size}-"):
                        part.unlink(missing_ok=True)
                        raise RuntimeError("AnimeThemes returned an inconsistent resume offset.")
                    mode = "ab"
                elif response.status_code == 200:
                    mode = "wb"
                elif response.status_code == 416 and part.exists():
                    content_range = response.headers.get("Content-Range", "")
                    total = content_range.rsplit("/", 1)[-1] if "/" in content_range else ""
                    if total.isdigit() and int(total) == part.stat().st_size:
                        part.replace(target)
                        return
                    part.unlink(missing_ok=True)
                    raise RuntimeError("The partial download could not be resumed; restarting it.")
                else:
                    response.raise_for_status()
                    mode = "wb"

                with part.open(mode) as stream:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            stream.write(chunk)

            if not part.exists() or part.stat().st_size == 0:
                raise RuntimeError(f"Downloaded template is empty: {target.name}")
            part.replace(target)
            return
        except Exception as exc:
            last_error = exc
            logger.warning("AnimeThemes download attempt %s/%s failed for %s: %s", attempt, attempts, target.name, exc)
            if attempt < attempts:
                time.sleep(min(2 ** attempt, 8))

    raise RuntimeError(f"Could not download template '{target.name}' after {attempts} attempts: {last_error}") from last_error


def _download_anime_rows_templates_impl(
    series_name: str,
    anime_rows: list[dict],
    progress: Callable[[int, int, str], None] | None = None,
) -> dict:
    """Synchronize OP/ED videos for all CLI-matched AnimeThemes results."""
    if not anime_rows:
        raise RuntimeError(f"No AnimeThemes entries were available for '{series_name}'.")

    primary = anime_rows[0]
    slugs = [str(item.get("slug")) for item in anime_rows if item.get("slug")]
    tasks_by_kind: dict[str, list[tuple[str, str]]] = {"OP": [], "ED": []}
    for anime in anime_rows:
        for theme in anime.get("themes", []) or []:
            kind = theme.get("type")
            if kind not in {"OP", "ED"}:
                continue
            best = None
            for entry in theme.get("entries", []) or []:
                for video in (entry.get("videos") or {}).get("nodes", []) or []:
                    link = video.get("link")
                    if not link:
                        continue
                    filename = video.get("filename") or Path(urlparse(link).path).name
                    try:
                        resolution = int(video.get("resolution") or 0)
                    except (TypeError, ValueError):
                        resolution = 0
                    is_nc = "NC" in filename.upper()
                    score = (10000 if is_nc else 0) + resolution
                    if best is None or score > best[0]:
                        best = (score, str(link), str(filename))
            if best:
                tasks_by_kind[kind].append((best[1], best[2]))

    # The CLI grouped all OPs first, then all EDs, numbering across every
    # matched anime rather than reusing each anime's theme sequence number.
    tasks = [
        (kind, sequence, link, filename)
        for kind in ("OP", "ED")
        for sequence, (link, filename) in enumerate(tasks_by_kind[kind], start=1)
    ]
    if not tasks:
        raise RuntimeError(f"No OP/ED video templates were found for '{series_name}'.")

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

    # Keep hand-supplied WAVs and the source fingerprints of converted WAVs.
    old_songs_dir = target_dir / "songs_files"
    if old_songs_dir.is_dir():
        shutil.copytree(old_songs_dir, songs_dir, dirs_exist_ok=True)

    successes: list[str] = []
    video_sources: dict[str, str] = {}
    try:
        safe_name = re.sub(r"[^A-Za-z0-9._ -]+", "_", series_name).strip(" .") or "anime"
        for index, (kind, sequence, url, filename) in enumerate(tasks, 1):
            suffix = Path(filename).suffix.lower() if filename else Path(urlparse(url).path).suffix.lower()
            if not suffix or len(suffix) > 8 or not re.fullmatch(r"\.[a-z0-9]{1,7}", suffix):
                suffix = ".webm"
            target = video_dir / f"{safe_name} - {kind}{sequence}{suffix}"
            previous_video = target_dir / "video_files" / target.name
            if (
                old_sources.get(target.name) == url
                and previous_video.is_file()
                and previous_video.stat().st_size > 0
            ):
                shutil.copy2(previous_video, target)
            else:
                _download(url, target)
            successes.append(str(target))
            video_sources[target.name] = url
            if progress:
                progress(index, len(tasks), target.name)

        # Convert as part of the synchronization flow, before a series analysis
        # can pass any WAV to the detector. Preserve the source videos if conversion
        # fails so a later Analyze attempt can retry after fixing FFmpeg.
        song_templates = []
        audio_preparation_error = None
        audio_preparation_warning = None
        try:
            cfg = load_config()
            song_templates = prepare_song_templates(
                staging,
                Path(cfg["plugins_path"]).expanduser().resolve(),
                on_template_progress=progress,
            )
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
                f"Only {converted_count} of {len(successes)} downloaded video templates "
                "were converted to WAV. Check FFmpeg and the application logs."
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
    progress: Callable[[int, int, str], None] | None = None,
) -> dict:
    payload = _post_graphql(slug)
    rows = payload.get("data", {}).get("search", {}).get("anime", {}).get("data", []) or []
    anime_rows = [anime for anime in rows if anime.get("slug") == slug]
    if len(anime_rows) != 1:
        raise ValueError("AnimeThemes did not return one unambiguous anime for the selected slug.")
    return _download_anime_rows_templates_impl(series_name, anime_rows, progress)


def download_selected_templates(
    series_name: str,
    slug: str,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict:
    with _TEMPLATE_LOCK:
        target_dir = _safe_series_template_dir(series_name)
        _recover_template_swap(target_dir)
        return _download_selected_templates_impl(series_name, slug, progress)


def _ensure_templates_impl(
    series_name: str,
    selected_slug: str | None = None,
    progress: Callable[[int, int, str], None] | None = None,
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
    wav_files = [
        item for item in wav_dir.glob("*.wav")
        if item.is_file() and item.stat().st_size > 44
    ] if wav_dir.exists() else []
    has_templates = bool(video_files or wav_files)

    if selected_slug:
        download_selected_templates(series_name, selected_slug, progress)
        return series_dir

    # Preserve user-managed local templates. AnimeThemes-managed templates,
    # however, are synchronized on every Analyze action like the original CLI.
    if has_templates and manifest_provider != "AnimeThemes":
        return series_dir

    query = sanitize_query_from_folder(series_name)
    if not query:
        raise RuntimeError(f"The series folder name '{series_name}' produces an empty AnimeThemes search term.")
    payload = _post_graphql(query)
    anime_rows = payload.get("data", {}).get("search", {}).get("anime", {}).get("data", []) or []
    if not anime_rows:
        raise RuntimeError(
            f"No AnimeThemes templates were found for '{series_name}' "
            f"(search term: '{query}'). Check the series folder name or AnimeThemes connectivity."
        )

    # Match the CLI exactly: all slug/romaji partial matches are synchronized;
    # if none match, the API's result list is used as the fallback.
    matched_animes = _select_cli_matches(series_name, query, anime_rows)
    result = _download_anime_rows_templates_impl(series_name, matched_animes, progress)
    if result.get("audio_preparation_error"):
        raise RuntimeError(
            f"AnimeThemes videos were downloaded but WAV preparation failed: {result['audio_preparation_error']}"
        )
    if result.get("audio_preparation_warning"):
        raise RuntimeError(result["audio_preparation_warning"])
    return series_dir


def ensure_templates(
    series_name: str,
    selected_slug: str | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> Path:
    with _TEMPLATE_LOCK:
        target_dir = _safe_series_template_dir(series_name)
        _recover_template_swap(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "video_files").mkdir(parents=True, exist_ok=True)
        (target_dir / "songs_files").mkdir(parents=True, exist_ok=True)
        return _ensure_templates_impl(series_name, selected_slug, progress)
