from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from backend.infrastructure.config import app_data_dir, resolve_within
from backend.infrastructure.database import db_session, utc_now
from backend.services.analysis_service import analyze_path, analyze_series
from backend.services.library_service import get_series_detail, scan_library
from backend.services.log_service import log_event

logger = logging.getLogger(__name__)
ACTIVE_STATUSES = ("queued", "running")


class JobService:
    """Single-worker, SQLite-backed local job queue."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="opedtagger-worker")
        self._cancel: dict[str, threading.Event] = {}
        self._lock = threading.RLock()

    def recover_interrupted_jobs(self) -> None:
        with db_session() as conn:
            rows = conn.execute("SELECT id,kind,status,payload_json FROM jobs WHERE status IN ('queued','running')").fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE jobs SET status='interrupted',stage='interrupted',finished_at=?,error=? WHERE id=?",
                    (utc_now(), "Application stopped before this job reached a terminal state. Review the result and start a new job if needed.", row["id"]),
                )
        upload_root = (app_data_dir() / "individual_uploads").resolve()
        for row in rows:
            if row["kind"] != "individual":
                continue
            try:
                payload = json.loads(row["payload_json"] or "{}")
                staged_path = resolve_within(upload_root, Path(payload.get("staged_path", "")))
                if staged_path.is_file():
                    staged_path.unlink()
            except (ValueError, OSError, TypeError):
                logger.warning("Could not safely clean interrupted individual-upload job %s", row["id"])
        with db_session() as conn:
            conn.execute(
                "UPDATE analysis_results SET injection_status='RECOVERY_REQUIRED',error=?,updated_at=? "
                "WHERE injection_status='IN_PROGRESS'",
                ("The application stopped during MKV injection. Inspect the original file and the backup before attempting another injection.", utc_now()),
            )
        for row in rows:
            log_event("WARNING", "JobManager", "A job was marked interrupted during startup recovery.", job_id=row["id"])

    def create_series_job(self, series_name: str, skip_indices: set[int] | None = None,
                          selected_slug: str | None = None) -> dict:
        scan_library()
        detail = get_series_detail(series_name)
        if not detail["episodes"]:
            raise ValueError(f"No MKV files found in '{series_name}'.")
        with db_session() as conn:
            existing = conn.execute(
                "SELECT id FROM jobs WHERE kind IN ('series','injection') AND series_name=? "
                "AND status IN ('queued','running') LIMIT 1",
                (series_name,),
            ).fetchone()
            if existing:
                raise RuntimeError(f"A processing job is already active for '{series_name}' ({existing['id']}).")
            job_id = uuid.uuid4().hex[:12]
            now = utc_now()
            payload = {"skip_indices": sorted(skip_indices or set()), "selected_anime_slug": selected_slug}
            try:
                conn.execute(
                    "INSERT INTO jobs(id,kind,series_name,status,stage,progress,total_items,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (job_id, "series", series_name, "queued", "queued", 0.0, len(detail["episodes"]), json.dumps(payload), now),
                )
            except sqlite3.IntegrityError as exc:
                raise RuntimeError(f"A processing job is already active for '{series_name}'.") from exc
        event = threading.Event()
        with self._lock:
            self._cancel[job_id] = event
        self._executor.submit(self._run_series, job_id, series_name, event, payload)
        log_event("INFO", "JobManager", "Series analysis job queued.", job_id=job_id, series_name=series_name)
        return self.get_job(job_id) or {"id": job_id, "status": "queued"}


    def create_injection_job(
        self,
        series_name: str,
        episode_ids: list[int],
        *,
        replace_existing_chapters: bool = False,
    ) -> dict:
        """Queue a durable, sequential batch chapter-injection job."""
        selected_ids = list(dict.fromkeys(int(value) for value in episode_ids))
        if not selected_ids:
            raise ValueError("Select at least one episode for chapter injection.")

        scan_library()
        detail = get_series_detail(series_name)
        episodes_by_id = {
            int(episode["id"]): episode
            for episode in detail["episodes"]
            if episode.get("id") is not None
        }
        missing = [episode_id for episode_id in selected_ids if episode_id not in episodes_by_id]
        if missing:
            raise ValueError("One or more selected episodes do not belong to this series or are no longer in the library.")

        eligible_states = {"AUTO_APPROVED", "REVIEW_RECOMMENDED", "MANUALLY_REVIEWED", "APPROVED"}
        for episode_id in selected_ids:
            episode = episodes_by_id[episode_id]
            if episode.get("analysis_status") != "COMPLETED" or not (episode.get("op") or episode.get("ed")):
                raise ValueError(f"{episode['file_name']}: no valid OP/ED detection is available for injection.")
            if episode.get("review_status") not in eligible_states:
                raise ValueError(
                    f"{episode['file_name']}: manual review is still required before injection."
                )
            if episode.get("injection_status") in {"SUCCEEDED", "IN_PROGRESS", "RECOVERY_REQUIRED"}:
                raise ValueError(
                    f"{episode['file_name']}: it is already injected or requires injection recovery."
                )

        with db_session() as conn:
            active = conn.execute(
                "SELECT id,kind FROM jobs WHERE lower(series_name)=lower(?) "
                "AND kind IN ('series','injection') AND status IN ('queued','running') LIMIT 1",
                (series_name,),
            ).fetchone()
            if active:
                raise RuntimeError(
                    f"Another {active['kind']} job is already active for '{series_name}' ({active['id']})."
                )
            job_id = uuid.uuid4().hex[:12]
            now = utc_now()
            payload = {
                "episode_ids": selected_ids,
                "replace_existing_chapters": bool(replace_existing_chapters),
            }
            conn.execute(
                "INSERT INTO jobs(id,kind,series_name,status,stage,progress,total_items,payload_json,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (job_id, "injection", series_name, "queued", "queued", 0.0,
                 len(selected_ids), json.dumps(payload), now),
            )
            for episode_id in selected_ids:
                episode = episodes_by_id[episode_id]
                conn.execute(
                    "INSERT INTO job_items(job_id,episode_id,item_key,file_name,status,progress) "
                    "VALUES(?,?,?,?,?,?)",
                    (job_id, episode_id, episode["file_path"], episode["file_name"], "queued", 0.0),
                )

        event = threading.Event()
        with self._lock:
            self._cancel[job_id] = event
        self._executor.submit(
            self._run_injection_batch,
            job_id,
            series_name,
            event,
            payload,
        )
        log_event(
            "INFO",
            "JobManager",
            "Batch chapter injection queued.",
            detail=f"{len(selected_ids)} episodes selected.",
            job_id=job_id,
            series_name=series_name,
        )
        return self.get_job(job_id) or {"id": job_id, "status": "queued"}

    def _run_injection_batch(self, job_id: str, series_name: str,
                             event: threading.Event, payload: dict) -> None:
        from backend.services.chapter_service import inject_episode_chapters

        episode_ids = payload.get("episode_ids") or []
        replace_existing = bool(payload.get("replace_existing_chapters"))
        total = max(len(episode_ids), 1)
        completed = 0
        errors = 0
        processed = 0
        try:
            self._update(
                job_id,
                status="running",
                stage="injecting_chapters",
                started_at=utc_now(),
                total_items=len(episode_ids),
            )
            detail = get_series_detail(series_name)
            episodes_by_id = {
                int(episode["id"]): episode
                for episode in detail["episodes"]
                if episode.get("id") is not None
            }

            for index, episode_id in enumerate(episode_ids):
                episode = episodes_by_id.get(int(episode_id))
                if episode is None:
                    errors += 1
                    processed += 1
                    with db_session() as conn:
                        missing_item = conn.execute(
                            "SELECT item_key,file_name FROM job_items WHERE job_id=? AND episode_id=?",
                            (job_id, int(episode_id)),
                        ).fetchone()
                    if missing_item:
                        self._update_item(
                            job_id,
                            missing_item["item_key"],
                            item_status="failed",
                            item_progress=100,
                            item_error="The episode file was removed or moved after batch selection.",
                            item_result={
                                "episode_id": int(episode_id),
                                "file_name": missing_item["file_name"],
                                "error": "The episode file was removed or moved after batch selection.",
                            },
                        )
                    continue
                item_key = episode["file_path"]
                file_name = episode["file_name"]
                if event.is_set():
                    for remaining_id in episode_ids[index:]:
                        remaining = episodes_by_id.get(int(remaining_id))
                        if remaining:
                            self._update_item(
                                job_id,
                                remaining["file_path"],
                                item_status="cancelled",
                                item_progress=0,
                                item_error="Batch injection cancelled before this episode started.",
                            )
                    break

                self._update(
                    job_id,
                    stage="injecting_chapters",
                    current_item=file_name,
                    progress=(index / total) * 100,
                )
                self._update_item(job_id, item_key, item_status="running", item_progress=0)
                try:
                    result = inject_episode_chapters(
                        int(episode_id),
                        replace_existing_chapters=replace_existing,
                    )
                except Exception as exc:
                    errors += 1
                    logger.exception("Batch injection failed for %s", file_name)
                    self._update_item(
                        job_id,
                        item_key,
                        item_status="failed",
                        item_progress=100,
                        item_error=str(exc),
                        item_result={"episode_id": episode_id, "file_name": file_name, "error": str(exc)},
                    )
                else:
                    completed += 1
                    self._update_item(
                        job_id,
                        item_key,
                        item_status="completed",
                        item_progress=100,
                        item_result=result,
                    )
                processed += 1
                self._update(
                    job_id,
                    stage="injecting_chapters",
                    current_item=file_name,
                    progress=(processed / total) * 100,
                )

            if event.is_set():
                final_status = "cancelled"
            elif errors and completed:
                final_status = "completed_with_errors"
            elif errors:
                final_status = "failed"
            else:
                final_status = "completed"
            self._update(
                job_id,
                status=final_status,
                stage=final_status,
                progress=100 if not event.is_set() else (processed / total) * 100,
                finished_at=utc_now(),
            )
            log_event(
                "INFO" if not errors else "WARNING",
                "JobManager",
                f"Batch chapter injection {final_status}.",
                detail=f"{completed} succeeded; {errors} failed; {len(episode_ids)} selected.",
                job_id=job_id,
                series_name=series_name,
            )
        except Exception as exc:
            logger.exception("Batch injection job %s failed", job_id)
            self._update(
                job_id,
                status="failed",
                stage="failed",
                error=str(exc),
                finished_at=utc_now(),
            )
            log_event(
                "ERROR", "JobManager", "Batch chapter injection job failed.",
                detail=str(exc), job_id=job_id, series_name=series_name,
            )
        finally:
            with self._lock:
                self._cancel.pop(job_id, None)


    def create_individual_job(self, staged_path: Path, series_name: str, selected_slug: str | None = None) -> dict:
        if not staged_path.exists() or staged_path.suffix.lower() != ".mkv":
            raise ValueError("The uploaded file is not a valid MKV file.")
        job_id = uuid.uuid4().hex[:12]
        payload = {"staged_path": str(staged_path.resolve()), "selected_anime_slug": selected_slug}
        with db_session() as conn:
            conn.execute(
                "INSERT INTO jobs(id,kind,series_name,status,stage,progress,total_items,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (job_id, "individual", series_name, "queued", "queued", 0.0, 1, json.dumps(payload), utc_now()),
            )
            conn.execute(
                "INSERT INTO job_items(job_id,item_key,file_name,status,progress) VALUES(?,?,?,?,?)",
                (job_id, str(staged_path.resolve()), staged_path.name, "queued", 0.0),
            )
        event = threading.Event()
        with self._lock:
            self._cancel[job_id] = event
        self._executor.submit(self._run_individual, job_id, staged_path, series_name, event, selected_slug)
        log_event("INFO", "JobManager", "Individual analysis job queued.", job_id=job_id, series_name=series_name, episode_name=staged_path.name)
        return self.get_job(job_id) or {"id": job_id, "status": "queued"}

    def _update(self, job_id: str, **fields: Any) -> None:
        mapping = {"stage": "stage", "status": "status", "progress": "progress", "current_item": "current_item",
                   "total_items": "total_items", "error": "error", "started_at": "started_at", "finished_at": "finished_at"}
        values = {mapping[k]: v for k, v in fields.items() if k in mapping}
        if not values:
            return
        sets = ",".join(f"{key}=?" for key in values)
        with db_session() as conn:
            conn.execute(f"UPDATE jobs SET {sets} WHERE id=?", (*values.values(), job_id))

    def _update_item(self, job_id: str, item_key: str, **fields: Any) -> None:
        allowed = {"status", "progress", "result_json", "error"}
        values = {k: v for k, v in fields.items() if k in allowed}
        if "item_result" in fields:
            values["result_json"] = json.dumps(fields["item_result"], ensure_ascii=False)
        if "item_error" in fields:
            values["error"] = fields["item_error"]
        if "item_status" in fields:
            values["status"] = fields["item_status"]
        if "item_progress" in fields:
            values["progress"] = fields["item_progress"]
        if not values:
            return
        sets = ",".join(f"{k}=?" for k in values)
        with db_session() as conn:
            conn.execute(f"UPDATE job_items SET {sets} WHERE job_id=? AND item_key=?", (*values.values(), job_id, item_key))

    def _run_series(self, job_id: str, series_name: str, event: threading.Event, payload: dict) -> None:
        self._update(job_id, status="running", stage="initializing", started_at=utc_now())
        try:
            # Create durable item rows before the worker starts processing.
            detail = get_series_detail(series_name)
            episode_keys = [episode["file_path"] for episode in detail["episodes"]]

            def update_job(**kwargs: Any) -> None:
                index = kwargs.pop("item_index", None)
                if "item_status" in kwargs or "item_result" in kwargs or "item_error" in kwargs or "item_progress" in kwargs:
                    if index is not None and 0 <= index < len(episode_keys):
                        self._update_item(job_id, episode_keys[index], **kwargs)
                    self._update(job_id, **{k: v for k, v in kwargs.items() if k in {"stage", "progress", "current_item", "total_items"}})
                    return
                self._update(job_id, **kwargs)

            # Create durable item rows before the worker starts processing.
            with db_session() as conn:
                for episode in detail["episodes"]:
                    conn.execute(
                        "INSERT OR IGNORE INTO job_items(job_id,episode_id,item_key,file_name,status,progress) VALUES(?,?,?,?,?,?)",
                        (job_id, episode["id"], episode["file_path"], episode["file_name"], "queued", 0.0),
                    )
            result = analyze_series(series_name, job_id, event, update_job,
                                    set(payload.get("skip_indices", [])), payload.get("selected_anime_slug"))
            self._update(job_id, status=result["status"], stage=result["status"], progress=100 if result["status"] != "cancelled" else float(0),
                         finished_at=utc_now(), error=None)
            log_event("INFO" if result["status"] == "completed" else "WARNING", "JobManager",
                      f"Series analysis job {result['status']}.", detail=f"{len(result['items'])} items; {result['errors']} errors.",
                      job_id=job_id, series_name=series_name)
        except Exception as exc:
            logger.exception("Series job %s failed", job_id)
            self._update(job_id, status="failed", stage="failed", finished_at=utc_now(), error=str(exc))
            log_event("ERROR", "JobManager", "Series analysis job failed.", detail=str(exc), job_id=job_id, series_name=series_name)
        finally:
            with self._lock:
                self._cancel.pop(job_id, None)

    def _run_individual(self, job_id: str, staged_path: Path, series_name: str, event: threading.Event, selected_slug: str | None = None) -> None:
        self._update(job_id, status="running", stage="analyzing", started_at=utc_now())
        try:
            if event.is_set():
                with db_session() as conn:
                    conn.execute("UPDATE job_items SET status='cancelled' WHERE job_id=?", (job_id,))
                self._update(job_id, status="cancelled", stage="cancelled", finished_at=utc_now())
                return
            def template_progress(stage: str, percentage: float, name: str) -> None:
                if event.is_set():
                    raise InterruptedError("Analysis cancellation requested.")
                self._update(
                    job_id,
                    stage=stage,
                    current_item=name,
                    progress=percentage * 0.2,
                )
            def episode_progress(pct: float) -> None:
                self._update(job_id, stage="analyzing", progress=20 + pct * 0.8, current_item=staged_path.name)
            result = analyze_path(staged_path, series_name, selected_slug=selected_slug,
                                  template_progress=template_progress, episode_progress=episode_progress,
                                  cancel_event=event)
            with db_session() as conn:
                conn.execute("UPDATE job_items SET status='completed',progress=100,result_json=? WHERE job_id=?",
                             (json.dumps(result, ensure_ascii=False), job_id))
            self._update(job_id, status="completed", stage="completed", progress=100, finished_at=utc_now())
            log_event("INFO", "JobManager", "Individual analysis completed; original file was not modified.",
                      job_id=job_id, series_name=series_name, episode_name=staged_path.name)
        except InterruptedError as exc:
            with db_session() as conn:
                conn.execute("UPDATE job_items SET status='cancelled',error=? WHERE job_id=?", (str(exc), job_id))
            self._update(job_id, status="cancelled", stage="cancelled", finished_at=utc_now())
            log_event("WARNING", "JobManager", "Individual analysis cancelled.", detail=str(exc), job_id=job_id,
                      series_name=series_name, episode_name=staged_path.name)
        except Exception as exc:
            logger.exception("Individual analysis job %s failed", job_id)
            with db_session() as conn:
                conn.execute("UPDATE job_items SET status='failed',error=? WHERE job_id=?", (str(exc), job_id))
            self._update(job_id, status="failed", stage="failed", finished_at=utc_now(), error=str(exc))
            log_event("ERROR", "JobManager", "Individual analysis failed.", detail=str(exc), job_id=job_id,
                      series_name=series_name, episode_name=staged_path.name)
        finally:
            staged_path.unlink(missing_ok=True)
            with self._lock:
                self._cancel.pop(job_id, None)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            event = self._cancel.get(job_id)
        if not event:
            return False
        event.set()
        self._update(job_id, stage="cancellation_requested")
        return True

    def get_job(self, job_id: str) -> dict | None:
        with db_session() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                return None
            items = conn.execute("SELECT * FROM job_items WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
        job = dict(row)
        payload = json.loads(job.pop("payload_json") or "{}")
        # Local staged paths are internal implementation details, not API data.
        payload.pop("staged_path", None)
        job["payload"] = payload
        job["items"] = []
        for item in items:
            value = dict(item)
            value["result"] = json.loads(value.pop("result_json")) if value.get("result_json") else None
            job["items"].append(value)
        return job

    def list_jobs(self, limit: int = 100) -> list[dict]:
        with db_session() as conn:
            ids = conn.execute("SELECT id FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [job for row in ids if (job := self.get_job(row["id"])) is not None]


job_service = JobService()
