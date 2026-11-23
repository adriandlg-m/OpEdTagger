from __future__ import annotations

import json
import os
from pathlib import Path

from backend.infrastructure.config import save_config
from backend.infrastructure.database import db_session, init_db, utc_now
from backend.services.analysis_service import _persist_analysis
from backend.services.library_service import get_series_detail
from core.fingerprints import file_fingerprint


def _insert_episode(path: Path, *, injection_status: str = "NOT_REQUESTED", content_fingerprint: str | None = None):
    stamp = utc_now()
    stat = path.stat()
    with db_session() as conn:
        conn.execute(
            "INSERT INTO series(name,directory,episode_count,last_scanned_at) VALUES(?,?,1,?)",
            ("Example", str(path.parent), stamp),
        )
        conn.execute(
            "INSERT INTO episodes(series_name,file_path,file_name,file_size,file_mtime,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            ("Example", str(path.resolve()), path.name, stat.st_size, stat.st_mtime, stamp, stamp),
        )
        episode_id = conn.execute("SELECT id FROM episodes WHERE file_path=?", (str(path.resolve()),)).fetchone()[0]
        conn.execute(
            "INSERT INTO analysis_results(episode_id,analysis_status,review_status,injection_status,op_json,ed_json,"
            "analyzer_version,template_signature,analyzed_file_size,analyzed_file_mtime,content_fingerprint,source,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (episode_id, "COMPLETED", "APPROVED", injection_status,
             json.dumps({"name": "OP1", "start": 90, "end": 180, "score": 250}), None,
             "chroma-template-v1", "sig", stat.st_size, stat.st_mtime, content_fingerprint, "engine", stamp, stamp),
        )
    return episode_id


def test_reanalysis_resets_previous_successful_injection_state(tmp_path, monkeypatch):
    library = tmp_path / "library"
    series_dir = library / "Example"
    series_dir.mkdir(parents=True)
    path = series_dir / "Example S01E01.mkv"
    path.write_bytes(b"video bytes for test")
    save_config({"library_path": str(library)})
    init_db()
    episode_id = _insert_episode(path, injection_status="SUCCEEDED", content_fingerprint=file_fingerprint(path))

    _persist_analysis(episode_id, path, {"name": "OP2", "start": 100.0, "end": 190.0, "score": 280.0},
                      None, "new-template-signature", expected_fingerprint=file_fingerprint(path))

    with db_session() as conn:
        row = conn.execute("SELECT review_status,injection_status,op_json FROM analysis_results WHERE episode_id=?", (episode_id,)).fetchone()
    assert row["review_status"] == "PENDING"
    assert row["injection_status"] == "NOT_REQUESTED"
    assert json.loads(row["op_json"])["name"] == "OP2"


def test_reanalysis_cannot_clear_recovery_required_injection_state(tmp_path):
    library = tmp_path / "library"
    series_dir = library / "Example"
    series_dir.mkdir(parents=True)
    path = series_dir / "Example S01E01.mkv"
    path.write_bytes(b"video bytes for test")
    save_config({"library_path": str(library)})
    init_db()
    episode_id = _insert_episode(path, injection_status="RECOVERY_REQUIRED", content_fingerprint=file_fingerprint(path))

    _persist_analysis(episode_id, path, {"name": "OP2", "start": 100.0, "end": 190.0, "score": 280.0},
                      None, "new-template-signature", expected_fingerprint=file_fingerprint(path))

    with db_session() as conn:
        row = conn.execute("SELECT injection_status FROM analysis_results WHERE episode_id=?", (episode_id,)).fetchone()
    assert row["injection_status"] == "RECOVERY_REQUIRED"


def test_series_detail_marks_same_size_and_mtime_content_change_stale(tmp_path):
    library = tmp_path / "library"
    series_dir = library / "Example"
    series_dir.mkdir(parents=True)
    path = series_dir / "Example S01E01.mkv"
    path.write_bytes(b"first content")
    save_config({"library_path": str(library)})
    init_db()
    _insert_episode(path, content_fingerprint=file_fingerprint(path))
    original_stat = path.stat()
    path.write_bytes(b"other content")  # same length as the original payload
    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    detail = get_series_detail("Example")

    assert detail["episodes"][0]["analysis_status"] == "PENDING"
    assert detail["episodes"][0]["op"] is None


def test_template_wav_cache_is_rebuilt_when_source_video_changes(tmp_path, monkeypatch):
    import subprocess
    from core import analyzer

    template_dir = tmp_path / "templates" / "Example"
    videos = template_dir / "video_files"
    videos.mkdir(parents=True)
    video = videos / "Example - OP1.webm"
    video.write_bytes(b"first source video")
    plugins = tmp_path / "plugins"
    calls = []
    monkeypatch.setattr(analyzer, "_binary", lambda _plugins, _name: Path("ffmpeg"))

    def fake_run(command, **kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(b"RIFF" + b"WAVDATA" * 20)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(analyzer.subprocess, "run", fake_run)
    first = analyzer.prepare_song_templates(template_dir, plugins)
    second = analyzer.prepare_song_templates(template_dir, plugins)
    assert first == second
    assert len(calls) == 1

    video.write_bytes(b"second source video")
    third = analyzer.prepare_song_templates(template_dir, plugins)
    assert third == first
    assert len(calls) == 2
    manifest = json.loads((template_dir / "songs_files" / ".template_wav_manifest.json").read_text(encoding="utf-8"))
    assert manifest[video.name]["source_fingerprint"] == file_fingerprint(video)


def test_template_wav_cache_removes_derived_audio_for_deleted_source(tmp_path, monkeypatch):
    import subprocess
    from core import analyzer

    template_dir = tmp_path / "templates" / "Example"
    videos = template_dir / "video_files"
    videos.mkdir(parents=True)
    video = videos / "Example - OP1.webm"
    video.write_bytes(b"source video")
    monkeypatch.setattr(analyzer, "_binary", lambda _plugins, _name: Path("ffmpeg"))

    def fake_run(command, **kwargs):
        Path(command[-1]).write_bytes(b"RIFF" + b"WAVDATA" * 20)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(analyzer.subprocess, "run", fake_run)
    generated = analyzer.prepare_song_templates(template_dir, tmp_path / "plugins")
    assert len(generated) == 1 and generated[0].exists()

    video.unlink()
    result = analyzer.prepare_song_templates(template_dir, tmp_path / "plugins")
    assert result == []
    assert not generated[0].exists()


def test_per_episode_analysis_failure_is_visible_in_series_detail(tmp_path):
    from backend.services.analysis_service import _persist_analysis_failure

    library = tmp_path / "library"
    series_dir = library / "Example"
    series_dir.mkdir(parents=True)
    path = series_dir / "Example S01E01.mkv"
    path.write_bytes(b"video bytes for test")
    save_config({"library_path": str(library)})
    init_db()
    episode_id = _insert_episode(path)
    with db_session() as conn:
        conn.execute("UPDATE analysis_results SET is_anomaly_op=1 WHERE episode_id=?", (episode_id,))

    _persist_analysis_failure(episode_id, path, "template-signature", "FFmpeg extraction failed")
    detail = get_series_detail("Example")

    episode = detail["episodes"][0]
    assert episode["analysis_status"] == "FAILED"
    assert episode["error"] == "FFmpeg extraction failed"
    assert episode["op"] is None
    with db_session() as conn:
        flags = conn.execute("SELECT is_anomaly_op,is_anomaly_ed FROM analysis_results WHERE episode_id=?", (episode_id,)).fetchone()
    assert tuple(flags) == (0, 0)
