from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import app


def test_local_api_health_dashboard_and_settings():
    with TestClient(app) as client:
        health = client.get("/api/system/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        dashboard = client.get("/api/dashboard")
        assert dashboard.status_code == 200
        assert dashboard.json()["total_series"] == 0

        settings = client.get("/api/settings")
        assert settings.status_code == 200
        assert "database_path" in settings.json()

        backups = client.get("/api/settings/backups")
        assert backups.status_code == 200
        assert "backup_path" in backups.json()
        assert "recovery_count" in backups.json()
        assert "available_free_bytes" in backups.json()

        logs = client.get("/api/logs")
        assert logs.status_code == 200
        assert isinstance(logs.json()["logs"], list)


def test_successful_injection_refreshes_episode_file_metadata(tmp_path, monkeypatch):
    import json
    from pathlib import Path

    from backend.infrastructure.config import save_config
    from backend.infrastructure.database import db_session, init_db, utc_now
    from backend.services.library_service import list_series
    from core.fingerprints import ANALYZER_VERSION, file_fingerprint
    from backend.services import chapter_service

    # MKVToolNix provisioning is a separate Windows integration; this API
    # regression test stubs the extracted chapter-injection service.
    monkeypatch.setattr(chapter_service, "ensure_chapter_plugins", lambda _cfg: {"status": "ready"})

    library = tmp_path / "library"
    series_dir = library / "Example"
    series_dir.mkdir(parents=True)
    video = series_dir / "Example S01E01.mkv"
    video.write_bytes(b"original mkv bytes")
    save_config({"library_path": str(library)})
    init_db()
    stamp = utc_now()
    stat = video.stat()
    with db_session() as conn:
        conn.execute("INSERT INTO series(name,directory,episode_count,last_scanned_at) VALUES(?,?,1,?)",
                     ("Example", str(series_dir), stamp))
        conn.execute("INSERT INTO episodes(series_name,file_path,file_name,file_size,file_mtime,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                     ("Example", str(video.resolve()), video.name, stat.st_size, stat.st_mtime, stamp, stamp))
        episode_id = conn.execute("SELECT id FROM episodes WHERE file_path=?", (str(video.resolve()),)).fetchone()[0]
        conn.execute(
            "INSERT INTO analysis_results(episode_id,analysis_status,review_status,injection_status,op_json,analyzer_version,"
            "template_signature,analyzed_file_size,analyzed_file_mtime,content_fingerprint,source,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (episode_id, "COMPLETED", "APPROVED", "NOT_REQUESTED",
             json.dumps({"name": "OP1", "start": 10, "end": 20, "score": 2.0}), ANALYZER_VERSION,
             "signature", stat.st_size, stat.st_mtime, file_fingerprint(video), "engine", stamp, stamp),
        )

    backup = tmp_path / "backup.mkv"
    backup.write_bytes(b"original mkv bytes")
    monkeypatch.setattr(chapter_service, "preview_episode_chapters", lambda _episode_id: {
        "chapters": [{"title": "Episode", "start": 0.0, "end": 60.0}],
    })

    def fake_inject(path, _chapters, _plugins, _backup_root, *, replace_existing, episode_id=None):
        path.write_bytes(b"mkv bytes after chapter injection")
        transaction_dir = tmp_path / "fake_transaction"
        transaction_dir.mkdir(exist_ok=True)
        return {"transaction_path": str(transaction_dir), "backup_path": str(backup)}

    monkeypatch.setattr(chapter_service, "inject_chapters_with_backup", fake_inject)
    monkeypatch.setattr(chapter_service, "finalize_injection_transaction", lambda _transaction: None)

    with TestClient(app) as client:
        response = client.post(f"/api/episodes/{episode_id}/inject", json={"confirm": True})
        assert response.status_code == 200, response.text

    new_stat = video.stat()
    with db_session() as conn:
        episode = conn.execute("SELECT file_size,file_mtime FROM episodes WHERE id=?", (episode_id,)).fetchone()
        result = conn.execute("SELECT injection_status,analyzed_file_size,analyzed_file_mtime,content_fingerprint FROM analysis_results WHERE episode_id=?",
                              (episode_id,)).fetchone()
    assert episode["file_size"] == new_stat.st_size
    assert episode["file_mtime"] == new_stat.st_mtime
    assert result["injection_status"] == "SUCCEEDED"
    assert result["analyzed_file_size"] == new_stat.st_size
    assert result["analyzed_file_mtime"] == new_stat.st_mtime
    assert result["content_fingerprint"] == file_fingerprint(video)
    summary = list_series(scan=False)
    assert summary["series"][0]["analyzed"] == 1
    assert summary["series"][0]["injected"] == 1


def test_series_artwork_metadata_endpoint_returns_unlinked_state(tmp_path):
    from backend.infrastructure.config import save_config
    from backend.infrastructure.database import db_session, init_db, utc_now

    library = tmp_path / "library"
    (library / "Example").mkdir(parents=True)
    save_config({"library_path": str(library)})
    init_db()
    with db_session() as conn:
        conn.execute(
            "INSERT INTO series(name,directory,episode_count,last_scanned_at) VALUES(?,?,?,?)",
            ("Example", str(library / "Example"), 0, utc_now()),
        )

    with TestClient(app) as client:
        response = client.get("/api/artwork/series/Example/metadata")

    assert response.status_code == 200
    assert response.json() == {"linked": False, "metadata": None, "source": None}


def test_series_artwork_metadata_endpoint_returns_live_anilist_details(tmp_path, monkeypatch):
    from backend.infrastructure.config import save_config
    from backend.infrastructure.database import db_session, init_db, utc_now
    from backend.services import artwork_service

    library = tmp_path / "library"
    (library / "Example").mkdir(parents=True)
    save_config({"library_path": str(library)})
    init_db()
    with db_session() as conn:
        conn.execute(
            "INSERT INTO series(name,directory,episode_count,last_scanned_at) VALUES(?,?,?,?)",
            ("Example", str(library / "Example"), 0, utc_now()),
        )
        conn.execute(
            "INSERT INTO series_anilist_links(series_name,anilist_id,linked_at) VALUES(?,?,?)",
            ("Example", 4242, utc_now()),
        )

    monkeypatch.setattr(artwork_service, "_METADATA_CACHE", {})
    monkeypatch.setattr(artwork_service, "_get_anime_details", lambda media_id: {
        "id": media_id,
        "title": {"userPreferred": "Example Anime"},
        "description": "Synopsis from AniList.",
        "format": "TV",
        "seasonYear": 2024,
        "episodes": 12,
        "genres": ["Drama"],
        "bannerImage": None,
        "coverImage": {},
    })

    with TestClient(app) as client:
        response = client.get("/api/artwork/series/Example/metadata")

    assert response.status_code == 200
    payload = response.json()
    assert payload["linked"] is True
    assert payload["metadata"]["anilist_id"] == 4242
    assert payload["metadata"]["title"] == "Example Anime"
    assert payload["metadata"]["description"] == "Synopsis from AniList."
    assert payload["metadata"]["year"] == 2024



def test_manual_correction_preserves_original_detection_and_records_history(tmp_path, monkeypatch):
    import json

    from backend.infrastructure.config import save_config
    from backend.infrastructure.database import db_session, init_db, utc_now
    from backend.api.routers import chapters
    from core.fingerprints import ANALYZER_VERSION, file_fingerprint

    library = tmp_path / "library"
    series_dir = library / "Example"
    series_dir.mkdir(parents=True)
    video = series_dir / "Example S01E01.mkv"
    video.write_bytes(b"original mkv bytes")
    save_config({"library_path": str(library)})
    init_db()
    stamp = utc_now()
    stat = video.stat()
    original = {"name": "OP1", "start": 10.0, "end": 100.0, "score": 2.0}
    with db_session() as conn:
        conn.execute(
            "INSERT INTO series(name,directory,episode_count,last_scanned_at) VALUES(?,?,1,?)",
            ("Example", str(series_dir), stamp),
        )
        conn.execute(
            "INSERT INTO episodes(series_name,file_path,file_name,file_size,file_mtime,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            ("Example", str(video.resolve()), video.name, stat.st_size, stat.st_mtime, stamp, stamp),
        )
        episode_id = conn.execute(
            "SELECT id FROM episodes WHERE file_path=?", (str(video.resolve()),)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO analysis_results(episode_id,analysis_status,review_status,injection_status,"
            "op_json,op_original_json,confidence_version,analyzer_version,template_signature,"
            "analyzed_file_size,analyzed_file_mtime,content_fingerprint,source,is_anomaly_op,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                episode_id, "COMPLETED", "PENDING", "NOT_REQUESTED",
                json.dumps({**original, "confidence_score": 42, "confidence_band": "LOW"}),
                json.dumps(original), "heuristic-confidence-v1", ANALYZER_VERSION,
                "signature", stat.st_size, stat.st_mtime, file_fingerprint(video), "engine", 1, stamp, stamp,
            ),
        )

    # This test focuses on review persistence/history; file identity and
    # containment guards are exercised by the injection-service tests.
    review_globals = chapters.review_episode.__globals__
    monkeypatch.setitem(review_globals, "resolve_library_path", lambda: library.resolve())
    monkeypatch.setitem(review_globals, "resolve_within", lambda _root, candidate: candidate.resolve())
    monkeypatch.setitem(review_globals, "assert_analysis_file_unchanged", lambda _episode, _result: None)
    monkeypatch.setitem(review_globals, "get_mkv_duration", lambda *_args: 120.0)

    from backend.api.schemas.models import ReviewUpdate
    response = chapters.review_episode(
        episode_id,
        ReviewUpdate(op={"name": "OP1", "start": 20.0, "end": 110.0, "score": 2.0}),
    )
    assert response["review_status"] == "MANUALLY_REVIEWED"

    with db_session() as conn:
        row = conn.execute(
            "SELECT op_json,op_original_json,review_status,is_anomaly_op FROM analysis_results WHERE episode_id=?",
            (episode_id,),
        ).fetchone()
        history = conn.execute(
            "SELECT segment_type,action,before_json,after_json FROM analysis_segment_history "
            "WHERE episode_id=? ORDER BY id",
            (episode_id,),
        ).fetchall()

    current = json.loads(row["op_json"])
    preserved_original = json.loads(row["op_original_json"])
    assert current["start"] == 20.0
    assert current["manual_verified"] is True
    assert preserved_original == original
    assert row["review_status"] == "MANUALLY_REVIEWED"
    assert row["is_anomaly_op"] == 0
    assert len(history) == 1
    assert history[0]["segment_type"] == "OP"
    assert history[0]["action"] == "CORRECTED"
    assert json.loads(history[0]["before_json"])["start"] == 10.0
    assert json.loads(history[0]["after_json"])["start"] == 20.0
