from __future__ import annotations

import json
import math
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from core.analyzer import _binary


def _find_tool(plugins_path: Path, name: str) -> Path:
    candidates = []
    if plugins_path.exists():
        candidates.extend(plugins_path.rglob(f"{name}.exe"))
        candidates.extend(plugins_path.rglob(name))
    if candidates:
        return sorted(candidates, key=lambda p: len(p.parts))[0]
    found = shutil.which(name)
    if found:
        return Path(found)
    raise FileNotFoundError(f"{name} was not found. Configure plugins_path or add it to PATH.")


def get_mkv_duration(mkv_path: Path, plugins_path: Path) -> float:
    ffprobe = _binary(plugins_path, "ffprobe")
    result = subprocess.run(
        [str(ffprobe), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(mkv_path)],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"Could not determine video duration: {(result.stderr or '').strip()[-500:]}")
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError("FFprobe returned a non-numeric video duration.") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError("FFprobe returned an invalid video duration.")
    return duration


def _format_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    remaining = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{remaining:012.9f}"


def build_chapters(op: dict | None, ed: dict | None, duration: float) -> list[dict[str, Any]]:
    if not math.isfinite(float(duration)) or duration <= 0:
        raise ValueError("Video duration must be a finite positive value.")
    segments = []
    for label, segment in (("Opening", op), ("Ending", ed)):
        if segment is None:
            continue
        try:
            start, end = float(segment["start"]), float(segment["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid {label} timestamps.") from exc
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= duration):
            raise ValueError(f"{label} timestamps must satisfy 0 <= start < end <= video duration ({duration:.3f}s).")
        segments.append((label, start, end, str(segment.get("name") or label)))
    if not segments:
        raise ValueError("There are no detected OP/ED segments to inject.")
    segments.sort(key=lambda x: x[1])
    if len(segments) == 2 and segments[0][2] > segments[1][1]:
        raise ValueError("Opening and Ending segments overlap.")

    chapters: list[dict[str, Any]] = []
    cursor = 0.0
    for kind, start, end, name in segments:
        if start > cursor:
            chapters.append({"title": "Prologue" if cursor == 0 else "Episode", "start": cursor, "end": start})
        chapters.append({"title": name if kind == "Opening" else name, "start": start, "end": end})
        cursor = end
    if cursor < duration:
        chapters.append({"title": "Episode", "start": cursor, "end": duration})
    if not chapters or chapters[0]["start"] != 0:
        raise ValueError("Chapter plan does not cover the start of the file.")
    return chapters


def write_chapters_xml(chapters: list[dict[str, Any]], path: Path) -> None:
    rows = ['<?xml version="1.0" encoding="UTF-8"?>', '<Chapters><EditionEntry>']
    for chapter in chapters:
        rows.extend([
            "<ChapterAtom>",
            f"<ChapterTimeStart>{_format_time(float(chapter['start']))}</ChapterTimeStart>",
            f"<ChapterTimeEnd>{_format_time(float(chapter['end']))}</ChapterTimeEnd>",
            "<ChapterDisplay>", f"<ChapterString>{escape(str(chapter['title']))}</ChapterString>",
            "<ChapterLanguage>eng</ChapterLanguage>", "</ChapterDisplay>", "</ChapterAtom>",
        ])
    rows.extend(["</EditionEntry></Chapters>"])
    path.write_text("\n".join(rows), encoding="utf-8")


def has_existing_chapters(mkv_path: Path, plugins_path: Path) -> bool:
    mkvmerge = _find_tool(plugins_path, "mkvmerge")
    result = subprocess.run([str(mkvmerge), "-J", str(mkv_path)], capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"Could not inspect existing MKV chapters: {result.stderr[-500:]}")
    metadata = json.loads(result.stdout)
    return bool(metadata.get("chapters"))


def read_mkv_chapters(mkv_path: Path, plugins_path: Path) -> list[dict[str, Any]]:
    """Read the chapter list back through FFprobe for post-write verification."""
    ffprobe = _binary(plugins_path, "ffprobe")
    result = subprocess.run(
        [str(ffprobe), "-v", "error", "-show_chapters",
         "-show_entries", "chapter=start_time,end_time:chapter_tags=title",
         "-of", "json", str(mkv_path)],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFprobe could not verify written chapters: {(result.stderr or '').strip()[-500:]}")
    try:
        payload = json.loads(result.stdout)
        raw_chapters = payload.get("chapters", [])
        chapters = []
        for item in raw_chapters:
            tags = item.get("tags") or {}
            chapters.append({
                "title": str(tags.get("title") or ""),
                "start": float(item["start_time"]),
                "end": float(item["end_time"]),
            })
        return chapters
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise RuntimeError("FFprobe returned an invalid chapter listing.") from exc


def verify_written_chapters(mkv_path: Path, expected: list[dict[str, Any]], plugins_path: Path) -> None:
    actual = read_mkv_chapters(mkv_path, plugins_path)
    if len(actual) != len(expected):
        raise RuntimeError(f"Post-injection verification failed: expected {len(expected)} chapters, found {len(actual)}.")
    tolerance = 0.1
    for index, (wanted, found) in enumerate(zip(expected, actual), start=1):
        if (found["title"] != str(wanted["title"])
                or abs(found["start"] - float(wanted["start"])) > tolerance
                or abs(found["end"] - float(wanted["end"])) > tolerance):
            raise RuntimeError(f"Post-injection verification failed: chapter {index} does not match the approved title or timestamps.")


def inject_chapters_with_backup(
    mkv_path: Path, chapters: list[dict[str, Any]], plugins_path: Path, backup_root: Path,
    *, replace_existing: bool,
) -> Path:
    if not mkv_path.exists() or mkv_path.suffix.lower() != ".mkv":
        raise FileNotFoundError("The target MKV file does not exist.")
    existing = has_existing_chapters(mkv_path, plugins_path)
    if existing and not replace_existing:
        raise ValueError("The MKV already contains chapters. Explicitly confirm that existing chapters may be replaced.")
    mkvpropedit = _find_tool(plugins_path, "mkvpropedit")
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = backup_root / f"{mkv_path.stem}.backup-{uuid.uuid4().hex[:10]}{mkv_path.suffix}"
    shutil.copy2(mkv_path, backup)
    xml_path = backup_root / f"{mkv_path.stem}.{uuid.uuid4().hex[:8]}.chapters.xml"
    try:
        write_chapters_xml(chapters, xml_path)
        result = subprocess.run(
            [str(mkvpropedit), str(mkv_path), "--chapters", str(xml_path)],
            capture_output=True, text=True, timeout=1800,
        )
        if result.returncode != 0:
            raise RuntimeError(f"mkvpropedit failed: {(result.stderr or result.stdout)[-1000:]}")
        verify_written_chapters(mkv_path, chapters, plugins_path)
        return backup
    except Exception as original_error:
        restore_tmp = mkv_path.with_name(mkv_path.name + f".{uuid.uuid4().hex[:8]}.restore")
        try:
            shutil.copy2(backup, restore_tmp)
            restore_tmp.replace(mkv_path)
        except Exception as restore_error:
            raise RuntimeError(
                f"Injection failed ({original_error}); automatic backup restoration also failed ({restore_error}). "
                f"The untouched backup is available at: {backup}"
            ) from restore_error
        raise
    finally:
        try:
            xml_path.unlink(missing_ok=True)
        except OSError:
            pass
