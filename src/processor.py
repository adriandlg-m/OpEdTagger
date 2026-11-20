# src/processor.py
import statistics
from pathlib import Path

from src.analyzer import evaluate_episode_templates
from src.database import save_master_db
from src.mkv_tools import apply_mkv_chapters_from_db
from src.utils import parse_season_episode, write_audit_log


def _detect_outliers(results: list[dict], threshold_seconds: float = 180.0) -> None:
    """Marks anomalous OP/ED start timestamps based on median deviation."""
    op_starts = [r["op"]["start"] for r in results if r.get("op") and r["op"].get("start") is not None]
    ed_starts = [r["ed"]["start"] for r in results if r.get("ed") and r["ed"].get("start") is not None]

    if op_starts:
        median_op = statistics.median(op_starts)
        for row in results:
            op_start = row["op"]["start"] if row.get("op") else None
            if op_start is not None and abs(op_start - median_op) > threshold_seconds:
                row["is_anomaly_op"] = True

    if ed_starts:
        median_ed = statistics.median(ed_starts)
        for row in results:
            ed_start = row["ed"]["start"] if row.get("ed") else None
            if ed_start is not None and abs(ed_start - median_ed) > threshold_seconds:
                row["is_anomaly_ed"] = True


def process_series_pipeline(
    series_name: str,
    target_lib_folder: Path,
    mkv_files: list[Path],
    wav_templates: list,
    master_db: dict,
    plugins_path: Path,
    on_episode_progress=None,
) -> list[dict]:
    """Processes series MKV episodes using cache or audio template evaluation and injects chapters."""
    if series_name not in master_db:
        master_db[series_name] = {}

    results: list[dict] = []
    total_episodes = len(mkv_files)

    for idx, mkv in enumerate(mkv_files):
        parsed = parse_season_episode(mkv.name)
        display_name = f"S{parsed[0]:02d}E{parsed[1]:02d}" if parsed else mkv.stem[:12]

        cached_episode = master_db.get(series_name, {}).get(mkv.name)

        if cached_episode:
            op_data = cached_episode.get("op")
            ed_data = cached_episode.get("ed")

            success, error_msg = apply_mkv_chapters_from_db(mkv, cached_episode, plugins_path)
            applied_status = "applied" if success else "error"
            if not success:
                write_audit_log(series_name, mkv.name, f"Error applying chapters from cache: {error_msg}")

            results.append({
                "index": idx,
                "file_name": mkv.name,
                "display_name": display_name,
                "source": "cache",
                "op": op_data,
                "ed": ed_data,
                "is_anomaly_op": False,
                "is_anomaly_ed": False,
                "db_status": "cached",
                "applied_status": applied_status,
                "error": error_msg if not success else None,
            })
            continue

        def update_prog(pct: float, ep_idx: int = idx) -> None:
            if callable(on_episode_progress):
                on_episode_progress(ep_idx, total_episodes, pct)

        op_candidates, ed_candidates = evaluate_episode_templates(
            mkv, wav_templates, plugins_path, update_prog
        )

        best_op = max(op_candidates, key=lambda x: x["score"]) if op_candidates else None
        best_ed = max(ed_candidates, key=lambda x: x["score"]) if ed_candidates else None

        op_record = (
            {
                "name": best_op["name"],
                "start": best_op["start"],
                "end": best_op["end"],
                "score": best_op["score"],
            }
            if best_op
            else None
        )
        ed_record = (
            {
                "name": best_ed["name"],
                "start": best_ed["start"],
                "end": best_ed["end"],
                "score": best_ed["score"],
            }
            if best_ed
            else None
        )

        if not best_op and not best_ed:
            write_audit_log(series_name, mkv.name, "Critical Alert: No template exceeded the threshold.")
        elif not best_op:
            write_audit_log(series_name, mkv.name, f"Alert: Only {best_ed['name']} detected, OP missing.")
        elif not best_ed:
            write_audit_log(series_name, mkv.name, f"Alert: Only {best_op['name']} detected, ED missing.")

        master_db[series_name][mkv.name] = {
            "op": op_record,
            "ed": ed_record,
        }
        save_master_db(master_db)
        db_status = "saved"

        error_msg = None
        if op_record or ed_record:
            success, error_msg = apply_mkv_chapters_from_db(
                mkv, master_db[series_name][mkv.name], plugins_path
            )
            applied_status = "applied" if success else "mkv_error"
            if not success:
                write_audit_log(series_name, mkv.name, f"Error injecting chapters into MKV: {error_msg}")
        else:
            applied_status = "na"

        results.append({
            "index": idx,
            "file_name": mkv.name,
            "display_name": display_name,
            "source": "evaluated",
            "op": op_record,
            "ed": ed_record,
            "is_anomaly_op": False,
            "is_anomaly_ed": False,
            "db_status": db_status,
            "applied_status": applied_status,
            "error": error_msg,
        })

    _detect_outliers(results)
    return results