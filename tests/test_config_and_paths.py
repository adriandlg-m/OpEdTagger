from __future__ import annotations

from pathlib import Path

import pytest

from backend.infrastructure.config import load_config, resolve_within, save_config


def test_default_config_uses_user_data_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("OPEDTAGGER_DATA_DIR", str(tmp_path / "appdata"))
    config = load_config()
    assert Path(config["database_path"]).parent == tmp_path / "appdata"
    assert Path(config["template_path"]).is_absolute()
    assert (tmp_path / "appdata" / "config.json").exists()


def test_save_config_validates_and_normalizes_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("OPEDTAGGER_DATA_DIR", str(tmp_path / "appdata"))
    library = tmp_path / "Anime"
    library.mkdir()
    config = save_config({"library_path": str(library)})
    assert config["library_path"] == str(library.resolve())
    assert Path(config["template_path"]).is_dir()
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


def test_save_config_rejects_managed_directories_inside_library(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    backup_dir = library / "backups"
    with pytest.raises(ValueError, match="inside the media library"):
        save_config({"library_path": str(library), "backup_path": str(backup_dir)})
    assert not backup_dir.exists()


def test_scan_library_reconciles_removed_episodes_and_series(tmp_path):
    from backend.infrastructure.database import db_session, init_db
    from backend.services.library_service import list_series, scan_library

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
