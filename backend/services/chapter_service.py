from __future__ import annotations

import json
import threading
from pathlib import Path

from backend.infrastructure.config import load_config, resolve_library_path, resolve_within
from backend.infrastructure.database import db_session, utc_now
from backend.services.analysis_service import assert_analysis_file_unchanged, preview_episode_chapters
from backend.services.log_service import log_event
from backend.services.plugin_manager import ensure_chapter_plugins
from core.fingerprints import file_fingerprint
from core.media_tools import (
    finalize_injection_transaction,
    inject_chapters_with_backup,
    rollback_injection_transaction,
)

CHAPTER_OPERATION_LOCK = threading.Lock()
INJECTION_ELIGIBLE_REVIEW_STATES = {
    "AUTO_APPROVED",
    "REVIEW_RECOMMENDED",
    "MANUALLY_REVIEWED",
    "APPROVED",
}


def preview_batch_injection(
    series_name: str,
    episode_ids: list[int],
    *,
    replace_existing_chapters: bool = False,
) -> dict:
    """Inspect sequential backup requirements before the user confirms a batch."""
    from core.backup_manager import estimate_sequential_backup_space
    from core.media_tools import has_existing_chapters

    selected_ids = list(dict.fromkeys(int(value) for value in episode_ids))
    if not selected_ids:
        raise ValueError("Select at least one episode for chapter injection.")
    scan_library()
    detail = get_series_detail(series_name)
    episodes_by_id = {
        int(item["id"]): item for item in detail["episodes"] if item.get("id") is not None
    }
    missing = [episode_id for episode_id in selected_ids if episode_id not in episodes_by_id]
    if missing:
        raise ValueError("One or more selected episodes do not belong to this series or are no longer in the library.")

    paths = []
    for episode_id in selected_ids:
        episode = episodes_by_id[episode_id]
        if episode.get("analysis_status") != "COMPLETED" or not (episode.get("op") or episode.get("ed")):
            raise ValueError(f"{episode['file_name']}: no valid OP/ED detection is available for injection.")
        if episode.get("review_status") not in INJECTION_ELIGIBLE_REVIEW_STATES:
            raise ValueError(f"{episode['file_name']}: manual review is still required before injection.")
        if episode.get("injection_status") in {"SUCCEEDED", "IN_PROGRESS", "RECOVERY_REQUIRED"}:
            raise ValueError(f"{episode['file_name']}: it is already injected or requires injection recovery.")
        resolve_within(resolve_library_path(), Path(episode["file_path"]))
        paths.append(Path(episode["file_path"]))

    cfg = load_config()
    plugin_state = ensure_chapter_plugins(cfg)
    if plugin_state.get("status") != "ready":
        detail_message = plugin_state.get("last_error") or plugin_state.get("message") or "MKVToolNix is unavailable."
        raise RuntimeError(f"Chapter tools are not ready: {detail_message}")

    estimate = estimate_sequential_backup_space(paths, Path(cfg["backup_path"]))
    files_with_chapters = [
        path.name for path in paths if has_existing_chapters(path, Path(cfg["plugins_path"]))
    ]
    estimate.update({
        "files_with_existing_chapters": files_with_chapters,
        "existing_chapters_count": len(files_with_chapters),
        "replace_existing_chapters": bool(replace_existing_chapters),
        "will_skip_existing_chapters": bool(files_with_chapters) and not replace_existing_chapters,
        "backup_policy": "temporary-sequential",
    })
    return estimate


def inject_episode_chapters(episode_id: int, *, replace_existing_chapters: bool = False) -> dict:
    """Safely inject and verify one episode's approved/eligible chapter plan."""
    if not CHAPTER_OPERATION_LOCK.acquire(blocking=False):
        raise RuntimeError("Another chapter review or injection operation is currently running.")

    claimed = False
    transaction = None
    database_committed = False
    episode = None
    try:
        with db_session() as conn:
            episode_row = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
            result_row = conn.execute(
                "SELECT * FROM analysis_results WHERE episode_id=?", (episode_id,)
            ).fetchone()
        if not episode_row or not result_row:
            raise FileNotFoundError("Episode analysis was not found.")
        episode = dict(episode_row)
        result = dict(result_row)

        if result["review_status"] not in INJECTION_ELIGIBLE_REVIEW_STATES:
            raise ValueError(
                "This episode still requires manual review. Correct or explicitly approve its detections before injection."
            )
        if result["injection_status"] in {"IN_PROGRESS", "RECOVERY_REQUIRED"}:
            raise RuntimeError("This episode requires inspection before another injection attempt.")

        resolve_within(resolve_library_path(), Path(episode["file_path"]))
        assert_analysis_file_unchanged(episode, result)

        cfg = load_config()
        plugin_state = ensure_chapter_plugins(cfg)
        if plugin_state.get("status") != "ready":
            detail = plugin_state.get("last_error") or plugin_state.get("message") or "MKVToolNix is unavailable."
            raise RuntimeError(f"Chapter tools are not ready: {detail}")

        with db_session() as conn:
            update = conn.execute(
                "UPDATE analysis_results SET injection_status='IN_PROGRESS',error=NULL,updated_at=? "
                "WHERE episode_id=? AND review_status IN "
                "('AUTO_APPROVED','REVIEW_RECOMMENDED','MANUALLY_REVIEWED','APPROVED') "
                "AND injection_status NOT IN ('IN_PROGRESS','RECOVERY_REQUIRED')",
                (utc_now(), episode_id),
            )
            if update.rowcount != 1:
                raise RuntimeError(
                    "The episode state changed concurrently. Refresh and review it again before injection."
                )
        claimed = True

        preview = preview_episode_chapters(episode_id)
        transaction = inject_chapters_with_backup(
            Path(episode["file_path"]),
            preview["chapters"],
            Path(cfg["plugins_path"]),
            Path(cfg["backup_path"]),
            replace_existing=replace_existing_chapters,
            episode_id=episode_id,
        )

        file_path = Path(episode["file_path"])
        stat = file_path.stat()
        fingerprint = file_fingerprint(file_path)
        stamp = utc_now()
        with db_session() as conn:
            conn.execute(
                "UPDATE analysis_results SET injection_status='SUCCEEDED',error=NULL,analyzed_file_size=?,"
                "analyzed_file_mtime=?,content_fingerprint=?,updated_at=? WHERE episode_id=?",
                (stat.st_size, stat.st_mtime, fingerprint, stamp, episode_id),
            )
            conn.execute(
                "UPDATE episodes SET file_size=?,file_mtime=?,updated_at=? WHERE id=?",
                (stat.st_size, stat.st_mtime, stamp, episode_id),
            )
        database_committed = True
        chapter_archive_path = None
        cleanup_pending = False
        try:
            chapter_archive_path = finalize_injection_transaction(transaction)
        except Exception as finalize_error:
            cleanup_pending = True
            log_event(
                "WARNING",
                "Chapters",
                "The verified injection committed, but temporary backup cleanup is pending.",
                detail=str(finalize_error),
                series_name=episode["series_name"],
                episode_name=episode["file_name"],
            )
        log_event(
            "INFO",
            "Chapters",
            "MKV chapters injected and verified.",
            detail=f"Chapter archive: {chapter_archive_path or 'none'}; cleanup_pending={cleanup_pending}",
            series_name=episode["series_name"],
            episode_name=episode["file_name"],
        )
        return {
            "episode_id": episode_id,
            "file_name": episode["file_name"],
            "injection_status": "SUCCEEDED",
            "backup_path": None,
            "chapter_archive_path": chapter_archive_path,
            "backup_cleanup_pending": cleanup_pending,
            "chapters": preview["chapters"],
        }
    except Exception as exc:
        if transaction is not None and not database_committed:
            try:
                rollback_injection_transaction(transaction, Path(load_config()["backup_path"]))
            except Exception as rollback_error:
                exc = RuntimeError(f"{exc}; automatic backup restoration also failed ({rollback_error})")
        if claimed and episode is not None:
            failed_state = "RECOVERY_REQUIRED" if "restoration also failed" in str(exc).lower() else "FAILED"
            with db_session() as conn:
                conn.execute(
                    "UPDATE analysis_results SET injection_status=?,error=?,updated_at=? WHERE episode_id=?",
                    (failed_state, str(exc), utc_now(), episode_id),
                )
            log_event(
                "ERROR",
                "Chapters",
                "MKV chapter injection failed.",
                detail=str(exc),
                series_name=episode["series_name"],
                episode_name=episode["file_name"],
            )
        raise
    finally:
        CHAPTER_OPERATION_LOCK.release()
