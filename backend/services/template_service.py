from __future__ import annotations

import logging
import shutil
import threading
import re
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import requests

from backend.infrastructure.config import load_config

logger = logging.getLogger(__name__)
_TEMPLATE_LOCK = threading.RLock()
GRAPHQL_URL = "https://rs.animethemes.moe/graphql"
HEADERS = {"Content-Type": "application/json", "User-Agent": "OpEdTagger/1.0 (+local media tagging application)"}
SEARCH_QUERY = """
query ($searchTerm: String!) {
  search(search: $searchTerm, first: 20, page: 1) {
    anime { data { slug title { romaji english } themes { type sequence slug entries { videos { nodes { link resolution filename } } } } } }
  }
}
"""


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
    response = requests.post(GRAPHQL_URL, json={"query": SEARCH_QUERY, "variables": {"searchTerm": query}}, headers=HEADERS, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise RuntimeError("AnimeThemes returned GraphQL errors: " + "; ".join(e.get("message", "Unknown error") for e in payload["errors"]))
    data = payload.get("data", {}).get("search", {}).get("anime", {}).get("data", []) or []
    result = []
    for anime in data:
        title = anime.get("title") or {}
        themes = anime.get("themes") or []
        result.append({"slug": anime.get("slug"), "title_romaji": title.get("romaji"), "title_english": title.get("english"),
                       "opening_count": sum(1 for t in themes if t.get("type") == "OP"),
                       "ending_count": sum(1 for t in themes if t.get("type") == "ED")})
    return result


def _choose_exact_candidate(series_name: str, candidates: list[dict]) -> dict | None:
    wanted = normalize(series_name)
    exact = [c for c in candidates if wanted and wanted in {normalize(c.get("slug") or ""), normalize(c.get("title_romaji") or ""), normalize(c.get("title_english") or "")}]
    return exact[0] if len(exact) == 1 else None


def _download(url: str, target: Path, timeout: int = 90) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("AnimeThemes returned a non-HTTPS media URL; refusing download.")
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    headers = {}
    mode = "wb"
    if part.exists() and part.stat().st_size:
        headers["Range"] = f"bytes={part.stat().st_size}-"
        mode = "ab"
    with requests.get(url, headers=headers, stream=True, timeout=timeout) as response:
        if response.status_code == 206 and mode == "ab":
            pass
        elif response.status_code == 200:
            mode = "wb"
        elif response.status_code == 416 and part.exists():
            part.replace(target)
            return
        else:
            response.raise_for_status()
        with part.open(mode) as stream:
            for chunk in response.iter_content(1024 * 1024):
                if chunk:
                    stream.write(chunk)
    if not part.exists() or part.stat().st_size == 0:
        raise RuntimeError(f"Downloaded template is empty: {target.name}")
    part.replace(target)


def _download_selected_templates_impl(series_name: str, slug: str, progress: Callable[[int, int, str], None] | None = None) -> dict:
    candidates = search_candidates(slug)
    matches = [c for c in candidates if c.get("slug") == slug]
    if len(matches) != 1:
        raise ValueError("The selected AnimeThemes slug could not be verified. Search again and select a valid result.")
    response = requests.post(GRAPHQL_URL, json={"query": SEARCH_QUERY, "variables": {"searchTerm": slug}}, headers=HEADERS, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise RuntimeError("AnimeThemes returned GraphQL errors for the selected anime.")
    data = payload.get("data", {}).get("search", {}).get("anime", {}).get("data", []) or []
    anime_rows = [a for a in data if a.get("slug") == slug]
    if len(anime_rows) != 1:
        raise ValueError("AnimeThemes did not return one unambiguous anime for the selected slug.")
    anime = anime_rows[0]

    target_dir = _safe_series_template_dir(series_name)
    template_root = target_dir.parent
    template_root.mkdir(parents=True, exist_ok=True)
    staging = template_root / f".{target_dir.name}.staging-{__import__('uuid').uuid4().hex[:10]}"
    video_dir = staging / "video_files"
    video_dir.mkdir(parents=True, exist_ok=True)
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
    tasks = sorted(unique.values(), key=lambda t: (t[0], t[1]))
    if not tasks:
        import shutil
        shutil.rmtree(staging, ignore_errors=True)
        raise RuntimeError(f"No OP/ED video templates were found for '{series_name}'.")

    successes = []
    try:
        for index, (kind, seq, url, filename) in enumerate(tasks, 1):
            suffix = Path(filename).suffix.lower() if filename else Path(urlparse(url).path).suffix.lower()
            if not suffix or len(suffix) > 8 or not re.fullmatch(r"\.[a-z0-9]{1,7}", suffix):
                suffix = ".webm"
            safe_name = re.sub(r"[^A-Za-z0-9._ -]+", "_", series_name).strip(" .") or "anime"
            target = video_dir / f"{safe_name} - {kind}{seq}{suffix}"
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
            __import__('json').dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        previous = None
        if target_dir.exists():
            previous = template_root / f".{target_dir.name}.previous-{__import__('uuid').uuid4().hex[:10]}"
            target_dir.replace(previous)
        try:
            staging.replace(target_dir)
        except Exception:
            if previous and previous.exists() and not target_dir.exists():
                previous.replace(target_dir)
            raise
        if previous and previous.exists():
            import shutil
            shutil.rmtree(previous, ignore_errors=True)
        return {"series_name": series_name, "anime_slug": slug, "downloaded": len(successes),
                "files": [str(target_dir / "video_files" / Path(path).name) for path in successes],
                "manifest": str(target_dir / "template_manifest.json")}
    except Exception:
        import shutil
        shutil.rmtree(staging, ignore_errors=True)
        raise

def download_selected_templates(series_name: str, slug: str, progress: Callable[[int, int, str], None] | None = None) -> dict:
    with _TEMPLATE_LOCK:
        target_dir = _safe_series_template_dir(series_name)
        _recover_template_swap(target_dir)
        return _download_selected_templates_impl(series_name, slug, progress)


def _ensure_templates_impl(series_name: str, selected_slug: str | None = None,
                           progress: Callable[[int, int, str], None] | None = None) -> Path:
    series_dir = _safe_series_template_dir(series_name)
    video_dir = series_dir / "video_files"
    wav_dir = series_dir / "songs_files"
    manifest_path = series_dir / "template_manifest.json"
    manifest_slug = None
    if manifest_path.exists():
        try:
            manifest_slug = __import__("json").loads(manifest_path.read_text(encoding="utf-8")).get("anime_slug")
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
        names = ", ".join(f"{c.get('title_english') or c.get('title_romaji') or c['slug']} ({c['slug']})" for c in candidates[:8])
        raise RuntimeError(f"AnimeThemes match is ambiguous for '{series_name}'. Select an anime slug explicitly. Candidates: {names}")
    raise RuntimeError(f"No templates found for '{series_name}'. Add templates or search AnimeThemes first.")


def ensure_templates(series_name: str, selected_slug: str | None = None,
                     progress: Callable[[int, int, str], None] | None = None) -> Path:
    with _TEMPLATE_LOCK:
        target_dir = _safe_series_template_dir(series_name)
        _recover_template_swap(target_dir)
        return _ensure_templates_impl(series_name, selected_slug, progress)
