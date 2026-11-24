from __future__ import annotations

import json
import logging
import statistics
import threading
import wave
from pathlib import Path
from typing import Callable

from backend.infrastructure.config import load_config, resolve_library_path, resolve_within
from backend.infrastructure.database import db_session, utc_now
from backend.services.library_service import scan_library, get_series_detail
from backend.services.log_service import log_event
from backend.services.template_service import ensure_templates
from backend.services.plugin_manager import ensure_analysis_plugins
from core.analyzer import evaluate_episode_templates, prepare_song_templates
from core.fingerprints import ANALYZER_VERSION, file_fingerprint, templates_signature
from core.media_tools import build_chapters, get_mkv_duration

logger = logging.getLogger(__name__)


def _get_episode(episode_id: int) -> dict:
    with db_session() as conn:
        row = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
    if not row:
        raise FileNotFoundError("Episode not found. Scan the library again.")
    record = dict(row)
    try:
        resolve_within(resolve_library_path(), Path(record["file_path"]))
    except Exception as exc:
        raise PermissionError("The episode file is no longer inside the configured library.") from exc
    return record


def _persist_analysis(episode_id: int | None, path: Path, op: dict | None, ed: dict | None,
                      template_sig: str, *, source: str = "engine", expected_fingerprint: str | None = None) -> None:
    stat = path.stat()
    status = "NO_MATCH" if not op and not ed else "COMPLETED"
    review = "NOT_REQUIRED" if status == "NO_MATCH" else "PENDING"
    stamp = utc_now()
    fingerprint = file_fingerprint(path)
    if expected_fingerprint and fingerprint != expected_fingerprint:
        raise RuntimeError(f"The MKV changed during analysis: {path.name}. Run the analysis again.")
    if episode_id is None:
        return
    with db_session() as conn:
        conn.execute(
            "INSERT INTO analysis_results(episode_id,analysis_status,review_status,injection_status,op_json,ed_json,"
            "analyzer_version,template_signature,analyzed_file_size,analyzed_file_mtime,content_fingerprint,source,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(episode_id) DO UPDATE SET "
            "analysis_status=excluded.analysis_status,review_status=excluded.review_status,"
            "op_json=excluded.op_json,ed_json=excluded.ed_json,is_anomaly_op=0,is_anomaly_ed=0,"
            "analyzer_version=excluded.analyzer_version,template_signature=excluded.template_signature,"
            "analyzed_file_size=excluded.analyzed_file_size,"
            "analyzed_file_mtime=excluded.analyzed_file_mtime,content_fingerprint=excluded.content_fingerprint,"
            "injection_status=CASE WHEN analysis_results.injection_status IN ('IN_PROGRESS','RECOVERY_REQUIRED') "
            "THEN analysis_results.injection_status ELSE excluded.injection_status END,"
            "source=excluded.source,error=NULL,updated_at=excluded.updated_at",
            (episode_id, status, review, "NOT_REQUESTED", json.dumps(op) if op else None,
             json.dumps(ed) if ed else None, ANALYZER_VERSION, template_sig, stat.st_size,
             stat.st_mtime, fingerprint, source, stamp, stamp),
        )


def _persist_analysis_failure(episode_id: int, path: Path, template_sig: str, error: str) -> None:
    """Persist a per-episode failure without erasing an injection recovery lock."""
    try:
        stat = path.stat()
        fingerprint = file_fingerprint(path)
    except (OSError, RuntimeError):
        stat = None
        fingerprint = None
    stamp = utc_now()
    with db_session() as conn:
        conn.execute(
            "INSERT INTO analysis_results(episode_id,analysis_status,review_status,injection_status,op_json,ed_json,"
            "analyzer_version,template_signature,analyzed_file_size,analyzed_file_mtime,content_fingerprint,source,error,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(episode_id) DO UPDATE SET "
            "analysis_status='FAILED',review_status='NOT_REQUIRED',op_json=NULL,ed_json=NULL,is_anomaly_op=0,is_anomaly_ed=0,"
            "analyzer_version=excluded.analyzer_version,template_signature=excluded.template_signature,"
            "analyzed_file_size=excluded.analyzed_file_size,analyzed_file_mtime=excluded.analyzed_file_mtime,"
            "content_fingerprint=excluded.content_fingerprint,"
            "injection_status=CASE WHEN analysis_results.injection_status IN ('IN_PROGRESS','RECOVERY_REQUIRED') "
            "THEN analysis_results.injection_status ELSE 'NOT_REQUESTED' END,"
            "source='engine',error=excluded.error,updated_at=excluded.updated_at",
            (episode_id, "FAILED", "NOT_REQUIRED", "NOT_REQUESTED", None, None, ANALYZER_VERSION, template_sig,
             stat.st_size if stat else None, stat.st_mtime if stat else None, fingerprint, "engine", error, stamp, stamp),
        )


def _valid_cache(episode_id: int, path: Path, template_sig: str) -> dict | None:
    stat = path.stat()
    with db_session() as conn:
        row = conn.execute("SELECT * FROM analysis_results WHERE episode_id=?", (episode_id,)).fetchone()
    if not row:
        return None
    if row["analysis_status"] not in {"COMPLETED", "NO_MATCH"}:
        return None
    if row["analyzer_version"] != ANALYZER_VERSION or row["template_signature"] != template_sig:
        return None
    if row["analyzed_file_size"] != stat.st_size or row["analyzed_file_mtime"] != stat.st_mtime:
        return None
    if not row["content_fingerprint"] or row["content_fingerprint"] != file_fingerprint(path):
        return None
    return {"op": json.loads(row["op_json"]) if row["op_json"] else None,
            "ed": json.loads(row["ed_json"]) if row["ed_json"] else None,
            "analysis_status": row["analysis_status"], "review_status": row["review_status"],
            "injection_status": row["injection_status"], "is_anomaly_op": bool(row["is_anomaly_op"]),
            "is_anomaly_ed": bool(row["is_anomaly_ed"]), "source": "cache"}


def assert_analysis_file_unchanged(episode: dict, result) -> None:
    """Refuse review/injection when the MKV differs from the analyzed file."""
    path = Path(episode["file_path"])
    if not path.exists() or not path.is_file():
        raise FileNotFoundError("The analyzed MKV no longer exists.")
    stat = path.stat()
    if (result["analyzed_file_size"] != stat.st_size
            or result["analyzed_file_mtime"] != stat.st_mtime
            or not result["content_fingerprint"]
            or result["content_fingerprint"] != file_fingerprint(path)):
        raise ValueError("The MKV has changed since this analysis. Run the analysis again before reviewing or injecting chapters.")


def _find_episode_id(path: Path) -> int | None:
    with db_session() as conn:
        row = conn.execute("SELECT id FROM episodes WHERE file_path=?", (str(path.resolve()),)).fetchone()
    return int(row["id"]) if row else None


def _validate_wav_templates(wav_templates: list[Path], series_name: str) -> list[Path]:
    """Exclude broken/empty WAVs before they reach the protected detector."""
    valid: list[Path] = []
    invalid: list[str] = []
    for template in wav_templates:
        try:
            with wave.open(str(template), "rb") as audio:
                if audio.getnframes() <= 0 or audio.getframerate() <= 0 or audio.getnchannels() <= 0:
                    raise wave.Error("WAV has no usable audio frames.")
                if audio.getsampwidth() not in {1, 2, 3, 4}:
                    raise wave.Error("WAV has an unsupported sample width.")
            valid.append(template)
        except (OSError, EOFError, wave.Error) as exc:
            invalid.append(f"{template.name} ({exc})")

    if invalid:
        logger.warning("Ignoring invalid WAV templates for %s: %s", series_name, "; ".join(invalid))
    if not valid:
        detail = "; ".join(invalid[:8]) if invalid else "No WAV files were generated."
        raise RuntimeError(
            f"No valid WAV templates are available for '{series_name}'. "
            f"Check the downloaded videos and FFmpeg conversion. {detail}"
        )
    return valid


def _get_templates(
    series_name: str,
    selected_slug: str | None,
    progress: Callable[[str, float, str], None] | None = None,
) -> tuple[list[Path], str]:
    cfg = load_config()
    plugin_state = ensure_analysis_plugins(cfg)
    if plugin_state.get("status") not in {"ready", "analysis_ready"}:
        reason = plugin_state.get("last_error") or plugin_state.get("message") or "FFmpeg/FFprobe are unavailable."
        raise RuntimeError(f"Multimedia tools are not ready: {reason}")

    if progress:
        progress("searching_templates", 0.5, f"Preparando templates de {series_name}")
    template_dir = ensure_templates(series_name, selected_slug, progress)

    def conversion_progress(index: int, total: int, name: str) -> None:
        if progress:
            percentage = 72.0 + ((index + 1) / max(total, 1)) * 18.0
            progress("converting_templates", percentage, f"Verificando conversión WAV · {name}")

    wav_templates = prepare_song_templates(
        template_dir,
        Path(cfg["plugins_path"]).expanduser().resolve(),
        on_template_progress=conversion_progress,
    )
    if progress:
        progress("validating_templates", 94.0, "Validando archivos WAV")
    wav_templates = _validate_wav_templates(wav_templates, series_name)
    if progress:
        progress("validating_templates", 100.0, f"{len(wav_templates)} plantillas WAV válidas")
    return wav_templates, templates_signature(wav_templates)

def _best(candidates: list[dict]) -> dict | None:
    return max(candidates, key=lambda item: item["score"]) if candidates else None


def _as_record(candidate: dict | None) -> dict | None:
    if not candidate:
        return None
    return {"name": candidate["name"], "start": float(candidate["start"]),
            "end": float(candidate["end"]), "score": float(candidate["score"])}


def analyze_path(path: Path, series_name: str, *, selected_slug: str | None = None,
                 episode_id: int | None = None, template_progress: Callable | None = None,
                 episode_progress: Callable | None = None, cancel_event: threading.Event | None = None) -> dict:
    if not path.exists() or not path.is_file() or path.suffix.lower() != ".mkv":
        raise FileNotFoundError("The selected file must be an existing MKV.")
    if episode_id is not None:
        with db_session() as conn:
            existing = conn.execute("SELECT injection_status FROM analysis_results WHERE episode_id=?", (episode_id,)).fetchone()
        if existing and existing["injection_status"] in {"IN_PROGRESS", "RECOVERY_REQUIRED"}:
            raise RuntimeError("This episode requires injection recovery before it can be analyzed again.")
    cfg = load_config()
    plugins = Path(cfg["plugins_path"]).expanduser().resolve()
    original_fingerprint = file_fingerprint(path)
    wavs, signature = _get_templates(series_name, selected_slug, template_progress)
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Analysis cancellation requested.")
    cached = _valid_cache(episode_id, path, signature) if episode_id is not None else None
    if cached and templates_signature(wavs) == signature and file_fingerprint(path) == original_fingerprint:
        return {**cached, "file_name": path.name, "file_path": str(path), "cached": True}

    op_candidates, ed_candidates = evaluate_episode_templates(
        path, wavs, plugins, progress_callback=episode_progress, cancel_event=cancel_event
    )
    op, ed = _as_record(_best(op_candidates)), _as_record(_best(ed_candidates))
    if templates_signature(wavs) != signature:
        raise RuntimeError("Templates changed during analysis. Run the analysis again.")
    _persist_analysis(episode_id, path, op, ed, signature, expected_fingerprint=original_fingerprint)
    result = {"file_name": path.name, "file_path": str(path), "op": op, "ed": ed,
              "analysis_status": "NO_MATCH" if not op and not ed else "COMPLETED",
              "review_status": "NOT_REQUIRED" if not op and not ed else "PENDING",
              "injection_status": "NOT_REQUESTED", "is_anomaly_op": False,
              "is_anomaly_ed": False, "source": "engine", "cached": False}
    log_event("INFO", "Analysis", "Episode analysis completed." if op or ed else "No OP/ED match found.",
              detail=f"File: {path.name}; OP: {'found' if op else 'not found'}; ED: {'found' if ed else 'not found'}.",
              series_name=series_name, episode_name=path.name)
    return result


def _apply_anomalies(series_name: str) -> None:
    detail = get_series_detail(series_name)
    episodes = detail["episodes"]
    op_values = [e["op"]["start"] for e in episodes if e.get("analysis_status") == "COMPLETED" and e.get("op")]
    ed_values = [e["ed"]["start"] for e in episodes if e.get("analysis_status") == "COMPLETED" and e.get("ed")]
    median_op = statistics.median(op_values) if op_values else None
    median_ed = statistics.median(ed_values) if ed_values else None
    with db_session() as conn:
        for episode in episodes:
            if episode.get("id") is None:
                continue
            is_completed = episode.get("analysis_status") == "COMPLETED"
            anomaly_op = bool(is_completed and median_op is not None and episode.get("op")
                              and abs(episode["op"]["start"] - median_op) > 180)
            anomaly_ed = bool(is_completed and median_ed is not None and episode.get("ed")
                              and abs(episode["ed"]["start"] - median_ed) > 180)
            conn.execute("UPDATE analysis_results SET is_anomaly_op=?,is_anomaly_ed=?,updated_at=? WHERE episode_id=?",
                         (int(anomaly_op), int(anomaly_ed), utc_now(), episode["id"]))


def analyze_series(series_name: str, job_id: str, cancel_event: threading.Event,
                   update_job: Callable, skip_indices: set[int] | None = None,
                   selected_slug: str | None = None) -> dict:
    scan_library()
    detail = get_series_detail(series_name)
    episodes = detail["episodes"]
    if not episodes:
        raise RuntimeError(f"No MKV files were found for '{series_name}'.")
    update_job(stage="preparing_dependencies", current_item="FFmpeg / FFprobe", total_items=len(episodes), progress=0)
    if cancel_event.is_set():
        for index, episode in enumerate(episodes):
            update_job(item_index=index, item_status="cancelled", item_progress=0,
                       current_item=episode["file_name"])
        return {"status": "cancelled", "items": [], "errors": 0, "total": len(episodes)}

    def template_cb(stage: str, percentage: float, name: str) -> None:
        if cancel_event.is_set():
            raise InterruptedError("Analysis cancellation requested.")
        update_job(stage=stage, current_item=name, progress=percentage)

    try:
        wavs, signature = _get_templates(series_name, selected_slug, template_cb)
    except InterruptedError:
        for index, episode in enumerate(episodes):
            update_job(item_index=index, item_status="cancelled", item_progress=0,
                       current_item=episode["file_name"])
        return {"status": "cancelled", "items": [], "errors": 0, "total": len(episodes)}
    cfg = load_config()
    plugins = Path(cfg["plugins_path"]).expanduser().resolve()
    skip_indices = skip_indices or set()
    items = []
    errors = 0
    total = len(episodes)
    for index, episode in enumerate(episodes):
        if cancel_event.is_set():
            update_job(stage="cancelling", current_item=episode["file_name"])
            break
        path = Path(episode["file_path"])
        if index in skip_indices:
            items.append({"file_name": path.name, "analysis_status": "SKIPPED", "op": None, "ed": None})
            update_job(item_index=index, item_status="skipped", item_progress=100,
                       current_item=path.name, progress=(index + 1) / total * 100)
            continue
        episode_id = int(episode["id"])
        update_job(stage="analyzing_episodes", current_item=path.name,
                   item_index=index, item_status="running", item_progress=0,
                   progress=index / total * 100)
        try:
            with db_session() as conn:
                injection_state = conn.execute(
                    "SELECT injection_status FROM analysis_results WHERE episode_id=?", (episode_id,)
                ).fetchone()
            if injection_state and injection_state["injection_status"] in {"IN_PROGRESS", "RECOVERY_REQUIRED"}:
                raise RuntimeError("This episode requires injection recovery before it can be analyzed again.")
            original_fingerprint = file_fingerprint(path)
            cached = _valid_cache(episode_id, path, signature)
            if cached:
                result = {**cached, "file_name": path.name, "file_path": str(path), "cached": True}
            else:
                def cb(pct: float) -> None:
                    update_job(stage="analyzing_episodes", current_item=path.name,
                               item_index=index, item_status="running", item_progress=pct,
                               progress=(index + pct / 100) / total * 100)
                op_candidates, ed_candidates = evaluate_episode_templates(path, wavs, plugins, cb, cancel_event)
                op, ed = _as_record(_best(op_candidates)), _as_record(_best(ed_candidates))
                if templates_signature(wavs) != signature:
                    raise RuntimeError("Templates changed during analysis. Run the analysis again.")
                _persist_analysis(episode_id, path, op, ed, signature, expected_fingerprint=original_fingerprint)
                result = {"file_name": path.name, "file_path": str(path), "op": op, "ed": ed,
                          "analysis_status": "NO_MATCH" if not op and not ed else "COMPLETED",
                          "review_status": "NOT_REQUIRED" if not op and not ed else "PENDING",
                          "injection_status": "NOT_REQUESTED", "is_anomaly_op": False,
                          "is_anomaly_ed": False, "source": "engine", "cached": False}
                log_event("INFO", "Analysis", "Episode analysis completed." if op or ed else "No OP/ED match found.",
                          detail=f"File: {path.name}; OP: {'found' if op else 'not found'}; ED: {'found' if ed else 'not found'}.",
                          job_id=job_id, series_name=series_name, episode_name=path.name)
            items.append(result)
            update_job(item_index=index, item_status="completed", item_progress=100,
                       item_result=result, current_item=path.name, progress=(index + 1) / total * 100)
        except InterruptedError as exc:
            items.append({"file_name": path.name, "analysis_status": "CANCELLED", "op": None, "ed": None})
            update_job(item_index=index, item_status="cancelled", item_progress=0,
                       item_error=str(exc), current_item=path.name)
            for remaining_index in range(index + 1, total):
                update_job(item_index=remaining_index, item_status="cancelled", item_progress=0,
                           current_item=episodes[remaining_index]["file_name"])
            break
        except Exception as exc:
            errors += 1
            logger.exception("Episode analysis failed for %s", path)
            try:
                _persist_analysis_failure(episode_id, path, signature, str(exc))
            except Exception:
                logger.exception("Could not persist failed analysis state for %s", path)
            log_event("ERROR", "Analysis", "Episode analysis failed.", detail=str(exc), job_id=job_id,
                      series_name=series_name, episode_name=path.name)
            items.append({"file_name": path.name, "analysis_status": "FAILED", "op": None, "ed": None, "error": str(exc)})
            update_job(item_index=index, item_status="failed", item_progress=100,
                       item_error=str(exc), current_item=path.name, progress=(index + 1) / total * 100)
    _apply_anomalies(series_name)
    if cancel_event.is_set():
        final = "cancelled"
    elif errors:
        final = "completed_with_errors" if len(items) > errors else "failed"
    else:
        final = "completed"
    return {"status": final, "items": items, "errors": errors, "total": total}


def preview_episode_chapters(episode_id: int) -> dict:
    episode = _get_episode(episode_id)
    with db_session() as conn:
        row = conn.execute("SELECT * FROM analysis_results WHERE episode_id=?", (episode_id,)).fetchone()
    if not row:
        raise ValueError("This episode has no analysis result yet.")
    assert_analysis_file_unchanged(episode, row)
    op = json.loads(row["op_json"]) if row["op_json"] else None
    ed = json.loads(row["ed_json"]) if row["ed_json"] else None
    cfg = load_config()
    duration = get_mkv_duration(Path(episode["file_path"]), Path(cfg["plugins_path"]))
    chapters = build_chapters(op, ed, duration)
    return {"episode_id": episode_id, "file_name": episode["file_name"], "duration": duration,
            "chapters": chapters, "review_status": row["review_status"],
            "injection_status": row["injection_status"]}
