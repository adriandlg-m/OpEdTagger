from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from backend.infrastructure.config import load_config

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS series (
    name TEXT PRIMARY KEY,
    directory TEXT NOT NULL,
    episode_count INTEGER NOT NULL DEFAULT 0,
    last_scanned_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS series_metadata (
    series_name TEXT PRIMARY KEY REFERENCES series(name) ON DELETE CASCADE,
    tmdb_id INTEGER NOT NULL,
    tmdb_name TEXT NOT NULL,
    tmdb_original_name TEXT,
    poster_file TEXT NOT NULL,
    poster_path TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS series_artwork (
    series_name TEXT PRIMARY KEY REFERENCES series(name) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    external_id TEXT,
    selected_title TEXT NOT NULL,
    original_title TEXT,
    poster_file TEXT NOT NULL,
    source_image_url TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS series_anilist_links (
    series_name TEXT PRIMARY KEY REFERENCES series(name) ON DELETE CASCADE,
    anilist_id INTEGER NOT NULL CHECK(anilist_id > 0),
    linked_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_series_anilist_id ON series_anilist_links(anilist_id);
CREATE TABLE IF NOT EXISTS integration_secrets (
    provider TEXT PRIMARY KEY,
    encrypted_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_name TEXT NOT NULL REFERENCES series(name) ON DELETE CASCADE,
    file_path TEXT NOT NULL UNIQUE,
    file_name TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    file_mtime REAL NOT NULL,
    season INTEGER,
    episode_number INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_episodes_series ON episodes(series_name);
CREATE TABLE IF NOT EXISTS analysis_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER NOT NULL UNIQUE REFERENCES episodes(id) ON DELETE CASCADE,
    analysis_status TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'NOT_REQUIRED',
    injection_status TEXT NOT NULL DEFAULT 'NOT_REQUESTED',
    op_json TEXT,
    ed_json TEXT,
    is_anomaly_op INTEGER NOT NULL DEFAULT 0,
    is_anomaly_ed INTEGER NOT NULL DEFAULT 0,
    analyzer_version TEXT NOT NULL,
    template_signature TEXT,
    analyzed_file_size INTEGER,
    analyzed_file_mtime REAL,
    content_fingerprint TEXT,
    source TEXT NOT NULL DEFAULT 'engine',
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    series_name TEXT,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    current_item TEXT,
    total_items INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_series_job
    ON jobs(series_name) WHERE kind='series' AND status IN ('queued','running');
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_series_job_ci
    ON jobs(lower(series_name)) WHERE kind='series' AND status IN ('queued','running');
CREATE TABLE IF NOT EXISTS job_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    episode_id INTEGER REFERENCES episodes(id) ON DELETE SET NULL,
    item_key TEXT NOT NULL,
    file_name TEXT NOT NULL,
    status TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    result_json TEXT,
    error TEXT,
    UNIQUE(job_id, item_key)
);
CREATE INDEX IF NOT EXISTS idx_job_items_job ON job_items(job_id);
CREATE TABLE IF NOT EXISTS activity_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    level TEXT NOT NULL,
    source TEXT NOT NULL,
    message TEXT NOT NULL,
    detail TEXT,
    job_id TEXT,
    series_name TEXT,
    episode_name TEXT
);
CREATE INDEX IF NOT EXISTS idx_activity_logs_timestamp ON activity_logs(timestamp DESC);
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS migration_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    imported_count INTEGER NOT NULL,
    skipped_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(source_path, source_hash)
);
"""


def database_path() -> Path:
    cfg = load_config()
    return Path(cfg["database_path"]).expanduser().resolve()


def connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30, isolation_level="DEFERRED")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


@contextmanager
def db_session() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db_session() as conn:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version,description,applied_at) VALUES(?,?,?)",
            (1, "Initial SQLite schema", utc_now()),
        )

        migration_done = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version=2"
        ).fetchone()
        if not migration_done:
            # Preserve posters chosen in older releases once. Their image files
            # remain where they are; current reads/writes use series_artwork.
            legacy_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='series_metadata'"
            ).fetchone()
            if legacy_table:
                legacy_rows = conn.execute(
                    "SELECT sm.series_name, sm.tmdb_id, sm.tmdb_name, sm.tmdb_original_name, "
                    "sm.poster_file, sm.updated_at "
                    "FROM series_metadata sm JOIN series s ON s.name=sm.series_name "
                    "WHERE sm.poster_file IS NOT NULL AND sm.poster_file <> ''"
                ).fetchall()
                for row in legacy_rows:
                    conn.execute(
                        "INSERT OR IGNORE INTO series_artwork "
                        "(series_name,provider,external_id,selected_title,original_title,poster_file,source_image_url,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (row['series_name'], 'tmdb_legacy', str(row['tmdb_id']), row['tmdb_name'] or row['series_name'],
                         row['tmdb_original_name'] or '', row['poster_file'], None, row['updated_at'] or utc_now()),
                    )

            # The old credential is no longer used. Remove it once while
            # retaining existing poster files and their metadata references.
            conn.execute("DELETE FROM integration_secrets WHERE provider='tmdb'")
            conn.execute(
                "INSERT INTO schema_migrations(version,description,applied_at) VALUES(?,?,?)",
                (2, "Provider-neutral artwork and AniList public search", utc_now()),
            )

        metadata_migration_done = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version=3"
        ).fetchone()
        if not metadata_migration_done:
            # Keep only the user's explicit AniList media association. Synopsis,
            # genres, banner URLs and other catalogue fields are fetched on demand
            # and are intentionally not persisted in SQLite.
            legacy_links = conn.execute(
                "SELECT series_name, external_id, updated_at FROM series_artwork "
                "WHERE provider='anilist' AND external_id IS NOT NULL AND external_id <> ''"
            ).fetchall()
            for row in legacy_links:
                try:
                    anilist_id = int(row["external_id"])
                except (TypeError, ValueError):
                    continue
                if anilist_id > 0:
                    conn.execute(
                        "INSERT OR IGNORE INTO series_anilist_links(series_name,anilist_id,linked_at) VALUES(?,?,?)",
                        (row["series_name"], anilist_id, row["updated_at"] or utc_now()),
                    )
            conn.execute(
                "INSERT INTO schema_migrations(version,description,applied_at) VALUES(?,?,?)",
                (3, "AniList series links for on-demand metadata", utc_now()),
            )

        confidence_migration_done = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version=4"
        ).fetchone()
        if not confidence_migration_done:
            analysis_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(analysis_results)").fetchall()
            }
            for column, declaration in (
                ("op_original_json", "TEXT"),
                ("ed_original_json", "TEXT"),
                ("confidence_version", "TEXT"),
            ):
                if column not in analysis_columns:
                    conn.execute(f"ALTER TABLE analysis_results ADD COLUMN {column} {declaration}")

            # Preserve last known detections as originals when upgrading.
            conn.execute(
                "UPDATE analysis_results SET op_original_json=op_json "
                "WHERE op_original_json IS NULL AND op_json IS NOT NULL"
            )
            conn.execute(
                "UPDATE analysis_results SET ed_original_json=ed_json "
                "WHERE ed_original_json IS NULL AND ed_json IS NOT NULL"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analysis_segment_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
                    segment_type TEXT NOT NULL CHECK(segment_type IN ('OP','ED')),
                    action TEXT NOT NULL,
                    before_json TEXT,
                    after_json TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_analysis_segment_history_episode "
                "ON analysis_segment_history(episode_id, id DESC)"
            )
            conn.execute(
                "INSERT INTO schema_migrations(version,description,applied_at) VALUES(?,?,?)",
                (4, "Per-segment confidence, immutable analysis snapshots and review history", utc_now()),
            )


def utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
