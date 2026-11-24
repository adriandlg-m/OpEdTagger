from __future__ import annotations

import json
import threading
from pathlib import Path
from fastapi import APIRouter, HTTPException

from backend.api.schemas.models import InjectionRequest, ReviewUpdate
from backend.infrastructure.config import load_config, resolve_library_path, resolve_within
from backend.infrastructure.database import db_session, utc_now
from backend.services.analysis_service import assert_analysis_file_unchanged, preview_episode_chapters
from backend.services.log_service import log_event
from backend.services.plugin_manager import ensure_chapter_plugins
from core.media_tools import get_mkv_duration, build_chapters, inject_chapters_with_backup

router = APIRouter(prefix="/episodes", tags=["Episodes / Chapters"])
_INJECTION_LOCK = threading.Lock()

@router.put("/{episode_id}/review")
def review_episode(episode_id: int, body: ReviewUpdate):
    if not _INJECTION_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Chapter review is unavailable while another chapter operation is running.")
    try:
        with db_session() as conn:
            episode = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
            result = conn.execute("SELECT * FROM analysis_results WHERE episode_id=?", (episode_id,)).fetchone()
        if not episode or not result:
            raise HTTPException(status_code=404, detail="Episode analysis was not found.")
        if result["injection_status"] in {"IN_PROGRESS", "RECOVERY_REQUIRED"}:
            raise HTTPException(status_code=409, detail="This episode requires injection recovery before its review can be changed.")
        try:
            resolve_within(resolve_library_path(), Path(episode["file_path"]))
            assert_analysis_file_unchanged(episode, result)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=403, detail="The episode is no longer available inside the configured library.") from exc
        op = body.op if "op" in body.model_fields_set else (json.loads(result["op_json"]) if result["op_json"] else None)
        ed = body.ed if "ed" in body.model_fields_set else (json.loads(result["ed_json"]) if result["ed_json"] else None)
        if body.approved and not op and not ed:
            raise HTTPException(status_code=422, detail="An episode without OP/ED matches cannot be approved for injection.")
        try:
            cfg = load_config()
            duration = get_mkv_duration(Path(episode["file_path"]), Path(cfg["plugins_path"]))
            if op or ed:
                build_chapters(op, ed, duration)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Review validation failed: {exc}") from exc
        review_status = "APPROVED" if body.approved else ("PENDING" if op or ed else "NOT_REQUIRED")
        analysis_status = "COMPLETED" if op or ed else "NO_MATCH"
        timestamps_changed = "op" in body.model_fields_set or "ed" in body.model_fields_set
        with db_session() as conn:
            updated = conn.execute(
                "UPDATE analysis_results SET op_json=?,ed_json=?,analysis_status=?,review_status=?,"
                "injection_status=CASE WHEN ? THEN 'NOT_REQUESTED' ELSE injection_status END,updated_at=? "
                "WHERE episode_id=? AND injection_status NOT IN ('IN_PROGRESS','RECOVERY_REQUIRED')",
                (json.dumps(op) if op else None, json.dumps(ed) if ed else None, analysis_status,
                 review_status, int(timestamps_changed), utc_now(), episode_id),
            )
            if updated.rowcount != 1:
                raise HTTPException(status_code=409, detail="The injection state changed concurrently. Refresh the episode before reviewing it.")
        log_event("INFO", "Review", "Episode detection reviewed.", detail=f"approved={body.approved}", series_name=episode["series_name"], episode_name=episode["file_name"])
        return {"episode_id": episode_id, "op": op, "ed": ed, "review_status": review_status}
    finally:
        _INJECTION_LOCK.release()

@router.get("/{episode_id}/chapter-preview")
def chapter_preview(episode_id: int):
    try:
        return preview_episode_chapters(episode_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@router.post("/{episode_id}/inject")
def inject_episode_chapters(episode_id: int, body: InjectionRequest):
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Explicit user confirmation is required.")
    if not _INJECTION_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Another chapter injection is currently running.")
    try:
        with db_session() as conn:
            episode = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
            result = conn.execute("SELECT * FROM analysis_results WHERE episode_id=?", (episode_id,)).fetchone()
        if not episode or not result:
            raise HTTPException(status_code=404, detail="Episode analysis was not found.")
        try:
            resolve_within(resolve_library_path(), Path(episode["file_path"]))
            assert_analysis_file_unchanged(episode, result)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=403, detail="The episode is no longer available inside the configured library.") from exc
        if result["review_status"] != "APPROVED":
            raise HTTPException(status_code=409, detail="Review and approve the detected timestamps before injection.")
        if result["injection_status"] in {"IN_PROGRESS", "RECOVERY_REQUIRED"}:
            raise HTTPException(status_code=409, detail="This episode requires inspection before another injection attempt.")
        cfg = load_config()
        plugin_state = ensure_chapter_plugins(cfg)
        if plugin_state.get("status") != "ready":
            detail = plugin_state.get("last_error") or plugin_state.get("message") or "MKVToolNix is unavailable."
            raise HTTPException(status_code=503, detail=f"Chapter tools are not ready: {detail}")
        with db_session() as conn:
            claimed = conn.execute(
                "UPDATE analysis_results SET injection_status='IN_PROGRESS',updated_at=? "
                "WHERE episode_id=? AND review_status='APPROVED' "
                "AND injection_status NOT IN ('IN_PROGRESS','RECOVERY_REQUIRED')",
                (utc_now(), episode_id),
            )
            if claimed.rowcount != 1:
                raise HTTPException(status_code=409, detail="The episode state changed concurrently. Refresh and review it again before injection.")
        try:
            preview = preview_episode_chapters(episode_id)
            backup = inject_chapters_with_backup(Path(episode["file_path"]), preview["chapters"],
                        Path(cfg["plugins_path"]), Path(cfg["backup_path"]),
                        replace_existing=body.replace_existing_chapters)
            file_path = Path(episode["file_path"])
            stat = file_path.stat()
            from core.fingerprints import file_fingerprint
            fingerprint = file_fingerprint(file_path)
            with db_session() as conn:
                stamp = utc_now()
                conn.execute(
                    "UPDATE analysis_results SET injection_status='SUCCEEDED',error=NULL,analyzed_file_size=?,"
                    "analyzed_file_mtime=?,content_fingerprint=?,updated_at=? WHERE episode_id=?",
                    (stat.st_size, stat.st_mtime, fingerprint, stamp, episode_id),
                )
                conn.execute(
                    "UPDATE episodes SET file_size=?,file_mtime=?,updated_at=? WHERE id=?",
                    (stat.st_size, stat.st_mtime, stamp, episode_id),
                )
            log_event("INFO", "Chapters", "MKV chapters injected and verified.", detail=f"Backup: {backup}",
                      series_name=episode["series_name"], episode_name=episode["file_name"])
            return {"episode_id": episode_id, "injection_status": "SUCCEEDED", "backup_path": str(backup), "chapters": preview["chapters"]}
        except Exception as exc:
            failed_state = "RECOVERY_REQUIRED" if "restoration also failed" in str(exc).lower() else "FAILED"
            with db_session() as conn:
                conn.execute("UPDATE analysis_results SET injection_status=?,error=?,updated_at=? WHERE episode_id=?",
                             (failed_state, str(exc), utc_now(), episode_id))
            log_event("ERROR", "Chapters", "MKV chapter injection failed.", detail=str(exc),
                      series_name=episode["series_name"], episode_name=episode["file_name"])
            raise HTTPException(status_code=500, detail=f"Injection failed. The application attempted to restore the backup if modification had started: {exc}") from exc
    finally:
        _INJECTION_LOCK.release()
