from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException

from backend.api.schemas.models import InjectionRequest, ReviewUpdate
from backend.infrastructure.config import load_config, resolve_library_path, resolve_within
from backend.infrastructure.database import db_session, utc_now
from backend.services.analysis_service import assert_analysis_file_unchanged, preview_episode_chapters
from backend.services.chapter_service import CHAPTER_OPERATION_LOCK, inject_episode_chapters
from core.confidence import episode_review_status
from backend.services.log_service import log_event
from core.media_tools import build_chapters, get_mkv_duration

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/episodes", tags=["Episodes / Chapters"])


@router.put("/{episode_id}/review")
def review_episode(episode_id: int, body: ReviewUpdate):
    if not CHAPTER_OPERATION_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Chapter review is unavailable while another chapter operation is running.",
        )
    try:
        with db_session() as conn:
            episode = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
            result = conn.execute("SELECT * FROM analysis_results WHERE episode_id=?", (episode_id,)).fetchone()
        if not episode or not result:
            raise HTTPException(status_code=404, detail="Episode analysis was not found.")
        if result["injection_status"] in {"IN_PROGRESS", "RECOVERY_REQUIRED"}:
            raise HTTPException(
                status_code=409,
                detail="This episode requires injection recovery before its review can be changed.",
            )
        try:
            resolve_within(resolve_library_path(), Path(episode["file_path"]))
            assert_analysis_file_unchanged(episode, result)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception(
                "Episode path/fingerprint validation failed before review (episode_id=%s).",
                episode_id,
            )
            raise HTTPException(
                status_code=403,
                detail="The episode is no longer available inside the configured library.",
            ) from exc

        previous = {
            "OP": json.loads(result["op_json"]) if result["op_json"] else None,
            "ED": json.loads(result["ed_json"]) if result["ed_json"] else None,
        }
        updated = {kind: (dict(value) if value else None) for kind, value in previous.items()}
        explicit = {
            "OP": "op" in body.model_fields_set,
            "ED": "ed" in body.model_fields_set,
        }
        requested = {"OP": body.op, "ED": body.ed}
        changes = []

        for kind in ("OP", "ED"):
            if explicit[kind]:
                candidate = dict(requested[kind]) if requested[kind] else None
                before = previous[kind]
                if candidate != before:
                    if candidate is not None:
                        candidate["manual_verified"] = True
                    updated[kind] = candidate
                    changes.append((kind, "CORRECTED" if candidate else "REMOVED", before, candidate))
            if body.approved and updated[kind] is not None:
                before = updated[kind]
                after = {**before, "manual_verified": True}
                updated[kind] = after
                if not before.get("manual_verified"):
                    changes.append((kind, "APPROVED", before, after))

        if body.approved and not updated["OP"] and not updated["ED"]:
            raise HTTPException(
                status_code=422,
                detail="An episode without OP/ED matches cannot be approved for injection.",
            )

        try:
            cfg = load_config()
            duration = get_mkv_duration(Path(episode["file_path"]), Path(cfg["plugins_path"]))
            if updated["OP"] or updated["ED"]:
                build_chapters(updated["OP"], updated["ED"], duration)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Review validation failed: {exc}") from exc

        timestamps_changed = any(
            previous[kind] != updated[kind] for kind in ("OP", "ED")
        )
        if body.approved:
            review_status = "APPROVED"
        else:
            review_status = episode_review_status(updated["OP"], updated["ED"])
        analysis_status = "COMPLETED" if updated["OP"] or updated["ED"] else "NO_MATCH"

        with db_session() as conn:
            for kind, action, before, after in changes:
                conn.execute(
                    "INSERT INTO analysis_segment_history"
                    "(episode_id,segment_type,action,before_json,after_json,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        episode_id,
                        kind,
                        action,
                        json.dumps(before, ensure_ascii=False) if before else None,
                        json.dumps(after, ensure_ascii=False) if after else None,
                        utc_now(),
                    ),
                )
            write = conn.execute(
                "UPDATE analysis_results SET op_json=?,ed_json=?,analysis_status=?,review_status=?,"
                "injection_status=CASE WHEN ? THEN 'NOT_REQUESTED' ELSE injection_status END,updated_at=? "
                "WHERE episode_id=? AND injection_status NOT IN ('IN_PROGRESS','RECOVERY_REQUIRED')",
                (
                    json.dumps(updated["OP"], ensure_ascii=False) if updated["OP"] else None,
                    json.dumps(updated["ED"], ensure_ascii=False) if updated["ED"] else None,
                    analysis_status,
                    review_status,
                    int(timestamps_changed),
                    utc_now(),
                    episode_id,
                ),
            )
            if write.rowcount != 1:
                raise HTTPException(
                    status_code=409,
                    detail="The injection state changed concurrently. Refresh the episode before reviewing it.",
                )

        log_event(
            "INFO",
            "Review",
            "Episode detection reviewed.",
            detail=f"approved={body.approved}; changed_segments={len(changes)}",
            series_name=episode["series_name"],
            episode_name=episode["file_name"],
        )
        return {
            "episode_id": episode_id,
            "op": updated["OP"],
            "ed": updated["ED"],
            "review_status": review_status,
            "history_entries": len(changes),
        }
    finally:
        CHAPTER_OPERATION_LOCK.release()


@router.get("/{episode_id}/chapter-preview")
def chapter_preview(episode_id: int):
    try:
        return preview_episode_chapters(episode_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{episode_id}/inject")
def inject_episode_chapter_endpoint(episode_id: int, body: InjectionRequest):
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Explicit user confirmation is required.")
    try:
        return inject_episode_chapters(
            episode_id,
            replace_existing_chapters=body.replace_existing_chapters,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Injection failed. The application attempted to restore the backup if modification had started: {exc}",
        ) from exc
