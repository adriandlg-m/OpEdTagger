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


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", re.sub(r"\(\d{4}\)|\[.*?\]", "", value.lower()))


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


def _download_selected_templates_impl(
    series_name: str,
    slug: str,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict:
    candidates = search_candidates(slug)
    matches = [candidate for candidate in candidates if candidate.get("slug") == slug]
    if len(matches) != 1:
        raise ValueError("The selected AnimeThemes slug could not be verified. Search again and select a valid result.")
    payload = _post_graphql(slug)
    data = payload.get("data", {}).get("search", {}).get("anime", {}).get("data", []) or []
    anime_rows = [anime for anime in data if anime.get("slug") == slug]
    if len(anime_rows) != 1:
        raise ValueError("AnimeThemes did not return one unambiguous anime for the selected slug.")
    anime = anime_rows[0]

    target_dir = _safe_series_template_dir(series_name)
    template_root = target_dir.parent
    template_root.mkdir(parents=True, exist_ok=True)
    staging = template_root / f".{target_dir.name}.staging-{uuid.uuid4().hex[:10]}"
    video_dir = staging / "video_files"
    songs_dir = staging / "songs_files"
    video_dir.mkdir(parents=True, exist_ok=True)
    songs_dir.mkdir(parents=True, exist_ok=True)

    # Keep user-supplied WAVs and the prior conversion manifest. The analyzer
    # compares video fingerprints and regenerates only WAVs whose source changed.
    old_songs_dir = target_dir / "songs_files"
    if old_songs_dir.is_dir():
        shutil.copytree(old_songs_dir, songs_dir, dirs_exist_ok=True)

    tasks = []
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
                filename = video.get("filename") or ""
                try:
                    resolution = int(video.get("resolution") or 0)
                except (TypeError, ValueError):
                    resolution = 0
                score = (10000 if "NC" in filename.upper() else 0) + resolution
                if best is None or score > best[0]:
                    best = (score, link, filename)
        if best:
            tasks.append((kind, int(theme.get("sequence") or 1), best[1], best[2]))

    unique = {}
    for task in tasks:
        unique[(task[0], task[1])] = task
    tasks = sorted(unique.values(), key=lambda item: (item[0], item[1]))
    if not tasks:
        shutil.rmtree(staging, ignore_errors=True)
        raise RuntimeError(f"No OP/ED video templates were found for '{series_name}'.")

    successes = []
    try:
        for index, (kind, sequence, url, filename) in enumerate(tasks, 1):
            suffix = Path(filename).suffix.lower() if filename else Path(urlparse(url).path).suffix.lower()
            if not suffix or len(suffix) > 8 or not re.fullmatch(r"\.[a-z0-9]{1,7}", suffix):
                suffix = ".webm"
            safe_name = re.sub(r"[^A-Za-z0-9._ -]+", "_", series_name).strip(" .") or "anime"
            target = video_dir / f"{safe_name} - {kind}{sequence}{suffix}"
            _download(url, target)
            successes.append(str(target))
            if progress:
                progress(index, len(tasks), target.name)

        manifest = {
            "provider": "AnimeThemes",
            "anime_slug": slug,
            "title_romaji": (anime.get("title") or {}).get("romaji"),
            "title_english": (anime.get("title") or {}).get("english"),
            "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "video_files": [Path(path).name for path in successes],
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
            "anime_slug": slug,
            "downloaded": len(successes),
            "files": [str(target_dir / "video_files" / Path(path).name) for path in successes],
            "video_files_dir": str(target_dir / "video_files"),
            "songs_files_dir": str(target_dir / "songs_files"),
            "manifest": str(target_dir / "template_manifest.json"),
        }
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


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
    manifest_slug = None
    if manifest_path.exists():
        try:
            manifest_slug = json.loads(manifest_path.read_text(encoding="utf-8")).get("anime_slug")
        except (OSError, ValueError, AttributeError):
            manifest_slug = None

    has_templates = any(video_dir.glob("*")) or any(wav_dir.glob("*.wav"))
    if has_templates and (not selected_slug or selected_slug == manifest_slug):
        return series_dir
    if selected_slug:
        download_selected_templates(series_name, selected_slug, progress)
        return series_dir
    if has_templates:
        return series_dir

    candidates = search_candidates(series_name)
    exact = _choose_exact_candidate(series_name, candidates)
    if exact:
        download_selected_templates(series_name, exact["slug"], progress)
        return series_dir
    if candidates:
        names = ", ".join(
            f"{candidate.get('title_english') or candidate.get('title_romaji') or candidate['slug']} "
            f"({candidate['slug']})"
            for candidate in candidates[:8]
        )
        raise RuntimeError(
            f"AnimeThemes match is ambiguous for '{series_name}'. Select an anime slug explicitly. Candidates: {names}"
        )
    raise RuntimeError(f"No templates found for '{series_name}'. Add templates or search AnimeThemes first.")


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
