from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from core.media_tools import build_chapters, write_chapters_xml


def test_build_chapters_with_opening_and_ending():
    chapters = build_chapters(
        {"name": "OP1", "start": 90, "end": 180, "score": 315},
        {"name": "ED1", "start": 1300, "end": 1390, "score": 314},
        1440,
    )
    assert chapters[0] == {"title": "Prologue", "start": 0.0, "end": 90.0}
    assert any(chapter["title"] == "OP1" for chapter in chapters)
    assert any(chapter["title"] == "ED1" for chapter in chapters)
    assert chapters[-1] == {"title": "Ending", "start": 1390.0, "end": 1440}


def test_chapter_tail_keeps_episode_name_when_no_ending_was_detected():
    chapters = build_chapters({"name": "OP1", "start": 90, "end": 180}, None, 1440)
    assert chapters[-1] == {"title": "Episode", "start": 180.0, "end": 1440}


def test_chapter_plan_without_opening_uses_episode_before_ending():
    chapters = build_chapters(None, {"name": "ED1", "start": 1300, "end": 1390}, 1440)
    assert chapters[0] == {"title": "Episode", "start": 0.0, "end": 1300.0}
    assert chapters[-1]["title"] == "Ending"


def test_build_chapters_rejects_missing_and_invalid_data():
    with pytest.raises(ValueError, match="no detected"):
        build_chapters(None, None, 100)
    with pytest.raises(ValueError, match="timestamps"):
        build_chapters({"start": 10, "end": 120}, None, 100)
    with pytest.raises(ValueError, match="overlap"):
        build_chapters({"start": 10, "end": 80}, {"start": 70, "end": 95}, 100)
    with pytest.raises(ValueError, match="finite positive"):
        build_chapters({"start": 1, "end": 2}, None, math.nan)


def test_write_chapters_xml_escapes_titles_and_uses_spanish_language(tmp_path):
    target = tmp_path / "chapters.xml"
    write_chapters_xml([{"title": "OP & ED <test>", "start": 0, "end": 30}], target)
    parsed = ET.parse(target)
    assert parsed.findtext(".//ChapterString") == "OP & ED <test>"
    assert parsed.findtext(".//ChapterLanguage") == "spa"
    assert parsed.findtext(".//ChapLanguageIETF") == "es"
    assert parsed.findtext(".//EditionUID")
    assert parsed.findtext(".//ChapterUID")


def test_sampled_fingerprint_changes_when_file_content_changes(tmp_path):
    from core.fingerprints import file_fingerprint

    target = tmp_path / "episode.mkv"
    target.write_bytes(b"A" * (2 * 1024 * 1024))
    before = file_fingerprint(target)
    target.write_bytes(b"B" * (2 * 1024 * 1024))
    after = file_fingerprint(target)
    assert before != after


def test_injection_restores_backup_when_post_verification_fails(tmp_path, monkeypatch):
    import json
    import subprocess
    from core import media_tools

    mkv = tmp_path / "episode.mkv"
    mkv.write_bytes(b"original-mkv-content")
    plugin_dir = tmp_path / "plugins"
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(media_tools, "_find_tool", lambda _root, name: Path(name))
    monkeypatch.setattr(media_tools, "_binary", lambda _root, name: Path(name))
    calls = {"merge": 0}

    def fake_run(command, **kwargs):
        if command[0] == "mkvmerge":
            calls["merge"] += 1
            return subprocess.CompletedProcess(command, 0, json.dumps({"chapters": []}), "")
        if command[0] == "ffprobe":
            return subprocess.CompletedProcess(command, 0, json.dumps({"chapters": []}), "")
        mkv.write_bytes(b"partially-modified-mkv-content")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(media_tools.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="Post-injection verification failed"):
        media_tools.inject_chapters_with_backup(
            mkv, [{"title": "Episode", "start": 0, "end": 100}],
            plugin_dir, backup_dir, replace_existing=False,
        )

    assert mkv.read_bytes() == b"original-mkv-content"
    backups = list(backup_dir.glob("*.backup-*.mkv"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"original-mkv-content"
    assert calls["merge"] == 1


def test_injection_accepts_exact_verified_chapter_list(tmp_path, monkeypatch):
    import json
    import subprocess
    from core import media_tools

    mkv = tmp_path / "episode.mkv"
    mkv.write_bytes(b"original-mkv-content")
    plugin_dir = tmp_path / "plugins"
    backup_dir = tmp_path / "backups"
    expected = [{"title": "Episode", "start": 0.0, "end": 100.0}]
    monkeypatch.setattr(media_tools, "_find_tool", lambda _root, name: Path(name))
    monkeypatch.setattr(media_tools, "_binary", lambda _root, name: Path(name))

    def fake_run(command, **kwargs):
        if command[0] == "mkvmerge":
            return subprocess.CompletedProcess(command, 0, json.dumps({"chapters": []}), "")
        if command[0] == "mkvpropedit":
            mkv.write_bytes(b"modified-mkv-content")
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[0] == "ffprobe":
            return subprocess.CompletedProcess(command, 0, json.dumps({"chapters": [
                {"start_time": "0.000000", "end_time": "100.000000", "tags": {"title": "Episode"}}
            ]}), "")
        raise AssertionError(f"Unexpected tool invocation: {command[0]}")

    monkeypatch.setattr(media_tools.subprocess, "run", fake_run)
    backup = media_tools.inject_chapters_with_backup(
        mkv, expected, plugin_dir, backup_dir, replace_existing=False,
    )

    assert mkv.read_bytes() == b"modified-mkv-content"
    assert backup.read_bytes() == b"original-mkv-content"
