from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from backend.infrastructure.config import app_data_dir, load_config, resolve_library_path, resolve_within
from backend.infrastructure.database import db_session, utc_now
from core.fingerprints import ANALYZER_VERSION, file_fingerprint

VIDEO_SUFFIXES = {".mkv"}


def parse_season_episode(filename: str) -> tuple[int, int] | None:
    patterns = [r"[Ss](\d{1,2})[Ee](\d{1,3})", r"(\d{1,2})x(\d{1,3})", r"[Ss]eason\s*(\d{1,2}).*?[Ee]pisode\s*(\d{1,3})"]
    for pattern in patterns:
        match = re.search(pattern, filename)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


def _segment(row: Any, key: str) -> dict | None:
    import json
    value = row[key]
    return json.loads(value) if value else None


def scan_library() -> dict:
    cfg = load_config()
    root = resolve_library_path(cfg)
    discovered: list[dict] = []
    present_series: set[str] = set()
    scanned_at = utc_now()
    with db_session() as conn:
        for series_candidate in sorted(root.iterdir()):
            if not series_candidate.is_dir():
                continue
            try:
                series_dir = resolve_within(root, series_candidate)
            except ValueError:
                continue
            present_series.add(series_dir.name)
            files = []
            for candidate in series_dir.rglob("*"):
                if not candidate.is_file() or candidate.suffix.lower() not in VIDEO_SUFFIXES:
                    continue
                try:
                    files.append(resolve_within(series_dir, candidate))
                except ValueError:
                    # Never index a symlink that escapes the configured series directory.
                    continue
            files.sort()
            current_paths = {str(file_path.resolve()) for file_path in files}
            conn.execute(
                "INSERT INTO series(name,directory,episode_count,last_scanned_at) VALUES(?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET directory=excluded.directory, episode_count=excluded.episode_count,last_scanned_at=excluded.last_scanned_at",
                (series_dir.name, str(series_dir.resolve()), len(files), scanned_at),
            )
            for file_path in files:
                stat = file_path.stat()
                parsed = parse_season_episode(file_path.name)
                conn.execute(
                    "INSERT INTO episodes(series_name,file_path,file_name,file_size,file_mtime,season,episode_number,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(file_path) DO UPDATE SET "
                    "series_name=excluded.series_name,file_name=excluded.file_name,file_size=excluded.file_size,file_mtime=excluded.file_mtime,"
                    "season=excluded.season,episode_number=excluded.episode_number,updated_at=excluded.updated_at",
                    (series_dir.name, str(file_path.resolve()), file_path.name, stat.st_size, stat.st_mtime,
                     parsed[0] if parsed else None, parsed[1] if parsed else None, scanned_at, scanned_at),
                )
                discovered.append({"series": series_dir.name, "file": str(file_path.resolve())})

            # Reconcile catalog rows for files removed or moved out of this series.
            old_paths = {row["file_path"] for row in conn.execute(
                "SELECT file_path FROM episodes WHERE series_name=?", (series_dir.name,)
            ).fetchall()}
            for stale_path in old_paths - current_paths:
                conn.execute("DELETE FROM episodes WHERE file_path=?", (stale_path,))

        # Series folders removed from the library are removed from the catalog;
        # job history remains independent and is deliberately retained.
        old_series = {row["name"] for row in conn.execute("SELECT name FROM series").fetchall()}
        for stale_series in old_series - present_series:
            conn.execute("DELETE FROM series WHERE name=?", (stale_series,))

    return {"library_path": str(root), "total_series": len(present_series),
            "total_episodes": len(discovered), "scanned_at": scanned_at}


def list_series(*, scan: bool = True) -> dict:
    if scan:
        scan_library()
    cfg = load_config()
    root = resolve_library_path(cfg)
    template_root = Path(cfg["template_path"])
    with db_session() as conn:
        rows = conn.execute(
            "SELECT s.name,s.directory,s.episode_count,sa.provider AS artwork_provider,sa.external_id AS artwork_external_id, "
            "sa.selected_title AS artwork_title,sa.poster_file,sa.updated_at AS poster_updated_at, "
            "COUNT(CASE WHEN ar.analysis_status IN ('COMPLETED','NO_MATCH') AND ar.analyzed_file_size=e.file_size AND ar.analyzed_file_mtime=e.file_mtime THEN 1 END) AS analyzed, "
            "COUNT(CASE WHEN ar.analysis_status='COMPLETED' AND ar.analyzed_file_size=e.file_size "
            "AND ar.analyzed_file_mtime=e.file_mtime AND (ar.is_anomaly_op=1 OR ar.is_anomaly_ed=1) THEN 1 END) AS outliers, "
            "SUM(CASE WHEN ar.injection_status='SUCCEEDED' AND ar.analyzed_file_size=e.file_size "
            "AND ar.analyzed_file_mtime=e.file_mtime THEN 1 ELSE 0 END) AS injected "
            "FROM series s LEFT JOIN episodes e ON e.series_name=s.name "
            "LEFT JOIN analysis_results ar ON ar.episode_id=e.id "
            "LEFT JOIN series_artwork sa ON sa.series_name=s.name "
            "GROUP BY s.name ORDER BY s.name COLLATE NOCASE"
        ).fetchall()
    items = []
    for row in rows:
        template_dir = template_root / row["name"]
        videos = list((template_dir / "video_files").glob("*")) if (template_dir / "video_files").exists() else []
        wavs = list((template_dir / "songs_files").glob("*.wav")) if (template_dir / "songs_files").exists() else []
        total = int(row["episode_count"] or 0)
        analyzed = int(row["analyzed"] or 0)
        outliers = int(row["outliers"] or 0)
        if not total:
            status = "Sin archivos"
        elif analyzed == 0:
            status = "Pendiente"
        elif analyzed < total:
            status = "En curso"
        elif outliers:
            status = "Outliers"
        else:
            status = "Listo"
        from backend.services.artwork_service import poster_endpoint_url
        poster_url = poster_endpoint_url(row["name"], row["poster_file"], row["poster_updated_at"])
        items.append({"id": row["name"], "name": row["name"], "title": row["name"],
                      "directory": row["directory"], "episodes": total, "analyzed": analyzed,
                      "outliers": outliers, "injected": int(row["injected"] or 0), "status": status,
                      "has_templates": bool(videos or wavs), "artwork_provider": row["artwork_provider"],
                      "artwork_title": row["artwork_title"], "poster_url": poster_url})
    return {"library_path": str(root), "total_series": len(items), "series": items}


def get_series_detail(series_name: str) -> dict:
    root = resolve_library_path()
    series_dir = (root / series_name).resolve()
    try:
        series_dir.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("Invalid series path.") from exc
    if not series_dir.exists() or not series_dir.is_dir():
        raise FileNotFoundError(f"Series '{series_name}' was not found in the configured library.")
    episodes = []
    with db_session() as conn:
        files = []
        for candidate in series_dir.rglob("*"):
            if not candidate.is_file() or candidate.suffix.lower() not in VIDEO_SUFFIXES:
                continue
            try:
                files.append(resolve_within(series_dir, candidate))
            except ValueError:
                continue
        files.sort()
        for file_path in files:
            stat = file_path.stat()
            row = conn.execute(
                "SELECT e.*, ar.analysis_status,ar.review_status,ar.injection_status,ar.op_json,ar.ed_json,"
                "ar.op_original_json,ar.ed_original_json,ar.is_anomaly_op,ar.is_anomaly_ed,ar.error,"
                "ar.analyzed_file_size,ar.analyzed_file_mtime,ar.analyzer_version,ar.content_fingerprint,ar.source "
                "FROM episodes e LEFT JOIN analysis_results ar ON ar.episode_id=e.id WHERE e.file_path=?",
                (str(file_path.resolve()),),
            ).fetchone()
            same_file_metadata = bool(row and row["analyzed_file_size"] == stat.st_size
                                      and row["analyzed_file_mtime"] == stat.st_mtime)
            fingerprint_matches = False
            if same_file_metadata and row and row["content_fingerprint"]:
                try:
                    fingerprint_matches = row["content_fingerprint"] == file_fingerprint(file_path)
                except (OSError, RuntimeError):
                    fingerprint_matches = False
            current_result = bool(
                same_file_metadata and fingerprint_matches
                and row["analysis_status"] in ("COMPLETED", "NO_MATCH")
                and row["analyzer_version"] == ANALYZER_VERSION
            )
            failed_result = bool(same_file_metadata and fingerprint_matches and row and row["analysis_status"] == "FAILED")
            legacy_result = bool(same_file_metadata and row and row["source"] == "legacy_json")
            visible_result = current_result or legacy_result
            season_episode = parse_season_episode(file_path.name)
            display = f"S{season_episode[0]:02d}E{season_episode[1]:02d}" if season_episode else file_path.stem
            op = _segment(row, "op_json") if visible_result else None
            ed = _segment(row, "ed_json") if visible_result else None
            relative_path = file_path.relative_to(series_dir).as_posix()
            relative_parent = file_path.parent.relative_to(series_dir).as_posix()
            folder_path = "" if relative_parent == "." else relative_parent
            episodes.append({
                "id": row["id"] if row else None, "file_name": file_path.name,
                "display_name": display, "file_path": str(file_path.resolve()),
                # Preserve the user's exact folder hierarchy. This is deliberately
                # independent from season/cour metadata supplied by external APIs.
                "relative_path": relative_path,
                "folder_path": folder_path,
                "folder_segments": [part for part in folder_path.split("/") if part],
                "analysis_status": ("STALE" if legacy_result else row["analysis_status"]) if current_result or legacy_result or failed_result else "PENDING",
                "review_status": row["review_status"] if current_result else "NOT_REQUIRED",
                "injection_status": row["injection_status"] if row and (current_result or failed_result or row["injection_status"] in {"IN_PROGRESS", "RECOVERY_REQUIRED"}) else "NOT_REQUESTED",
                "op": op, "ed": ed,
                "legacy_result": legacy_result,
                "is_anomaly_op": bool(row["is_anomaly_op"]) if current_result else False,
                "is_anomaly_ed": bool(row["is_anomaly_ed"]) if current_result else False,
                "error": row["error"] if row and (current_result or legacy_result or failed_result) else None,
                "original_op": _segment(row, "op_original_json") if current_result else None,
                "original_ed": _segment(row, "ed_original_json") if current_result else None,
                "review_history": [],
            })
        history_rows = conn.execute(
            "SELECT h.episode_id,h.segment_type,h.action,h.before_json,h.after_json,h.created_at "
            "FROM analysis_segment_history h JOIN episodes e ON e.id=h.episode_id "
            "WHERE e.series_name=? ORDER BY h.id DESC",
            (series_name,),
        ).fetchall()
        history_by_episode: dict[int, list[dict]] = {}
        for history in history_rows:
            history_by_episode.setdefault(int(history["episode_id"]), []).append({
                "segment_type": history["segment_type"],
                "action": history["action"],
                "before": json.loads(history["before_json"]) if history["before_json"] else None,
                "after": json.loads(history["after_json"]) if history["after_json"] else None,
                "created_at": history["created_at"],
            })
        for episode in episodes:
            if episode["id"] is not None:
                episode["review_history"] = history_by_episode.get(int(episode["id"]), [])
        metadata = conn.execute(
            "SELECT provider,external_id,selected_title,poster_file,updated_at "
            "FROM series_artwork WHERE series_name=?",
            (series_name,),
        ).fetchone()
    from backend.services.artwork_service import poster_endpoint_url
    poster_url = poster_endpoint_url(
        series_name, metadata["poster_file"] if metadata else None,
        metadata["updated_at"] if metadata else None,
    )
    outliers = sum(1 for ep in episodes if ep["is_anomaly_op"] or ep["is_anomaly_ed"])
    analyzed = sum(1 for ep in episodes if ep["analysis_status"] in ("COMPLETED", "NO_MATCH"))
    return {"name": series_name, "directory": str(series_dir), "total_episodes": len(episodes),
            "analyzed_episodes": analyzed, "outliers": outliers, "episodes": episodes,
            "artwork_provider": metadata["provider"] if metadata else None,
            "artwork_external_id": metadata["external_id"] if metadata else None,
            "artwork_title": metadata["selected_title"] if metadata else None,
            "poster_url": poster_url}


def dashboard_summary(*, scan: bool = False) -> dict:
    result = list_series(scan=scan)
    total_eps = sum(s["episodes"] for s in result["series"])
    analyzed = sum(s["analyzed"] for s in result["series"])
    outliers = sum(s["outliers"] for s in result["series"])
    with db_session() as conn:
        active_jobs = conn.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
        recent = conn.execute("SELECT id,kind,series_name,status,created_at,finished_at FROM jobs ORDER BY created_at DESC LIMIT 5").fetchall()
    return {"library_path": result["library_path"], "total_series": result["total_series"],
            "total_episodes": total_eps, "analyzed_episodes": analyzed, "pending_episodes": max(total_eps-analyzed, 0),
            "outliers": outliers, "active_jobs": active_jobs, "recent_jobs": [dict(r) for r in recent],
            "series": result["series"]}
