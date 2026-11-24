from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from backend.services import plugin_manager


def test_archive_member_validation_rejects_path_traversal(tmp_path):
    with pytest.raises(ValueError, match="Unsafe archive member path"):
        plugin_manager._safe_member_path(tmp_path, "../outside.exe")
    with pytest.raises(ValueError, match="Drive-qualified"):
        plugin_manager._safe_member_path(tmp_path, "C:/Windows/system32/tool.exe")


def test_zip_extraction_rejects_path_traversal_before_writing(tmp_path):
    archive = tmp_path / "malicious.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../outside.exe", b"not allowed")

    target = tmp_path / "extracted"
    with pytest.raises(ValueError, match="Unsafe archive member path"):
        plugin_manager._extract_zip_safely(archive, target)
    assert not (tmp_path / "outside.exe").exists()


def test_zip_extraction_accepts_valid_archive(tmp_path):
    archive = tmp_path / "valid.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("ffmpeg/bin/ffmpeg.exe", b"binary")

    target = tmp_path / "extracted"
    plugin_manager._extract_zip_safely(archive, target)
    assert (target / "ffmpeg" / "bin" / "ffmpeg.exe").read_bytes() == b"binary"


def test_plugin_status_reports_local_paths_and_missing_tools(tmp_path, monkeypatch):
    from backend.infrastructure import config as config_module

    monkeypatch.setattr(plugin_manager, "load_config", lambda: {"plugins_path": str(tmp_path / "plugins")})
    status = plugin_manager.plugin_manager_status()
    assert status["plugins_path"] == str(tmp_path / "plugins")
    assert set(status["installed"]) == {"ffmpeg", "ffprobe", "mkvmerge", "mkvpropedit"}
    assert not any(item["available"] for item in status["installed"].values())
