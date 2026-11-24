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


def test_plugin_directory_recovery_restores_previous_install(tmp_path):
    previous = tmp_path / ".FFMPEG.previous-old"
    previous.mkdir()
    (previous / "marker.txt").write_text("working", encoding="utf-8")

    plugin_manager._recover_plugin_directory(tmp_path, "FFMPEG")

    assert (tmp_path / "FFMPEG" / "marker.txt").read_text(encoding="utf-8") == "working"
    assert not previous.exists()


def test_plugin_directory_recovery_removes_previous_after_new_install_exists(tmp_path):
    target = tmp_path / "FFMPEG"
    previous = tmp_path / ".FFMPEG.previous-old"
    target.mkdir()
    previous.mkdir()
    (target / "version.txt").write_text("new", encoding="utf-8")
    (previous / "version.txt").write_text("old", encoding="utf-8")

    plugin_manager._recover_plugin_directory(tmp_path, "FFMPEG")

    assert (target / "version.txt").read_text(encoding="utf-8") == "new"
    assert not previous.exists()



def _tool_snapshot(*, ffmpeg=True, ffprobe=True, mkvmerge=False, mkvpropedit=False):
    values = {
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "mkvmerge": mkvmerge,
        "mkvpropedit": mkvpropedit,
    }
    return {
        name: {"available": available, "path": f"/plugins/{name}", "version": "test"}
        for name, available in values.items()
    }


def test_analysis_setup_does_not_download_mkvtoolnix(tmp_path, monkeypatch):
    tools = _tool_snapshot()
    monkeypatch.setattr(plugin_manager, "_can_auto_install", lambda: True)
    monkeypatch.setattr(plugin_manager, "_clean_stale_staging", lambda _root: None)
    monkeypatch.setattr(plugin_manager, "_inspect_local_tools", lambda _root: {
        name: dict(value) for name, value in tools.items()
    })
    monkeypatch.setattr(
        plugin_manager,
        "_install_mkvtoolnix",
        lambda _root: pytest.fail("MKVToolNix must remain lazy until chapter injection."),
    )

    state = plugin_manager.ensure_analysis_plugins({"plugins_path": str(tmp_path / "plugins")})

    assert state["status"] == "analysis_ready"
    assert state["installed"]["ffmpeg"]["available"]
    assert state["installed"]["ffprobe"]["available"]
    assert not state["installed"]["mkvmerge"]["available"]


def test_chapter_setup_installs_mkvtoolnix_on_demand(tmp_path, monkeypatch):
    tools = _tool_snapshot()
    monkeypatch.setattr(plugin_manager, "_can_auto_install", lambda: True)
    monkeypatch.setattr(plugin_manager, "_clean_stale_staging", lambda _root: None)

    def inspect(_root):
        return {name: dict(value) for name, value in tools.items()}

    def install_mkv(_root):
        tools["mkvmerge"]["available"] = True
        tools["mkvpropedit"]["available"] = True

    monkeypatch.setattr(plugin_manager, "_inspect_local_tools", inspect)
    monkeypatch.setattr(plugin_manager, "_install_mkvtoolnix", install_mkv)

    state = plugin_manager.ensure_chapter_plugins({"plugins_path": str(tmp_path / "plugins")})

    assert state["status"] == "ready"
    assert state["installed"]["mkvmerge"]["available"]
    assert state["installed"]["mkvpropedit"]["available"]



def test_background_setup_returns_without_waiting_for_tool_download(tmp_path, monkeypatch):
    import threading

    started = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(plugin_manager, "_BACKGROUND_THREAD", None)

    def delayed_setup(_config):
        started.set()
        release.wait(timeout=3)
        return {"status": "analysis_ready"}

    monkeypatch.setattr(plugin_manager, "ensure_analysis_plugins", delayed_setup)
    state = plugin_manager.start_background_analysis_setup({"plugins_path": str(tmp_path / "plugins")})

    try:
        assert state["status"] == "checking"
        assert started.wait(timeout=1)
        assert plugin_manager._BACKGROUND_THREAD is not None
        assert plugin_manager._BACKGROUND_THREAD.daemon
    finally:
        release.set()
        if plugin_manager._BACKGROUND_THREAD is not None:
            plugin_manager._BACKGROUND_THREAD.join(timeout=1)

def test_plugin_status_reports_local_paths_and_missing_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_manager, "load_config", lambda: {"plugins_path": str(tmp_path / "plugins")})
    status = plugin_manager.plugin_manager_status()
    assert status["plugins_path"] == str(tmp_path / "plugins")
    assert set(status["installed"]) == {"ffmpeg", "ffprobe", "mkvmerge", "mkvpropedit"}
    assert not any(item["available"] for item in status["installed"].values())
