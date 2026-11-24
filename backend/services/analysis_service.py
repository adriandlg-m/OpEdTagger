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
from core.confidence import CONFIDENCE_MODEL_VERSION, assess_segment, episode_review_status
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
    op = assess_segment(op)
    ed = assess_segment(ed)
    status = "NO_MATCH" if not op and not ed else "COMPLETED"
    review = episode_review_status(op, ed)
    stamp = utc_now()
    fingerprint = file_fingerprint(path)
    if expected_fingerprint and fingerprint != expected_fingerprint:
        raise RuntimeError(f"The MKV changed during analysis: {path.name}. Run the analysis again.")
    if episode_id is None:
        return
    with db_session() as conn:
        conn.execute(
            "INSERT INTO analysis_results(episode_id,analysis_status,review_status,injection_status,op_json,ed_json,"
            "op_original_json,ed_original_json,confidence_version,analyzer_version,template_signature,"
            "analyzed_file_size,analyzed_file_mtime,content_fingerprint,source,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(episode_id) DO UPDATE SET "
            "analysis_status=excluded.analysis_status,review_status=excluded.review_status,"
            "op_json=excluded.op_json,ed_json=excluded.ed_json,"
            "op_original_json=excluded.op_original_json,ed_original_json=excluded.ed_original_json,"
            "confidence_version=excluded.confidence_version,is_anomaly_op=0,is_anomaly_ed=0,"
            "analyzer_version=excluded.analyzer_version,template_signature=excluded.template_signature,"
            "analyzed_file_size=excluded.analyzed_file_size,"
            "analyzed_file_mtime=excluded.analyzed_file_mtime,content_fingerprint=excluded.content_fingerprint,"
            "injection_status=CASE WHEN analysis_results.injection_status IN ('IN_PROGRESS','RECOVERY_REQUIRED') "
            "THEN analysis_results.injection_status ELSE excluded.injection_status END,"
            "source=excluded.source,error=NULL,updated_at=excluded.updated_at",
            (episode_id, status, review, "NOT_REQUESTED", json.dumps(op) if op else None,
             json.dumps(ed) if ed else None, json.dumps(op) if op else None, json.dumps(ed) if ed else None,
             CONFIDENCE_MODEL_VERSION, ANALYZER_VERSION, template_sig, stat.st_size,
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
    if row["confidence_version"] != CONFIDENCE_MODEL_VERSION:
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
    record = {"name": candidate["name"], "start": float(candidate["start"]),
              "end": float(candidate["end"]), "score": float(candidate["score"])}
    for key in ("similarity_score", "peak_prominence", "template_frames", "template_margin"):
        if key in candidate:
            record[key] = candidate[key]
    return record


def _best_record(candidates: list[dict]) -> dict | None:
    winner = _best(candidates)
    if not winner:
        return None
    record = _as_record(winner)
    winner_similarity = winner.get("similarity_score")
    alternatives = [
        float(item["similarity_score"])
        for item in candidates
        if item is not winner
        and item.get("similarity_score") is not None
    ]
    if winner_similarity is not None and alternatives:
        runner_up = max(alternatives)
        record["template_margin"] = max(
            0.0,
            min(1.0, (float(winner_similarity) - runner_up) / max(abs(float(winner_similarity)), 1e-8)),
        )
    return record


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
    op, ed = _best_record(op_candidates), _best_record(ed_candidates)
    if templates_signature(wavs) != signature:
        raise RuntimeError("Templates changed during analysis. Run the analysis again.")
    _persist_analysis(episode_id, path, op, ed, signature, expected_fingerprint=original_fingerprint)
    op = assess_segment(op)
    ed = assess_segment(ed)
    result = {"file_name": path.name, "file_path": str(path), "op": op, "ed": ed,
              "analysis_status": "NO_MATCH" if not op and not ed else "COMPLETED",
              "review_status": episode_review_status(op, ed),
              "injection_status": "NOT_REQUESTED", "is_anomaly_op": False,
              "is_anomaly_ed": False, "source": "engine", "cached": False}
    log_event("INFO", "Analysis", "Episode analysis completed." if op or ed else "No OP/ED match found.",
              detail=f"File: {path.name}; OP: {'found' if op else 'not found'}; ED: {'found' if ed else 'not found'}.",
              series_name=series_name, episode_name=path.name)
    return result


def _apply_anomalies(series_name: str) -> None:
    """Score detections against other episodes that use the same OP/ED template.

    Cross-episode timing is only used when there are at least three comparable
    detections. Different openings/endings are never compared to one another.
    """
    with db_session() as conn:
        rows = conn.execute(
            "SELECT e.id AS episode_id, ar.op_json, ar.ed_json, "
            "ar.op_original_json, ar.ed_original_json "
            "FROM episodes e JOIN analysis_results ar ON ar.episode_id=e.id "
            "WHERE e.series_name=? AND ar.analysis_status='COMPLETED'",
            (series_name,),
        ).fetchall()

    records = []
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        current = {}
        original = {}
        for kind in ("OP", "ED"):
            current_json = row[f"{kind.lower()}_json"]
            original_json = row[f"{kind.lower()}_original_json"]
            try:
                current_segment = json.loads(current_json) if current_json else None
                original_segment = json.loads(original_json) if original_json else current_segment
            except (TypeError, ValueError):
                current_segment = original_segment = None
            current[kind] = current_segment
            original[kind] = original_segment
            # Keep dismissed and manually verified detections out of the
            # automatic outlier baseline. Their original values remain available
            # for audit, but must not influence current anomaly counts.
            if original_segment and current_segment and not current_segment.get("manual_verified"):
                key = (kind, str(original_segment.get("name") or kind))
                groups.setdefault(key, []).append({
                    "episode_id": int(row["episode_id"]),
                    "start": float(original_segment.get("start", 0.0)),
                })
        records.append({
            "episode_id": int(row["episode_id"]),
            "current": current,
            "original": original,
        })

    assessments: dict[tuple[int, str], tuple[dict | None, bool]] = {}
    for (kind, _template_name), matches in groups.items():
        starts = [item["start"] for item in matches]
        enough_samples = len(starts) >= 3
        median = statistics.median(starts) if enough_samples else None
        mad = statistics.median([abs(value - median) for value in starts]) if enough_samples else None
        # A robust tolerance adapts to normal timing variation but never becomes
        # narrower than 30 seconds for a stable OP/ED across episodes.
        anomaly_limit = max(30.0, 3.0 * 1.4826 * mad) if mad is not None else None
        consistency_scale = max(60.0, 2.0 * anomaly_limit) if anomaly_limit is not None else None

        for item in matches:
            segment = next(
                record["original"][kind]
                for record in records
                if record["episode_id"] == item["episode_id"]
            )
            consistency = None
            anomalous = False
            if enough_samples and median is not None and anomaly_limit is not None and consistency_scale is not None:
                deviation = abs(item["start"] - median)
                anomalous = deviation > anomaly_limit
                consistency = max(0.0, min(1.0, 1.0 - deviation / consistency_scale))
            assessed = assess_segment(segment, temporal_consistency=consistency, anomalous=anomalous)
            assessments[(item["episode_id"], kind)] = (assessed, anomalous)

    with db_session() as conn:
        for record in records:
            updated_segments = {}
            anomaly_flags = {}
            for kind in ("OP", "ED"):
                current_segment = record["current"][kind]
                original_segment = record["original"][kind]
                assessed, anomalous = assessments.get((record["episode_id"], kind), (None, False))
                if current_segment is None or (current_segment and current_segment.get("manual_verified")):
                    anomalous = False
                if assessed is not None:
                    if current_segment is not None:
                        current_segment = {
                            **current_segment,
                            "confidence_score": assessed.get("confidence_score"),
                            "confidence_band": assessed.get("confidence_band"),
                            "confidence_factors": assessed.get("confidence_factors"),
                        }
                    original_segment = assessed
                record["original"][kind] = assessed
                updated_segments[kind] = current_segment
                anomaly_flags[kind] = anomalous
            status = episode_review_status(updated_segments["OP"], updated_segments["ED"])
            conn.execute(
                "UPDATE analysis_results SET op_json=?,ed_json=?,op_original_json=?,ed_original_json=?,"
                "is_anomaly_op=?,is_anomaly_ed=?,review_status=?,confidence_version=?,updated_at=? "
                "WHERE episode_id=?",
                (
                    json.dumps(updated_segments["OP"], ensure_ascii=False) if updated_segments["OP"] else None,
                    json.dumps(updated_segments["ED"], ensure_ascii=False) if updated_segments["ED"] else None,
                    json.dumps(record["original"]["OP"], ensure_ascii=False) if record["original"]["OP"] else None,
                    json.dumps(record["original"]["ED"], ensure_ascii=False) if record["original"]["ED"] else None,
                    int(anomaly_flags["OP"]), int(anomaly_flags["ED"]), status,
                    CONFIDENCE_MODEL_VERSION, utc_now(), record["episode_id"],
                ),
            )


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
        update_job(stage=stage, current_item=name, progress=percentage * 0.2)

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
                       current_item=path.name, progress=20 + (index + 1) / total * 80)
            continue
        episode_id = int(episode["id"])
        update_job(stage="analyzing_episodes", current_item=path.name,
                   item_index=index, item_status="running", item_progress=0,
                   progress=20 + index / total * 80)
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
                               progress=20 + (index + pct / 100) / total * 80)
                op_candidates, ed_candidates = evaluate_episode_templates(path, wavs, plugins, cb, cancel_event)
                op, ed = _best_record(op_candidates), _best_record(ed_candidates)
                if templates_signature(wavs) != signature:
                    raise RuntimeError("Templates changed during analysis. Run the analysis again.")
                _persist_analysis(episode_id, path, op, ed, signature, expected_fingerprint=original_fingerprint)
                op = assess_segment(op)
                ed = assess_segment(ed)
                result = {"file_name": path.name, "file_path": str(path), "op": op, "ed": ed,
                          "analysis_status": "NO_MATCH" if not op and not ed else "COMPLETED",
                          "review_status": episode_review_status(op, ed),
                          "injection_status": "NOT_REQUESTED", "is_anomaly_op": False,
                          "is_anomaly_ed": False, "source": "engine", "cached": False}
                log_event("INFO", "Analysis", "Episode analysis completed." if op or ed else "No OP/ED match found.",
                          detail=f"File: {path.name}; OP: {'found' if op else 'not found'}; ED: {'found' if ed else 'not found'}.",
                          job_id=job_id, series_name=series_name, episode_name=path.name)
            items.append(result)
            update_job(item_index=index, item_status="completed", item_progress=100,
                       item_result=result, current_item=path.name, progress=20 + (index + 1) / total * 80)
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
                       item_error=str(exc), current_item=path.name, progress=20 + (index + 1) / total * 80)
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
