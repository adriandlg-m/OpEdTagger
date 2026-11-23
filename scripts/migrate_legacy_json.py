from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from backend.infrastructure.config import resolve_library_path
from backend.infrastructure.database import db_session, init_db, utc_now
from backend.services.library_service import parse_season_episode


def migrate(source: Path, *, dry_run: bool = True) -> dict:
    if not source.exists():
        raise FileNotFoundError(source)
    raw_bytes = source.read_bytes()
    digest = hashlib.sha256(raw_bytes).hexdigest()
    payload = json.loads(raw_bytes.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Legacy JSON must contain an object keyed by series.")
    library = resolve_library_path()
    candidates = []
    missing = []
    for series_name, files in payload.items():
        if not isinstance(files, dict):
            continue
        series_dir = (library / series_name).resolve()
        try:
            series_dir.relative_to(library.resolve())
        except ValueError:
            missing.append(f"{series_name}: invalid series folder")
            continue
        for file_name, result in files.items():
            if not isinstance(result, dict):
                missing.append(f"{series_name}/{file_name}: malformed record")
                continue
            video = (series_dir / Path(file_name).name).resolve()
            try:
                video.relative_to(series_dir)
            except ValueError:
                missing.append(f"{series_name}/{file_name}: invalid filename")
                continue
            if not video.exists() or video.suffix.lower() != ".mkv":
                missing.append(f"{series_name}/{file_name}: file not found")
                continue
            op, ed = result.get("op"), result.get("ed")
            candidates.append((series_name, video, op, ed))
    report = {"source": str(source.resolve()), "sha256": digest, "records_in_json": sum(len(v) for v in payload.values() if isinstance(v, dict)),
              "eligible_records": len(candidates), "skipped_records": len(missing), "missing_or_invalid": missing[:30],
              "dry_run": dry_run, "imported": 0, "already_migrated": False}
    if dry_run:
        return report
    init_db()
    backup = source.with_suffix(source.suffix + ".pre-migration.bak")
    if not backup.exists():
        shutil.copy2(source, backup)
    now = utc_now()
    with db_session() as conn:
        previous = conn.execute("SELECT id FROM migration_history WHERE source_path=? AND source_hash=?",
                                 (str(source.resolve()), digest)).fetchone()
        if previous:
            report["already_migrated"] = True
            return report
        for series_name, video, op, ed in candidates:
            stat = video.stat()
            parsed = parse_season_episode(video.name)
            conn.execute("INSERT INTO series(name,directory,episode_count,last_scanned_at) VALUES(?,?,1,?) "
                         "ON CONFLICT(name) DO UPDATE SET directory=excluded.directory,last_scanned_at=excluded.last_scanned_at",
                         (series_name, str(video.parent), now))
            conn.execute("INSERT INTO episodes(series_name,file_path,file_name,file_size,file_mtime,season,episode_number,created_at,updated_at) "
                         "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(file_path) DO UPDATE SET file_size=excluded.file_size,file_mtime=excluded.file_mtime,updated_at=excluded.updated_at",
                         (series_name, str(video.resolve()), video.name, stat.st_size, stat.st_mtime,
                          parsed[0] if parsed else None, parsed[1] if parsed else None, now, now))
            episode = conn.execute("SELECT id FROM episodes WHERE file_path=?", (str(video.resolve()),)).fetchone()
            # Legacy results are retained for reference, but cannot be approved/injected
            # until re-analysis establishes a current analyzer version and file fingerprint.
            status = "STALE"
            review = "NOT_REQUIRED"
            inserted = conn.execute("INSERT INTO analysis_results(episode_id,analysis_status,review_status,injection_status,op_json,ed_json,"
                         "analyzer_version,template_signature,analyzed_file_size,analyzed_file_mtime,source,created_at,updated_at) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(episode_id) DO NOTHING",
                         (episode["id"], status, review, "NOT_REQUESTED", json.dumps(op) if op else None, json.dumps(ed) if ed else None,
                          "legacy-json", None, stat.st_size, stat.st_mtime, "legacy_json", now, now))
            report["imported"] += max(inserted.rowcount, 0)
        conn.execute("INSERT INTO migration_history(source_path,source_hash,imported_count,skipped_count,created_at) VALUES(?,?,?,?,?)",
                     (str(source.resolve()), digest, report["imported"], report["skipped_records"], now))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely import legacy anime_master.json into OpEdTagger SQLite.")
    parser.add_argument("source", type=Path, help="Path to the legacy anime_master.json")
    parser.add_argument("--apply", action="store_true", help="Apply the migration. Default is dry-run only.")
    args = parser.parse_args()
    result = migrate(args.source, dry_run=not args.apply)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not args.apply:
        print("Dry-run only. Re-run with --apply after reviewing the report.")

if __name__ == "__main__":
    main()
