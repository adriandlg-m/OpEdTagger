from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from threading import RLock
from typing import Any

_LOCK = RLock()


def app_data_dir() -> Path:
    override = os.getenv("OPEDTAGGER_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "OpEdTagger"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OpEdTagger"
    return Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "OpEdTagger"


def config_path() -> Path:
    override = os.getenv("OPEDTAGGER_CONFIG_PATH")
    return Path(override).expanduser().resolve() if override else app_data_dir() / "config.json"


def default_config() -> dict[str, Any]:
    data_dir = app_data_dir()
    return {
        "library_path": "",
        "template_path": str(data_dir / "templates"),
        "plugins_path": str(data_dir / "plugins"),
        "database_path": str(data_dir / "opedtagger.sqlite3"),
        "backup_path": str(data_dir / "backups"),
        "log_path": str(data_dir / "logs" / "opedtagger.log"),
        "max_workers": 1,
        "auto_download_templates": False,
    }


def load_config() -> dict[str, Any]:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        if not path.exists():
            cfg = default_config()
            temp_path = path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            temp_path.replace(path)
            return cfg
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(stored, dict):
                raise ValueError("The configuration root must be an object.")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(f"Could not read configuration file '{path}': {exc}") from exc
        merged = default_config()
        merged.update(stored)
        return merged


def save_config(changes: dict[str, Any]) -> dict[str, Any]:
    allowed = {"library_path", "template_path", "plugins_path", "backup_path", "auto_download_templates"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"Unsupported settings: {', '.join(sorted(unknown))}")
    with _LOCK:
        cfg = load_config()
        for key, value in changes.items():
            cfg[key] = value
        managed_paths = {}
        for key in ("template_path", "plugins_path", "backup_path"):
            raw = str(cfg.get(key) or "").strip()
            if not raw:
                raise ValueError(f"{key} cannot be empty.")
            managed_paths[key] = Path(raw).expanduser().resolve()
            cfg[key] = str(managed_paths[key])
        library_raw = str(cfg.get("library_path") or "").strip()
        if library_raw:
            library = Path(library_raw).expanduser().resolve()
            if not library.exists() or not library.is_dir():
                raise ValueError("library_path must point to an existing directory.")
            for key, managed_path in managed_paths.items():
                try:
                    managed_path.relative_to(library)
                except ValueError:
                    continue
                raise ValueError(f"{key} must not be located inside the media library; otherwise templates or MKV backups could be indexed as episodes.")
            try:
                app_data_dir().resolve().relative_to(library)
            except ValueError:
                pass
            else:
                raise ValueError("The application data directory must not be located inside the media library.")
            cfg["library_path"] = str(library)
        for managed_path in managed_paths.values():
            managed_path.mkdir(parents=True, exist_ok=True)
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_path.replace(path)
        return cfg


def resolve_library_path(cfg: dict[str, Any] | None = None, *, must_exist: bool = True) -> Path:
    cfg = cfg or load_config()
    raw = str(cfg.get("library_path") or "").strip()
    if not raw:
        raise ValueError("Library path is not configured. Set it in Settings first.")
    path = Path(raw).expanduser().resolve()
    if must_exist and (not path.exists() or not path.is_dir()):
        raise ValueError(f"Library directory does not exist or is not a directory: {path}")
    return path


def resolve_within(root: Path, candidate: Path) -> Path:
    root = root.resolve()
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("The requested path is outside the configured library.") from exc
    return candidate
