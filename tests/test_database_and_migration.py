from __future__ import annotations

import json
from pathlib import Path

from backend.infrastructure.config import save_config
from backend.infrastructure.database import db_session, init_db
from scripts.migrate_legacy_json import migrate


def test_sqlite_schema_and_log_storage(tmp_path):
    init_db()
    with db_session() as conn:
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "series", "episodes", "analysis_results", "analysis_segment_history",
            "jobs", "job_items", "activity_logs", "migration_history", "schema_migrations",
        }.issubset(tables)
        analysis_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(analysis_results)").fetchall()
        }
        assert {"op_original_json", "ed_original_json", "confidence_version"}.issubset(analysis_columns)
        migration_versions = {
            row["version"] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        assert {4, 5}.issubset(migration_versions)
        indexes = {
            row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        }
        assert "idx_one_active_series_operation" in indexes
        conn.execute(
            "INSERT INTO activity_logs(timestamp,level,source,message) VALUES(?,?,?,?)",
            ("2026-01-01T00:00:00+00:00", "INFO", "test", "persisted"),
        )
    with db_session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM activity_logs").fetchone()[0] == 1


def test_legacy_migration_dry_run_apply_and_idempotency(tmp_path):
    library = tmp_path / "library"
    series_dir = library / "Example Show"
    series_dir.mkdir(parents=True)
    video = series_dir / "Example Show - S01E01.mkv"
    video.write_bytes(b"dummy mkv for migration metadata only")
    save_config({"library_path": str(library)})

    source = tmp_path / "anime_master.json"
    source.write_text(json.dumps({"Example Show": {
        video.name: {"op": {"name": "OP1", "start": 90, "end": 180, "score": 300}, "ed": None},
        "missing.mkv": {"op": None, "ed": None},
    }}), encoding="utf-8")

    dry = migrate(source, dry_run=True)
    assert dry["eligible_records"] == 1
    assert dry["imported"] == 0
    assert not source.with_suffix(".json.pre-migration.bak").exists()

    applied = migrate(source, dry_run=False)
    assert applied["imported"] == 1
    assert source.with_suffix(".json.pre-migration.bak").exists()
    repeated = migrate(source, dry_run=False)
    assert repeated["already_migrated"] is True
    assert repeated["imported"] == 0
    with db_session() as conn:
        record = conn.execute("SELECT analysis_status,review_status,injection_status FROM analysis_results").fetchone()
        assert tuple(record) == ("STALE", "NOT_REQUIRED", "NOT_REQUESTED")


def test_only_one_active_series_job_is_allowed(tmp_path):
    import sqlite3
    import pytest

    init_db()
    with db_session() as conn:
        conn.execute(
            "INSERT INTO jobs(id,kind,series_name,status,stage,created_at) VALUES(?,?,?,?,?,?)",
            ("job-one", "series", "Example Show", "queued", "queued", "2026-01-01T00:00:00+00:00"),
        )
    with pytest.raises(sqlite3.IntegrityError):
        with db_session() as conn:
            conn.execute(
                "INSERT INTO jobs(id,kind,series_name,status,stage,created_at) VALUES(?,?,?,?,?,?)",
                ("job-two", "series", "Example Show", "running", "running", "2026-01-01T00:00:01+00:00"),
            )
    with pytest.raises(sqlite3.IntegrityError):
        with db_session() as conn:
            conn.execute(
                "INSERT INTO jobs(id,kind,series_name,status,stage,created_at) VALUES(?,?,?,?,?,?)",
                ("job-case-variant", "series", "example show", "running", "running", "2026-01-01T00:00:01+00:00"),
            )
    with db_session() as conn:
        conn.execute("UPDATE jobs SET status='completed' WHERE id='job-one'")
        conn.execute(
            "INSERT INTO jobs(id,kind,series_name,status,stage,created_at) VALUES(?,?,?,?,?,?)",
            ("job-two", "series", "Example Show", "queued", "queued", "2026-01-01T00:00:02+00:00"),
        )
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE series_name='Example Show'").fetchone()[0] == 2


def test_interrupted_jobs_are_not_automatically_resumed(tmp_path):
    from backend.services.job_service import JobService

    init_db()
    with db_session() as conn:
        conn.execute(
            "INSERT INTO jobs(id,kind,series_name,status,stage,created_at) VALUES(?,?,?,?,?,?)",
            ("interrupted-job", "series", "Example Show", "running", "analyzing", "2026-01-01T00:00:00+00:00"),
        )
    service = JobService()
    service.recover_interrupted_jobs()
    with db_session() as conn:
        row = conn.execute("SELECT status,stage,error FROM jobs WHERE id='interrupted-job'").fetchone()
    assert row["status"] == "interrupted"
    assert row["stage"] == "interrupted"
    assert "start a new job" in row["error"]


def test_interrupted_individual_job_cleans_only_its_staged_upload(tmp_path):
    import json
    from backend.infrastructure.config import app_data_dir
    from backend.services.job_service import JobService

    init_db()
    upload_dir = app_data_dir() / "individual_uploads"
    upload_dir.mkdir(parents=True)
    staged = upload_dir / "interrupted.mkv"
    staged.write_bytes(b"temporary upload")
    with db_session() as conn:
        conn.execute(
            "INSERT INTO jobs(id,kind,series_name,status,stage,payload_json,created_at) VALUES(?,?,?,?,?,?,?)",
            ("individual-interrupted", "individual", "Example", "running", "analyzing",
             json.dumps({"staged_path": str(staged.resolve())}), "2026-01-01T00:00:00+00:00"),
        )

    JobService().recover_interrupted_jobs()

    assert not staged.exists()
    with db_session() as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id='individual-interrupted'").fetchone()
    assert row["status"] == "interrupted"


def test_migration_report_counts_only_new_analysis_records(tmp_path):
    library = tmp_path / "library"
    series_dir = library / "Example Show"
    series_dir.mkdir(parents=True)
    video = series_dir / "Example Show - S01E01.mkv"
    video.write_bytes(b"dummy mkv for migration metadata only")
    save_config({"library_path": str(library)})
    init_db()
    stat = video.stat()
    now = "2026-01-01T00:00:00+00:00"
    with db_session() as conn:
        conn.execute("INSERT INTO series(name,directory,episode_count,last_scanned_at) VALUES(?,?,1,?)",
                     (series_dir.name, str(series_dir), now))
        conn.execute("INSERT INTO episodes(series_name,file_path,file_name,file_size,file_mtime,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                     (series_dir.name, str(video.resolve()), video.name, stat.st_size, stat.st_mtime, now, now))
        episode_id = conn.execute("SELECT id FROM episodes WHERE file_path=?", (str(video.resolve()),)).fetchone()[0]
        conn.execute("INSERT INTO analysis_results(episode_id,analysis_status,analyzer_version,source,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                     (episode_id, "STALE", "legacy-json", "legacy_json", now, now))

    source = tmp_path / "anime_master.json"
    source.write_text(json.dumps({"Example Show": {video.name: {"op": None, "ed": None}}}), encoding="utf-8")
    report = migrate(source, dry_run=False)

    assert report["eligible_records"] == 1
    assert report["imported"] == 0
