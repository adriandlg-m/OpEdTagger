from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.infrastructure import config as config_module
from backend.infrastructure.config import load_config, resolve_within, save_config


def _isolate_paths(monkeypatch, tmp_path):
    app_data = tmp_path / "appdata"
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setenv("OPEDTAGGER_DATA_DIR", str(app_data))
    monkeypatch.setattr(config_module, "project_root", lambda: root)
    return app_data, root


def test_default_config_keeps_user_data_separate_and_uses_project_root_for_templates_and_plugins(tmp_path, monkeypatch):
    app_data, root = _isolate_paths(monkeypatch, tmp_path)
    config = load_config()

    assert Path(config["database_path"]).parent == app_data
    assert Path(config["template_path"]) == root / "Templates"
    assert Path(config["plugins_path"]) == root / "plugins"
    assert (app_data / "config.json").exists()


def test_load_config_migrates_old_appdata_directories_without_deleting_the_source(tmp_path, monkeypatch):
    app_data, root = _isolate_paths(monkeypatch, tmp_path)
    old_templates = app_data / "templates"
    old_plugins = app_data / "plugins"
    (old_templates / "Show" / "video_files").mkdir(parents=True)
    (old_templates / "Show" / "video_files" / "opening.webm").write_bytes(b"template-video")
    (old_plugins / "FFMPEG" / "bin").mkdir(parents=True)
    (old_plugins / "FFMPEG" / "bin" / "ffmpeg.exe").write_bytes(b"old-tool")

    app_data.mkdir(parents=True, exist_ok=True)
    (app_data / "config.json").write_text(json.dumps({
        "library_path": "",
        "template_path": str(old_templates),
        "plugins_path": str(old_plugins),
    }), encoding="utf-8")

    config = load_config()

    assert Path(config["template_path"]) == root / "Templates"
    assert Path(config["plugins_path"]) == root / "plugins"
    assert (root / "Templates" / "Show" / "video_files" / "opening.webm").read_bytes() == b"template-video"
    assert (root / "plugins" / "FFMPEG" / "bin" / "ffmpeg.exe").read_bytes() == b"old-tool"
    # The original locations remain available until the user verifies the migration.
    assert (old_templates / "Show" / "video_files" / "opening.webm").exists()
    assert (old_plugins / "FFMPEG" / "bin" / "ffmpeg.exe").exists()
    persisted = json.loads((app_data / "config.json").read_text(encoding="utf-8"))
    assert persisted["template_path"] == str(root / "Templates")
    assert persisted["plugins_path"] == str(root / "plugins")


def test_load_config_imports_original_cli_template_folder_on_fresh_install(tmp_path, monkeypatch):
    _app_data, root = _isolate_paths(monkeypatch, tmp_path)
    legacy = root / "template" / "Show" / "video_files"
    legacy.mkdir(parents=True)
    (legacy / "OP1.webm").write_bytes(b"legacy-video")

    config = load_config()

    assert Path(config["template_path"]) == root / "Templates"
    assert (root / "Templates" / "Show" / "video_files" / "OP1.webm").read_bytes() == b"legacy-video"


def test_save_config_validates_and_normalizes_paths(tmp_path, monkeypatch):
    _app_data, _root = _isolate_paths(monkeypatch, tmp_path)
    library = tmp_path / "Anime"
    library.mkdir()
    config = save_config({"library_path": str(library)})
    assert config["library_path"] == str(library.resolve())
    assert Path(config["template_path"]).is_dir()
    assert Path(config["plugins_path"]).is_dir()
    with pytest.raises(ValueError, match="existing directory"):
        save_config({"library_path": str(tmp_path / "missing")})


def test_resolve_within_rejects_traversal(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    inside = root / "Show" / "episode.mkv"
    inside.parent.mkdir()
    inside.touch()
    assert resolve_within(root, inside) == inside.resolve()
    with pytest.raises(ValueError, match="outside"):
        resolve_within(root, tmp_path / "outside.mkv")


def test_resolve_within_rejects_symlink_escape(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    outside = tmp_path / "outside.mkv"
    outside.touch()
    link = root / "escape.mkv"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are not available in this environment.")
    with pytest.raises(ValueError):
        resolve_within(root, link)


def test_save_config_rejects_managed_directories_inside_library(tmp_path, monkeypatch):
    _app_data, _root = _isolate_paths(monkeypatch, tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    backup_dir = library / "backups"
    with pytest.raises(ValueError, match="inside the media library"):
        save_config({"library_path": str(library), "backup_path": str(backup_dir)})
    assert not backup_dir.exists()


def test_scan_library_reconciles_removed_episodes_and_series(tmp_path, monkeypatch):
    from backend.infrastructure.database import db_session, init_db
    from backend.services.library_service import list_series, scan_library

    _isolate_paths(monkeypatch, tmp_path)
    library = tmp_path / "library"
    show = library / "Show One"
    show.mkdir(parents=True)
    first = show / "Show One S01E01.mkv"
    second = show / "Show One S01E02.mkv"
    first.write_bytes(b"episode one")
    second.write_bytes(b"episode two")
    save_config({"library_path": str(library)})
    init_db()

    initial = scan_library()
    assert initial["total_series"] == 1 and initial["total_episodes"] == 2
    second.unlink()
    scan_library()
    assert list_series(scan=False)["series"][0]["episodes"] == 1

    import shutil
    shutil.rmtree(show)
    replacement = library / "Show Two"
    replacement.mkdir()
    (replacement / "Show Two S01E01.mkv").write_bytes(b"replacement")
    scan_library()
    summary = list_series(scan=False)
    assert [item["name"] for item in summary["series"]] == ["Show Two"]
    with db_session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM series").fetchone()[0] == 1
