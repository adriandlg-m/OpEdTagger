from __future__ import annotations

import json
import math
import secrets
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from core.analyzer import _binary
from core.backup_manager import assert_backup_space, ensure_backup_layout, move_transaction_to_recovery, write_transaction_manifest
from core.fingerprints import file_fingerprint


def _find_tool(plugins_path: Path, name: str) -> Path:
    candidates = []
    if plugins_path.exists():
        candidates.extend(plugins_path.rglob(f"{name}.exe"))
        candidates.extend(plugins_path.rglob(name))
    if candidates:
        return sorted(candidates, key=lambda path: len(path.parts))[0]
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


def _chapter_uid() -> int:
    # Matroska UIDs are unsigned 64-bit values; avoid zero.
    return secrets.randbelow((1 << 63) - 1) + 1


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
    segments.sort(key=lambda item: item[1])
    if len(segments) == 2 and segments[0][2] > segments[1][1]:
        raise ValueError("Opening and Ending segments overlap.")

    chapters: list[dict[str, Any]] = []
    cursor = 0.0
    for kind, start, end, name in segments:
        if start > cursor:
            # Keep the CLI's chapter naming: material before an OP is a
            # Prologue; the body between OP and ED is Episode.
            title = "Prologue" if cursor == 0 and kind == "Opening" else "Episode"
            chapters.append({"title": title, "start": cursor, "end": start})
        chapters.append({"title": name, "start": start, "end": end})
        cursor = end

    if cursor < duration:
        # The CLI called the tail after a detected ED "Ending". Without an
        # ED, the remaining material is still part of the episode.
        tail_title = "Ending" if ed is not None else "Episode"
        chapters.append({"title": tail_title, "start": cursor, "end": duration})

    if not chapters or chapters[0]["start"] != 0:
        raise ValueError("Chapter plan does not cover the start of the file.")
    return chapters


def write_chapters_xml(chapters: list[dict[str, Any]], path: Path) -> None:
    rows = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<Chapters>",
        "<EditionEntry>",
        "<EditionFlagHidden>0</EditionFlagHidden>",
        "<EditionFlagDefault>1</EditionFlagDefault>",
        f"<EditionUID>{_chapter_uid()}</EditionUID>",
    ]
    for chapter in chapters:
        rows.extend([
            "<ChapterAtom>",
            f"<ChapterTimeStart>{_format_time(float(chapter['start']))}</ChapterTimeStart>",
            f"<ChapterTimeEnd>{_format_time(float(chapter['end']))}</ChapterTimeEnd>",
            f"<ChapterUID>{_chapter_uid()}</ChapterUID>",
            "<ChapterDisplay>",
            f"<ChapterString>{escape(str(chapter['title']))}</ChapterString>",
            "<ChapterLanguage>spa</ChapterLanguage>",
            "<ChapLanguageIETF>es</ChapLanguageIETF>",
            "</ChapterDisplay>",
            "</ChapterAtom>",
        ])
    rows.extend(["</EditionEntry>", "</Chapters>"])
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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _restore_from_transaction(
    mkv_path: Path, transaction_dir: Path, manifest: dict[str, Any],
) -> None:
    backup = transaction_dir / str(manifest.get("backup_file") or "original.mkv")
    expected_fingerprint = str(manifest.get("original_fingerprint") or "")
    if not backup.is_file():
        raise RuntimeError("The temporary original MKV backup is missing.")
    if backup.stat().st_size != int(manifest.get("original_size") or -1):
        raise RuntimeError("The temporary original MKV backup has an unexpected size.")
    if expected_fingerprint and file_fingerprint(backup) != expected_fingerprint:
        raise RuntimeError("The temporary original MKV backup failed its integrity check.")

    # Copy directly over the target. This avoids requiring a second full-size
    # restore file on the media volume. The untouched backup remains in the
    # recovery transaction until the restored target has passed verification.
    shutil.copy2(backup, mkv_path)
    if mkv_path.stat().st_size != backup.stat().st_size:
        raise RuntimeError("The restored MKV has an unexpected size.")
    if expected_fingerprint and file_fingerprint(mkv_path) != expected_fingerprint:
        raise RuntimeError("The restored MKV did not match the original fingerprint.")




def inject_chapters_with_backup(
    mkv_path: Path, chapters: list[dict[str, Any]], plugins_path: Path, backup_root: Path,
    *, replace_existing: bool, episode_id: int | None = None,
) -> dict[str, Any]:
    """Inject chapters with a temporary full backup and post-write verification."""
    from xml.etree import ElementTree
    from core.backup_manager import (
        assert_backup_space, ensure_backup_layout, move_transaction_to_recovery,
        write_transaction_manifest,
    )

    mkv_path = mkv_path.expanduser().resolve()
    plugins_path = plugins_path.expanduser().resolve()
    backup_root = backup_root.expanduser().resolve()
    if not mkv_path.is_file() or mkv_path.suffix.lower() != ".mkv":
        raise FileNotFoundError("The target MKV file does not exist.")

    existing = has_existing_chapters(mkv_path, plugins_path)
    if existing and not replace_existing:
        raise ValueError("The MKV already contains chapters. Explicitly confirm that existing chapters may be replaced.")

    mkvpropedit = _find_tool(plugins_path, "mkvpropedit")
    mkvextract = _find_tool(plugins_path, "mkvextract") if existing else None
    assert_backup_space(mkv_path, backup_root)
    directories = ensure_backup_layout(backup_root)
    transaction_id = uuid.uuid4().hex
    transaction_dir = directories["temporary"] / transaction_id
    transaction_dir.mkdir(parents=True, exist_ok=False)
    backup = transaction_dir / "original.mkv"
    original_chapters = transaction_dir / "original_chapters.xml"
    new_chapters = transaction_dir / "new_chapters.xml"

    original_stat = mkv_path.stat()
    original_fingerprint = file_fingerprint(mkv_path)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "transaction_id": transaction_id,
        "episode_id": episode_id,
        "file_name": mkv_path.name,
        "target_path": str(mkv_path),
        "backup_file": "original.mkv",
        "original_size": original_stat.st_size,
        "original_mtime_ns": original_stat.st_mtime_ns,
        "original_fingerprint": original_fingerprint,
        "had_existing_chapters": existing,
        "phase": "PREPARING",
    }
    write_transaction_manifest(transaction_dir, manifest)
    modification_started = False
    try:
        shutil.copy2(mkv_path, backup)
        if backup.stat().st_size != original_stat.st_size:
            raise RuntimeError("The temporary backup size does not match the original MKV.")
        if file_fingerprint(backup) != original_fingerprint:
            raise RuntimeError("The temporary backup failed its integrity check.")
        current_stat = mkv_path.stat()
        if (current_stat.st_size, current_stat.st_mtime_ns) != (original_stat.st_size, original_stat.st_mtime_ns):
            raise RuntimeError("The source MKV changed while its temporary backup was being created.")

        if existing:
            assert mkvextract is not None
            extract = subprocess.run(
                [str(mkvextract), str(mkv_path), "chapters", str(original_chapters)],
                capture_output=True, text=True, timeout=300, check=False,
            )
            if extract.returncode != 0:
                raise RuntimeError(
                    "Could not preserve the existing chapter structure as XML: "
                    f"{(extract.stderr or extract.stdout or '')[-800:]}"
                )
            if not original_chapters.is_file() or original_chapters.stat().st_size == 0:
                raise RuntimeError("MKVToolNix did not produce the original-chapters XML archive.")
            try:
                exported_root = ElementTree.parse(original_chapters).getroot()
            except ElementTree.ParseError as exc:
                raise RuntimeError("The exported original-chapters XML is invalid.") from exc
            if exported_root.tag != "Chapters":
                raise RuntimeError("The exported original-chapters XML has an unexpected root element.")

        manifest["phase"] = "BACKUP_READY"
        manifest["backup_verified"] = True
        write_transaction_manifest(transaction_dir, manifest)

        write_chapters_xml(chapters, new_chapters)
        manifest["phase"] = "INJECTING"
        write_transaction_manifest(transaction_dir, manifest)
        modification_started = True

        result = subprocess.run(
            [str(mkvpropedit), str(mkv_path), "--chapters", str(new_chapters)],
            capture_output=True, text=True, timeout=1800, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"mkvpropedit failed: {(result.stderr or result.stdout)[-1000:]}")
        verify_written_chapters(mkv_path, chapters, plugins_path)

        manifest["phase"] = "VERIFIED"
        manifest["verified_chapter_count"] = len(chapters)
        write_transaction_manifest(transaction_dir, manifest)
        return {
            "transaction_id": transaction_id,
            "transaction_path": str(transaction_dir),
            "backup_path": str(backup),
            "original_chapters_path": str(original_chapters) if existing else None,
            "had_existing_chapters": existing,
            "target_path": str(mkv_path),
            "episode_id": episode_id,
            "phase": "VERIFIED",
        }
    except Exception as original_error:
        if modification_started:
            try:
                _restore_from_transaction(mkv_path, transaction_dir, manifest)
            except Exception as restore_error:
                recovery_dir = move_transaction_to_recovery(
                    transaction_dir, backup_root,
                    reason=f"Injection failed ({original_error}); automatic restoration also failed ({restore_error}).",
                )
                raise RuntimeError(
                    f"Injection failed ({original_error}); automatic backup restoration also failed ({restore_error}). "
                    f"The untouched backup is available at: {recovery_dir / 'original.mkv'}"
                ) from restore_error
        shutil.rmtree(transaction_dir, ignore_errors=True)
        raise
    finally:
        new_chapters.unlink(missing_ok=True)


def finalize_injection_transaction(transaction: dict[str, Any]) -> str | None:
    """Finalize only after the episode's SUCCEEDED state has committed to SQLite."""
    from core.backup_manager import write_transaction_manifest

    transaction_dir = Path(transaction["transaction_path"]).resolve()
    if not transaction_dir.is_dir():
        return None
    manifest = json.loads((transaction_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("phase") != "VERIFIED":
        raise RuntimeError("Only a verified chapter transaction can be finalized.")

    backup_root = transaction_dir.parent.parent
    archive_path = None
    original_chapters = transaction_dir / "original_chapters.xml"
    if original_chapters.is_file():
        archive_dir = backup_root / "chapters" / str(manifest["transaction_id"])
        archive_dir.mkdir(parents=True, exist_ok=False)
        destination = archive_dir / "original_chapters.xml"
        original_chapters.replace(destination)
        archive_manifest = {
            "schema_version": 1,
            "transaction_id": manifest["transaction_id"],
            "episode_id": manifest.get("episode_id"),
            "file_name": manifest.get("file_name"),
            "target_path": manifest.get("target_path"),
            "created_at": __import__("datetime").datetime.now().astimezone().isoformat(),
            "archive_file": "original_chapters.xml",
        }
        _write_json(archive_dir / "manifest.json", archive_manifest)
        archive_path = str(destination)

    manifest["phase"] = "DATABASE_COMMITTED"
    manifest["chapter_archive_path"] = archive_path
    write_transaction_manifest(transaction_dir, manifest)
    shutil.rmtree(transaction_dir)
    return archive_path


def rollback_injection_transaction(transaction: dict[str, Any], backup_root: Path) -> None:
    """Restore the complete original MKV if the database commit did not succeed."""
    from core.backup_manager import move_transaction_to_recovery

    transaction_dir = Path(transaction["transaction_path"]).resolve()
    manifest = json.loads((transaction_dir / "manifest.json").read_text(encoding="utf-8"))
    target = Path(manifest["target_path"])
    try:
        _restore_from_transaction(target, transaction_dir, manifest)
    except Exception as restore_error:
        recovery_dir = move_transaction_to_recovery(
            transaction_dir, backup_root,
            reason=f"Database commit failed and restoring the original MKV also failed: {restore_error}",
        )
        raise RuntimeError(
            "Database commit failed and automatic backup restoration also failed. "
            f"The untouched backup is available at: {recovery_dir / 'original.mkv'}"
        ) from restore_error
    shutil.rmtree(transaction_dir, ignore_errors=True)


def reconcile_backup_transactions(
    backup_root: Path,
    get_episode_injection_status,
    set_episode_injection_status=None,
) -> dict[str, int]:
    """Resolve temporary transaction folders after an unclean application exit."""
    from core.backup_manager import ensure_backup_layout, move_transaction_to_recovery

    directories = ensure_backup_layout(backup_root)
    summary = {"finalized": 0, "removed_incomplete": 0, "moved_to_recovery": 0, "errors": 0}
    for transaction_dir in list(directories["temporary"].iterdir()):
        if not transaction_dir.is_dir() or transaction_dir.is_symlink():
            continue
        try:
            manifest = json.loads((transaction_dir / "manifest.json").read_text(encoding="utf-8"))
            phase = manifest.get("phase")
            episode_id = manifest.get("episode_id")
            if phase in {"PREPARING", "BACKUP_READY"}:
                shutil.rmtree(transaction_dir)
                if episode_id is not None and set_episode_injection_status is not None:
                    set_episode_injection_status(
                        int(episode_id),
                        "NOT_REQUESTED",
                        None,
                    )
                summary["removed_incomplete"] += 1
                continue
            episode_id = manifest.get("episode_id")
            state = get_episode_injection_status(int(episode_id)) if episode_id is not None else None
            if phase == "VERIFIED" and state == "SUCCEEDED":
                finalize_injection_transaction({"transaction_path": str(transaction_dir)})
                summary["finalized"] += 1
            else:
                reason = (
                    "The application stopped during chapter injection. "
                    "Inspect the backup and target MKV before retrying."
                )
                recovery_dir = move_transaction_to_recovery(
                    transaction_dir, backup_root, reason=reason,
                )
                if episode_id is not None and set_episode_injection_status is not None:
                    set_episode_injection_status(
                        int(episode_id),
                        "RECOVERY_REQUIRED",
                        f"{reason} Backup: {recovery_dir / 'original.mkv'}",
                    )
                summary["moved_to_recovery"] += 1
        except Exception as exc:
            if 'episode_id' in locals() and episode_id is not None and set_episode_injection_status is not None:
                try:
                    set_episode_injection_status(
                        int(episode_id),
                        "RECOVERY_REQUIRED",
                        f"Could not reconcile the MKV backup transaction: {exc}",
                    )
                except Exception:
                    pass
            summary["errors"] += 1
    return summary
